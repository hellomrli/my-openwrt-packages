#!/usr/bin/env python3
"""Mirror kenzok8/openwrt-honk, including the per-release build assets.

The Honk feed is not a plain package tree.  ``honk/Makefile`` and
``luci-app-honk/Makefile`` only carry a fallback that points at the rolling
``staging`` release with ``SKIP`` hashes; a real build consumes
``generated-stage.mk`` / ``generated-doona-stage.mk`` from a ``source-<run>``
release plus the tarballs those files hash-lock.  Mirroring the branch head
alone would hand the firmware a package that silently downloads unverified
bytes from upstream.

This module therefore mirrors a *release*, not a branch:

1. resolve the newest ``v<date>-r<run>`` release; its tag records the build
   run id and ``target_commitish`` points at the wrapper commit it was built
   from;
2. check that commit out and copy the package directories into the mirror;
3. download the matching ``source-<run>`` assets, verify every digest against
   the generated stage files and re-upload them to a mirror release, so builds
   never contact kenzok8 again;
4. rewrite both stage files to the mirror release URL and write
   ``honk-source.json`` describing the commit, the release and every asset.

Only top-level directories that contain a ``Makefile`` are copied; the
upstream ``ci/`` and ``scripts/`` trees are build tooling, not OpenWrt
packages.  A new package directory added upstream is picked up automatically.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_SLUG = "kenzok8/openwrt-honk"
UPSTREAM_URL = f"https://github.com/{UPSTREAM_SLUG}.git"
MIRROR_SLUG = "hellomrli/my-openwrt-packages"
MIRROR_URL = f"https://github.com/{MIRROR_SLUG}.git"
PIN_NAME = "honk-source.json"
WRAPPER_TAG_RE = re.compile(r"^v(?P<date>\d{4}\.\d{2}\.\d{2})-r(?P<run>\d+)$")
ARCHES = ("x86_64", "aarch64", "armv7")
# Root directory content required in each asset, keyed by pin asset kind.
REQUIRED_LAYOUT = {
    "honk": ("honk-core", "LICENSE"),
    "doona": ("index.html", "LICENSE", "NOTICE", "SOURCE", "THIRD-PARTY-NOTICES.txt"),
}
DOWNLOAD_ATTEMPTS = 3


class SyncError(RuntimeError):
    pass


def gh(*args: str) -> str:
    result = subprocess.run(
        ["gh", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise SyncError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def api_json(path: str) -> dict:
    return json.loads(gh("api", path))


def run(cmd: list[str], cwd: Path | None = None) -> str:
    print("+", " ".join(cmd), flush=True)
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.stdout:
        print(result.stdout, end="", flush=True)
    if result.returncode != 0:
        raise SyncError(f"command failed ({result.returncode}): {' '.join(cmd)}")
    return result.stdout.strip()


def sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def make_value(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:=(.+)$", text, re.MULTILINE)
    if not match:
        raise SyncError(f"generated file has no {key}:= value")
    return match.group(1).strip()


def latest_wrapper_release() -> dict:
    releases = json.loads(gh("api", f"repos/{UPSTREAM_SLUG}/releases?per_page=20"))
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        if WRAPPER_TAG_RE.match(release.get("tag_name", "")):
            return release
    raise SyncError(f"{UPSTREAM_SLUG} has no v<date>-r<run> release")


def resolve_commit(tag: str) -> str:
    """Return the commit a release tag points at, peeling annotated tags."""
    reference = api_json(f"repos/{UPSTREAM_SLUG}/git/ref/tags/{tag}")
    target = reference["object"]
    if target["type"] == "tag":
        tag_object = api_json(f"repos/{UPSTREAM_SLUG}/git/tags/{target['sha']}")
        target = tag_object["object"]
    commit = target["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SyncError(f"Tag {tag} did not resolve to a commit")
    return commit


def checkout_wrapper(commit: str, destination: Path) -> None:
    destination.mkdir(parents=True)
    for command in (
        ["git", "init", "--quiet"],
        ["git", "remote", "add", "origin", UPSTREAM_URL],
        ["git", "fetch", "--quiet", "--depth", "1", "origin", commit],
        ["git", "checkout", "--quiet", "FETCH_HEAD"],
    ):
        run(command, cwd=destination)
    actual = run(["git", "rev-parse", "HEAD"], cwd=destination)
    if actual != commit:
        raise SyncError(f"Fetched wrapper commit {actual}, expected {commit}")


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    for attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "my-openwrt-packages-sync"}
            )
            with urllib.request.urlopen(request, timeout=180) as response, partial.open(
                "wb"
            ) as handle:
                shutil.copyfileobj(response, handle)
            partial.replace(destination)
            return
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            partial.unlink(missing_ok=True)
            if attempt == DOWNLOAD_ATTEMPTS - 1:
                raise SyncError(f"Could not download {url}: {exc}") from exc
            print(f"::warning::Download failed ({exc}); retrying {url}", flush=True)
            time.sleep(5)


def verify_layout(path: Path, kind: str) -> None:
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getnames()
    roots = {name.split("/")[0] for name in members}
    if len(roots) != 1:
        raise SyncError(f"Unexpected archive layout in {path.name}: {sorted(roots)}")
    root = roots.pop()
    names = {name[len(root) + 1 :] for name in members if name.startswith(root + "/")}
    missing = [name for name in REQUIRED_LAYOUT[kind] if name not in names]
    if missing:
        raise SyncError(f"{path.name} is missing {missing}")


def asset_key(kind: str, arch: str | None = None) -> str:
    return f"{kind}_{arch}" if arch else kind


def mirror_release_assets(tag: str) -> dict[str, int]:
    try:
        release = api_json(f"repos/{MIRROR_SLUG}/releases/tags/{tag}")
    except SyncError:
        return {}
    return {asset["name"]: int(asset.get("size", 0)) for asset in release.get("assets", [])}


def ensure_release(tag: str, title: str, body: str) -> None:
    try:
        api_json(f"repos/{MIRROR_SLUG}/releases/tags/{tag}")
        return
    except SyncError:
        pass
    print(f"Creating mirror release {tag}", flush=True)
    gh(
        "api",
        "-X",
        "POST",
        f"repos/{MIRROR_SLUG}/releases",
        "-f",
        f"tag_name={tag}",
        "-f",
        "target_commitish=main",
        "-f",
        f"name={title}",
        "-f",
        f"body={body}",
    )


def upload_asset(tag: str, path: Path) -> None:
    print(f"+ gh release upload {tag} {path.name}", flush=True)
    gh("release", "upload", tag, str(path), "--clobber", "-R", MIRROR_SLUG)


def reusable(pin: dict, source_tag: str, commit: str, mirror_tag: str) -> bool:
    if pin.get("source_tag") != source_tag or pin.get("wrapper_commit") != commit:
        return False
    assets = pin.get("assets") or {}
    sizes = mirror_release_assets(mirror_tag)
    for asset in assets.values():
        if sizes.get(asset["name"]) != asset.get("size"):
            return False
    return bool(assets)


def sync(source: dict) -> dict:
    path = ROOT / source["path"]
    release = latest_wrapper_release()
    tag = release["tag_name"]
    match = WRAPPER_TAG_RE.match(tag)
    if not match:
        raise SyncError(f"Unexpected wrapper release tag: {tag}")
    run_id = match.group("run")
    source_tag = f"source-{run_id}"
    mirror_tag = f"honk-source-{run_id}"
    release_url = f"https://github.com/{MIRROR_SLUG}/releases/download/{mirror_tag}"
    commit = resolve_commit(tag)

    pin_path = path / PIN_NAME
    if pin_path.is_file():
        try:
            existing = json.loads(pin_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
        if reusable(existing, source_tag, commit, mirror_tag):
            print(f"Honk {tag} is already mirrored; nothing to do.", flush=True)
            return {
                "name": source["name"],
                "path": source["path"],
                "repo": UPSTREAM_URL,
                "branch": tag,
                "commit": commit,
                "description": source.get("description", ""),
            }

    with tempfile.TemporaryDirectory(prefix="honk-sync-") as directory:
        work = Path(directory)
        checkout = work / "wrapper"
        checkout_wrapper(commit, checkout)

        packages = sorted(
            entry
            for entry in checkout.iterdir()
            if entry.is_dir() and (entry / "Makefile").is_file()
        )
        if not packages:
            raise SyncError("Wrapper checkout contains no OpenWrt package directory")

        staged = work / "openwrt-honk"
        staged.mkdir()
        for package in packages:
            shutil.copytree(package, staged / package.name)

        assets_dir = work / "assets"
        for name in (
            "generated-stage.mk",
            "generated-doona-stage.mk",
            "generated-provenance.json",
        ):
            download(
                f"https://github.com/{UPSTREAM_SLUG}/releases/download/{source_tag}/{name}",
                assets_dir / name,
            )

        provenance = json.loads((assets_dir / "generated-provenance.json").read_text())
        core = provenance.get("core", {})
        core_commit = core.get("commit", "")
        if not re.fullmatch(r"[0-9a-f]{40}", core_commit):
            raise SyncError("Release provenance has no core commit")
        if core_commit != make_value((staged / "honk/Makefile").read_text(), "CORE_COMMIT"):
            raise SyncError("Release provenance does not match the wrapper CORE_COMMIT")
        architectures = core.get("architectures", [])
        if not architectures or any(arch not in ARCHES for arch in architectures):
            raise SyncError(f"Unsupported core architectures: {architectures}")

        stage_text = (assets_dir / "generated-stage.mk").read_text()
        doona_stage_text = (assets_dir / "generated-doona-stage.mk").read_text()
        upstream_url = f"https://github.com/{UPSTREAM_SLUG}/releases/download/{source_tag}"
        for text, name in ((stage_text, "generated-stage.mk"), (doona_stage_text, "generated-doona-stage.mk")):
            if upstream_url not in text:
                raise SyncError(f"{name} does not point at {source_tag}")
        if make_value(stage_text, "HONK_HASH_X86_64") == "SKIP":
            raise SyncError("generated-stage.mk carries a SKIP hash")

        assets: dict[str, dict] = {}
        for arch in architectures:
            name = make_value(stage_text, f"HONK_ASSET_{arch.upper()}")
            expected = make_value(stage_text, f"HONK_HASH_{arch.upper()}")
            destination = assets_dir / name
            download(f"{upstream_url}/{name}", destination)
            actual = sha256_file(destination)
            if actual != expected:
                raise SyncError(f"{name} hash mismatch: {actual} != {expected}")
            verify_layout(destination, "honk")
            assets[asset_key("honk", arch)] = {
                "name": name,
                "sha256": actual,
                "size": destination.stat().st_size,
            }

        doona_name = make_value(doona_stage_text, "PKG_SOURCE")
        doona_expected = make_value(doona_stage_text, "PKG_HASH")
        doona_path = assets_dir / doona_name
        download(f"{upstream_url}/{doona_name}", doona_path)
        doona_actual = sha256_file(doona_path)
        if doona_actual != doona_expected:
            raise SyncError(f"{doona_name} hash mismatch: {doona_actual} != {doona_expected}")
        verify_layout(doona_path, "doona")
        assets["doona"] = {
            "name": doona_name,
            "sha256": doona_actual,
            "size": doona_path.stat().st_size,
        }

        ensure_release(
            mirror_tag,
            f"Honk source {tag}",
            f"Mirrored from {UPSTREAM_URL} release {tag} (wrapper {commit}).\n\n"
            "Assets are the hash-locked inputs for the OpenWrt honk / luci-app-honk "
            "packages in this repository.",
        )
        existing_sizes = mirror_release_assets(mirror_tag)
        for asset in assets.values():
            if existing_sizes.get(asset["name"]) != asset["size"]:
                upload_asset(mirror_tag, assets_dir / asset["name"])

        for name, destination in (
            ("generated-stage.mk", staged / "honk/generated-stage.mk"),
            ("generated-doona-stage.mk", staged / "luci-app-honk/generated-doona-stage.mk"),
        ):
            text = (assets_dir / name).read_text()
            destination.write_text(text.replace(upstream_url, release_url))

        shutil.copyfile(
            assets_dir / "generated-provenance.json",
            staged / "honk/generated-provenance.json",
        )

        pin = {
            "format": 1,
            "upstream": UPSTREAM_URL,
            "upstream_release": tag,
            "source_tag": source_tag,
            "wrapper_commit": commit,
            "mirror_repository": MIRROR_URL,
            "mirror_release": mirror_tag,
            "release_url": release_url,
            "core": {
                "repository": core.get("repository", ""),
                "commit": core_commit,
                "version": core.get("version", ""),
                "features": core.get("features", ""),
                "architectures": architectures,
            },
            "assets": assets,
        }
        (staged / PIN_NAME).write_text(
            json.dumps(pin, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        if path.exists():
            shutil.rmtree(path)
        shutil.copytree(staged, path)

    print(f"Mirrored Honk {tag} as {mirror_tag}", flush=True)
    return {
        "name": source["name"],
        "path": source["path"],
        "repo": UPSTREAM_URL,
        "branch": tag,
        "commit": commit,
        "description": source.get("description", ""),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default="openwrt-honk", help="mirror directory")
    args = parser.parse_args()
    try:
        sync({"name": "openwrt-honk", "path": args.path})
    except SyncError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
