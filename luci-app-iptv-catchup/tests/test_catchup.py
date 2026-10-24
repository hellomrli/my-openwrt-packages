import io
import json
import os
import threading
import time
import unittest
from datetime import datetime, timezone
from http.client import HTTPConnection
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urljoin, urlsplit

from scripts import catchup_server as cs


NOW = 1792855000
ANCHOR_TIME = NOW - 33
ANCHOR_SEQ = 1750168375
CID = "3221225934"


def anchor_at(unix=ANCHOR_TIME, seq=ANCHOR_SEQ):
    dt = datetime.fromtimestamp(unix, timezone.utc)
    bucket = dt.strftime("%d%H") + ("00" if dt.minute < 30 else "30")
    url = (f"http://gateway.test/prefix/PLTV/77777777/224/{CID}/1/"
           f"{dt:%Y%m}/{bucket}/{unix}-1-{seq}.hls.ts?zduration=10&ts_min=1"
           "&prog_time=20261024150000&IASHttpSessionId=a%2Bb&ispcode=1108&ztecid=" + CID)
    return cs.Anchor(url, unix, seq)


class Response(io.BytesIO):
    def __init__(self, body, status=200, url="http://gateway.test/index.m3u8"):
        super().__init__(body)
        self.status, self.url = status, url

    def geturl(self):
        return self.url


