#!/usr/bin/env python3
"""Check the candidate records emitted and accepted by the release workflows."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/auto-bump.yml"
VERIFIER = ROOT / "scripts/verify-candidate.sh"


def embedded_python(path, marker):
    source = path.read_text()
    terminator = "\n          PYEOF" if path == WORKFLOW else "\nPYEOF"
    block = source.split(marker, 1)[1].split(terminator, 1)[0]
    return textwrap.dedent(block.lstrip("\n"))


class ReleaseCandidateRecordTest(unittest.TestCase):
    def setUp(self):
        self.env = os.environ.copy()
        self.env.update({
            "CANDIDATE_SHA": "a" * 40,
            "BASE_SHA": "a" * 40,
            "STAGING": "auto-bump-staging-123-1",
            "AUTO_BUMP_RUN_ID": "123",
            "AUTO_BUMP_RUN_ATTEMPT": "1",
            "ASSEMBLE_DAE_RUN_ID": "201",
            "ASSEMBLE_DAE_ATTEMPT": "1",
            "ASSEMBLE_DAE_HEAD_SHA": "b" * 40,
            "ASSEMBLE_DAED_RUN_ID": "202",
            "ASSEMBLE_DAED_ATTEMPT": "1",
            "ASSEMBLE_DAED_HEAD_SHA": "b" * 40,
            "AUDIT_RUN_ID": "203",
            "AUDIT_ATTEMPT": "1",
            "SMOKE_RUN_ID": "204",
            "SMOKE_ATTEMPT": "1",
            "MATRIX_RUN_ID": "205",
            "MATRIX_ATTEMPT": "1",
        })
        self.generator = embedded_python(WORKFLOW, "python3 <<'PYEOF' > verification.json")
        self.validator = embedded_python(VERIFIER, "python3 - \"$workdir/verification.json\" <<'PYEOF' > \"$workdir/checks.tsv\"")

    def record(self, lane):
        self.env["LANE"] = lane
        result = subprocess.run(["python3", "-c", self.generator], env=self.env,
                                capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def validate(self, record):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "verification.json"
            path.write_text(json.dumps(record))
            return subprocess.run(["python3", "-c", self.validator, str(path)],
                                  capture_output=True, text=True)

    def test_current_main_candidate_uses_three_gates(self):
        record = self.record("current")
        self.assertEqual(record["schema_version"], 2)
        self.assertEqual(record["kind"], "main")
        self.assertEqual(record["candidate_sha"], record["base_sha"])
        self.assertEqual([item["step"] for item in record["verifications"]],
                         ["audit", "smoke", "matrix"])
        self.assertEqual(self.validate(record).returncode, 0)

    def test_upstream_candidate_keeps_all_five_gates(self):
        self.env["BASE_SHA"] = "b" * 40
        record = self.record("core")
        self.assertEqual(record["kind"], "upstream")
        self.assertEqual([item["step"] for item in record["verifications"]],
                         ["assemble-dae", "assemble-daed", "audit", "smoke", "matrix"])
        self.assertEqual(self.validate(record).returncode, 0)

    def test_main_candidate_rejects_extra_or_missing_gates(self):
        record = self.record("current")
        record["verifications"].append({
            "step": "assemble-dae", "workflow": ".github/workflows/assemble-dae-src.yml",
            "run_id": 201, "attempt": 1, "head_sha": "b" * 40,
        })
        self.assertNotEqual(self.validate(record).returncode, 0)
        record["verifications"] = record["verifications"][:2]
        self.assertNotEqual(self.validate(record).returncode, 0)

    def test_old_schema_is_not_promotable(self):
        record = self.record("current")
        record["schema_version"] = 1
        self.assertNotEqual(self.validate(record).returncode, 0)


if __name__ == "__main__":
    unittest.main()
