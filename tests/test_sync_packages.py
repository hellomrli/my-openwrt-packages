"""Exercise snapshot publication and failure isolation with a local Git upstream."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("sync_packages", SCRIPTS / "sync-packages.py")
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


class PackageSyncTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "mirror"
        self.root.mkdir()
        self.upstream = self.root.parent / "upstream"
        for name in ("scanner", "ui"):
            package = self.upstream / "packages" / name
            package.mkdir(parents=True)
            (package / "Makefile").write_text(f"new {name}\n")
            dest = self.root / name
            dest.mkdir()
            (dest / "Makefile").write_text(f"old {name}\n")
        subprocess.run(["git", "init", "--quiet", "-b", "main"], cwd=self.upstream, check=True)
        subprocess.run(["git", "add", "packages"], cwd=self.upstream, check=True)
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Test fixture"],
                       cwd=self.upstream, check=True)
        self.sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.upstream, text=True).strip()
        self.pair = {"name": "pair", "exports": {"scanner": "packages/scanner", "ui": "packages/ui"},
                     "repo": self.upstream.as_uri(), "branch": "main"}
        self.output = self.root.parent / "outputs"
        self.output.touch()
        self.enterContext(patch.dict(os.environ, GITHUB_OUTPUT=str(self.output)))
        self.summary = self.root / "SYNCED_SOURCES.md"

    def configure(self, *sources):
        (self.root / "sources.json").write_text(json.dumps(sources))

    def test_failed_source_does_not_block_or_enter_commit(self):
        honk = {"name": "honk", "path": "honk", "repo": "unused", "driver": "honk-release"}
        (self.root / "honk").mkdir()
        (self.root / "honk" / "Makefile").write_text("last good")
        previous = "| honk | `honk` | `old-tag` | `123456789abc` | unused |"
        self.summary.write_text(previous + "\n\n- `honk/Makefile`\n")
        overlay = self.root / "overrides" / "scanner"
        overlay.mkdir(parents=True)
        (overlay / "extra").write_text("override")
        self.configure(honk, self.pair)

        def fail_after_write(source, *, root):
            (root / source["path"] / "Makefile").write_text("incomplete")
            raise sync.honk_sync.SyncError("release unavailable")

        with patch.object(sync.honk_sync, "sync", side_effect=fail_after_write):
            self.assertNotEqual(sync.main(self.root), 0)
        self.assertEqual(self.output.read_text(), "paths=scanner ui\n")
        self.assertEqual((self.root / "honk" / "Makefile").read_text(), "last good")
        self.assertIn(previous, self.summary.read_text())
        self.assertIn("- `honk/Makefile`", self.summary.read_text())
        self.assertIn("- `scanner/extra`", self.summary.read_text())
        self.assertEqual(self.summary.read_text().count(f"`{self.sha}`"), 2)
        self.assertEqual((self.root / "scanner" / "extra").read_text(), "override")
        for name in ("scanner", "ui"):
            self.assertEqual((self.root / name / "Makefile").read_text(), f"new {name}\n")

    def test_incomplete_pair_keeps_both_snapshots_and_provenance(self):
        self.pair["exports"]["ui"] = "missing"
        self.configure(self.pair)
        self.summary.write_text("old summary\n")
        self.assertNotEqual(sync.main(self.root), 0)
        self.assertEqual(self.output.read_text(), "")
        self.assertEqual(self.summary.read_text(), "old summary\n")
        for name in ("scanner", "ui"):
            self.assertEqual((self.root / name / "Makefile").read_text(), f"old {name}\n")

    def test_only_selected_source_runs_and_noop_keeps_timestamp(self):
        self.configure({"name": "honk", "path": "honk", "driver": "honk-release"}, self.pair)
        with patch.object(sync.honk_sync, "sync") as honk:
            self.assertEqual(sync.main(self.root, "pair"), 0)
            original = self.summary.read_text().splitlines(keepends=True)
            original[2] = "Last sync: `2000-01-01 00:00:00 UTC`\n"
            self.summary.write_text("".join(original))
            self.assertEqual(sync.main(self.root, "pair"), 0)
            self.assertEqual(self.summary.read_text(), "".join(original))
            honk.assert_not_called()

    def test_swap_failure_restores_all_previous_trees(self):
        staged = self.root.parent / "staged"
        (staged / "scanner").mkdir(parents=True)
        with self.assertRaises(OSError):
            sync.install(staged, self.root, self.pair["exports"], self.root.parent / "previous")
        for name in ("scanner", "ui"):
            self.assertEqual((self.root / name / "Makefile").read_text(), f"old {name}\n")

    def test_fatal_summary_failure_does_not_authorize_commit(self):
        self.configure(self.pair)
        with patch.object(sync, "write_summary", side_effect=OSError("disk full")), self.assertRaises(OSError):
            sync.main(self.root)
        self.assertEqual(self.output.read_text(), "")
