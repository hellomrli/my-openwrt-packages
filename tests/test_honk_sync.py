"""Exercise release mirroring without network access or GitHub writes."""

import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import honk_sync


def archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        for name in files:
            contents = name.encode()
            member = tarfile.TarInfo(f"source/{name}")
            member.size = len(contents)
            tar.addfile(member, io.BytesIO(contents))
    return output.getvalue()


class HonkSyncTests(unittest.TestCase):
    COMMIT = "756d514e0f4cd37647082e61058d5b382ba88dc0"
    CORE = "aebc0d78d567cac1cd74ee9c56674857c5441451"
    DOONA = "4493e8c36f2d8428462fd6df009270e654c1c660"

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.destination = self.root / "openwrt-honk"
        self.destination.mkdir()
        (self.destination / "previous-mirror").write_text("keep on failure")
        self.source = {"name": "openwrt-honk", "path": "openwrt-honk"}
        self.release = {"tag_name": "v2026.10.30-r41015660706"}
        self.inputs = {}
        hashes = {}
        stage = []
        for arch in honk_sync.ARCHES:
            name = f"honk-core-{self.CORE}-{arch}.tar.gz"
            self.inputs[name] = archive(honk_sync.REQUIRED_LAYOUT["honk"])
            digest = hashlib.sha256(self.inputs[name]).hexdigest()
            hashes[f"HONK_HASH_{arch.upper()}"] = digest
            stage += [f"HONK_ASSET_{arch.upper()}:={name}", f"HONK_HASH_{arch.upper()}:={digest}"]
        doona_name = "doona-4493e8c3-fixture.tar.gz"
        self.inputs[doona_name] = archive(honk_sync.REQUIRED_LAYOUT["doona"])
        doona_hash = hashlib.sha256(self.inputs[doona_name]).hexdigest()
        self.manifest = {
            "candidate_sha": self.COMMIT,
            "source_tag": "source-41012758310",
            "core_commit": self.CORE,
            "core_archives_sha256": hashes,
            "doona_commit": self.DOONA,
            "doona_archive": doona_name,
            "doona_archive_sha256": doona_hash,
            "honk_version": "2026.10.24-r2",
            "luci_version": "2026.10.30-r1",
        }
        self.inputs["generated-stage.mk"] = ("\n".join(stage) + "\n").encode()
        self.inputs["generated-doona-stage.mk"] = (
            f"PKG_SOURCE:={doona_name}\nPKG_HASH:={doona_hash}\nPKG_SOURCE_SUBDIR:=doona\n"
        ).encode()
        self.inputs["generated-provenance.json"] = json.dumps({"core": {
            "repository": "https://github.com/Glassyiris/honk.git",
            "commit": self.CORE,
            "features": "clash-api,ebpf,rprx,native-api",
            "architectures": list(honk_sync.ARCHES),
        }}).encode()
        self.set_source_tag(self.manifest["source_tag"])
        self.downloads = []
        self.patch("ROOT", self.root)
        self.patch("latest_wrapper_release", mock.Mock(return_value=self.release))
        self.patch("resolve_commit", mock.Mock(return_value=self.COMMIT))
        self.patch("gh", mock.Mock(side_effect=self.gh))
        self.checkout = self.patch("checkout_wrapper", mock.Mock(side_effect=self.checkout_wrapper))
        self.patch("download", mock.Mock(side_effect=self.download))
        self.releases = self.patch("ensure_release", mock.Mock())
        self.uploads = self.patch("upload_asset", mock.Mock())
        self.remote_assets = self.patch("mirror_release_assets", mock.Mock(return_value={}))

    def patch(self, name, value):
        patcher = mock.patch.object(honk_sync, name, value)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def set_source_tag(self, tag):
        self.manifest["source_tag"] = tag
        self.upstream_url = f"https://github.com/{honk_sync.UPSTREAM_SLUG}/releases/download/{tag}"
        for name in ("generated-stage.mk", "generated-doona-stage.mk"):
            lines = [line for line in self.inputs[name].decode().splitlines()
                     if not line.startswith("PKG_SOURCE_URL:=")]
            self.inputs[name] = (f"PKG_SOURCE_URL:={self.upstream_url}/\n" + "\n".join(lines) + "\n").encode()

    def gh(self, *args):
        self.assertEqual(args, (
            "release", "download", self.release["tag_name"], "--repo", honk_sync.UPSTREAM_SLUG,
            "--pattern", "release-manifest.json", "--output", "-",
        ))
        return json.dumps(self.manifest)

    def checkout_wrapper(self, commit, destination):
        self.assertEqual(commit, self.COMMIT)
        for package, version, release in (("honk", "2026.10.24", "2"),
                                           ("luci-app-honk", "2026.10.30", "1")):
            path = destination / package
            path.mkdir(parents=True)
            (path / "Makefile").write_text(
                f"PKG_NAME:={package}\nPKG_VERSION:={version}\nPKG_RELEASE:={release}\n"
                f"CORE_COMMIT:={self.CORE}\n"
            )

    def download(self, url, destination):
        self.assertTrue(url.startswith(self.upstream_url + "/"), url)
        name = url.removeprefix(self.upstream_url + "/")
        self.downloads.append(name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.inputs[name])

    def sync(self):
        result = honk_sync.sync(self.source)
        self.assertEqual(result["repo"], honk_sync.UPSTREAM_URL)
        return json.loads((self.destination / honk_sync.PIN_NAME).read_text())

    def assert_rejected(self):
        with self.assertRaises(honk_sync.SyncError):
            honk_sync.sync(self.source)
        self.assertEqual((self.destination / "previous-mirror").read_text(), "keep on failure")
        self.releases.assert_not_called()
        self.uploads.assert_not_called()

    def test_promoted_release_uses_manifest_source_not_wrapper_run(self):
        pin = self.sync()
        self.assertEqual(pin["source_tag"], "source-41012758310")
        self.assertEqual(pin["mirror_release"], "honk-source-41012758310")
        self.assertEqual(pin["wrapper_commit"], self.COMMIT)
        self.assertEqual(pin["upstream_release"], self.release["tag_name"])
        self.assertEqual(len(pin["assets"]), 4)
        self.assertEqual(self.uploads.call_count, 4)
        for file in ("honk/generated-stage.mk", "luci-app-honk/generated-doona-stage.mk"):
            text = (self.destination / file).read_text()
            self.assertIn(pin["release_url"], text)
            self.assertNotIn(self.upstream_url, text)

    def test_short_release_number_is_not_a_source_run(self):
        self.release["tag_name"] = "v2026.10.29-r7"
        self.set_source_tag("source-40914867358")
        pin = self.sync()
        self.assertEqual(pin["mirror_release"], "honk-source-40914867358")

    def test_repeated_wrapper_number_does_not_overwrite_previous_source_release(self):
        self.release["tag_name"] = "v2026.10.29-r7"
        self.set_source_tag("source-40914867358")
        previous = self.sync()
        self.release["tag_name"] = "v2026.10.30-r7"
        self.set_source_tag("source-41012758310")
        current = self.sync()
        self.assertNotEqual(previous["mirror_release"], current["mirror_release"])

    def test_reused_core_without_manifest_hashes_still_verifies_stage_hashes(self):
        self.manifest["core_archives_sha256"] = {}
        self.sync()

    def test_complete_mirror_is_reused(self):
        pin = self.sync()
        self.remote_assets.return_value = {value["name"]: value["size"] for value in pin["assets"].values()}
        self.checkout.reset_mock()
        self.uploads.reset_mock()
        self.downloads.clear()
        self.assertEqual(self.sync(), pin)
        self.checkout.assert_not_called()
        self.uploads.assert_not_called()
        self.assertEqual(self.downloads, [])

    def test_manifest_is_checked_even_when_mirror_exists(self):
        self.sync()
        self.manifest["candidate_sha"] = "0" * 40
        with self.assertRaisesRegex(honk_sync.SyncError, "wrapper tag commit"):
            self.sync()

    def test_invalid_or_missing_manifest_fails_without_guessing_a_source(self):
        for raw in ("not json", "null", "[]"):
            with self.subTest(raw=raw), mock.patch.object(honk_sync, "gh", return_value=raw):
                self.assert_rejected()
        with mock.patch.object(honk_sync, "gh", side_effect=honk_sync.SyncError("asset missing")):
            self.assert_rejected()
        self.assertEqual(self.downloads, [])

    def test_invalid_manifest_fields_are_rejected(self):
        cases = {
            "source_tag": [None, 7, "source-7/../staging", "staging", "source-7?download=1"],
            "candidate_sha": [None, "0" * 40],
            "core_commit": [None, 123, "short"],
            "doona_commit": [None, "short"],
            "doona_archive": [None, "../doona.tar.gz", "doona.tar.gz/extra"],
            "doona_archive_sha256": [None, "SKIP", "short"],
            "core_archives_sha256": [None, [], {"HONK_HASH_X86_64": "SKIP"}, {"UNKNOWN": "a" * 64}],
        }
        for key, values in cases.items():
            original = self.manifest[key]
            for value in values:
                with self.subTest(key=key, value=value):
                    self.manifest[key] = value
                    self.assert_rejected()
            self.manifest[key] = original
        self.assertEqual(self.downloads, [])

    def test_mismatched_wrapper_versions_or_core_are_rejected(self):
        for field in ("honk_version", "luci_version", "core_commit"):
            with self.subTest(field=field):
                original = self.manifest[field]
                self.manifest[field] = "0" * 40
                self.assert_rejected()
                self.manifest[field] = original

    def test_stage_url_cannot_hide_wrong_source_behind_a_comment(self):
        name = "generated-stage.mk"
        self.inputs[name] = self.inputs[name].replace(
            self.upstream_url.encode(), (self.upstream_url + "-wrong").encode()
        ) + f"# Expected: {self.upstream_url}\n".encode()
        self.assert_rejected()

    def test_core_stage_and_manifest_hashes_must_agree(self):
        self.manifest["core_archives_sha256"]["HONK_HASH_X86_64"] = "0" * 64
        self.assert_rejected()

    def test_partial_manifest_core_hash_map_is_rejected(self):
        del self.manifest["core_archives_sha256"]["HONK_HASH_ARMV7"]
        self.assert_rejected()

    def test_doona_stage_and_manifest_must_agree(self):
        for field in ("doona_archive", "doona_archive_sha256"):
            with self.subTest(field=field):
                original = self.manifest[field]
                self.manifest[field] = "other.tar.gz" if field == "doona_archive" else "0" * 64
                self.assert_rejected()
                self.manifest[field] = original

    def test_stage_assets_cannot_escape_download_directory(self):
        self.inputs["generated-stage.mk"] = self.inputs["generated-stage.mk"].replace(
            b"HONK_ASSET_X86_64:=", b"HONK_ASSET_X86_64:=../"
        )
        self.assert_rejected()

    def test_skip_hash_is_rejected_for_every_architecture(self):
        original = self.inputs["generated-stage.mk"]
        self.manifest["core_archives_sha256"] = {}
        for arch in honk_sync.ARCHES:
            with self.subTest(arch=arch):
                key = f"HONK_HASH_{arch.upper()}"
                digest = honk_sync.make_value(original.decode(), key)
                self.inputs["generated-stage.mk"] = original.replace(
                    f"{key}:={digest}".encode(), f"{key}:=SKIP".encode()
                )
                self.assert_rejected()

    def test_corrupt_archive_preserves_previous_mirror(self):
        name = f"honk-core-{self.CORE}-x86_64.tar.gz"
        self.inputs[name] += b"corrupted"
        self.assert_rejected()

    def test_null_existing_pin_is_replaced(self):
        (self.destination / honk_sync.PIN_NAME).write_text("null")
        self.sync()


if __name__ == "__main__":
    unittest.main()
