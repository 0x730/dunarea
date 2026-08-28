import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from ops import backup
from ops import offsite_backup
from ops import write_build_revision


class SqliteBackupContractTests(unittest.TestCase):
    def _database(self, path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "CREATE TABLE cache (key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO cache(key, payload) VALUES (?, ?)", ("hist:initial", "{}")
        )
        connection.commit()
        return connection

    def test_online_copy_includes_committed_wal_rows_and_is_single_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "cache.db"
            destination = root / "backups"
            connection = self._database(source)
            try:
                connection.execute(
                    "INSERT INTO cache(key, payload) VALUES (?, ?)",
                    ("inhga_day:2026-08-27", '{"present":true}'),
                )
                connection.commit()
                self.assertTrue(backup.backup(str(source), str(destination), 14))
            finally:
                connection.close()

            target = destination / f"cache-{datetime.now().date().isoformat()}.db"
            copied = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
            try:
                keys = {row[0] for row in copied.execute("SELECT key FROM cache")}
                journal = copied.execute("PRAGMA journal_mode").fetchone()[0]
            finally:
                copied.close()
            self.assertIn("inhga_day:2026-08-27", keys)
            self.assertEqual(journal, "delete")
            self.assertFalse(Path(str(target) + "-wal").exists())
            self.assertFalse(Path(str(target) + "-shm").exists())

    def test_failed_source_schema_leaves_no_partial_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "cache.db"
            destination = root / "backups"
            sqlite3.connect(source).close()
            destination.mkdir()
            stale = destination / f"cache-{datetime.now().date().isoformat()}.db.partial"
            stale.write_bytes(b"stale")

            self.assertFalse(backup.backup(str(source), str(destination), 14))
            self.assertFalse(stale.exists())
            self.assertFalse(Path(str(stale) + "-wal").exists())
            self.assertFalse(Path(str(stale) + "-shm").exists())


