#!/usr/bin/env python3
"""Generate direct and catchup variants from the maintained live playlist."""

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

if __package__:
    from .sync_iptv import channel_name, extract_channel_id, parse_m3u, split_extinf
else:
    from sync_iptv import channel_name, extract_channel_id, parse_m3u, split_extinf


ROOT = Path(__file__).resolve().parent.parent
CATCHUP_RE = re.compile(r'\s+(?:catchup(?:-[\w-]+)?|timeshift)="[^"]*"')
DIRECT_BASE = "http://cdnrrs.gx.chinamobile.com/PLTV/77777777/224"
PLACEHOLDERS = {
    "ku9": ("${(b)yyyyMMddHHmmss}", "${(e)yyyyMMddHHmmss}"),
    "unix": ("{utc}", "{utcend}"),
}


def validate_base_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("base_url must be a string")
    parts = urlsplit(value)
    if (parts.scheme not in ("http", "https") or not parts.hostname
            or parts.query or parts.fragment or parts.username or parts.password
            or any(char.isspace() or char in '\"<>{}' for char in value)):
        raise ValueError("base_url must be an HTTP(S) URL without credentials, query or fragment")
    parts.port  # Validate port syntax and range before generating unusable URLs.
    return value.rstrip("/")


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("catchup configuration must be an object")
    config["base_url"] = validate_base_url(config["base_url"])
    if config.get("format", "ku9") not in PLACEHOLDERS:
        raise ValueError("format must be ku9 or unix")
    days = config["days_by_channel"]
    if not isinstance(days, dict):
        raise ValueError("days_by_channel must be an object")
    if any(not key or type(value) is not int or not 1 <= value <= 7 for key, value in days.items()):
        raise ValueError("catchup retention must be between 1 and 7 days")
    return config


def entry_days(extinf: str, config: dict) -> int:
    # HD/4K variants can share a tvg-id but have different archive availability.
    return config["days_by_channel"].get(channel_name(extinf), 0)


def channel_days(text: str, config: dict) -> dict[str, int]:
    return {
        entry.channel_id: entry_days(entry.extinf, config)
        for entry in parse_m3u(text)[1]
        if entry.channel_id and entry_days(entry.extinf, config)
    }


def build_playlists(text: str, config: dict) -> tuple[str, str]:
    start, end = PLACEHOLDERS[config.get("format", "ku9")]
    base = validate_base_url(config["base_url"])
    catchup_lines, direct_lines = [], []
    pending: tuple[int, str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("#EXTINF:"):
            metadata, name = split_extinf(line)
            extinf = f"{CATCHUP_RE.sub('', metadata)},{name}"
            pending = (len(catchup_lines), extinf)
            catchup_lines.append(extinf)
            direct_lines.append(extinf)
            continue
        direct_url = raw_line
        if pending and line and not line.startswith("#"):
            index, extinf = pending
            cid = extract_channel_id(line)
            days = entry_days(extinf, config)
            if cid:
                if days:
                    metadata, name = split_extinf(extinf)
                    source = f"{base}/catchup/{cid}/{start}/{end}/index.m3u8"
                    catchup_lines[index] = (
                        f'{metadata} catchup="default" catchup-days="{days}" '
                        f'catchup-source="{source}",{name}'
                    )
                if urlsplit(line).hostname != "cdnrrs.gx.chinamobile.com":
                    direct_url = f"{DIRECT_BASE}/{cid}/index.m3u8?servicetype=1"
            pending = None
        catchup_lines.append(raw_line)
        direct_lines.append(direct_url)
    return "\n".join(catchup_lines).rstrip() + "\n", "\n".join(direct_lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--playlist", type=Path, default=ROOT / "IPTV.m3u")
    parser.add_argument("--config", type=Path, default=ROOT / "catchup.json")
    parser.add_argument("--catchup-output", type=Path, default=ROOT / "IPTV-catchup.m3u")
    parser.add_argument("--direct-output", type=Path, default=ROOT / "gx.m3u")
    parser.add_argument("--base-url", help="Player-accessible catchup service URL.")
    parser.add_argument("--format", choices=PLACEHOLDERS, help="ku9 local dates or unix UTC seconds.")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        if args.base_url:
            config["base_url"] = validate_base_url(args.base_url)
        if args.format:
            config["format"] = args.format
        text = args.playlist.read_text(encoding="utf-8-sig")
        if not parse_m3u(text)[1]:
            raise ValueError("playlist contains no channels")
        catchup, direct = build_playlists(text, config)
        args.catchup_output.write_text(catchup, encoding="utf-8", newline="\n")
        args.direct_output.write_text(direct, encoding="utf-8", newline="\n")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"build failed: {exc}\n")
    print(f"Generated {args.catchup_output} and {args.direct_output}; catchup channels={len(channel_days(text, config))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
