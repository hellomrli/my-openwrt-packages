#!/usr/bin/env python3
import argparse
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


DEFAULT_UPSTREAM = "https://raw.githubusercontent.com/Healer-sys/Home/main/iptv/gx.m3u"
CHANNEL_ID_RE = re.compile(r"/(\d+)/index\.m3u8", re.IGNORECASE)
GROUP_RE = re.compile(r'group-title="([^"]*)"')
TVG_NAME_RE = re.compile(r'tvg-name="([^"]*)"')
TVG_CHNO_RE = re.compile(r'\s+tvg-chno="[^"]*"')
LOGO_RE = re.compile(r'tvg-logo="([^"]*)"')
CATCHUP_SOURCE_RE = re.compile(r'catchup-source="([^"]*)"')

# The upstream playlist points some logos at the operator's image host, which
# hangs for 100 seconds per request inside Jellyfin (guide refresh errors).
# Rewrite those to the icons shipped with this repository.
LOGO_BASE = "https://raw.githubusercontent.com/hellomrli/my-IPTV/main/icon/"
BAD_LOGO_HOSTS = ("images.center.bcs.ottcn.com",)
LOGO_NAME_OVERRIDES = {"CCTV-16 4K": "CCTV16"}

# Channel numbers are assigned per group so the lineup stays sorted in clients
# and there is room to insert new channels without renumbering everything.
GROUP_NUMBER_BASE = {
    "央视": 1,
    "卫视": 100,
    "广西": 200,
    "精彩热播": 300,
    "国际": 400,
    "4K频道": 500,
    "全国热播": 600,
}
DEFAULT_GROUP_NUMBER_BASE = 900

QUALITY_WORDS = {
    "8k": 70,
    "4k": 60,
    "蓝光": 55,
    "极清": 50,
    "超清": 40,
    "高清": 30,
    "标清": 10,
}

EXCLUDED_CHANNEL_NAMES = {
    "南宁公共",
    "玉林公共",
    "防城港公共",
    "钦州公共影视",
    "苏州4K",
    "古装剧场",
    "军旅剧场",
    "家庭剧场",
    "惊悚悬疑",
    "精品综合",
    "金牌综艺",
    "精品记录",
    "精品纪录",
    "潮妈辣婆",
    "西藏卫视藏语",
    "西藏卫视藏语频道",
    "卡酷少儿",
    "山东教育",
    "北海新闻",
    "钦州综合",
}

EXCLUDED_CHANNEL_PREFIXES = (
    "CGTN",
    "CETV",
)

EXCLUDED_CHANNEL_KEYWORDS = (
    "中国教育",
    "中国国际电视台",
    "标清4M",
    "西藏卫视藏语",
)


def is_excluded_channel_name(name: str, extinf: str = "") -> bool:
    text = f"{name} {extinf}"
    return (
        name in EXCLUDED_CHANNEL_NAMES
        or any(name.startswith(prefix) for prefix in EXCLUDED_CHANNEL_PREFIXES)
        or any(keyword in text for keyword in EXCLUDED_CHANNEL_KEYWORDS)
        or "CGTN" in text
    )


@dataclass
class Entry:
    extinf: str
    url: str
    name: str
    group: str
    key: str
    channel_id: str | None
    quality: int
    index: int


def read_text(path_or_url: str) -> str:
    if path_or_url.startswith(("http://", "https://")):
        request = Request(path_or_url, headers={"User-Agent": "my-IPTV-sync/1.0"})
        with urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8-sig")
    return Path(path_or_url).read_text(encoding="utf-8-sig")


def split_extinf(extinf: str) -> tuple[str, str]:
    quoted = False
    for index, char in enumerate(extinf):
        if char == '"':
            quoted = not quoted
        elif char == "," and not quoted:
            return extinf[:index], extinf[index + 1:]
    return extinf, ""


def channel_name(extinf: str) -> str:
    name = split_extinf(extinf)[1].strip()
    if name:
        return name
    match = TVG_NAME_RE.search(extinf)
    return match.group(1).strip() if match else ""


def group_name(extinf: str) -> str:
    match = GROUP_RE.search(extinf)
    return match.group(1).strip() if match else ""


