"""Exercise the release entrypoint and the vendored Ops receipt contract offline."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


class ReleaseReceiptTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="danube-receipt-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        source = Path(__file__).resolve().parents[1]
        (self.root / "ops").mkdir()
        for name in ("write_build_revision.py", "write-release-receipt.mjs"):
            shutil.copyfile(source / "ops" / name, self.root / "ops" / name)
        shutil.copyfile(source / ".gitignore", self.root / ".gitignore")
        self.environment = os.environ.copy()
        for key in ("FORGE_DEPLOY_SHA", "FORGE_COMMIT_HASH", "GIT_COMMIT"):
            self.environment.pop(key, None)
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=Receipt Test", "-c", "user.email=receipt@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
        self.revision = self.git("rev-parse", "HEAD").strip()

    def git(self, *arguments):
        return subprocess.check_output(
            ["git", *arguments], cwd=self.root, env=self.environment, text=True,
            stderr=subprocess.PIPE, timeout=10,
        )

    def receipt(self):
        return json.loads((self.root / ".release-receipt.json").read_text())

    def run_writer(self, *artifacts, cwd=None):
        return subprocess.run(
            ["node", str(self.root / "ops/write-release-receipt.mjs"), *artifacts],
            cwd=cwd or self.root, env=self.environment, text=True,
            capture_output=True, timeout=10,
        )

    def run_preparation(self):
        # The provider's existing command substitution must still contain ONLY
        # the SHA. Run outside the repository to exercise --repository as well.
        environment = {**self.environment, "FORGE_RELEASE_DIRECTORY": str(self.root)}
        return subprocess.run(
            ["bash", "-c", '''set -euo pipefail
RELEASE_SHA="$(python3 "$FORGE_RELEASE_DIRECTORY/ops/write_build_revision.py" \
  --repository "$FORGE_RELEASE_DIRECTORY" \
  --output "$FORGE_RELEASE_DIRECTORY/.build-revision")"
test "$(cat "$FORGE_RELEASE_DIRECTORY/.build-revision")" = "$RELEASE_SHA"
printf '%s\\n' "$RELEASE_SHA"
'''],
            cwd=self.root.parent, env=environment, text=True,
            capture_output=True, timeout=15,
        )

    def test_entrypoint_preserves_forge_sha_capture_and_logs_receipt(self):
        # A stale inherited provider SHA must not disagree with .build-revision.
        self.environment["FORGE_DEPLOY_SHA"] = "a" * 40
        self.environment["FORGE_COMMIT_HASH"] = "b" * 40
        self.environment["GIT_COMMIT"] = "c" * 40
        result = self.run_preparation()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, self.revision + "\n")
        self.assertIn(f"release receipt {self.revision[:12]} 0 artifact(s)", result.stderr)
        receipt = self.receipt()
        self.assertEqual(receipt["schemaVersion"], 1)
        self.assertEqual(receipt["revision"], self.revision)
        self.assertEqual(receipt["artifacts"], {})
        self.assertRegex(receipt["node"], r"^v\d+\.\d+\.\d+$")
        self.assertIsNotNone(datetime.fromisoformat(receipt["builtAt"]).tzinfo)
        self.assertEqual(self.git("check-ignore", ".release-receipt.json").strip(),
                         ".release-receipt.json")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_receipt_failure_stops_release_preparation_without_a_success_sha(self):
        binaries = self.root / "bin"
        binaries.mkdir()
        node = binaries / "node"
        node.write_text("#!/bin/sh\nexit 23\n")
        node.chmod(0o755)
        self.environment["PATH"] = str(binaries) + os.pathsep + self.environment["PATH"]
        result = self.run_preparation()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("release_receipt_write_failed", result.stderr)
        self.assertFalse((self.root / ".release-receipt.json").exists())

    def test_root_anchored_artifacts_include_hashes_missing_files_and_directories(self):
        nested = self.root / "dist/nested"
        nested.mkdir(parents=True)
        content = b"deterministic fixture\n"
        (self.root / "dist/example.mjs").write_bytes(content)
        result = self.run_writer("dist/example.mjs", "dist/missing.mjs", "dist/nested", cwd=nested)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((nested / ".release-receipt.json").exists())
        self.assertEqual(self.receipt()["artifacts"], {
            "dist/example.mjs": {"present": True, "sha256": hashlib.sha256(content).hexdigest(),
                                 "bytes": len(content)},
            "dist/missing.mjs": {"present": False, "reason": "missing"},
            "dist/nested": {"present": False, "reason": "not-a-file"},
        })

    def test_multiple_builds_merge_artifacts_for_the_same_revision(self):
        for name in ("a.mjs", "b.mjs"):
            (self.root / name).write_text(name)
            result = self.run_writer(name)
            self.assertEqual(result.returncode, 0, result.stderr)
        # The revision-only Danube hook must also retain an earlier build's entry.
        result = self.run_preparation()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(self.receipt()["artifacts"]), {"a.mjs", "b.mjs"})

    def test_new_revision_does_not_inherit_old_artifacts(self):
        result = self.run_writer("old.mjs")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.git("-c", "user.name=Receipt Test", "-c", "user.email=receipt@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", "next")
        result = self.run_preparation()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(self.receipt()["revision"], self.revision)
        self.assertEqual(self.receipt()["revision"], self.git("rev-parse", "HEAD").strip())
        self.assertEqual(self.receipt()["artifacts"], {})

    def test_no_repository_or_no_commit_refuses_a_receipt(self):
        with tempfile.TemporaryDirectory(prefix="danube-no-revision-") as directory:
            outside = Path(directory)
            for initialized in (False, True):
                with self.subTest(initialized=initialized):
                    if initialized:
                        subprocess.run(["git", "init", "-q", str(outside)], check=True,
                                       capture_output=True, timeout=10)
                    result = self.run_writer(cwd=outside)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((outside / ".release-receipt.json").exists())

    def test_corrupt_receipt_is_replaced(self):
        (self.root / ".release-receipt.json").write_text("{corrupt")
        result = self.run_preparation()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.receipt()["revision"], self.revision)
        self.assertEqual(self.receipt()["artifacts"], {})
