#!/usr/bin/env python3
"""Translate Guangxi Mobile historical TS segments into catchup and timeshift HLS.

Based on the anchor-relative prototype in the test branch. No media is recorded
locally. Run only on a trusted LAN; this service has no authentication.
"""

import argparse
import json
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

if __package__:
    from .build_playlists import ROOT, channel_days, load_config, validate_base_url
else:
    from build_playlists import ROOT, channel_days, load_config, validate_base_url


SEG_DUR = 10
MAX_WINDOW = 4 * 3600
LIVE_DELAY = 20
ANCHOR_TTL = 5
TARGET_TTL = 15
REQUEST_BUDGET = 12
BEIJING = timezone(timedelta(hours=8))
DRIFTS = (0, -1, 1, -6, 6, -12, 12, -18, 18, -30, 30, -60, 60,
          -120, 120, -240, 240, -480, 480, -960, 960)
SEGMENT_RE = re.compile(
    r"(?P<prefix>.*/\d+)/\d{6}/\d{6}/(?P<unix>\d+)-(?P<track>\d+)-(?P<seq>\d+)\.hls\.ts$"
)


class ServiceError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Budget:
    def __init__(self, seconds: float = REQUEST_BUDGET):
        self.deadline = time.monotonic() + seconds

    def timeout(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ServiceError(504, "upstream request budget exhausted")
        return min(4.0, remaining)


def parse_ts(value: str) -> int:
    if re.fullmatch(r"[0-9]{14}", value):
        return int(datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=BEIJING).timestamp())
    if not re.fullmatch(r"[0-9]{1,10}", value):
        raise ValueError("expected Unix seconds or YYYYMMDDHHMMSS in UTC+08:00")
    return int(value)


def parse_range(start: str, end: str) -> tuple[int, int]:
    a = parse_ts(start)
    # Keep the prototype's duration form, but never reinterpret a reversed date.
    b = a + int(end) if re.fullmatch(r"[0-9]{1,5}", end) and 0 < int(end) <= MAX_WINDOW else parse_ts(end)
    if a <= 0 or b <= a or b - a > MAX_WINDOW:
        raise ValueError("range must be positive, ordered and at most 4 hours")
    return a, b


@dataclass(frozen=True)
class Anchor:
    url: str
    unix: int
    seq: int


def parse_anchor(body: str, final_url: str) -> Anchor:
    if not body.lstrip().startswith("#EXTM3U"):
        raise ServiceError(502, "upstream did not return HLS")
    anchor = None
    duration = None
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-KEY:") and "METHOD=NONE" not in line:
            raise ServiceError(502, "encrypted upstream segments are not supported")
        if line.startswith("#EXTINF:"):
            try:
                duration = float(line.split(":", 1)[1].split(",", 1)[0])
            except ValueError as exc:
                raise ServiceError(502, "invalid upstream segment duration") from exc
        elif line and not line.startswith("#"):
            url = urljoin(final_url, line)
            match = SEGMENT_RE.fullmatch(urlsplit(url).path)
            if not match or duration != SEG_DUR:
                raise ServiceError(502, "upstream must use timestamped 10-second TS segments")
            anchor = Anchor(url, int(match["unix"]), int(match["seq"]))
            duration = None
    if anchor is None:
        raise ServiceError(502, "upstream playlist contains no usable segments")
    return anchor


def seg_url(anchor: Anchor, unix: int, seq: int) -> str:
    parts = urlsplit(anchor.url)
    match = SEGMENT_RE.fullmatch(parts.path)
    if not match:
        raise ServiceError(502, "invalid segment anchor")
    dt = datetime.fromtimestamp(unix, timezone.utc)
    bucket = dt.strftime("%d%H") + ("00" if dt.minute < 30 else "30")
    path = f'{match["prefix"]}/{dt:%Y%m}/{bucket}/{unix}-{match["track"]}-{seq}.hls.ts'
    query = [(key, dt.strftime("%Y%m%d%H%M%S") if key == "prog_time" else value)
             for key, value in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), ""))


class ChannelCatalog:
    """Reload the allowlist when a sync changes channel IDs or retention settings."""

    def __init__(self, playlist: Path, config: Path):
        self.playlist, self.config = playlist, config
        self.fingerprint = None
        self.channels: dict[str, int] = {}
        self.lock = threading.Lock()
        self.get("")

    def get(self, cid: str, default: int = 0) -> int:
        with self.lock:
            fingerprint = tuple((path.stat().st_mtime_ns, path.stat().st_size)
                                for path in (self.playlist, self.config))
            if fingerprint != self.fingerprint:
                self.channels = channel_days(self.playlist.read_text(encoding="utf-8-sig"), load_config(self.config))
                self.fingerprint = fingerprint
            return self.channels.get(cid, default)


