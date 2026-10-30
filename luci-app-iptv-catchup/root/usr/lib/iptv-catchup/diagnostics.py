"""Bounded, URL-free diagnostics for handled catchup HTTP requests."""

import json
import logging
import threading
import time
import uuid
from collections import Counter, deque
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


LOG_MODES = ("off", "errors", "all")
ENDPOINTS = ("status", "catchup", "timeshift", "segment", "subscription", "unknown")
MAX_RECENT = 20
SLOW_REQUEST_MS = 2000
LOGGER = logging.getLogger("iptv.catchup.access")


@dataclass
class ResolutionMetrics:
    upstream_requests: int = 0
    upstream_ms: float = 0.0
    segment_probes: int = 0
    anchor_cache_hits: int = 0
    target_cache_hits: int = 0
    sequence_hint_hits: int = 0
    sequence_corrections: int = 0
    sequence_offset: int | None = None


@dataclass
class RequestTrace:
    method: str
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started: float = field(default_factory=lambda: time.monotonic())
    timestamp: float = field(default_factory=lambda: time.time())
    endpoint: str = "unknown"
    channel: str | None = None
    media_start: int | None = None
    media_end: int | None = None
    status: int = 500
    error: str | None = None
    upstream_status: int | None = None
    client_disconnected: bool = False
    metrics: ResolutionMetrics = field(default_factory=ResolutionMetrics)


class Diagnostics:
    def __init__(self, access_log: str = "errors"):
        if access_log not in LOG_MODES:
            raise ValueError("access-log must be off, errors or all")
        self.access_log = access_log
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.in_flight = 0
        self.completed = 0
        self.failed = 0
        self.slow = 0
        self.disconnected = 0
        self.duration_ms = 0.0
        self.max_duration_ms = 0.0
        self.responses = Counter()
        self.endpoints = Counter()
        self.resolution = asdict(ResolutionMetrics())
        del self.resolution["sequence_offset"]
        self.recent_errors = deque(maxlen=MAX_RECENT)
        self.recent_slow = deque(maxlen=MAX_RECENT)

    def begin(self, method: str) -> RequestTrace:
        trace = RequestTrace(method=method if method in ("GET", "HEAD") else "OTHER")
        with self.lock:
            self.in_flight += 1
        return trace

    def finish(self, trace: RequestTrace) -> None:
        duration = max(0.0, (time.monotonic() - trace.started) * 1000)
        failed = trace.status >= 400 or trace.client_disconnected
        slow = duration >= SLOW_REQUEST_MS
        endpoint = trace.endpoint if trace.endpoint in ENDPOINTS else "unknown"
        metrics = asdict(trace.metrics)
        metrics["upstream_ms"] = round(metrics["upstream_ms"], 3)
        record = {
            "event": "iptv_request",
            "request_id": trace.request_id,
            "timestamp": datetime.fromtimestamp(trace.timestamp, timezone.utc).isoformat(timespec="seconds"),
            "method": trace.method,
            "endpoint": endpoint,
            "status": trace.status,
            "duration_ms": round(duration, 3),
            "resolution": metrics,
        }
        # Only typed route fields and fixed error codes are retained, never raw
        # request URLs, upstream session tokens, headers or exception messages.
        for name in ("channel", "media_start", "media_end", "error", "upstream_status"):
            value = getattr(trace, name)
            if value is not None:
                record[name] = value
        if trace.client_disconnected:
            record["client_disconnected"] = True
        with self.lock:
            self.in_flight -= 1
            self.completed += 1
            self.failed += int(failed)
            self.slow += int(slow)
            self.disconnected += int(trace.client_disconnected)
            self.duration_ms += duration
            self.max_duration_ms = max(self.max_duration_ms, duration)
            self.responses[str(trace.status)] += 1
            self.endpoints[endpoint] += 1
            for name in self.resolution:
                self.resolution[name] += getattr(trace.metrics, name)
            if failed:
                self.recent_errors.append(record)
            if slow:
                self.recent_slow.append(record)
        if self.access_log == "all" or self.access_log == "errors" and (failed or slow):
            LOGGER.log(logging.WARNING if failed or slow else logging.INFO,
                       "%s", json.dumps(record, separators=(",", ":")))

    def snapshot(self) -> dict:
        with self.lock:
            resolution = dict(self.resolution)
            resolution["upstream_ms"] = round(resolution["upstream_ms"], 3)
            return {
                "uptime_seconds": max(0, int(time.monotonic() - self.started)),
                "access_log": self.access_log,
                "in_flight": self.in_flight,
                "completed_requests": self.completed,
                "failed_requests": self.failed,
                "slow_requests": self.slow,
                "client_disconnects": self.disconnected,
                "responses": dict(self.responses),
                "endpoints": dict(self.endpoints),
                "latency_ms": {"average": round(self.duration_ms / self.completed, 3) if self.completed else 0,
                               "max": round(self.max_duration_ms, 3)},
                "resolution": resolution,
                "recent_errors": deepcopy(list(self.recent_errors)),
                "recent_slow_requests": deepcopy(list(self.recent_slow)),
            }


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
