#!/usr/bin/env python3
"""Sync selected OpenWrt packages from upstream Git repositories.

Most sources are a plain copy of the upstream tree.  ``driver: "honk-release"``
sources are mirrored release-by-release instead, because the Honk packages are
only usable together with the generated stage files and hash-locked tarballs
that upstream publishes per release (see ``scripts/honk_sync.py``).

Files under ``overrides/`` are copied over every synced result afterwards.
Upstream packages this firmware has to diverge from (Lucky 3.x no longer ships
from GitHub at all, see ``overrides/README.md``) would otherwise be reverted on
every run, and a revert here silently changes what the firmware builds.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import honk_sync

ROOT = Path(__file__).resolve().parents[1]


class SyncError(RuntimeError):
    pass


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
        print(result.stdout, end="")
    result.check_returncode()
    return result.stdout.strip()


def exports(source: dict) -> dict[str, str]:
    if "exports" in source:
        return source["exports"]
    return {source["path"]: "."}


def copy_package(package: Path, dest: Path, keep_links: bool) -> None:
    metadata = shutil.ignore_patterns(".git", ".github", "*.md~")

    def ignore(directory, names):
        skipped = metadata(directory, names)
        for name in set(names) - skipped:
            entry = Path(directory) / name
            if entry.is_symlink():
                target = entry.resolve(strict=True)
                if (entry.readlink().is_absolute() or not target.is_relative_to(package)
                        or {".git", ".github"}.intersection(target.relative_to(package).parts)):
                    raise SyncError(f"Unsupported package link: {entry.relative_to(package)}")
        return skipped

    shutil.copytree(package, dest, symlinks=keep_links, ignore=ignore)


def clone_source(source: dict, workdir: Path, staged: Path) -> list[dict]:
    repo = source["repo"]
    branch = source.get("branch")
    clone_dir = workdir / "checkout"

    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd += ["-b", branch]
    cmd += [repo, str(clone_dir)]
    run(cmd)

    commit = run(["git", "rev-parse", "HEAD"], cwd=clone_dir)
    actual_branch = branch or run(["git", "branch", "--show-current"], cwd=clone_dir) or "default"

    records = []
    for target, relative in exports(source).items():
        package = clone_dir / relative
        if not package.resolve().is_relative_to(clone_dir):
            raise SyncError(f"Export outside upstream checkout: {relative}")
        if "exports" in source and not (package / "Makefile").is_file():
            raise SyncError(f"No package Makefile in {relative} at {commit}")
        dest = staged / target
        copy_package(package, dest, keep_links="exports" in source)
        if not any(dest.rglob("Makefile")):
            raise SyncError(f"No package Makefile in {target} at {commit}")
        records.append({"name": target if "exports" in source else source["name"], "path": target,
                        "repo": repo, "branch": actual_branch, "commit": commit})
    return records


def apply_overrides(overrides: Path, staged: Path, targets: dict) -> list[str]:
    applied: list[str] = []
    for target in targets:
        for source in sorted((overrides / target).rglob("*")):
            if source.is_dir():
                continue
            relative = source.relative_to(overrides)
            destination = staged / relative
            if not destination.resolve().is_relative_to(staged / target):
                raise SyncError(f"Override outside staged package: {relative}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            applied.append(str(relative))
            print(f"+ override {relative}")
    return applied


def install(staged: Path, root: Path, targets: dict, backup: Path) -> None:
    backup.mkdir()
    with ExitStack() as rollback:
        for name in targets:
            dest = root / name
            if dest.exists() or dest.is_symlink():
                dest.rename(backup / name)
                rollback.callback((backup / name).rename, dest)
            (staged / name).rename(dest)
            rollback.callback(dest.rename, staged / name)
        rollback.pop_all()


def write_summary(summary: Path, targets: list[str], records: list[dict], applied: list[str]) -> None:
    previous = summary.read_text(encoding="utf-8") if summary.exists() else ""
    rows = {match[1]: match[0] for match in re.finditer(r"^\| [^|\n]+ \| `([^`\n]+)` \| .+$", previous, re.M)}
    updated = {item["path"] for item in records}
    retained = [path for path in re.findall(r"^- `([^`\n]+)`$", previous, re.M)
                if path.split("/")[0] in targets and path.split("/")[0] not in updated]
    lines = [
        "| Package | Path | Branch | Commit | Upstream |",
        "|---------|------|--------|--------|----------|",
    ]
    for item in records:
        rows[item["path"]] = (f"| {item['name']} | `{item['path']}` | `{item['branch']}` | "
                              f"`{item['commit']}` | {item['repo']} |")
    lines.extend(rows[name] for name in targets if name in rows)
    lines.append("")
    if applied or retained:
        lines.append("Overridden after sync (see `overrides/README.md`):")
        lines.append("")
        for path in sorted(applied + retained):
            lines.append(f"- `{path}`")
        lines.append("")
    lines.append("> This file is generated by `scripts/sync-packages.py`.")
    lines.append("")
    body = "\n".join(lines)
    if not previous.endswith("\n\n" + body):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        summary.write_text(f"# Synced Sources\n\nLast sync: `{now}`\n\n{body}", encoding="utf-8")


def main(root: Path, only: str | None = None) -> int:
    sources = json.loads((root / "sources.json").read_text(encoding="utf-8"))
    targets = [name for source in sources for name in exports(source)]
    reserved = {"scripts", "tests", "docs", "overrides"}
    if (len(set(targets)) != len(targets) or reserved.intersection(targets)
            or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_+-]*", name) for name in targets)):
        raise SyncError("Package targets must be distinct top-level package directories")
    overrides = root / "overrides"
    if overrides.exists() and any(entry.is_dir() and entry.name not in targets for entry in overrides.iterdir()):
        raise SyncError("Override directory does not belong to a configured package")
    if only and only not in {source["name"] for source in sources}:
        raise SyncError(f"Unknown source: {only}")

    records, applied, failed = [], [], []
    for source in sources:
        if only and source["name"] != only:
            continue
        packages = exports(source)
        with tempfile.TemporaryDirectory(dir=root, prefix=".package-sync-") as tmp:
            work = Path(tmp)
            staged = work / "staged"
            staged.mkdir()
            try:
                if source.get("driver") == "honk-release":
                    path = source["path"]
                    if (root / path).exists():
                        shutil.copytree(root / path, staged / path, symlinks=True)
                    prepared = [honk_sync.sync(source, root=staged)]
                elif "driver" not in source:
                    prepared = clone_source(source, work, staged)
                else:
                    raise SyncError(f"Unknown source driver: {source['driver']}")
                replaced = apply_overrides(overrides, staged, packages)
            except Exception as exc:
                failed.append(source["name"])
                print(f"::error::{source['name']}: {exc}; keeping previous snapshot", file=sys.stderr)
                continue
            install(staged, root, packages, work / "previous")
            records.extend(prepared)
            applied.extend(replaced)

    if records:
        write_summary(root / "SYNCED_SOURCES.md", targets, records, applied)
        if output := os.environ.get("GITHUB_OUTPUT"):
            with open(output, "a", encoding="utf-8") as handle:
                print("paths=" + " ".join(item["path"] for item in records), file=handle)
    if failed:
        print("Failed sources: " + ", ".join(failed), file=sys.stderr)
    return int(bool(failed))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", metavar="SOURCE", help="sync just this named source")
    args = parser.parse_args()
    try:
        raise SystemExit(main(ROOT, args.only))
    except (SyncError, honk_sync.SyncError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
