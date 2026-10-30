#!/usr/bin/env python3
"""Publish gateway-adjusted playlists alongside the catchup service on OpenWrt."""

import argparse
from ipaddress import IPv4Address
from pathlib import Path

if __package__:
    from .build_playlists import PLACEHOLDERS, build_playlists, load_config, validate_base_url
    from .catchup_server import CatchupHTTPServer, CatchupService, ChannelCatalog, ServiceError
    from .diagnostics import LOG_MODES, configure_logging
    from .sync_iptv import extract_channel_id, parse_m3u
else:
    from build_playlists import PLACEHOLDERS, build_playlists, load_config, validate_base_url
    from catchup_server import CatchupHTTPServer, CatchupService, ChannelCatalog, ServiceError
    from diagnostics import LOG_MODES, configure_logging
    from sync_iptv import extract_channel_id, parse_m3u


def gateway_playlist(text: str, gateway: str) -> str:
    gateway = validate_base_url(gateway)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        cid = extract_channel_id(stripped) if stripped and not stripped.startswith("#") else None
        lines.append(f"{gateway}/{cid}/index.m3u8?servicetype=1" if cid else line)
    return "\n".join(lines).rstrip() + "\n"


class RouterService(CatchupService):
    def __init__(self, gateway: str, playlist: Path, config: Path, base_url: str, format: str):
        if format not in PLACEHOLDERS:
            raise ValueError("format must be ku9 or unix")
        self.playlist_path, self.config_path = playlist, config
        self.base_url, self.format = validate_base_url(base_url), format
        if not parse_m3u(playlist.read_text(encoding="utf-8-sig"))[1]:
            raise ValueError("playlist contains no channels")
        super().__init__(gateway, ChannelCatalog(playlist, config))

    def document(self, name: str) -> str:
        if name not in ("IPTV.m3u", "IPTV-catchup.m3u", "gx.m3u"):
            raise ServiceError(404, "unknown playlist", "not_found")
        # Reload data on demand without writing generated lists to router flash.
        text = gateway_playlist(self.playlist_path.read_text(encoding="utf-8-sig"), self.gateway)
        config = load_config(self.config_path)
        config.update(base_url=self.base_url, format=self.format)
        catchup, direct = build_playlists(text, config)
        return {"IPTV.m3u": text, "IPTV-catchup.m3u": catchup, "gx.m3u": direct}[name]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="LAN IPv4 address, not a public interface")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--base-url", default="")
    parser.add_argument("--format", choices=PLACEHOLDERS, default="ku9")
    parser.add_argument("--playlist", type=Path, default=Path("/etc/iptv-catchup/IPTV.m3u"))
    parser.add_argument("--config", type=Path, default=Path("/etc/iptv-catchup/catchup.json"))
    parser.add_argument("--access-log", choices=LOG_MODES, default="errors",
                        help="JSON access logs: errors/slow requests (default), all, or off.")
    args = parser.parse_args()
    configure_logging()
    try:
        host = str(IPv4Address(args.host))
        if not 1024 <= args.port <= 65535:
            raise ValueError("port must be between 1024 and 65535")
        if host == "0.0.0.0" and not args.base_url:
            raise ValueError("base-url is required when listening on all interfaces")
        base = args.base_url or f"http://{host}:{args.port}"
        service = RouterService(args.gateway, args.playlist, args.config, base, args.format)
        server = CatchupHTTPServer((host, args.port), service, access_log=args.access_log)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"startup failed: {exc}\n")
    with server:
        print(f"IPTV catchup listening on {host}:{args.port}; subscription {service.base_url}/IPTV-catchup.m3u", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
