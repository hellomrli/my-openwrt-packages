import io
import json
import logging
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from scripts import catchup_server as cs
from scripts import diagnostics as diag


class DiagnosticsTests(unittest.TestCase):
    def test_timing_totals_and_slow_error_records(self):
        with patch.object(diag.time, "monotonic", return_value=100):
            diagnostics = diag.Diagnostics("off")
            trace = diagnostics.begin("GET")
        trace.endpoint, trace.status, trace.error = "segment", 504, "upstream_timeout"
        trace.metrics.upstream_requests = 3
        trace.metrics.upstream_ms = 2300.125
        trace.metrics.segment_probes = 2
        trace.metrics.sequence_offset = 2
        with patch.object(diag.time, "monotonic", return_value=102.5):
            diagnostics.finish(trace)
            status = diagnostics.snapshot()
        self.assertEqual(status["uptime_seconds"], 2)
        self.assertEqual(status["in_flight"], 0)
        self.assertEqual(status["completed_requests"], 1)
        self.assertEqual(status["failed_requests"], 1)
        self.assertEqual(status["slow_requests"], 1)
        self.assertEqual(status["latency_ms"], {"average": 2500, "max": 2500})
        self.assertEqual(status["responses"], {"504": 1})
        self.assertEqual(status["endpoints"], {"segment": 1})
        self.assertEqual(status["resolution"]["upstream_ms"], 2300.125)
        self.assertEqual(status["resolution"]["segment_probes"], 2)
        self.assertNotIn("sequence_offset", status["resolution"])
        self.assertEqual(status["recent_errors"][0]["resolution"]["sequence_offset"], 2)
        self.assertEqual(status["recent_errors"], status["recent_slow_requests"])

    def test_history_is_bounded_detached_and_not_evicted_by_health_checks(self):
        diagnostics = diag.Diagnostics("off")
        for _ in range(diag.MAX_RECENT + 5):
            trace = diagnostics.begin("GET")
            trace.endpoint, trace.status = "/untrusted-secret", 404
            trace.started -= 3
            diagnostics.finish(trace)
        for _ in range(30):
            trace = diagnostics.begin("HEAD")
            trace.endpoint, trace.status = "status", 200
            diagnostics.finish(trace)
        status = diagnostics.snapshot()
        self.assertEqual(len(status["recent_errors"]), diag.MAX_RECENT)
        self.assertEqual(len(status["recent_slow_requests"]), diag.MAX_RECENT)
        self.assertEqual(status["endpoints"], {"unknown": diag.MAX_RECENT + 5, "status": 30})
        self.assertNotIn("untrusted-secret", json.dumps(status))
        status["recent_errors"][0]["resolution"]["segment_probes"] = 999
        self.assertEqual(diagnostics.snapshot()["recent_errors"][0]["resolution"]["segment_probes"], 0)
        self.assertEqual(status["recent_slow_requests"][0]["resolution"]["segment_probes"], 0)

    def test_modes_filter_logs_without_disabling_statistics(self):
        for mode, expected in (("off", 0), ("errors", 3), ("all", 4)):
            with self.subTest(mode=mode), patch.object(diag.LOGGER, "log") as log:
                diagnostics = diag.Diagnostics(mode)
                for code, slow, disconnected in ((200, False, False), (404, False, False),
                                                  (302, True, False), (200, False, True)):
                    trace = diagnostics.begin("GET")
                    trace.status, trace.client_disconnected = code, disconnected
                    if slow:
                        trace.started -= 3
                    diagnostics.finish(trace)
                self.assertEqual(log.call_count, expected)
                for call in log.call_args_list:
                    self.assertEqual(json.loads(call.args[2])["event"], "iptv_request")
                self.assertEqual(diagnostics.snapshot()["completed_requests"], 4)
                self.assertEqual(diagnostics.snapshot()["failed_requests"], 2)
                self.assertEqual(diagnostics.snapshot()["client_disconnects"], 1)
        with self.assertRaises(ValueError):
            diag.Diagnostics("invalid")

    def test_concurrent_accounting(self):
        diagnostics = diag.Diagnostics("off")

        def complete(_):
            trace = diagnostics.begin("GET")
            trace.endpoint, trace.status = "segment", 302
            trace.metrics.segment_probes = 1
            diagnostics.finish(trace)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(complete, range(100)))
        status = diagnostics.snapshot()
        self.assertEqual(status["in_flight"], 0)
        self.assertEqual(status["completed_requests"], 100)
        self.assertEqual(status["resolution"]["segment_probes"], 100)
        self.assertEqual(status["responses"], {"302": 100})