class OffsiteBackupContractTests(unittest.TestCase):
    def _config(self, root: Path, *, alerts: bool = False):
        key = root / "encryption.key"
        key.write_text("danube-distinct-test-key-0123456789abcdef\n", encoding="ascii")
        os.chmod(key, 0o600)
        values = {
            "DANUBE_BACKUP_S3_ENDPOINT": "https://fra1.digitaloceanspaces.com",
            "DANUBE_BACKUP_S3_REGION": "fra1",
            "DANUBE_BACKUP_S3_BUCKET": "fleet-backup-test",
            "DANUBE_BACKUP_S3_ACCESS_KEY_ID": "test-access",
            "DANUBE_BACKUP_S3_SECRET_ACCESS_KEY": "test-secret",
            "DANUBE_BACKUP_ENCRYPTION_KEY_FILE": str(key),
        }
        if alerts:
            values.update(
                {
                    "DANUBE_BACKUP_TEM_SECRET_KEY": "scw-secret",
                    "DANUBE_BACKUP_TEM_PROJECT_ID": "project-id",
                    "DANUBE_BACKUP_TEM_REGION": "fr-par",
                    "DANUBE_BACKUP_ALERT_FROM": "Danube <noreply@dunarea.info>",
                    "DANUBE_BACKUP_ALERT_REPLY_TO": "daniel@0x730.com",
                    "DANUBE_BACKUP_ALERT_TO": "daniel@0x730.com",
                }
            )
        config_file = root / "offsite.env"
        config_file.write_text(
            "\n".join(f"{name}={value}" for name, value in values.items()) + "\n",
            encoding="utf-8",
        )
        os.chmod(config_file, 0o600)
        return offsite_backup.load_config(config_file), config_file

    def test_configuration_requires_owner_only_files_and_complete_alert_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, config_file = self._config(root)
            self.assertEqual(config.bucket, "fleet-backup-test")
            self.assertIsNone(config.alert)
            os.chmod(config_file, 0o644)
            with self.assertRaisesRegex(
                offsite_backup.BackupError, "backup_config_insecure_or_missing"
            ):
                offsite_backup.load_config(config_file)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _config, config_file = self._config(root)
            with config_file.open("a", encoding="utf-8") as handle:
                handle.write("DANUBE_BACKUP_TEM_REGION=fr-par\n")
            with self.assertRaisesRegex(
                offsite_backup.BackupError, "backup_alert_configuration_partial"
            ):
                offsite_backup.load_config(config_file)

    def test_object_parser_and_retention_are_danube_prefix_bounded(self):
        now = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
        newest = offsite_backup.parse_object_key(
            "database/danube/2026/08/danube-20260827T120000Z.sqlite3.enc"
        )
        old = offsite_backup.parse_object_key(
            "database/danube/2026/07/danube-20260701T120000Z.sqlite3.enc"
        )
        foreign = offsite_backup.parse_object_key(
            "database/undigo/2026/07/danube-20260701T120000Z.sqlite3.enc"
        )
        malformed = offsite_backup.parse_object_key(
            "database/danube/2025/07/danube-20260701T120000Z.sqlite3.enc"
        )
        future = offsite_backup.parse_object_key(
            "database/danube/2027/07/danube-20270701T120000Z.sqlite3.enc"
        )
        self.assertIsNotNone(newest)
        self.assertIsNotNone(old)
        self.assertIsNone(foreign)
        self.assertIsNone(malformed)
        deletions = offsite_backup.retention_candidates(
            [newest, old, future], now, keep_days=30  # type: ignore[list-item]
        )
        self.assertEqual([item.key for item in deletions], [old.key])

    @unittest.skipUnless(shutil.which("openssl"), "openssl absent")
    def test_authenticated_encryption_round_trip_and_tamper_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, _ = self._config(root)
            source = root / "source.db"
            source.write_bytes(os.urandom(128 * 1024))
            staging = root / "staging"
            context = "database/danube/2026/08/danube-20260827T120000Z.sqlite3.enc"
            with offsite_backup.staging_workspace(staging) as workspace:
                encrypted = workspace / "backup.enc"
                restored = workspace / "restored.db"
                offsite_backup.encrypt_file(source, encrypted, config, workspace, context)
                offsite_backup.decrypt_file(
                    encrypted, restored, config, workspace, context
                )
                self.assertEqual(restored.read_bytes(), source.read_bytes())

                tampered = workspace / "tampered.enc"
                data = bytearray(encrypted.read_bytes())
                data[len(data) // 2] ^= 1
                tampered.write_bytes(data)
                with self.assertRaisesRegex(
                    offsite_backup.BackupError, "backup_decryption_failed"
                ):
                    offsite_backup.decrypt_file(
                        tampered,
                        workspace / "tampered.db",
                        config,
                        workspace,
                        context,
                    )

    def test_staging_is_removed_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            workspace_path = None
            with self.assertRaisesRegex(RuntimeError, "forced"):
                with offsite_backup.staging_workspace(staging) as workspace:
                    workspace_path = workspace
                    (workspace / "secret.db").write_bytes(b"sensitive")
                    raise RuntimeError("forced")
            self.assertIsNotNone(workspace_path)
            self.assertFalse(workspace_path.exists())
            self.assertEqual(list(staging.glob(".danube-offsite-*")), [])

    def test_backup_failure_never_constructs_a_spaces_client(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _config, config_file = self._config(root)
            args = mock.Mock(
                config=str(config_file),
                source=str(root / "missing.db"),
                dest=str(root / "backups"),
                keep_days=14,
                offsite_keep_days=30,
                staging=str(root / "staging"),
                status_file=str(root / "status.json"),
            )
            with mock.patch.object(
                offsite_backup.sqlite_backup, "backup", return_value=False
            ), mock.patch.object(offsite_backup, "SpacesClient") as spaces:
                with self.assertRaisesRegex(
                    offsite_backup.BackupError, "backup_sqlite_copy_failed"
                ):
                    offsite_backup.perform_backup(args)
            spaces.assert_not_called()
            self.assertFalse((root / "staging").exists())

    def test_delete_refuses_every_foreign_or_malformed_key_before_curl(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, _ = self._config(root)
            workspace = root / "workspace"
            workspace.mkdir()
            client = offsite_backup.SpacesClient(config, workspace)
            with mock.patch.object(client, "_request") as request:
                with self.assertRaisesRegex(
                    offsite_backup.BackupError, "backup_retention_scope_refused"
                ):
                    client.delete("database/undigo/object.enc")
            request.assert_not_called()


class BuildRevisionContractTests(unittest.TestCase):
    def test_generated_revision_is_the_checkout_head(self):
        root = Path(__file__).resolve().parents[1]
        expected = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory, ".build-revision")
            actual = write_build_revision.write_revision(root, output)
            self.assertEqual(actual, expected)
            self.assertEqual(output.read_text(encoding="ascii").strip(), expected)


class OperationsDocumentationContractTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]

    def test_release_workflow_is_push_then_explicit_forge_api_deploy(self):
        deploy = self.root.joinpath("DEPLOY.md").read_text(encoding="utf-8")
        readme = self.root.joinpath("README.md").read_text(encoding="utf-8")
        changelog = self.root.joinpath("CHANGELOG.md").read_text(encoding="utf-8")
        normalized = " ".join(deploy.split())

        self.assertIn("Quick Deploy este `false`", deploy)
        self.assertIn("Push-ul singur nu pornește producția", deploy)
        self.assertIn(
            "POST /orgs/{organization}/servers/{server}/sites/{site}/deployments",
            normalized,
        )
        self.assertIn("Push-ul singur nu pornește producția", readme)
        self.assertIn("invocarea explicită a deploy-ului prin Forge API", changelog)

    def test_recovery_state_records_first_runs_and_alert_boundary(self):
        deploy = self.root.joinpath("DEPLOY.md").read_text(encoding="utf-8")
        ops_readme = self.root.joinpath("ops/README.md").read_text(encoding="utf-8")
        normalized_deploy = " ".join(deploy.split())
        normalized_ops = " ".join(ops_readme.split())

        for token in (
            "2117004",
            "2117005",
            "963 rânduri",
            "647 permanente",
            "2026-08-28T03:15:01Z",
            "2026-08-28T08:17:01Z",
            "2026-08-28T09:38:20Z",
            "nu există probă de primire în inbox",
        ):
            self.assertIn(token, normalized_deploy)
        self.assertIn("2117004", ops_readme)
        self.assertIn("2117005", ops_readme)
        self.assertIn("ops/backup.py", ops_readme)
        self.assertNotIn(
            "15 3 * * * cd /home/dunarea/dunarea.info/current && "
            "/usr/bin/python3 ops/backup.py",
            normalized_ops,
        )

    def test_public_discovery_contract_is_documented(self):
        deploy = self.root.joinpath("DEPLOY.md").read_text(encoding="utf-8")
        readme = self.root.joinpath("README.md").read_text(encoding="utf-8")

        for document in (deploy, readme):
            self.assertIn("https://dunarea.info/", document)
            self.assertIn("/sitemap.xml", document)


class VerifyDeployShellContractTests(unittest.TestCase):
    def test_one_unpredictable_workspace_is_trapped_and_syntax_is_valid(self):
        script = Path(__file__).resolve().parents[1] / "ops/verify_deploy.sh"
        text = script.read_text(encoding="utf-8")

        self.assertEqual(text.count("mktemp -d"), 1)
        self.assertIn("/tmp/danube-verify.XXXXXX", text)
        self.assertIn("trap cleanup_workspace EXIT", text)
        self.assertIn("trap 'exit 130' HUP INT TERM", text)
        self.assertIn('rm -rf -- "$VERIFY_WORKSPACE"', text)
        self.assertIn('--status-file "$OFFSITE_CHECK_STATUS_FILE"', text)
        self.assertNotIn('--status-file "$OFFSITE_STATUS"', text)
        self.assertNotIn("$$", text)
        self.assertNotIn("/tmp/_health", text)
        self.assertNotIn("/tmp/_offsite-monitor", text)
        self.assertNotIn("/tmp/_security-", text)
        subprocess.run(["bash", "-n", str(script)], check=True)


if __name__ == "__main__":
    unittest.main()
