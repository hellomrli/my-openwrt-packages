import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from scripts import catchup_server as cs
from scripts import upstream


class SlowHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        try:
            self.respond()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def respond(self):
        if self.path == "/slow-headers":
            self.connection.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
            while not self.server.stop_writes.wait(0.02):
                self.connection.sendall(b"x")
        elif self.path == "/slow-body":
            self.send_response(200)
            self.send_header("Content-Length", "10000")
            self.end_headers()
            while not self.server.stop_writes.wait(0.02):
                self.wfile.write(b"x")
                self.wfile.flush()
        elif self.path.startswith("/redirect/"):
            count = int(self.path.rsplit("/", 1)[1])
            self.server.stop_writes.wait(0.04)
            self.send_response(302)
            self.send_header("Content-Length", "0")
            self.send_header("Location", f"/redirect/{count - 1}" if count else "/ok")
            self.end_headers()
        else:
            body = b"ok" if self.path == "/ok" else b"not found"
            self.send_response(200 if self.path == "/ok" else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"no_proxy": "*", "NO_PROXY": "*"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
        self.server.stop_writes = threading.Event()
        worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.stop_writes.set)
        self.base = "http://%s:%s" % self.server.server_address

    def fetch(self, path, seconds=1.0):
        with upstream.open_with_deadline(Request(self.base + path), time.monotonic() + seconds) as response:
            return response.status, response.read(1024 * 1024)

    def test_normal_response_and_http_error_cleanup(self):
        self.assertEqual(self.fetch("/ok"), (200, b"ok"))
        with self.assertRaises(HTTPError) as raised:
            self.fetch("/missing")
        self.assertEqual(raised.exception.code, 404)
        self.assertTrue(raised.exception.closed)

    def test_trickling_headers_do_not_reset_total_deadline(self):
        self.assert_deadline("/slow-headers")

    def test_trickling_body_does_not_reset_total_deadline(self):
        self.assert_deadline("/slow-body")

    def test_redirects_share_one_deadline(self):
        self.assert_deadline("/redirect/8")

    def assert_deadline(self, path):
        start = time.monotonic()
        with self.assertRaises(TimeoutError):
            self.fetch(path, 0.15)
        self.assertLess(time.monotonic() - start, 0.8)
        self.assertEqual(self.fetch("/ok"), (200, b"ok"))

    def test_catchup_releases_its_slot_after_a_slow_upstream(self):
        service = cs.CatchupService(self.base, {"1": 1})
        server = cs.CatchupHTTPServer(("127.0.0.1", 0), service)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def slow_playlist(cid, start, end, budget):
            with service.request(self.base + "/slow-body", budget) as response:
                response.read(1024 * 1024)
            return "unreachable"

        connection = HTTPConnection(*server.server_address, timeout=2)
        self.addCleanup(connection.close)
        short_budget = cs.Budget(0.15)
        with patch.object(service, "playlist", side_effect=slow_playlist), patch.object(cs, "Budget", return_value=short_budget):
            connection.request("GET", "/catchup/1/100/60/index.m3u8")
            response = connection.getresponse()
            self.assertEqual(response.status, 504)
            response.read()
        acquired = 0
        try:
            for _ in range(4):
                self.assertTrue(server.slots.acquire(timeout=0.5))
                acquired += 1
        finally:
            for _ in range(acquired):
                server.slots.release()


class ResolverTests(unittest.TestCase):
    def test_numeric_address_does_not_need_a_dns_worker(self):
        resolver = upstream.Resolver(workers=0)
        result = resolver.resolve("127.0.0.1", 80, time.monotonic() + 1)
        self.assertEqual(result[0][-1], ("127.0.0.1", 80))

    def test_stuck_dns_is_bounded_and_does_not_block_numeric_hosts(self):
        resolver = upstream.Resolver(workers=1)
        release = threading.Event()
        original = socket.getaddrinfo

        def lookup(host, port, **kwargs):
            if kwargs.get("flags") == socket.AI_NUMERICHOST:
                return original(host, port, **kwargs)
            release.wait(5)
            return original("127.0.0.1", port, type=socket.SOCK_STREAM)

        with patch.object(upstream.socket, "getaddrinfo", side_effect=lookup):
            try:
                start = time.monotonic()
                with self.assertRaises(TimeoutError):
                    resolver.resolve("stuck.invalid", 80, start + 0.08)
                self.assertLess(time.monotonic() - start, 0.5)
                work = resolver.pending[("stuck.invalid", 80)]
                with self.assertRaisesRegex(TimeoutError, "busy"):
                    resolver.resolve("other.invalid", 80, time.monotonic() + 1)
                self.assertEqual(len(resolver.pending), 1)
                self.assertTrue(resolver.resolve("127.0.0.1", 80, time.monotonic() + 1))
            finally:
                release.set()
                for pending in list(resolver.pending.values()):
                    self.assertTrue(pending[0].wait(2))
            self.assertTrue(work[0].is_set())
            self.assertFalse(resolver.pending)

    def test_dns_cache_is_bounded_and_reused(self):
        resolver = upstream.Resolver(cache_size=2)
        calls = []
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 80))]

        def lookup(host, port, **kwargs):
            if kwargs.get("flags"):
                raise socket.gaierror(socket.EAI_NONAME, "not numeric")
            calls.append(host)
            return addresses

        with patch.object(upstream.socket, "getaddrinfo", side_effect=lookup):
            for name in ("a.invalid", "a.invalid", "b.invalid", "c.invalid"):
                self.assertEqual(resolver.resolve(name, 80, time.monotonic() + 1), addresses)
        self.assertEqual(calls, ["a.invalid", "b.invalid", "c.invalid"])
        self.assertEqual(len(resolver.cache), 2)


@unittest.skipUnless(shutil.which("openssl"), "optional local TLS fixture requires openssl")
class TLSDeadlineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="iptv-tls-test-")
        self.addCleanup(temp.cleanup)
        cert, key = (Path(temp.name) / name for name in ("cert.pem", "key.pem"))
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                        "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "1",
                        "-subj", "/CN=localhost", "-addext", "subjectAltName=IP:127.0.0.1"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=5)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        self.trusted = ssl.create_default_context(cafile=str(cert))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
        self.server.stop_writes = threading.Event()
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.stop_writes.set)
        self.base = "https://%s:%s" % self.server.server_address
        env = patch.dict(os.environ, {"no_proxy": "*", "NO_PROXY": "*"})
        env.start()
        self.addCleanup(env.stop)

    def test_https_keeps_certificate_validation_enabled(self):
        with self.assertRaises(URLError) as raised:
            with upstream.open_with_deadline(Request(self.base + "/ok"), time.monotonic() + 2):
                self.fail("self-signed certificate unexpectedly accepted")
        self.assertIsInstance(raised.exception.reason, ssl.SSLCertVerificationError)
        with upstream.open_with_deadline(Request(self.base + "/ok"), time.monotonic() + 2,
                                         context=self.trusted) as response:
            self.assertEqual(response.read(), b"ok")

    def test_https_trickling_headers_are_interrupted(self):
        start = time.monotonic()
        with self.assertRaises(TimeoutError):
            with upstream.open_with_deadline(Request(self.base + "/slow-headers"), start + 0.15,
                                             context=self.trusted) as response:
                response.read()
        self.assertLess(time.monotonic() - start, 0.8)


if __name__ == "__main__":
    unittest.main()