class Response(io.BytesIO):
    status = 206

    def geturl(self):
        return "http://gateway.test/1/index.m3u8"


class ResolutionMetricsTests(unittest.TestCase):
    def setUp(self):
        self.service = cs.CatchupService("http://gateway.test", {"1": 1})
        self.unix, self.seq = int(time.time()) - 60, 1000
        url = (f"http://gateway.test/PLTV/224/1/1/202610/300000/{self.unix}-1-{self.seq}.hls.ts"
               "?token=upstream-secret")
        self.service.anchors["1"] = (time.monotonic(), cs.Anchor(url, self.unix, self.seq))

    def test_counts_probe_search_cache_and_neighbor_correction(self):
        first = self.unix - 100
        expected = {first: self.seq - 8, first + 10: self.seq - 7}

        def fetch(request, deadline):
            match = cs.SEGMENT_RE.fullmatch(urlsplit(request.full_url).path)
            if int(match["seq"]) == expected[int(match["unix"])]:
                return Response(b"\x47" + b"x" * 187)
            raise HTTPError(request.full_url, 404, "not found", {}, io.BytesIO())

        with patch.object(cs, "open_with_deadline", side_effect=fetch):
            search = cs.Budget()
            self.service.resolve_segment("1", first, self.seq - 10, search)
            cached = cs.Budget()
            self.service.resolve_segment("1", first, self.seq - 10, cached)
            neighbor = cs.Budget()
            self.service.resolve_segment("1", first + 10, self.seq - 9, neighbor)
        self.assertEqual(search.metrics.upstream_requests, 5)
        self.assertEqual(search.metrics.segment_probes, 5)
        self.assertEqual(search.metrics.anchor_cache_hits, 1)
        self.assertEqual(search.metrics.sequence_hint_hits, 0)
        self.assertEqual(cached.metrics.target_cache_hits, 1)
        self.assertEqual(cached.metrics.upstream_requests, 0)
        self.assertEqual(cached.metrics.segment_probes, 0)
        self.assertEqual(neighbor.metrics.sequence_hint_hits, 1)
        self.assertEqual(neighbor.metrics.upstream_requests, 1)
        self.assertEqual(neighbor.metrics.segment_probes, 1)
        for budget in (search, cached, neighbor):
            self.assertEqual(budget.metrics.sequence_offset, 2)
            self.assertEqual(budget.metrics.sequence_corrections, 1)

    def test_failed_probe_search_is_accounted(self):
        errors = []

        def missing(request, deadline):
            error = HTTPError(request.full_url, 404, "not found", {}, io.BytesIO())
            errors.append(error)
            raise error

        budget = cs.Budget()
        with patch.object(cs, "open_with_deadline", side_effect=missing), self.assertRaises(cs.ServiceError) as raised:
            self.service.resolve_segment("1", self.unix - 100, self.seq - 10, budget)
        self.assertEqual(raised.exception.reason, "segment_missing")
        self.assertEqual(budget.metrics.upstream_requests, len(cs.DRIFTS))
        self.assertEqual(budget.metrics.segment_probes, len(cs.DRIFTS))
        self.assertTrue(all(error.closed for error in errors))

    def test_upstream_timing_includes_body_and_failed_attempts(self):
        with patch.object(cs.time, "monotonic", return_value=100) as clock:
            budget = cs.Budget()
            with patch.object(cs, "open_with_deadline", return_value=Response(b"\x47")):
                with self.service.request("http://gateway.test/segment", budget) as response:
                    self.assertEqual(response.read(), b"\x47")
                    clock.return_value = 100.025
            with patch.object(cs, "open_with_deadline", return_value=Response()), self.assertRaises(TimeoutError):
                with self.service.request("http://gateway.test/segment", budget):
                    clock.return_value = 100.075
                    raise TimeoutError("private upstream URL")
        self.assertEqual(budget.metrics.upstream_requests, 2)
        self.assertAlmostEqual(budget.metrics.upstream_ms, 75)

    def test_invalid_media_and_expired_budget_metrics(self):
        budget = cs.Budget()
        with patch.object(cs, "open_with_deadline", return_value=Response(b"<html>")), self.assertRaises(cs.ServiceError) as raised:
            self.service.probe("http://gateway.test/segment", budget)
        self.assertEqual(raised.exception.reason, "invalid_ts")
        self.assertEqual(budget.metrics.segment_probes, 1)
        self.assertEqual(budget.metrics.upstream_requests, 1)
        expired = cs.Budget(-1)
        with patch.object(cs, "open_with_deadline") as fetch, self.assertRaises(cs.ServiceError) as raised:
            self.service.probe("http://gateway.test/segment", expired)
        self.assertEqual(raised.exception.reason, "budget_exhausted")
        self.assertEqual(expired.metrics.segment_probes, 0)
        fetch.assert_not_called()