class CatchupService:
    def __init__(self, gateway: str, channels: dict[str, int] | ChannelCatalog):
        self.gateway = validate_base_url(gateway)
        self.channels = channels
        self.anchors: OrderedDict[str, tuple[float, Anchor]] = OrderedDict()
        self.targets: OrderedDict[tuple[str, int, int], tuple[float, str, int]] = OrderedDict()
        self.lock = threading.Lock()

    def check_range(self, cid: str, start: int, now: float) -> None:
        days = self.channels.get(cid, 0)
        if not days:
            raise ServiceError(404, "channel is not enabled for catchup")
        if start < now - days * 86400 - SEG_DUR:
            raise ServiceError(416, "requested time is outside the configured retention window")
        if start >= now - LIVE_DELAY:
            raise ServiceError(416, "requested time is not available at the live edge yet")

    def request(self, url: str, budget: Budget, byte_range: bool = False):
        headers = {"User-Agent": "my-IPTV-catchup/2.0"}
        if byte_range:
            headers["Range"] = "bytes=0-187"
        return urlopen(Request(url, headers=headers), timeout=budget.timeout())

    def fetch_live(self, cid: str, budget: Budget) -> Anchor:
        with self.lock:
            hit = self.anchors.get(cid)
            if hit and time.monotonic() - hit[0] < ANCHOR_TTL:
                return hit[1]
        for attempt in range(3):
            url = f"{self.gateway}/{cid}/index.m3u8?servicetype=1"
            if attempt:
                url += f"&_cb={time.time_ns()}"
            with self.request(url, budget) as response:
                body = response.read(1024 * 1024 + 1)
                if len(body) > 1024 * 1024:
                    raise ServiceError(502, "upstream playlist is too large")
                anchor = parse_anchor(body.decode("utf-8-sig"), response.geturl())
            if time.time() - 180 <= anchor.unix <= time.time():
                with self.lock:
                    self.anchors[cid] = (time.monotonic(), anchor)
                    self.anchors.move_to_end(cid)
                    while len(self.anchors) > 128:
                        self.anchors.popitem(last=False)
                return anchor
        raise ServiceError(502, "stale live playlist; check gateway caching")

    def probe(self, url: str, budget: Budget) -> bool:
        try:
            with self.request(url, budget, byte_range=True) as response:
                # A 200 HTML error page is not a playable MPEG-TS segment.
                if response.status not in (200, 206) or response.read(1) != b"\x47":
                    raise ServiceError(502, "upstream returned invalid TS data")
                return True
        except HTTPError as exc:
            exc.close()
            if exc.code in (404, 410):
                return False
            raise

    def resolve_segment(self, cid: str, unix: int, seq: int, budget: Budget) -> tuple[str, int]:
        key = (cid, unix, seq)
        with self.lock:
            hit = self.targets.get(key)
            if hit and time.monotonic() - hit[0] < TARGET_TTL:
                return hit[1], hit[2]
        anchor = self.fetch_live(cid, budget)
        for drift in DRIFTS:
            if seq + drift < 0:
                continue
            url = seg_url(anchor, unix, seq + drift)
            if self.probe(url, budget):
                with self.lock:
                    self.targets[key] = (time.monotonic(), url, seq + drift)
                    self.targets.move_to_end(key)
                    while len(self.targets) > 4096:
                        self.targets.popitem(last=False)
                return url, seq + drift
        raise ServiceError(404, "historical segment is unavailable")

    def playlist(self, cid: str, start: int, end: int | None, budget: Budget) -> str:
        now = time.time()
        timeshift = end is None
        if start <= 0:
            raise ServiceError(400, "start must be a positive timestamp")
        # A timeshift URL stays unchanged across refreshes; its rolling window,
        # not the original session start, must remain inside retention.
        check_start = max(start, int(now) - MAX_WINDOW) if timeshift else start
        self.check_range(cid, check_start, now)
        if end is not None and (end <= start or end - start > MAX_WINDOW):
            raise ServiceError(400, "range must be ordered and at most 4 hours")
        anchor = self.fetch_live(cid, budget)
        # Only emit complete segments, aligned to the anchor's phase, not :00.
        safe_end = min(int(now) - LIVE_DELAY, anchor.unix + SEG_DUR)
        edge = anchor.unix + ((safe_end - anchor.unix) // SEG_DUR) * SEG_DUR
        ongoing = end is not None and end > edge
        end = edge if timeshift else min(end, edge)
        if start >= end:
            raise ServiceError(416, "no complete segments are available for the requested range")
        if timeshift:
            start = max(start, end - MAX_WINDOW)
        first = anchor.unix + ((start - anchor.unix) // SEG_DUR) * SEG_DUR
        seq = anchor.seq + (first - anchor.unix) // SEG_DUR
        _, seq = self.resolve_segment(cid, first, seq, budget)
        lines = ["#EXTM3U", "#EXT-X-VERSION:6", f"#EXT-X-TARGETDURATION:{SEG_DUR}",
                 f"#EXT-X-MEDIA-SEQUENCE:{first // SEG_DUR if timeshift else 0}",
                 f"#EXT-X-START:TIME-OFFSET:{start - first:.3f},PRECISE=YES"]
        if not timeshift and not ongoing:
            lines.append("#EXT-X-PLAYLIST-TYPE:VOD")
        prefix = "../../../segment" if timeshift else "../../../../segment"
        # Resolve and validate later segments on demand. Never pre-probe four
        # hours of media, and never freeze expiring upstream sessions in VOD.
        for index, unix in enumerate(range(first, end, SEG_DUR)):
            dt = datetime.fromtimestamp(unix, timezone.utc)
            lines.extend([f"#EXT-X-PROGRAM-DATE-TIME:{dt:%Y-%m-%dT%H:%M:%SZ}",
                          f"#EXTINF:{SEG_DUR}.0,", f"{prefix}/{cid}/{unix}/{seq + index}.ts"])
        if not timeshift and not ongoing:
            lines.append("#EXT-X-ENDLIST")
        return "\n".join(lines) + "\n"

    def status(self) -> dict:
        with self.lock:
            return {"status": "ok", "cached_targets": len(self.targets),
                    "anchors": {cid: {"age": int(time.monotonic() - rec[0])}
                                for cid, rec in self.anchors.items()}}

    def document(self, name: str) -> str:
        raise ServiceError(404, "playlist publishing is not enabled")


class CatchupHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, service: CatchupService):
        self.service = service
        self.slots = threading.BoundedSemaphore(4)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def send_body(self, code: int, body: str, ctype="text/plain; charset=utf-8", location=None):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        if location:
            self.send_header("Location", location)
        if code == 503:
            self.send_header("Retry-After", "2")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            self.handle_get()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def handle_get(self):
        path = unquote(urlsplit(self.path).path)
        service = self.server.service
        if path == "/status":
            self.send_body(200, json.dumps(service.status()), "application/json")
            return
        replay = re.fullmatch(r"/catchup/([0-9]{1,20})/([^/]+)/([^/]+)/index\.m3u8", path)
        shift = re.fullmatch(r"/timeshift/([0-9]{1,20})/([^/]+)/index\.m3u8", path)
        segment = re.fullmatch(r"/segment/([0-9]{1,20})/([0-9]{1,10})/([0-9]{1,19})\.ts", path)
        document = path in ("/IPTV.m3u", "/IPTV-catchup.m3u", "/gx.m3u")
        if not (replay or shift or segment or document):
            self.send_body(404, "unknown endpoint")
            return
        if not self.server.slots.acquire(blocking=False):
            self.send_body(503, "too many upstream requests")
            return
        try:
            budget = Budget()
            if document:
                self.send_body(200, service.document(path[1:]), "application/vnd.apple.mpegurl; charset=utf-8")
            elif segment:
                cid, unix, seq = segment.groups()
                service.check_range(cid, int(unix), time.time())
                url, _ = service.resolve_segment(cid, int(unix), int(seq), budget)
                self.send_body(302, "", location=url)
            else:
                try:
                    if replay:
                        cid, start, end = replay.groups()
                        start, end = parse_range(start, end)
                    else:
                        cid, start = shift.groups()
                        start, end = parse_ts(start), None
                except ValueError as exc:
                    raise ServiceError(400, str(exc)) from exc
                body = service.playlist(cid, start, end, budget)
                self.send_body(200, body, "application/vnd.apple.mpegurl")
        except ServiceError as exc:
            self.send_body(exc.status, str(exc))
        except HTTPError as exc:
            exc.close()
            self.send_body(502, f"upstream HTTP {exc.code}")
        except (TimeoutError, URLError):
            self.send_body(504, "upstream connection failed or timed out")
        except (OSError, ValueError, KeyError, TypeError):
            self.send_body(502, "invalid upstream data or local channel configuration")
        finally:
            self.server.slots.release()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", type=int, nargs="?", default=8081)
    parser.add_argument("gateway", nargs="?", default="http://192.168.50.250:8080")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address; trusted LAN only.")
    parser.add_argument("--playlist", type=Path, default=ROOT / "IPTV.m3u")
    parser.add_argument("--config", type=Path, default=ROOT / "catchup.json")
    args = parser.parse_args()
    try:
        service = CatchupService(args.gateway, ChannelCatalog(args.playlist, args.config))
        server = CatchupHTTPServer((args.host, args.port), service)
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        parser.exit(1, f"startup failed: {exc}\n")
    with server:
        print(f"catchup server on {args.host}:{args.port}, gateway {service.gateway}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
