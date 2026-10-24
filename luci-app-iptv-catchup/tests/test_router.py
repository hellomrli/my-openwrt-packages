import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from scripts.catchup_server import CatchupHTTPServer
from scripts.openwrt_service import RouterService, gateway_playlist
from scripts.sync_iptv import parse_m3u


PLAYLIST = '''#EXTM3U x-tvg-url="https://epg.test/epg.xml"
#EXTINF:-1 tvg-id="1" group-title="TV",Channel
http://old.test/123/index.m3u8?servicetype=1
#EXTINF:-1,External
https://external.test/live.m3u8
'''
CONFIG = {"base_url": "http://old.test:8081", "format": "unix", "days_by_channel": {"Channel": 1}}


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.playlist, self.config = (Path(self.tmp.name) / name for name in ("IPTV.m3u", "catchup.json"))
        self.playlist.write_text(PLAYLIST)
        self.config.write_text(json.dumps(CONFIG))
        self.service = RouterService("http://192.0.2.1:8080", self.playlist, self.config,
                                     "http://192.0.2.1:8088", "ku9")

    def test_live_gateway_rewrite_preserves_external_and_metadata(self):
        result = self.service.document("IPTV.m3u")
        self.assertIn('x-tvg-url="https://epg.test/epg.xml"', result)
        self.assertIn("http://192.0.2.1:8080/123/index.m3u8?servicetype=1", result)
        self.assertIn("https://external.test/live.m3u8", result)
        self.assertEqual(len(parse_m3u(result)[1]), 2)
        self.assertNotIn("catchup-source", result)

    def test_uci_base_and_format_override_bundled_config(self):
        result = self.service.document("IPTV-catchup.m3u")
        self.assertIn('catchup-source="http://192.0.2.1:8088/catchup/123/${(b)yyyyMMddHHmmss}/${(e)yyyyMMddHHmmss}/index.m3u8"', result)
        self.assertIn('catchup-days="1"', result)
        self.service.format = "unix"
        self.assertIn("/123/{utc}/{utcend}/index.m3u8", self.service.document("IPTV-catchup.m3u"))

    def test_direct_list_uses_operator_urls(self):
        result = self.service.document("gx.m3u")
        self.assertIn("http://cdnrrs.gx.chinamobile.com/PLTV/77777777/224/123/index.m3u8", result)
        self.assertNotIn("catchup-source", result)
        self.assertIn("https://external.test/live.m3u8", result)

    def test_reload_subscription_and_allowlist_without_writing_generated_files(self):
        self.playlist.write_text(PLAYLIST.replace("/123/", "/999/"))
        self.assertIn("/catchup/999/", self.service.document("IPTV-catchup.m3u"))
        self.assertEqual(self.service.channels.get("999"), 1)
        self.assertEqual(self.service.channels.get("123"), 0)
        self.assertEqual(sorted(p.name for p in Path(self.tmp.name).iterdir()), ["IPTV.m3u", "catchup.json"])

    def test_invalid_gateway_format_and_empty_playlist_fail_early(self):
        with self.assertRaises(ValueError):
            gateway_playlist(PLAYLIST, "file:///etc/passwd")
        with self.assertRaises(ValueError):
            RouterService("http://gateway.test", self.playlist, self.config, "http://host.test", "bad")
        self.playlist.write_text("#EXTM3U\n")
        with self.assertRaises(ValueError):
            RouterService("http://gateway.test", self.playlist, self.config, "http://host.test", "ku9")

    def test_http_subscription_head_status_and_traversal(self):
        server = CatchupHTTPServer(("127.0.0.1", 0), self.service)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        for name in ("IPTV.m3u", "IPTV-catchup.m3u", "gx.m3u"):
            conn = HTTPConnection(*server.server_address, timeout=3)
            conn.request("GET", "/" + name)
            response = conn.getresponse()
            body = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(body.decode(), self.service.document(name))
            conn.request("HEAD", "/" + name)
            response = conn.getresponse()
            self.assertEqual(int(response.getheader("Content-Length")), len(body))
            self.assertEqual(response.read(), b"")
            conn.close()
        for path, expected in (("/status", 200), ("/../etc/passwd", 404), ("/%2e%2e/etc/passwd", 404)):
            conn = HTTPConnection(*server.server_address, timeout=3)
            conn.request("GET", path)
            response = conn.getresponse()
            response.read()
            self.assertEqual(response.status, expected)
            conn.close()