class DiagnosticsHTTPTests(unittest.TestCase):
    def setUp(self):
        self.service = cs.CatchupService("http://gateway.test", {"1": 1, "2": 1})
        self.server = cs.CatchupHTTPServer(("127.0.0.1", 0), self.service, access_log="off")
        worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        worker.start()
        self.addCleanup(worker.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.conn = HTTPConnection(*self.server.server_address, timeout=3)
        self.addCleanup(self.conn.close)

    def request(self, path, method="GET", headers=None):
        self.conn.request(method, path, headers=headers or {})
        response = self.conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    def snapshot(self):
        # A subsequent request on the same connection cannot overtake completion
        # accounting for the previous request, unlike a second client connection.
        status, _, body = self.request("/status")
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result["status"], "ok")
        self.assertIn("cached_targets", result)
        return result["diagnostics"]

    def test_ids_on_head_errors_and_keepalive_ignore_client_id(self):
        status, headers, _ = self.request("/missing?token=client-secret", headers={
            "X-Request-ID": "untrusted-id", "Authorization": "Bearer private-auth",
            "User-Agent": "private-agent", "X-Forwarded-For": "private-client",
        })
        self.assertEqual(status, 404)
        request_id = headers["X-Request-ID"]
        self.assertRegex(request_id, r"^[0-9a-f]{32}$")
        self.assertEqual(headers["Access-Control-Expose-Headers"], "X-Request-ID")
        snapshot = self.snapshot()
        self.assertEqual(snapshot["recent_errors"][0]["request_id"], request_id)
        for secret in ("untrusted-id", "client-secret", "private-auth", "private-agent", "private-client", "/missing"):
            self.assertNotIn(secret, json.dumps(snapshot))
        status, headers, body = self.request("/status", "HEAD")
        self.assertEqual((status, body), (200, b""))
        self.assertGreater(int(headers["Content-Length"]), 0)
        self.assertNotEqual(headers["X-Request-ID"], request_id)
        self.assertEqual(self.snapshot()["completed_requests"], 3)

    def test_logs_redact_upstream_urls_queries_and_client_address(self):
        self.server.diagnostics.access_log = "all"
        upstream_url = "http://gateway.test/segment.ts?token=upstream-secret"
        with self.assertLogs(diag.LOGGER, level=logging.INFO) as logs:
            with patch.object(self.service, "check_range"), patch.object(
                self.service, "resolve_segment", return_value=(upstream_url, 1)
            ):
                status, headers, _ = self.request("/segment/1/1792854900/1.ts?token=client-secret")
            self.assertEqual(status, 302)
            self.assertEqual(headers["Location"], upstream_url)
            self.request("/private-path?token=client-secret")
            self.snapshot()
            self.server.diagnostics.access_log = "off"
        records = [json.loads(record.getMessage()) for record in logs.records]
        segment = next(record for record in records if record["endpoint"] == "segment")
        self.assertEqual(segment["request_id"], headers["X-Request-ID"])
        self.assertEqual(segment["channel"], "1")
        self.assertEqual(segment["media_start"], 1792854900)
        for secret in (upstream_url, "upstream-secret", "client-secret", "private-path", "127.0.0.1"):
            self.assertNotIn(secret, json.dumps(records))

    def test_playlist_and_segment_report_real_resolution_metrics(self):
        unix = int(time.time()) - 60
        url = f"http://gateway.test/PLTV/224/1/1/202610/300000/{unix}-1-1000.hls.ts?token=secret"
        live = Response(("#EXTM3U\n#EXTINF:10,\n" + url + "\n").encode())
        segment = Response(b"\x47" + b"x" * 187)
        with patch.object(cs, "open_with_deadline", side_effect=[live, segment]) as fetch:
            status, _, body = self.request(f"/catchup/1/{unix - 30}/20/index.m3u8")
            self.assertEqual(status, 200)
            self.assertIn(b"#EXT-X-ENDLIST", body)
            snapshot = self.snapshot()
            self.assertEqual(snapshot["resolution"]["upstream_requests"], 2)
            self.assertEqual(snapshot["resolution"]["segment_probes"], 1)
            self.assertEqual(snapshot["resolution"]["anchor_cache_hits"], 1)
            status, headers, _ = self.request(f"/segment/1/{unix - 30}/997.ts")
            self.assertEqual(status, 302)
            self.assertIn("token=secret", headers["Location"])
            snapshot = self.snapshot()
            self.assertEqual(snapshot["resolution"]["target_cache_hits"], 1)
            self.assertEqual(fetch.call_count, 2)
        self.assertTrue(live.closed and segment.closed)
        self.assertNotIn("secret", json.dumps(snapshot))

    def test_failure_categories_and_upstream_http_status(self):
        http_error = HTTPError("http://gateway.test/?token=secret", 429, "private-secret", {}, io.BytesIO())
        cases = ((cs.ServiceError(404, "private-secret", "segment_missing"), 404, "segment_missing"),
                 (TimeoutError("private-secret"), 504, "upstream_timeout"),
                 (URLError(TimeoutError("private-secret")), 504, "upstream_timeout"),
                 (URLError(ConnectionRefusedError("private-secret")), 502, "upstream_connection"),
                 (http_error, 502, "upstream_http"),
                 (ValueError("private-secret"), 502, "invalid_upstream_or_config"))
        for error, expected, reason in cases:
            with self.subTest(error=reason), patch.object(self.service, "playlist", side_effect=error):
                status, _, _ = self.request("/catchup/1/1792854900/60/index.m3u8")
                self.assertEqual(status, expected)
                record = self.snapshot()["recent_errors"][-1]
                self.assertEqual(record["error"], reason)
                self.assertEqual(record["resolution"]["upstream_requests"], 0)
                self.assertNotIn("secret", json.dumps(record))
                if error is http_error:
                    self.assertEqual(record["upstream_status"], 429)
                    self.assertTrue(http_error.closed)

    def test_invalid_time_and_path_are_accounted_without_upstream_io(self):
        with patch.object(self.service, "request") as upstream:
            for path, reason in (("/catchup/1/invalid/60/index.m3u8", "invalid_time"),
                                 ("http://[invalid", "invalid_path")):
                self.assertEqual(self.request(path, headers={"Host": "localhost"})[0], 400)
                self.assertEqual(self.snapshot()["recent_errors"][-1]["error"], reason)
            upstream.assert_not_called()

    def test_busy_response_and_status_do_not_need_upstream_slots(self):
        for _ in range(4):
            self.server.slots.acquire()
        try:
            status, headers, _ = self.request("/timeshift/1/1792854900/index.m3u8")
            self.assertEqual(status, 503)
            self.assertEqual(headers["Retry-After"], "2")
            snapshot = self.snapshot()
            self.assertEqual(snapshot["in_flight"], 1)
            self.assertEqual(snapshot["failed_requests"], 1)
            self.assertEqual(snapshot["recent_errors"][-1]["error"], "busy")
            self.assertEqual(snapshot["resolution"]["upstream_requests"], 0)
        finally:
            for _ in range(4):
                self.server.slots.release()

    def test_concurrent_requests_keep_metrics_isolated(self):
        entered = [threading.Event(), threading.Event()]
        release = threading.Event()

        def blocked(cid, start, end, budget):
            budget.metrics.upstream_requests = int(cid)
            budget.metrics.segment_probes = int(cid)
            entered[int(cid) - 1].set()
            if not release.wait(3):
                raise TimeoutError()
            raise cs.ServiceError(404, "missing", "segment_missing")

        def request(cid):
            conn = HTTPConnection(*self.server.server_address, timeout=3)
            try:
                conn.request("GET", f"/catchup/{cid}/1792854900/60/index.m3u8")
                response = conn.getresponse()
                response.read()
                conn.request("HEAD", "/status")
                conn.getresponse().read()
                return response.status
            finally:
                conn.close()

        with patch.object(self.service, "playlist", side_effect=blocked), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(request, cid) for cid in ("1", "2")]
            try:
                self.assertTrue(all(event.wait(2) for event in entered))
                self.assertEqual(self.snapshot()["in_flight"], 3)
            finally:
                release.set()
            self.assertEqual([future.result(timeout=3) for future in futures], [404, 404])
        snapshot = self.snapshot()
        self.assertEqual(snapshot["failed_requests"], 2)
        self.assertEqual(snapshot["resolution"]["upstream_requests"], 3)
        errors = snapshot["recent_errors"]
        self.assertEqual(len({record["request_id"] for record in errors}), 2)
        for record in errors:
            self.assertEqual(record["resolution"]["upstream_requests"], int(record["channel"]))
            self.assertEqual(record["resolution"]["segment_probes"], int(record["channel"]))

    def test_write_timeout_never_retries_response_or_holds_upstream_slots(self):
        handler = object.__new__(cs.Handler)
        handler.server, handler.command, handler.path = self.server, "GET", "/IPTV.m3u"

        def failed_write(code, *args):
            handler.trace.status = code
            acquired = 0
            try:
                for _ in range(4):
                    self.assertTrue(self.server.slots.acquire(blocking=False))
                    acquired += 1
            finally:
                for _ in range(acquired):
                    self.server.slots.release()
            raise TimeoutError("client stopped reading")

        with patch.object(self.service, "document", return_value="#EXTM3U\n"), patch.object(
            handler, "send_body", side_effect=failed_write
        ) as send:
            handler.do_GET()
        send.assert_called_once()
        self.assertTrue(handler.close_connection)
        snapshot = self.server.diagnostics.snapshot()
        self.assertEqual(snapshot["in_flight"], 0)
        self.assertEqual(snapshot["client_disconnects"], 1)
        self.assertEqual(snapshot["recent_errors"][-1]["status"], 200)
        self.assertTrue(snapshot["recent_errors"][-1]["client_disconnected"])


if __name__ == "__main__":
    unittest.main()