class TimestampTests(unittest.TestCase):
    def test_dates_are_independent_of_host_timezone(self):
        expected = int(datetime(2026, 10, 24, 11, 30, tzinfo=timezone.utc).timestamp())
        old = os.environ.get("TZ")
        try:
            for tz in ("UTC", "Asia/Shanghai", "America/New_York"):
                with self.subTest(tz=tz):
                    os.environ["TZ"] = tz
                    time.tzset()
                    self.assertEqual(cs.parse_ts("20261024193000"), expected)
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()

    def test_unix_seconds(self):
        self.assertEqual(cs.parse_ts(str(NOW)), NOW)

    def test_bad_dates_and_milliseconds(self):
        for value in ("20260230000000", "1792855000000", "-1", "no", "", "${(b)yyyyMMddHHmmss}"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                cs.parse_ts(value)

    def test_legacy_duration(self):
        self.assertEqual(cs.parse_range(str(NOW), "120"), (NOW, NOW + 120))

    def test_reversed_equal_negative_and_oversized_ranges(self):
        for start, end in ((str(NOW), str(NOW - 1)), (str(NOW), str(NOW)),
                           ("20261024193000", "20261024190000"),
                           (str(NOW), "0"), (str(NOW), "-1"), (str(NOW), "14401"),
                           (str(NOW), str(NOW + 14401))):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                cs.parse_range(start, end)


class AnchorTests(unittest.TestCase):
    def test_latest_segment_and_relative_uri(self):
        body = ("#EXTM3U\n#EXTINF:10.0,\n1/202610/241500/1792854967-1-123.hls.ts?token=abc\n"
                "#EXTINF:10.0,\n1/202610/241500/1792854977-1-124.hls.ts?token=def\n")
        anchor = cs.parse_anchor(body, "http://gateway.test/PLTV/224/1/index.m3u8?session=bad")
        self.assertEqual((anchor.unix, anchor.seq), (1792854977, 124))
        self.assertEqual(urlsplit(anchor.url).query, "token=def")
        self.assertTrue(anchor.url.startswith("http://gateway.test/PLTV/224/1/1/"))

    def test_bad_media_and_unsupported_encryption(self):
        for body in ("<html>not HLS</html>", "#EXTM3U\n", "#EXTM3U\n#EXTINF:6,\n" + anchor_at().url,
                     '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\n',
                     "#EXTM3U\n#EXTINF:10,\ninvalid.ts\n"):
            with self.subTest(body=body), self.assertRaises(cs.ServiceError):
                cs.parse_anchor(body, "http://gateway.test/index.m3u8")

    def test_half_hour_and_calendar_buckets(self):
        for stamp, expected in (("2026-10-24T14:00:07", "/202610/241400/"),
                                ("2026-10-24T14:29:57", "/202610/241400/"),
                                ("2026-10-24T14:30:07", "/202610/241430/"),
                                ("2026-10-25T00:00:07", "/202610/250000/"),
                                ("2026-11-01T00:00:07", "/202611/010000/"),
                                ("2027-01-01T00:00:07", "/202701/010000/")):
            with self.subTest(stamp=stamp):
                dt = datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)
                url = cs.seg_url(anchor_at(), int(dt.timestamp()), 100)
                self.assertIn(expected, url)
                query = parse_qs(urlsplit(url).query)
                self.assertEqual(query["prog_time"], [dt.strftime("%Y%m%d%H%M%S")])
                self.assertEqual(query["IASHttpSessionId"], ["a+b"])
                self.assertEqual(query["ztecid"], [CID])


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch.object(cs.time, "time", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.service = cs.CatchupService("http://gateway.test/", {CID: 1})
        self.live = patch.object(self.service, "fetch_live", return_value=anchor_at()).start()
        self.addCleanup(patch.stopall)
        self.probe = patch.object(self.service, "probe", return_value=True).start()

    def test_anchor_phase_covers_start_without_dropping_first_seconds(self):
        start = ANCHOR_TIME - 60 + 3
        result = self.service.playlist(CID, start, start + 25, cs.Budget())
        urls = [line for line in result.splitlines() if not line.startswith("#")]
        self.assertEqual(len(urls), 3)
        self.assertIn(f"/{ANCHOR_TIME - 60}/{ANCHOR_SEQ - 6}.ts", urls[0])
        self.assertIn("#EXT-X-START:TIME-OFFSET:3.000,PRECISE=YES", result)
        self.assertIn("#EXT-X-ENDLIST", result)
        self.assertEqual(self.probe.call_count, 1)
        full = urljoin(f"http://service.test/catchup/{CID}/{start}/{start + 25}/index.m3u8", urls[0])
        self.assertEqual(urlsplit(full).path, f"/segment/{CID}/{ANCHOR_TIME - 60}/{ANCHOR_SEQ - 6}.ts")

    def test_timeshift_grows_without_endlist(self):
        result = self.service.playlist(CID, ANCHOR_TIME - 60, None, cs.Budget())
        self.assertNotIn("#EXT-X-ENDLIST", result)
        self.assertNotIn("#EXT-X-PLAYLIST-TYPE:VOD", result)
        urls = [line for line in result.splitlines() if not line.startswith("#")]
        self.assertEqual(len(urls), 7)
        full = urljoin(f"http://service.test/timeshift/{CID}/{ANCHOR_TIME - 60}/index.m3u8", urls[0])
        self.assertTrue(urlsplit(full).path.startswith(f"/segment/{CID}/"))
        self.live.return_value = anchor_at(ANCHOR_TIME + 10, ANCHOR_SEQ + 1)
        with patch.object(cs.time, "time", return_value=NOW + 10):
            grown = self.service.playlist(CID, ANCHOR_TIME - 60, None, cs.Budget())
        self.assertEqual(grown.count("#EXTINF:"), result.count("#EXTINF:") + 1)
        self.assertIn(f"#EXT-X-MEDIA-SEQUENCE:{(ANCHOR_TIME - 60) // 10}", grown)

    def test_current_programme_is_not_frozen_as_vod(self):
        result = self.service.playlist(CID, NOW - 600, NOW + 600, cs.Budget())
        self.assertNotIn("#EXT-X-ENDLIST", result)
        self.assertNotIn("#EXT-X-PLAYLIST-TYPE:VOD", result)

    def test_current_programme_eventually_becomes_complete(self):
        end = NOW + 120
        first = self.service.playlist(CID, NOW - 600, end, cs.Budget())
        with patch.object(cs.time, "time", return_value=NOW + 200):
            self.live.return_value = anchor_at(ANCHOR_TIME + 200, ANCHOR_SEQ + 20)
            final = self.service.playlist(CID, NOW - 600, end, cs.Budget())
        self.assertNotIn("#EXT-X-ENDLIST", first)
        self.assertIn("#EXT-X-ENDLIST", final)
        self.assertIn("#EXT-X-PLAYLIST-TYPE:VOD", final)
        old_urls = [line for line in first.splitlines() if not line.startswith("#")]
        new_urls = [line for line in final.splitlines() if not line.startswith("#")]
        self.assertEqual(old_urls, new_urls[:len(old_urls)])

    def test_relative_segment_urls_preserve_reverse_proxy_prefix(self):
        start, end = NOW - 600, NOW - 500
        for mode in ("catchup", "timeshift"):
            with self.subTest(mode=mode):
                result = self.service.playlist(CID, start, end if mode == "catchup" else None, cs.Budget())
                segment = next(line for line in result.splitlines() if not line.startswith("#"))
                url = (f"https://player.test/iptv/catchup/{CID}/{start}/{end}/index.m3u8"
                       if mode == "catchup" else f"https://player.test/iptv/timeshift/{CID}/{start}/index.m3u8")
                self.assertTrue(urljoin(url, segment).startswith("https://player.test/iptv/segment/"))

    def test_timeshift_window_is_bounded(self):
        result = self.service.playlist(CID, NOW - 8 * 3600, None, cs.Budget())
        self.assertLessEqual(result.count("#EXTINF:"), 1441)

    def test_timeshift_survives_original_start_aging_out_of_retention(self):
        start = ANCHOR_TIME - 60
        first = self.service.playlist(CID, start, None, cs.Budget())
        elapsed = 2 * 86400
        self.live.return_value = anchor_at(ANCHOR_TIME + elapsed, ANCHOR_SEQ + elapsed // cs.SEG_DUR)
        with patch.object(cs.time, "time", return_value=NOW + elapsed):
            refreshed = self.service.playlist(CID, start, None, cs.Budget())
        self.assertNotIn("#EXT-X-ENDLIST", refreshed)
        self.assertEqual(refreshed.count("#EXTINF:"), cs.MAX_WINDOW // cs.SEG_DUR)
        self.assertNotIn(f"/{start}/", refreshed)
        self.assertNotEqual(first, refreshed)

    def test_timeshift_rejects_zero_start_without_io(self):
        with self.assertRaises(cs.ServiceError) as ctx:
            self.service.playlist(CID, 0, None, cs.Budget())
        self.assertEqual(ctx.exception.status, 400)
        self.live.assert_not_called()

    def test_unknown_retired_future_and_oversized_requests_fail_without_io(self):
        for cid, start, end, status in (("999", NOW - 120, NOW - 60, 404),
                                       (CID, NOW - 86460, NOW - 86400, 416),
                                       (CID, NOW + 60, NOW + 120, 416),
                                       (CID, NOW - 20000, NOW, 400)):
            with self.subTest(start=start), self.assertRaises(cs.ServiceError) as ctx:
                self.service.playlist(cid, start, end, cs.Budget())
            self.assertEqual(ctx.exception.status, status)
        self.live.assert_not_called()

    def test_empty_archive_is_404_not_empty_200(self):
        self.probe.return_value = False
        with self.assertRaises(cs.ServiceError) as ctx:
            self.service.playlist(CID, NOW - 300, NOW - 200, cs.Budget())
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(self.probe.call_count, len(cs.DRIFTS))

    def test_odd_sequence_drift_and_cache(self):
        self.probe.side_effect = [False, True]
        url, seq = self.service.resolve_segment(CID, ANCHOR_TIME - 30, ANCHOR_SEQ - 3, cs.Budget())
        self.assertEqual(seq, ANCHOR_SEQ - 4)
        self.assertIn(f"-{seq}.hls.ts", url)
        self.service.resolve_segment(CID, ANCHOR_TIME - 30, ANCHOR_SEQ - 3, cs.Budget())
        self.assertEqual(self.probe.call_count, 2)


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.service = cs.CatchupService("http://gateway.test", {CID: 1})

    def test_ts_probe_closes_response_and_uses_small_range(self):
        response = Response(b"\x47" + b"x" * 187, 206)
        with patch.object(cs, "urlopen", return_value=response) as fetch:
            self.assertTrue(self.service.probe("http://gateway.test/test.ts", cs.Budget()))
        self.assertTrue(response.closed)
        self.assertEqual(fetch.call_args.args[0].headers["Range"], "bytes=0-187")

    def test_html_200_is_not_ts(self):
        response = Response(b"<html>bad gateway</html>")
        with patch.object(self.service, "request", return_value=response), self.assertRaises(cs.ServiceError):
            self.service.probe("http://gateway.test/test.ts", cs.Budget())
        self.assertTrue(response.closed)

    def test_missing_segment_vs_upstream_error(self):
        for status in (404, 410, 429, 500):
            error = HTTPError("http://gateway.test", status, "error", {}, io.BytesIO())
            with self.subTest(status=status), patch.object(self.service, "request", side_effect=error):
                if status in (404, 410):
                    self.assertFalse(self.service.probe("http://gateway.test/test.ts", cs.Budget()))
                else:
                    with self.assertRaises(HTTPError):
                        self.service.probe("http://gateway.test/test.ts", cs.Budget())
            self.assertTrue(error.closed)

    def test_request_budget_is_bounded(self):
        with patch.object(cs.time, "monotonic", return_value=100):
            budget = cs.Budget()
        with patch.object(cs.time, "monotonic", return_value=113), self.assertRaises(cs.ServiceError) as ctx:
            budget.timeout()
        self.assertEqual(ctx.exception.status, 504)

    def test_live_anchor_retries_stale_cache_and_caches_fresh_value(self):
        stale, fresh = anchor_at(NOW - 200), anchor_at()
        responses = [Response(("#EXTM3U\n#EXTINF:10,\n" + a.url).encode()) for a in (stale, fresh)]
        with patch.object(cs.time, "time", return_value=NOW), patch.object(self.service, "request", side_effect=responses) as fetch:
            anchor = self.service.fetch_live(CID, cs.Budget())
            self.assertEqual(anchor.unix, ANCHOR_TIME)
            self.assertEqual(self.service.fetch_live(CID, cs.Budget()), anchor)
            self.assertEqual(fetch.call_count, 2)
            self.assertIn("&_cb=", fetch.call_args.args[0])
        self.assertTrue(all(response.closed for response in responses))

    def test_anchor_refreshes_after_ttl(self):
        responses = [Response(("#EXTM3U\n#EXTINF:10,\n" + a.url).encode())
                     for a in (anchor_at(), anchor_at(ANCHOR_TIME + 10, ANCHOR_SEQ + 1))]
        with patch.object(cs.time, "time", return_value=NOW + 10), patch.object(self.service, "request", side_effect=responses):
            with patch.object(cs.time, "monotonic", return_value=100):
                budget = cs.Budget(30)
                first = self.service.fetch_live(CID, budget)
            with patch.object(cs.time, "monotonic", return_value=100 + cs.ANCHOR_TTL + 1):
                second = self.service.fetch_live(CID, budget)
        self.assertEqual(second.unix, first.unix + 10)

    def test_stale_live_anchor_fails(self):
        responses = [Response(("#EXTM3U\n#EXTINF:10,\n" + anchor_at(NOW - 200).url).encode()) for _ in range(3)]
        with patch.object(cs.time, "time", return_value=NOW), patch.object(self.service, "request", side_effect=responses):
            with self.assertRaises(cs.ServiceError) as ctx:
                self.service.fetch_live(CID, cs.Budget())
        self.assertEqual(ctx.exception.status, 502)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = cs.CatchupService("http://gateway.test", {CID: 1})
        cls.server = cs.CatchupHTTPServer(("127.0.0.1", 0), cls.service)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, path, method="GET"):
        conn = HTTPConnection(*self.server.server_address, timeout=3)
        try:
            conn.request(method, path)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_status_json_and_head(self):
        status, headers, body = self.request("/status")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "ok")
        self.assertEqual(headers["Cache-Control"], "no-store")
        status, headers, body = self.request("/status", "HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertGreater(int(headers["Content-Length"]), 0)

    def test_malformed_replay_is_400(self):
        status, _, _ = self.request(f"/catchup/{CID}/20261024193000/20261024190000/index.m3u8")
        self.assertEqual(status, 400)

    def test_unknown_route_and_channel(self):
        self.assertEqual(self.request("/not-found")[0], 404)
        self.assertEqual(self.request(f"/catchup/999/{NOW - 100}/60/index.m3u8")[0], 404)

    def test_no_segments_response_is_404(self):
        with patch.object(self.service, "playlist", side_effect=cs.ServiceError(404, "no segments")):
            self.assertEqual(self.request(f"/catchup/{CID}/{NOW - 100}/60/index.m3u8")[0], 404)

    def test_upstream_deadline_response_is_504(self):
        with patch.object(self.service, "playlist", side_effect=TimeoutError):
            self.assertEqual(self.request(f"/catchup/{CID}/{NOW - 100}/60/index.m3u8")[0], 504)

    def test_segment_redirect_keeps_gateway_not_server_hostname(self):
        with patch.object(self.service, "check_range"), patch.object(self.service, "resolve_segment", return_value=(anchor_at().url, 1)):
            status, headers, body = self.request(f"/segment/{CID}/{NOW - 100}/1.ts")
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], anchor_at().url)
        self.assertEqual(body, b"")

    def test_overload_returns_503(self):
        for _ in range(4):
            self.server.slots.acquire()
        try:
            status, headers, _ = self.request(f"/timeshift/{CID}/{NOW - 100}/index.m3u8")
            self.assertEqual(status, 503)
            self.assertEqual(headers["Retry-After"], "2")
            self.assertEqual(self.request("/status")[0], 200)
        finally:
            for _ in range(4):
                self.server.slots.release()


if __name__ == "__main__":
    unittest.main()
