"""Bounded HTTP reads, including DNS, headers and slowly arriving response bodies."""

import socket
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from functools import partial
from http.client import HTTPConnection, HTTPSConnection
from urllib.error import HTTPError
from urllib.request import HTTPHandler, HTTPSHandler, build_opener


class Resolver:
    """A stuck libc resolver must not pin an HTTP handler or spawn unlimited threads."""

    def __init__(self, workers=4, cache_size=128, ttl=60):
        self.workers, self.cache_size, self.ttl = workers, cache_size, ttl
        self.lock = threading.Lock()
        self.cache = OrderedDict()
        self.pending = {}

    def resolve(self, host, port, deadline):
        remaining(deadline)
        try:
            return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM,
                                      flags=socket.AI_NUMERICHOST)
        except socket.gaierror:
            pass
        key = (host, port)
        with self.lock:
            hit = self.cache.get(key)
            if hit and time.monotonic() - hit[0] < self.ttl:
                return hit[1]
            work = self.pending.get(key)
            if work is None:
                if len(self.pending) >= self.workers:
                    raise TimeoutError("upstream DNS resolver is busy")
                work = [threading.Event(), None, None]
                self.pending[key] = work
                worker = threading.Thread(target=self._lookup, args=(key, work), daemon=True,
                                          name="iptv-dns")
                try:
                    worker.start()
                except Exception:
                    del self.pending[key]
                    raise
        if not work[0].wait(remaining(deadline)):
            raise TimeoutError("upstream DNS deadline exceeded")
        remaining(deadline)
        if work[2] is not None:
            raise work[2]
        return work[1]

    def _lookup(self, key, work):
        try:
            work[1] = socket.getaddrinfo(*key, type=socket.SOCK_STREAM)
        except Exception as exc:
            work[2] = exc
        finally:
            with self.lock:
                if work[2] is None:
                    self.cache[key] = (time.monotonic(), work[1])
                    self.cache.move_to_end(key)
                    while len(self.cache) > self.cache_size:
                        self.cache.popitem(last=False)
                self.pending.pop(key, None)
                work[0].set()


def remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError("upstream request deadline exceeded")
    return value


RESOLVER = Resolver()


class DeadlineIO:
    def __init__(self, deadline):
        self.deadline = deadline
        self.lock = threading.Lock()
        self.sockets = []
        self.expired = False
        self.timer = None

    def __enter__(self):
        self.timer = threading.Timer(remaining(self.deadline), self._expire)
        self.timer.daemon = True
        self.timer.start()
        return self

    def track(self, sock):
        with self.lock:
            if not self.expired and time.monotonic() < self.deadline:
                self.sockets.append(sock)
                return sock
        self._shutdown(sock)
        sock.close()
        raise TimeoutError("upstream request deadline exceeded")

    @staticmethod
    def _shutdown(sock):
        # close() alone leaves makefile() readers alive; shutdown wakes their recv().
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _expire(self):
        with self.lock:
            self.expired = True
            for sock in self.sockets:
                self._shutdown(sock)

    def __exit__(self, exc_type, exc, tb):
        self.timer.cancel()
        with self.lock:
            for sock in self.sockets:
                sock.close()
            self.sockets.clear()
            expired = self.expired
        if expired:
            raise TimeoutError("upstream request deadline exceeded")
        remaining(self.deadline)

    def connect(self, address, timeout=None, source_address=None, **kwargs):
        error = None
        for family, kind, proto, _, target in RESOLVER.resolve(*address, self.deadline):
            sock = self.track(socket.socket(family, kind, proto))
            try:
                sock.settimeout(min(4.0, remaining(self.deadline)))
                if source_address:
                    sock.bind(source_address)
                sock.connect(target)
                remaining(self.deadline)
                return sock
            except OSError as exc:
                sock.close()
                error = exc
        remaining(self.deadline)
        if error is not None:
            raise error
        raise OSError("upstream DNS returned no addresses")


class ConnectionMixin:
    def __init__(self, *args, guard, **kwargs):
        super().__init__(*args, **kwargs)
        self.guard = guard
        self._create_connection = guard.connect


class DeadlineHTTPConnection(ConnectionMixin, HTTPConnection):
    pass


class DeadlineHTTPSConnection(ConnectionMixin, HTTPSConnection):
    def connect(self):
        HTTPConnection.connect(self)
        self.sock.settimeout(min(4.0, remaining(self.guard.deadline)))
        self.sock = self.guard.track(self._context.wrap_socket(
            self.sock, server_hostname=self._tunnel_host or self.host,
            do_handshake_on_connect=False))
        self.sock.do_handshake()
        remaining(self.guard.deadline)


class DeadlineHTTPHandler(HTTPHandler):
    def __init__(self, guard):
        super().__init__()
        self.guard = guard

    def http_open(self, request):
        return self.do_open(partial(DeadlineHTTPConnection, guard=self.guard), request)


class DeadlineHTTPSHandler(HTTPSHandler):
    def __init__(self, guard, context=None):
        super().__init__(context=context)
        self.guard = guard

    def https_open(self, request):
        return self.do_open(partial(DeadlineHTTPSConnection, guard=self.guard), request,
                            context=self._context)


@contextmanager
def open_with_deadline(request, deadline, context=None):
    with DeadlineIO(deadline) as guard:
        opener = build_opener(DeadlineHTTPHandler(guard), DeadlineHTTPSHandler(guard, context))
        try:
            response = opener.open(request, timeout=min(4.0, remaining(deadline)))
        except HTTPError as exc:
            exc.close()
            raise
        with response:
            yield response
