#!/usr/bin/env python3
"""Build this pure-data package with upstream apk-tools 3 (no cross compiler)."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", required=True, type=Path, help="apk-tools 3 binary with mkpkg enabled")
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("run under fakeroot (or root) to ensure root-owned package files")
    makefile = (PACKAGE / "Makefile").read_text()
    name = re.search(r"^PKG_NAME:=(.+)$", makefile, re.M)[1].strip()
    version = re.search(r"^PKG_VERSION:=(.+)$", makefile, re.M)[1].strip()
    release = re.search(r"^PKG_RELEASE:=(.+)$", makefile, re.M)[1].strip()
    manifest = json.loads((PACKAGE / "SOURCE.json").read_text())
    for relative, expected in manifest["sha256"].items():
        if hashlib.sha256((PACKAGE / relative).read_bytes()).hexdigest() != expected:
            raise ValueError(f"source manifest mismatch: {relative}")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    artifact = output / f"{name}-{version}-r{release}.apk"
    with tempfile.TemporaryDirectory(prefix="iptv-apk-") as tmp:
        stage = Path(tmp) / "root"
        shutil.copytree(PACKAGE / "root", stage, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        metadata = stage / "lib/apk/packages"
        metadata.mkdir(parents=True)
        conffiles = re.search(r"define Package/[^\n]+/conffiles\n(.*?)\nendef", makefile, re.S)[1] + "\n"
        (metadata / f"{name}.conffiles").write_text(conffiles)
        (metadata / f"{name}.conffiles_static").write_text("".join(
            f"{path} {hashlib.sha256((stage / path.lstrip('/')).read_bytes()).hexdigest()}\n"
            for path in conffiles.splitlines() if path
        ))
        paths = ["/" + str(p.relative_to(stage)) for p in stage.rglob("*") if p.is_file()]
        paths.append(f"/lib/apk/packages/{name}.list")
        (metadata / f"{name}.list").write_text("\n".join(sorted(paths)) + "\n")
        epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "1792800000"))
        for path in [stage, *stage.rglob("*")]:
            os.chown(path, 0, 0)
            path.chmod(0o755 if path.is_dir() or path == stage / "etc/init.d/iptv-catchup" else 0o644)
            os.utime(path, (epoch, epoch))
        command = [str(args.apk.resolve()), "mkpkg", "--compat", "3.0.0", "--no-xattrs",
                   "--files", str(stage), "--output", str(artifact)]
        for key, value in {
            "name": name, "version": f"{version}-r{release}", "arch": "noarch",
            "description": "LuCI IPTV catchup and timeshift service for Guangxi Mobile",
            "url": "https://github.com/hellomrli/my-openwrt-packages",
            "origin": name, "depends": "luci-base rpcd python3",
            "build-time": str(epoch),
        }.items():
            command.extend(["--info", f"{key}:{value}"])
        # Use exactly the same lifecycle bodies as the SDK package definition.
        for section, kinds in (("postinst", ("post-install", "post-upgrade")),
                               ("prerm", ("pre-deinstall",))):
            body = re.search(rf"define Package/[^\n]+/{section}\n(.*?)\nendef", makefile, re.S)[1]
            script = Path(tmp) / section
            script.write_text(body.replace("$$", "$") + "\n")
            for kind in kinds:
                command.extend(["--script", f"{kind}:{script}"])
        subprocess.run(command, check=True)
    (output / "SHA256SUMS").write_text(f"{hashlib.sha256(artifact.read_bytes()).hexdigest()}  {artifact.name}\n")
    print(artifact)


if __name__ == "__main__":
    main()