def normalize_name(name: str) -> str:
    value = name.strip().upper()
    value = re.sub(r"\s+", "", value)
    for word in ["极清", "超清", "高清", "标清", "蓝光"]:
        value = value.replace(word, "")
    value = value.replace("＋", "+")
    value = value.replace("CCTV-", "CCTV")
    value = re.sub(r"[-_·.（）()]", "", value)
    value = value.replace("央视", "CCTV")
    value = value.replace("中央", "CCTV")
    return value


def quality_score(extinf: str, url: str) -> int:
    text = f"{extinf} {url}".lower()
    score = 0
    for word, value in QUALITY_WORDS.items():
        if word.lower() in text:
            score = max(score, value)

    bitrate_match = re.search(r"(\d{3,5})k", text)
    if bitrate_match:
        bitrate = int(bitrate_match.group(1))
        if bitrate >= 12000:
            score = max(score, 50)
        elif bitrate >= 8000:
            score = max(score, 40)
        elif bitrate >= 4000:
            score = max(score, 30)
        elif bitrate >= 1500:
            score = max(score, 10)
    return score


def parse_m3u(text: str) -> tuple[str, list[Entry]]:
    lines = [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    header = "#EXTM3U"
    entries: list[Entry] = []
    pending_extinf: str | None = None

    for line in lines:
        if not line:
            continue
        if line.startswith("#EXTM3U"):
            header = line
            continue
        if line.startswith("#EXTINF"):
            pending_extinf = line
            continue
        if pending_extinf and not line.startswith("#"):
            name = channel_name(pending_extinf)
            entries.append(
                Entry(
                    extinf=pending_extinf,
                    url=line,
                    name=name,
                    group=group_name(pending_extinf),
                    key=normalize_name(name),
                    channel_id=extract_channel_id(line),
                    quality=quality_score(pending_extinf, line),
                    index=len(entries),
                )
            )
            pending_extinf = None

    return header, entries


def extract_channel_id(url: str) -> str | None:
    match = CHANNEL_ID_RE.search(url)
    return match.group(1) if match else None


def best_by_channel(entries: list[Entry]) -> dict[str, Entry]:
    best: dict[str, Entry] = {}
    for entry in entries:
        if not entry.key:
            continue
        current = best.get(entry.key)
        if current is None or (entry.quality, -entry.index) > (current.quality, -current.index):
            best[entry.key] = entry
    return best


def proxy_url_from_template(template_url: str, channel_id: str) -> str:
    return CHANNEL_ID_RE.sub(f"/{channel_id}/index.m3u8", template_url, count=1)


def update_catchup_channel(extinf: str, old_id: str | None, new_id: str) -> str:
    if not old_id or old_id == new_id:
        return extinf
    metadata, name = split_extinf(extinf)
    metadata = CATCHUP_SOURCE_RE.sub(
        lambda match: 'catchup-source="' + match.group(1).replace(f"/{old_id}/", f"/{new_id}/") + '"',
        metadata,
    )
    return f"{metadata},{name}"


def build_added_extinf(upstream: Entry, group: str) -> str:
    extinf = upstream.extinf
    if GROUP_RE.search(extinf):
        extinf = GROUP_RE.sub(f'group-title="{group}"', extinf, count=1)
    else:
        extinf = extinf.replace("#EXTINF:-1", f'#EXTINF:-1 group-title="{group}"', 1)
    return extinf


def sanitize_logo(extinf: str, name: str) -> str:
    match = LOGO_RE.search(extinf)
    if not match or not any(host in match.group(1) for host in BAD_LOGO_HOSTS):
        return extinf
    icon = LOGO_NAME_OVERRIDES.get(name, name)
    return extinf[: match.start()] + f'tvg-logo="{LOGO_BASE}{quote(icon)}.png"' + extinf[match.end():]


def with_channel_number(extinf: str, number: int) -> str:
    extinf = TVG_CHNO_RE.sub("", extinf)
    match = re.search(r'tvg-id="[^"]*"', extinf)
    if match:
        return extinf[: match.end()] + f' tvg-chno="{number}"' + extinf[match.end():]
    return re.sub(r"(#EXTINF:[^\s,]+)", rf'\1 tvg-chno="{number}"', extinf, count=1)


def number_entries(entries: list[tuple[str, str]]) -> list[tuple[str, str]]:
    counters: dict[int, int] = {}
    numbered: list[tuple[str, str]] = []
    for extinf, url in entries:
        base = GROUP_NUMBER_BASE.get(group_name(extinf), DEFAULT_GROUP_NUMBER_BASE)
        offset = counters.get(base, 0)
        counters[base] = offset + 1
        numbered.append((with_channel_number(extinf, base + offset), url))
    return numbered


def group_sort_entries(entries: list[tuple[str, str]]) -> list[tuple[str, str]]:
    group_order: list[str] = []
    buckets: dict[str, list[tuple[str, str]]] = {}
    for extinf, url in entries:
        group = group_name(extinf)
        if group not in buckets:
            buckets[group] = []
            group_order.append(group)
        buckets[group].append((extinf, url))
    return [entry for group in group_order for entry in buckets[group]]


def sync_playlist(template_text: str, upstream_text: str, add_missing: bool) -> tuple[str, dict[str, int]]:
    template_header, template_entries = parse_m3u(template_text)
    upstream_header, upstream_entries = parse_m3u(upstream_text)
    upstream_best = {key: entry for key, entry in best_by_channel(upstream_entries).items() if entry.channel_id}
    template_best = best_by_channel(template_entries)
    seen: set[str] = set()
    output_entries: list[tuple[str, str]] = []
    stats = {"updated": 0, "unchanged": 0, "removed_duplicates": 0, "excluded": 0, "added": 0, "missing_upstream": 0}

    for entry in template_entries:
        if is_excluded_channel_name(entry.name, entry.extinf):
            stats["excluded"] += 1
            continue
        if template_best.get(entry.key) is not entry:
            stats["removed_duplicates"] += 1
            continue
        if entry.key in seen:
            stats["removed_duplicates"] += 1
            continue

        seen.add(entry.key)
        upstream = upstream_best.get(entry.key)
        if upstream and upstream.channel_id:
            new_url = proxy_url_from_template(entry.url, upstream.channel_id)
            stats["updated" if new_url != entry.url else "unchanged"] += 1
            output_entries.append((update_catchup_channel(entry.extinf, entry.channel_id, upstream.channel_id), new_url))
        else:
            stats["missing_upstream"] += 1
            output_entries.append((entry.extinf, entry.url))

    template_url = next((entry.url for entry in template_entries if entry.channel_id), None)
    if add_missing and template_url:
        for key, upstream in upstream_best.items():
            if key in seen or is_excluded_channel_name(upstream.name, upstream.extinf) or not upstream.channel_id:
                continue
            group = upstream.group or "未分组"
            output_entries.append((build_added_extinf(upstream, group), proxy_url_from_template(template_url, upstream.channel_id)))
            seen.add(key)
            stats["added"] += 1

    output_entries = [(sanitize_logo(extinf, channel_name(extinf)), url) for extinf, url in output_entries]

    header = template_header if template_header else upstream_header
    parts = [header]
    last_group = None
    for extinf, url in number_entries(group_sort_entries(output_entries)):
        group = group_name(extinf)
        if last_group is not None and group != last_group:
            parts.append("")
        parts.extend([extinf, url])
        last_group = group

    return "\n".join(parts).rstrip() + "\n", stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync IPTV.m3u from upstream while preserving local proxy format.")
    parser.add_argument("--upstream", default=DEFAULT_UPSTREAM, help="Upstream m3u URL or file path.")
    parser.add_argument("--template", default="IPTV.m3u", help="Local template m3u path.")
    parser.add_argument("--output", default="IPTV.m3u", help="Output m3u path.")
    parser.add_argument("--no-add-missing", action="store_true", help="Only update channels already present in the template.")
    parser.add_argument("--backup", action="store_true", help="Create a .bak copy before overwriting output.")
    args = parser.parse_args()

    template_text = read_text(args.template)
    upstream_text = read_text(args.upstream)
    output, stats = sync_playlist(template_text, upstream_text, add_missing=not args.no_add_missing)

    output_path = Path(args.output)
    if args.backup and output_path.exists():
        backup_path = output_path.with_suffix(output_path.suffix + ".bak")
        shutil.copy2(output_path, backup_path)
        print(f"Backup written: {backup_path}")

    output_path.write_text(output, encoding="utf-8", newline="\n")
    print(
        "Sync complete: "
        + ", ".join(f"{key}={value}" for key, value in stats.items())
        + f", output={output_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
