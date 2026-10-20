#!/usr/bin/env python3
"""Exercise APK feed lookup and upgrade decisions with observed apk list shapes."""

import os
import pathlib
import subprocess
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HELPERS = ROOT / "luci-app-daede/root/usr/share/luci-app-daede"
LOG = pathlib.Path("/tmp/luci-app-daede.pkg-luci-app-daede.log")
LOCK = pathlib.Path("/tmp/luci-app-daede.pkg-luci-app-daede.lock")
INDEX_FILES = tuple(pathlib.Path("/tmp/luci-app-daede.idx." + suffix)
                    for suffix in ("lock", "status", "log", "run.lock"))


class PackageUpdatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="daede-updates-test-")
        self.addCleanup(self.tmp.cleanup)
        self.mock = pathlib.Path(self.tmp.name)
        self.installed = self.mock / "installed"
        self.calls = self.mock / "calls"
        self.installed.write_text("1.15-r10")
        (self.mock / "apk").write_text("""#!/bin/sh
case "$1" in
  info) [ -s "$MOCK_INSTALLED_FILE" ] ;;
  list)
    case "$2" in
      -I|--installed)
        v=$(cat "$MOCK_INSTALLED_FILE")
        [ -n "$v" ] && echo "luci-app-daede-$v noarch {/feed/luci-app-daede} () [installed]"
        ;;
      -a)
        [ -n "$MOCK_REPO_VERSION" ] && echo "luci-app-daede-$MOCK_REPO_VERSION noarch {/feed/luci-app-daede} ()"
        ;;
      -u) ;;
      *)
        v=$(cat "$MOCK_INSTALLED_FILE")
        [ -n "$MOCK_REPO_VERSION" ] && echo "luci-app-daede-$MOCK_REPO_VERSION noarch {/feed/luci-app-daede} ()"
        [ -n "$v" ] && echo "luci-app-daede-$v noarch {/feed/luci-app-daede} () [installed]"
        ;;
    esac
    ;;
  update) [ "$MOCK_UPDATE_FAIL" != 1 ] ;;
  add)
    echo "$*" >> "$MOCK_CALLS"
    if [ "$2" != -s ]; then echo "${2#*=}" > "$MOCK_INSTALLED_FILE"; fi
    ;;
esac
""")
        (self.mock / "flock").write_text("""#!/bin/sh
case "$1" in
  -n|9) exit 0 ;;
  *) shift; exec "$@" ;;
esac
""")
        for name in ("apk", "flock"):
            (self.mock / name).chmod(0o755)
        self.env = os.environ.copy()
        self.env.update({
            "PATH": str(self.mock) + os.pathsep + self.env["PATH"],
            "MOCK_INSTALLED_FILE": str(self.installed),
            "MOCK_CALLS": str(self.calls),
            "MOCK_REPO_VERSION": "1.15-r9",
            "MOCK_UPDATE_FAIL": "0",
        })

    def probe(self):
        result = subprocess.run(["sh", str(HELPERS / "pkg-info.sh"), "luci-app-daede"],
                                env=self.env, capture_output=True, text=True, check=True)
        return result.stdout.rstrip("\n").split("\t")

    def upgrade_log(self):
        for path in (LOG, LOCK):
            if path.exists():
                self.fail(f"pre-existing update state: {path}")
        self.addCleanup(lambda: LOG.unlink(missing_ok=True))
        self.addCleanup(lambda: LOCK.unlink(missing_ok=True))
        subprocess.run(["sh", str(HELPERS / "update-pkg.sh"), "luci-app-daede"],
                       env=self.env, capture_output=True, text=True, check=True)
        for _ in range(100):
            if LOG.exists():
                output = LOG.read_text(errors="replace")
                if "✓" in output or "✗" in output:
                    return output
            time.sleep(0.05)
        self.fail("upgrade did not write a terminal status")

    def refresh_status(self):
        for path in INDEX_FILES:
            if path.exists():
                self.fail(f"pre-existing index state: {path}")
            self.addCleanup(lambda item=path: item.unlink(missing_ok=True))
        subprocess.run(["sh", str(HELPERS / "refresh-index.sh")],
                       env=self.env, capture_output=True, text=True, check=True)
        status = INDEX_FILES[1]
        for _ in range(100):
            if status.exists() and status.read_text().strip() in ("success", "failed"):
                return status.read_text().strip()
            time.sleep(0.05)
        self.fail("index refresh did not write a terminal status")

    def test_local_newer_than_feed_is_not_a_feed_candidate(self):
        self.assertEqual(self.probe(), ["1.15-r10", "1.15-r9", "managed"])

    def test_missing_feed_does_not_claim_local_version_as_latest(self):
        self.env["MOCK_REPO_VERSION"] = ""
        self.assertEqual(self.probe(), ["1.15-r10", "", "managed"])

    def test_missing_install_is_reported(self):
        self.installed.write_text("")
        self.assertEqual(self.probe(), ["", "1.15-r9", "absent"])

    def test_failed_index_refresh_never_installs(self):
        self.env["MOCK_UPDATE_FAIL"] = "1"
        self.assertIn("✗ 失败", self.upgrade_log())
        self.assertFalse(self.calls.exists())

    def test_background_index_refresh_reports_result(self):
        self.assertEqual(self.refresh_status(), "success")

    def test_background_index_refresh_reports_failure(self):
        self.env["MOCK_UPDATE_FAIL"] = "1"
        self.assertEqual(self.refresh_status(), "failed")

    def test_missing_feed_never_installs(self):
        self.env["MOCK_REPO_VERSION"] = ""
        self.assertIn("软件源中没有找到", self.upgrade_log())
        self.assertFalse(self.calls.exists())

    def test_older_feed_is_a_successful_noop(self):
        self.assertIn("✓ 无需升级", self.upgrade_log())
        self.assertFalse(self.calls.exists())

    def test_newer_feed_installs_exact_version(self):
        self.env["MOCK_REPO_VERSION"] = "1.15-r11"
        self.assertIn("✓ 完成", self.upgrade_log())
        self.assertEqual(self.installed.read_text().strip(), "1.15-r11")
        self.assertIn("luci-app-daede=1.15-r11", self.calls.read_text())


if __name__ == "__main__":
    unittest.main()
