import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from ops import backup
from ops import offsite_backup
from ops import prune_releases
from ops import runtime_hygiene
from ops import source_freshness
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
                    "DANUBE_BACKUP_CLOUDFLARE_ACCOUNT_ID": "a" * 32,
                    "DANUBE_BACKUP_CLOUDFLARE_API_TOKEN": "cf-email-token",
                    "DANUBE_BACKUP_ALERT_FROM": "Danube <alerts@0x730.com>",
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

    def test_configuration_requires_owner_only_files_and_complete_cloudflare_group(self):
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
                handle.write(
                    "DANUBE_BACKUP_CLOUDFLARE_ACCOUNT_ID=" + "a" * 32 + "\n"
                )
            with self.assertRaisesRegex(
                offsite_backup.BackupError, "backup_alert_configuration_partial"
            ):
                offsite_backup.load_config(config_file)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _config, config_file = self._config(root)
            with config_file.open("a", encoding="utf-8") as handle:
                handle.write("DANUBE_BACKUP_TEM_REGION=fr-par\n")
            with self.assertRaisesRegex(
                offsite_backup.BackupError, "backup_alert_configuration_legacy"
            ):
                offsite_backup.load_config(config_file)

    def test_cloudflare_alert_requires_accepted_recipient_and_keeps_token_in_header(self):
        with tempfile.TemporaryDirectory() as directory:
            config, _ = self._config(Path(directory), alerts=True)
            self.assertIsNotNone(config.alert)
            response = mock.MagicMock()
            response.status = 200
            response.read.return_value = json.dumps(
                {
                    "success": True,
                    "errors": [],
                    "result": {
                        "delivered": [],
                        "queued": ["daniel@0x730.com"],
                        "permanent_bounces": [],
                    },
                }
            ).encode("utf-8")
            response.__enter__.return_value = response

            with mock.patch.object(
                offsite_backup.urllib.request, "urlopen", return_value=response
            ) as urlopen:
                offsite_backup.deliver_alert(config.alert, "stale", age_hours=31)

            request = urlopen.call_args.args[0]
            self.assertEqual(
                request.full_url,
                "https://api.cloudflare.com/client/v4/accounts/"
                + "a" * 32
                + "/email/sending/send",
            )
            self.assertEqual(
                request.get_header("Authorization"), "Bearer cf-email-token"
            )
            payload = json.loads(request.data.decode("utf-8"))
            self.assertEqual(
                payload["from"],
                {"name": "Danube", "address": "alerts@0x730.com"},
            )
            self.assertEqual(payload["to"], "daniel@0x730.com")
            self.assertEqual(
                payload["reply_to"], {"address": "daniel@0x730.com"}
            )
            self.assertIn("Danube encrypted SQLite backup monitor", payload["text"])
            self.assertIn("Encrypted backup needs attention", payload["html"])
            self.assertIn(">STALE<", payload["html"])
            self.assertNotIn("<img", payload["html"])
            self.assertNotIn("https://", payload["html"])
            self.assertNotIn("cf-email-token", request.data.decode("utf-8"))

    def test_cloudflare_alert_html_is_self_contained_and_escapes_object_name(self):
        message = offsite_backup._alert_message(
            "fresh",
            object_name="database/danube/<latest&ready>.sqlite3.enc",
            age_hours=2,
            test=True,
        )

        self.assertEqual(message["subject"], "[Danube] SQLite backup test: fresh")
        self.assertIn("operator-requested delivery test", message["text"])
        self.assertIn("DELIVERY TEST", message["html"])
        self.assertIn("Encrypted backup is fresh", message["html"])
        self.assertIn(">FRESH<", message["html"])
        self.assertIn(
            "database/danube/&lt;latest&amp;ready&gt;.sqlite3.enc",
            message["html"],
        )
        self.assertNotIn("<latest&ready>", message["html"])
        self.assertNotIn("src=", message["html"])
        self.assertLess(len(message["html"].encode("utf-8")), 20_000)

    def test_cloudflare_alert_html_distinguishes_missing_and_failure_states(self):
        missing = offsite_backup._alert_message(
            "missing", object_name=None, age_hours=None, test=False
        )
        failed = offsite_backup._alert_message(
            "provider-check-failed", object_name=None, age_hours=None, test=False
        )

        self.assertIn("No valid encrypted backup was found", missing["html"])
        self.assertIn(">MISSING<", missing["html"])
        self.assertIn("The recovery check did not complete", failed["html"])
        self.assertIn(">PROVIDER CHECK FAILED<", failed["html"])
        for message in (missing, failed):
            self.assertIn("Treat this as an application recovery incident", message["text"])
            self.assertIn("Treat this as a recovery incident", message["html"])

    def test_cloudflare_alert_rejects_bounce_or_unconfirmed_recipient(self):
        with tempfile.TemporaryDirectory() as directory:
            config, _ = self._config(Path(directory), alerts=True)
            self.assertIsNotNone(config.alert)
            for result in (
                {
                    "delivered": [],
                    "queued": [],
                    "permanent_bounces": ["daniel@0x730.com"],
                },
                {"delivered": [], "queued": [], "permanent_bounces": []},
            ):
                with self.subTest(result=result):
                    response = mock.MagicMock()
                    response.status = 200
                    response.read.return_value = json.dumps(
                        {"success": True, "errors": [], "result": result}
                    ).encode("utf-8")
                    response.__enter__.return_value = response
                    with mock.patch.object(
                        offsite_backup.urllib.request,
                        "urlopen",
                        return_value=response,
                    ), self.assertRaisesRegex(
                        offsite_backup.BackupError,
                        "backup_alert_delivery_failed",
                    ):
                        offsite_backup.deliver_alert(config.alert, "stale")

    def test_main_does_not_duplicate_an_alert_accepted_before_monitor_failure(self):
        accepted_failure = offsite_backup.BackupError(
            "backup_stale", alert_accepted=True
        )
        with mock.patch.object(
            offsite_backup, "perform_monitor", side_effect=accepted_failure
        ), mock.patch.object(
            offsite_backup, "deliver_alert"
        ) as deliver, mock.patch.object(offsite_backup, "_write_status"):
            result = offsite_backup.main(["monitor", "--alert"])
        self.assertEqual(result, 1)
        deliver.assert_not_called()

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


class ReleasePruneContractTests(unittest.TestCase):
    def _layout(self, root: Path):
        releases = root / "releases"
        releases.mkdir(parents=True)
        paths = []
        for index, name in enumerate(("100", "200", "300"), start=1):
            release = releases / name
            release.mkdir()
            release.joinpath("payload.bin").write_bytes(bytes([index]) * index)
            os.utime(release, ns=(index, index))
            paths.append(release)
        current = root / "current"
        current.symlink_to(paths[2], target_is_directory=True)
        return releases, current, paths

    def test_plan_keeps_active_and_newest_rollback_then_apply_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory) / "site"
            releases, current, paths = self._layout(site)
            allowed = {releases: current}

            plan = prune_releases.plan_releases(
                releases, current_link=current, allowed_roots=allowed
            )
            self.assertEqual([path.name for path in plan.keep], ["300", "200"])
            self.assertEqual([path.name for path in plan.remove], ["100"])
            self.assertTrue(paths[0].exists())

            reclaimed = prune_releases.apply_plan(plan)
            self.assertGreater(reclaimed, 0)
            self.assertFalse(paths[0].exists())
            self.assertEqual(
                sorted(path.name for path in releases.iterdir()), ["200", "300"]
            )
            self.assertEqual(current.resolve(), paths[2])

    def test_apply_refuses_when_the_active_release_changed_after_dry_run(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory) / "site"
            releases, current, paths = self._layout(site)
            plan = prune_releases.plan_releases(
                releases,
                current_link=current,
                allowed_roots={releases: current},
            )
            current.unlink()
            current.symlink_to(paths[0], target_is_directory=True)

            with self.assertRaisesRegex(
                prune_releases.PruneError, "release_current_changed"
            ):
                prune_releases.apply_plan(plan)
            self.assertTrue(all(path.exists() for path in paths))

    def test_plan_refuses_unknown_roots_foreign_links_and_non_directory_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory) / "site"
            releases, current, _paths = self._layout(site)
            with self.assertRaisesRegex(
                prune_releases.PruneError, "release_root_refused"
            ):
                prune_releases.plan_releases(
                    releases, current_link=current, allowed_roots={}
                )

            foreign = site / "foreign-current"
            foreign.symlink_to(releases / "300", target_is_directory=True)
            with self.assertRaisesRegex(
                prune_releases.PruneError, "release_current_link_refused"
            ):
                prune_releases.plan_releases(
                    releases,
                    current_link=foreign,
                    allowed_roots={releases: current},
                )

            releases.joinpath("unexpected.txt").write_text("stop", encoding="ascii")
            with self.assertRaisesRegex(
                prune_releases.PruneError, "release_entry_invalid"
            ):
                prune_releases.plan_releases(
                    releases, current_link=current, allowed_roots={releases: current}
                )


class RuntimeHygieneContractTests(unittest.TestCase):
    def test_thresholds_realert_escalation_and_recovery_hysteresis(self):
        now = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
        healthy = runtime_hygiene.Observation(34, 13, 179 * 1024 * 1024)
        warning = runtime_hygiene.Observation(80, 13, 179 * 1024 * 1024)
        critical = runtime_hygiene.Observation(90, 13, 179 * 1024 * 1024)

        self.assertEqual(
            runtime_hygiene.decide_alert(healthy, {}, now).state, "healthy"
        )
        first = runtime_hygiene.decide_alert(warning, {}, now)
        self.assertEqual((first.state, first.send), ("warning", "incident"))

        active = {
            "active": True,
            "severity": "warning",
            "lastAlertAt": "2026-09-01T10:00:00Z",
        }
        muted = runtime_hygiene.decide_alert(warning, active, now + timedelta(hours=1))
        self.assertEqual((muted.state, muted.send), ("warning_muted", None))
        realert = runtime_hygiene.decide_alert(
            warning, active, now + timedelta(hours=6)
        )
        self.assertEqual((realert.state, realert.send), ("warning", "incident"))
        escalated = runtime_hygiene.decide_alert(
            critical, active, now + timedelta(hours=1)
        )
        self.assertEqual((escalated.state, escalated.send), ("critical", "incident"))

        recovering = runtime_hygiene.decide_alert(
            runtime_hygiene.Observation(78, 13, 100), active, now
        )
        self.assertEqual((recovering.state, recovering.send), ("recovering", None))
        recovered = runtime_hygiene.decide_alert(
            runtime_hygiene.Observation(74, 13, 100), active, now
        )
        self.assertEqual((recovered.state, recovered.send), ("recovered", "recovery"))

    def test_journal_is_the_byte_count_of_the_journal_directories(self):
        """Ops 0089: `journalctl --disk-usage` rulat ca utilizator neprivilegiat
        numără doar jurnalele pe care le poate deschide; pe hostul comun a
        raportat sănătos un jurnal de 2,1 GiB."""
        self.assertEqual(
            runtime_hygiene.JOURNAL_DIRECTORIES, ("/var/log/journal", "/run/log/journal")
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            persistent = root / "var"
            (persistent / "machine").mkdir(parents=True)
            (persistent / "machine" / "system.journal").write_bytes(os.urandom(65536))
            missing = root / "run"

            size = runtime_hygiene.journal_directory_bytes((str(persistent), str(missing)))
            self.assertGreater(size, 0)

            with self.assertRaises(runtime_hygiene.HygieneError):
                runtime_hygiene.journal_directory_bytes((str(missing), str(root / "none")))

            if os.geteuid() != 0:
                locked = persistent / "locked"
                locked.mkdir()
                (locked / "user.journal").write_bytes(b"x" * 4096)
                os.chmod(locked, 0)
                try:
                    with self.assertRaises(runtime_hygiene.HygieneError):
                        runtime_hygiene.journal_directory_bytes((str(persistent),))
                finally:
                    os.chmod(locked, 0o700)

        with mock.patch.object(
            runtime_hygiene, "journal_directory_bytes", return_value=3 * 1024**3
        ) as measured, mock.patch.object(
            runtime_hygiene.subprocess, "run", side_effect=AssertionError("journalctl")
        ):
            observation = runtime_hygiene.collect_observation()
        measured.assert_called_once_with(runtime_hygiene.JOURNAL_DIRECTORIES)
        self.assertEqual(observation.journal_bytes, 3 * 1024**3)

    def test_du_runs_in_the_c_locale_and_reads_the_total_by_position(self):
        seen = {}

        def fake_run(command, **kwargs):
            seen["env"] = kwargs.get("env") or {}
            return subprocess.CompletedProcess(command, 0, "4096\t/a\n12288\tinsgesamt\n", "")

        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(runtime_hygiene.subprocess, "run", side_effect=fake_run):
            size = runtime_hygiene.journal_directory_bytes((directory,))
        self.assertEqual(size, 12288)
        self.assertEqual(seen["env"].get("LC_ALL"), "C")

    def test_an_unmeasurable_journal_still_evaluates_disk_and_inodes(self):
        """Ops marchează numai jurnalul `unavailable`; un `du` eșuat nu are voie
        să orbească alertele de disk și inode ale hostului comun."""
        with mock.patch.object(
            runtime_hygiene, "journal_directory_bytes",
            side_effect=runtime_hygiene.HygieneError("host_journal_unavailable"),
        ):
            observation = runtime_hygiene.collect_observation()
        self.assertIsNone(observation.journal_bytes)
        self.assertIsInstance(observation.disk_percent, int)

        blind = runtime_hygiene.Observation(91, 13, None)
        decision = runtime_hygiene.decide_alert(
            blind, {}, datetime(2026, 9, 26, 10, 13, tzinfo=timezone.utc))
        self.assertEqual(decision.severity, "critical")
        self.assertIn("disk=91%", decision.reasons)
        self.assertIn("journal=unavailable", decision.reasons)

        quiet = runtime_hygiene.Observation(34, 13, None)
        decision = runtime_hygiene.decide_alert(
            quiet, {"active": True, "severity": "warning"},
            datetime(2026, 9, 26, 10, 13, tzinfo=timezone.utc))
        # Un jurnal nemăsurabil nu închide incidentul: nu poate proba revenirea.
        self.assertNotEqual(decision.send, "recovery")
        self.assertIn("journal=unavailable", decision.reasons)
        message = runtime_hygiene._message(
            quiet, decision, "2026-09-26T10:13:00Z", test=False)
        self.assertIn("Journal: unavailable", message["text"])

    def test_main_records_owner_only_incident_and_one_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "host-status.json"
            base_args = [
                "--config",
                "unused.env",
                "--state-file",
                str(state),
                "--alert",
            ]
            critical = runtime_hygiene.Observation(91, 13, 179 * 1024 * 1024)
            healthy = runtime_hygiene.Observation(34, 13, 179 * 1024 * 1024)

            with mock.patch.object(
                runtime_hygiene, "collect_observation", return_value=critical
            ), mock.patch.object(
                offsite_backup, "load_alert_config", return_value=mock.sentinel.alert
            ), mock.patch.object(offsite_backup, "_send_email") as send:
                self.assertEqual(runtime_hygiene.main(base_args), 1)
                send.assert_called_once()
                self.assertIn("shared host critical", send.call_args.args[1]["subject"])
            recorded = json.loads(state.read_text(encoding="utf-8"))
            self.assertTrue(recorded["active"])
            self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o600)

            with mock.patch.object(
                runtime_hygiene, "collect_observation", return_value=healthy
            ), mock.patch.object(
                offsite_backup, "load_alert_config", return_value=mock.sentinel.alert
            ), mock.patch.object(offsite_backup, "_send_email") as send:
                self.assertEqual(runtime_hygiene.main(base_args), 0)
                send.assert_called_once()
                self.assertIn("shared host recovered", send.call_args.args[1]["subject"])
            recorded = json.loads(state.read_text(encoding="utf-8"))
            self.assertFalse(recorded["active"])


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
            "2026-08-28T20:10:56Z",
            "cf-bounce.0x730.com",
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

    def test_recovery_alert_contract_uses_cloudflare_without_dunarea_sender(self):
        deploy = self.root.joinpath("DEPLOY.md").read_text(encoding="utf-8")
        ops_readme = self.root.joinpath("ops/README.md").read_text(encoding="utf-8")
        example = self.root.joinpath("ops/offsite-backup.env.example").read_text(
            encoding="utf-8"
        )

        for document in (deploy, ops_readme):
            self.assertIn("Cloudflare Email Sending", document)
            self.assertIn("`dunarea.info`", document)
        self.assertIn("DANUBE_BACKUP_CLOUDFLARE_ACCOUNT_ID=", example)
        self.assertIn("DANUBE_BACKUP_CLOUDFLARE_API_TOKEN=", example)
        self.assertIn("alerts@0x730.com", example)
        self.assertIn("template HTML", deploy)
        self.assertIn("fallback plain-text", deploy)
        self.assertIn("fallback plain-text", ops_readme)
        self.assertNotIn("DANUBE_BACKUP_TEM_SECRET_KEY=", example)
        self.assertNotIn("@dunarea.info", example)

    def test_public_discovery_contract_is_documented(self):
        deploy = self.root.joinpath("DEPLOY.md").read_text(encoding="utf-8")
        readme = self.root.joinpath("README.md").read_text(encoding="utf-8")

        for document in (deploy, readme):
            self.assertIn("https://dunarea.info/", document)
            self.assertIn("/sitemap.xml", document)

    def test_cloudflare_free_api_rate_limit_matches_the_shipped_route_model(self):
        policy = self.root.joinpath("ops/cloudflare-edge-policy.md").read_text(
            encoding="utf-8"
        )
        deploy = self.root.joinpath("DEPLOY.md").read_text(encoding="utf-8")
        source = self.root.joinpath("server.py").read_text(encoding="utf-8")
        api_routes = re.findall(r'^    "(/api/[^"]+)":', source, re.MULTILINE)

        self.assertEqual(len(api_routes), 47)
        self.assertIn("do_POST = _method_not_allowed", source)
        self.assertIn("do_DELETE = _method_not_allowed", source)
        self.assertIn('starts_with(http.request.uri.path, "/api/")', policy)
        self.assertIn("60 cereri / 10 secunde / IP", policy)
        self.assertIn("durată blocare | 10 secunde", policy)
        self.assertIn("exact o regulă", policy)
        self.assertNotIn("| `matches`", policy)
        self.assertIn("ar cere Advanced Rate Limiting", policy)
        self.assertIn("DMARC este deja strict `p=reject`", deploy)

    def test_shared_host_hygiene_contract_is_source_owned_and_bounded(self):
        deploy = self.root.joinpath("DEPLOY.md").read_text(encoding="utf-8")
        logrotate = self.root.joinpath(
            "ops/logrotate/0x730-processes"
        ).read_text(encoding="utf-8")

        for token in (
            "/home/dunarea/.forge/*.log",
            "/home/dunarea/dunarea.info/*.log",
            "/home/forge/.pm2/logs/*.log",
            "/home/forge/swing.boostit.dev/logs/*.log",
            "daily",
            "maxsize 20M",
            "rotate 14",
            "maxage 14",
            "compress",
            "delaycompress",
            "copytruncate",
            "su dunarea dunarea",
            "su forge forge",
        ):
            self.assertIn(token, logrotate)
        self.assertEqual(logrotate.count("/home/forge/.pm2/logs/*.log"), 1)
        self.assertEqual(
            logrotate.count("/home/forge/swing.boostit.dev/logs/*.log"), 1
        )
        self.assertEqual(logrotate.count("maxsize 20M"), 2)
        self.assertEqual(logrotate.count("rotate 14"), 2)
        self.assertEqual(logrotate.count("maxage 14"), 2)
        self.assertEqual(logrotate.count("delaycompress"), 2)
        self.assertEqual(logrotate.count("copytruncate"), 2)
        for token in (
            "deployment_retention=1",
            "maximum două",
            "80%",
            "90%",
            "75%",
            "șase ore",
            "runtime_hygiene.py",
            "prune_releases.py",
            "singurul proprietar",
            "pm2-logrotate",
        ):
            self.assertIn(token, deploy)


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


class SourceFreshnessContractTests(unittest.TestCase):
    def _alert_env(self, root: Path, extra: dict[str, str] | None = None) -> Path:
        values = {
            "DANUBE_BACKUP_CLOUDFLARE_ACCOUNT_ID": "a" * 32,
            "DANUBE_BACKUP_CLOUDFLARE_API_TOKEN": "cf-email-token",
            "DANUBE_BACKUP_ALERT_FROM": "Danube <alerts@0x730.com>",
            "DANUBE_BACKUP_ALERT_REPLY_TO": "daniel@0x730.com",
            "DANUBE_BACKUP_ALERT_TO": "daniel@0x730.com",
            **(extra or {}),
        }
        config_file = root / "offsite.env"
        config_file.write_text(
            "\n".join(f"{name}={value}" for name, value in values.items()) + "\n",
            encoding="utf-8",
        )
        os.chmod(config_file, 0o600)
        return config_file

    def test_stale_paths_report_only_true_flags_with_their_json_path(self):
        payload = {
            "stale": False,
            "statii": [{"stale": False}, {"nume": "x", "stale": True}],
            "surse": {"afdj": {"stale": True}, "sen": {"stale": False}},
        }
        self.assertEqual(
            source_freshness._stale_paths(payload),
            ["statii[1]", "surse.afdj"],
        )
        self.assertEqual(source_freshness._stale_paths({"stale": True}), ["."])
        # un "stale" cu valoare ne-booleana nu declanșează și nu e explorat
        self.assertEqual(source_freshness._stale_paths({"stale": {"x": 1}}), [])

    def test_collect_evidence_aggregates_health_stale_and_overview_errors(self):
        responses = {
            "/api/health": {
                "status": "ok",
                "warmup_done": True,
                "anomaly_report_age_s": 13 * 3600,
            },
            "/api/overview": {"errors": {"inhga": "upstream_timeout"}},
            "/api/hydroinfo": {"stale": True, "statii": []},
        }

        def fetch(base, path, timeout):
            return responses.get(path, {"stale": False})

        with mock.patch.object(source_freshness, "_fetch_json", fetch):
            evidence = source_freshness.collect_evidence("http://x", 5, 12)

        self.assertEqual(evidence["state"], "failed")
        self.assertIn(
            "/api/overview: sursa inhga: upstream_timeout", evidence["failures"]
        )
        self.assertIn("/api/hydroinfo: stale", evidence["staleSources"])
        self.assertTrue(
            any("raport anomalii vechi" in item for item in evidence["staleSources"])
        )
        self.assertEqual(evidence["reportAgeSeconds"], 13 * 3600)

        responses["/api/health"]["anomaly_report_age_s"] = 3600
        responses["/api/overview"] = {"errors": {}}
        responses["/api/hydroinfo"] = {"stale": False, "statii": []}
        with mock.patch.object(source_freshness, "_fetch_json", fetch):
            evidence = source_freshness.collect_evidence("http://x", 5, 12)
        self.assertEqual(evidence["state"], "fresh")
        self.assertEqual(evidence["staleSources"], [])
        self.assertEqual(evidence["failures"], [])

    def test_unreachable_endpoint_is_a_categorized_failure_not_a_crash(self):
        def fetch(base, path, timeout):
            if path == "/api/afdj":
                raise urllib.error.HTTPError(base + path, 503, "busy", None, None)
            if path == "/api/sen":
                raise TimeoutError()
            return {
                "status": "ok",
                "warmup_done": True,
                "anomaly_report_age_s": 60,
                "stale": False,
            }

        with mock.patch.object(source_freshness, "_fetch_json", fetch):
            evidence = source_freshness.collect_evidence("http://x", 5, 12)

        self.assertEqual(evidence["state"], "failed")
        self.assertIn("/api/afdj: http_503", evidence["failures"])
        self.assertIn("/api/sen: timeout", evidence["failures"])
        # motivele publice nu transportă detalii upstream sau URL-uri
        for item in evidence["failures"]:
            self.assertNotIn("http://x", item.split(": ", 1)[1])

    def test_alert_html_is_self_contained_and_escapes_sources(self):
        message = source_freshness._freshness_message(
            "stale",
            ["/api/hydroinfo: stale <script>&x", "/api/afdj: http_503"],
            "2026-09-01T09:25:00Z",
            test=True,
        )
        self.assertEqual(message["subject"], "[Danube] data sources test: stale")
        self.assertIn("DELIVERY TEST", message["html"])
        self.assertIn("Some sources have stale or undated observations or fallback snapshots", message["html"])
        self.assertIn(">STALE<", message["html"])
        self.assertIn("stale &lt;script&gt;&amp;x", message["html"])
        self.assertNotIn("<script>", message["html"])
        self.assertNotIn("src=", message["html"])
        self.assertIn("/api/hydroinfo", message["text"])
        self.assertLess(len(message["html"].encode("utf-8")), 20_000)

        incident = source_freshness._freshness_message(
            "failed", [f"sursa-{i}" for i in range(40)], "2026-09-01T09:25:00Z",
            test=False,
        )
        self.assertIn("FRESHNESS INCIDENT", incident["html"])
        self.assertIn("and 20 more", incident["html"])
        self.assertLess(len(incident["html"].encode("utf-8")), 20_000)

    def test_main_alerts_only_on_incident_and_always_on_test_alert(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_file = self._alert_env(root)
            status_file = root / "status.json"
            base_args = [
                "--config", str(config_file),
                "--status-file", str(status_file),
                "--base-url", "http://127.0.0.1:7300",
            ]

            fresh = {
                "state": "fresh", "checkedAt": "2026-09-01T09:25:00Z",
                "baseUrl": "http://127.0.0.1:7300", "endpointsChecked": 15,
                "staleSources": [], "failures": [],
            }
            with mock.patch.object(
                source_freshness, "collect_evidence", return_value=dict(fresh)
            ), mock.patch.object(offsite_backup, "_send_email") as send:
                self.assertEqual(source_freshness.main(base_args + ["--alert"]), 0)
                send.assert_not_called()

            stale = dict(fresh, state="stale",
                         staleSources=["/api/hydroinfo: stale"])
            with mock.patch.object(
                source_freshness, "collect_evidence", return_value=dict(stale)
            ), mock.patch.object(offsite_backup, "_send_email") as send:
                self.assertEqual(source_freshness.main(base_args + ["--alert"]), 1)
                send.assert_called_once()
                _, message = send.call_args[0]
                self.assertIn("/api/hydroinfo: stale", message["text"])

            with mock.patch.object(
                source_freshness, "collect_evidence", return_value=dict(fresh)
            ), mock.patch.object(offsite_backup, "_send_email") as send:
                self.assertEqual(
                    source_freshness.main(base_args + ["--test-alert"]), 0
                )
                send.assert_called_once()

            recorded = json.loads(status_file.read_text(encoding="utf-8"))
            self.assertIn("sourceFreshness", recorded)
            self.assertEqual(recorded["sourceFreshness"]["state"], "fresh")
            self.assertTrue(recorded["sourceFreshness"]["testAlertAccepted"])

    GONYU = "/api/danubehis: .: observation Gönyű: stale (2026-08-13; limit 2 days)"
    INHGA = "/api/inhga: stale"

    def test_alert_decision_mutes_repeats_reminds_weekly_and_recovers_once(self):
        """Din 13.09.2026 același gol Gönyű a produs câte o alertă pe zi; un
        incident nou ar fi sosit sub același subiect zilnic."""
        now = datetime(2026, 9, 26, 9, 25, tzinfo=timezone.utc)
        decide = source_freshness.decide_alert
        key = source_freshness._problem_key
        gonyu, inhga = key(self.GONYU), key(self.INHGA)
        yesterday = "2026-09-25T09:25:00Z"

        first = decide("stale", [self.GONYU], {}, now)
        self.assertEqual(first["send"], "incident")
        self.assertEqual(first["newProblems"], [gonyu])
        self.assertEqual(first["alertedProblems"], [gonyu])
        self.assertNotIn("incidentSince", first)

        memory = {"alertedProblems": [gonyu], "lastAlertAt": yesterday,
                  "incidentSince": "2026-09-13T09:25:28Z"}
        repeat = decide("stale", [self.GONYU], memory, now)
        self.assertIsNone(repeat["send"])
        self.assertEqual(repeat["lastAlertAt"], yesterday)
        self.assertEqual(repeat["incidentSince"], "2026-09-13T09:25:28Z")

        added = decide("failed", [self.GONYU, self.INHGA], memory, now)
        self.assertEqual(added["send"], "incident")
        self.assertEqual(added["newProblems"], [inhga])
        self.assertEqual(added["alertedProblems"], sorted([gonyu, inhga]))
        self.assertEqual(added["incidentSince"], "2026-09-13T09:25:28Z")

        week_old = dict(memory, lastAlertAt="2026-09-19T09:25:00Z")
        self.assertEqual(decide("stale", [self.GONYU], week_old, now)["send"], "reminder")
        six_days = dict(memory, lastAlertAt="2026-09-20T11:00:00Z")
        self.assertIsNone(decide("stale", [self.GONYU], six_days, now)["send"])

        both = {"alertedProblems": sorted([gonyu, inhga]), "lastAlertAt": yesterday}
        shrunk = decide("stale", [self.GONYU], both, now)
        self.assertIsNone(shrunk["send"])
        self.assertEqual(shrunk["alertedProblems"], [gonyu])
        # Reapariția după micșorare este din nou o problemă nouă.
        self.assertEqual(decide("stale", [self.GONYU, self.INHGA], shrunk, now)["send"], "incident")

        recovered = decide("fresh", [], memory, now)
        self.assertEqual(recovered["send"], "recovery")
        self.assertEqual(recovered["alertedProblems"], [])
        self.assertNotIn("incidentSince", recovered)
        self.assertIsNone(decide("fresh", [], recovered, now)["send"])

        # Vârsta raportului crește orar; aceeași condiție nu e o problemă nouă.
        aged = {"alertedProblems": [key(
            "/api/health: raport anomalii vechi de 13h (limita 12h)")], "lastAlertAt": yesterday}
        self.assertIsNone(decide(
            "stale", ["/api/health: raport anomalii vechi de 37h (limita 12h)"], aged, now)["send"])
        # Memorie invalidă sau din viitor: se alertează, nu se tace.
        for broken in ({"alertedProblems": [gonyu], "lastAlertAt": "nope"},
                       {"alertedProblems": [gonyu], "lastAlertAt": "2026-09-27T00:00:00Z"},
                       {"alertedProblems": "x", "lastAlertAt": yesterday}):
            self.assertIsNotNone(decide("stale", [self.GONYU], broken, now)["send"])
        # O dată de început invalidă nu ajunge într-un subiect.
        bad_since = dict(memory, incidentSince="nope")
        self.assertNotIn("incidentSince", decide("stale", [self.GONYU], bad_since, now))

    def test_alert_key_ignores_the_rolling_observation_date(self):
        """O stație mereu cu 3 zile în urmă își schimbă zilnic data; aceeași
        condiție nu trebuie să devină în fiecare zi o problemă „nouă”."""
        now = datetime(2026, 9, 26, 9, 25, tzinfo=timezone.utc)
        day1 = "/api/danubehis: .: observation Vác: stale (2026-09-22; limit 2 days)"
        day2 = "/api/danubehis: .: observation Vác: stale (2026-09-23; limit 2 days)"
        memory = {"alertedProblems": [source_freshness._problem_key(day1)],
                  "lastAlertAt": "2026-09-25T09:25:00Z"}
        self.assertIsNone(source_freshness.decide_alert("stale", [day2], memory, now)["send"])
        # O stare diferită a aceleiași surse rămâne o problemă nouă.
        unknown = "/api/inhga: .: observation inhga: unknown (2026-09-25; limit 1 days; missing debit_bazias_m3s)"
        stale = "/api/inhga: .: observation inhga: stale (2026-09-23; limit 1 days)"
        self.assertNotEqual(source_freshness._problem_key(unknown),
                            source_freshness._problem_key(stale))

    def test_weekly_reminder_tolerates_run_time_jitter(self):
        # Ștampila vine după colectare; o rulare cu câteva secunde mai rapidă
        # peste o săptămână nu trebuie să amâne reamintirea cu încă o zi.
        memory = {"alertedProblems": [source_freshness._problem_key(self.GONYU)],
                  "lastAlertAt": "2026-09-19T09:25:40Z"}
        now = datetime(2026, 9, 26, 9, 25, 10, tzinfo=timezone.utc)
        self.assertEqual(
            source_freshness.decide_alert("stale", [self.GONYU], memory, now)["send"], "reminder")

    def test_recovery_and_reminder_messages_say_what_they_are(self):
        recovery = source_freshness._freshness_message(
            "fresh", [], "2026-09-27T09:25:00Z", test=False, kind="recovery")
        self.assertEqual(recovery["subject"], "[Danube] data sources recovered")
        self.assertIn("RECOVERY", recovery["html"])
        self.assertNotIn("FRESHNESS INCIDENT", recovery["html"])
        self.assertNotIn("Treat non-fresh sources as a data incident", recovery["text"])
        self.assertIn("incident is closed", recovery["text"])

        reminder = source_freshness._freshness_message(
            "stale", [self.GONYU], "2026-09-26T09:25:00Z", test=False,
            kind="reminder", since="2026-09-13T09:25:28Z")
        self.assertEqual(
            reminder["subject"],
            "[Danube] data sources stale, weekly reminder (since 2026-09-13T09:25:28Z)")
        self.assertIn("WEEKLY REMINDER", reminder["html"])
        self.assertIn("2026-09-13T09:25:28Z", reminder["html"])
        self.assertIn("not been fresh since 2026-09-13T09:25:28Z", reminder["text"])
        # Fără dată validă de început, subiectul nu inventează una.
        undated = source_freshness._freshness_message(
            "stale", [self.GONYU], "2026-09-26T09:25:00Z", test=False, kind="reminder")
        self.assertEqual(undated["subject"], "[Danube] data sources stale, weekly reminder")

        added = source_freshness._freshness_message(
            "failed", [self.GONYU, self.INHGA], "2026-09-26T09:25:00Z", test=False,
            new_problems=[self.INHGA])
        self.assertIn(f"- NEW · {self.INHGA}", added["text"])
        self.assertIn(f"- {self.GONYU}", added["text"])
        self.assertIn("FRESHNESS INCIDENT", added["html"])

    def test_a_failed_send_keeps_the_alert_memory_so_recovery_is_not_lost(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_file = self._alert_env(root)
            status_file = root / "status.json"
            args = ["--config", str(config_file), "--status-file", str(status_file),
                    "--base-url", "http://127.0.0.1:7300", "--alert"]
            fresh = {"state": "fresh", "checkedAt": "2026-09-26T09:25:00Z",
                     "baseUrl": "http://127.0.0.1:7300", "endpointsChecked": 16,
                     "staleSources": [], "failures": []}
            stale = dict(fresh, state="stale", staleSources=[self.GONYU])

            def run(evidence, day, fail=False):
                when = datetime(2026, 9, day, 9, 25, tzinfo=timezone.utc)
                effect = offsite_backup.BackupError("backup_alert_failed") if fail else None
                with mock.patch.object(source_freshness, "collect_evidence",
                                       return_value=dict(evidence)), \
                        mock.patch.object(offsite_backup, "_utcnow", return_value=when), \
                        mock.patch.object(offsite_backup, "_send_email",
                                          side_effect=effect) as send:
                    code = source_freshness.main(args)
                recorded = json.loads(status_file.read_text(encoding="utf-8"))
                return code, send, recorded["sourceFreshness"]

            run(stale, 25)
            code, send, section = run(fresh, 26, fail=True)
            self.assertEqual(code, 1)
            self.assertEqual(section["state"], "failed")
            self.assertEqual(section["alertedProblems"],
                             [source_freshness._problem_key(self.GONYU)])
            self.assertEqual(section["incidentSince"], "2026-09-25T09:25:00Z")

            code, send, section = run(fresh, 27)
            self.assertEqual(code, 0)
            send.assert_called_once()
            self.assertEqual(send.call_args.args[1]["subject"], "[Danube] data sources recovered")
            self.assertNotIn("incidentSince", section)

    def test_main_keeps_alert_memory_in_the_status_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_file = self._alert_env(root)
            status_file = root / "status.json"
            args = ["--config", str(config_file), "--status-file", str(status_file),
                    "--base-url", "http://127.0.0.1:7300"]
            fresh = {"state": "fresh", "checkedAt": "2026-09-26T09:25:00Z",
                     "baseUrl": "http://127.0.0.1:7300", "endpointsChecked": 16,
                     "staleSources": [], "failures": []}
            stale = dict(fresh, state="stale", staleSources=[self.GONYU])

            def run(evidence, day, extra=("--alert",)):
                when = datetime(2026, 9, day, 9, 25, tzinfo=timezone.utc)
                with mock.patch.object(source_freshness, "collect_evidence",
                                       return_value=dict(evidence)), \
                        mock.patch.object(offsite_backup, "_utcnow", return_value=when), \
                        mock.patch.object(offsite_backup, "_send_email") as send:
                    code = source_freshness.main(args + list(extra))
                recorded = json.loads(status_file.read_text(encoding="utf-8"))
                return code, send, recorded["sourceFreshness"]

            code, send, section = run(stale, 25)
            self.assertEqual(code, 1)
            send.assert_called_once()
            self.assertIn(self.GONYU, send.call_args.args[1]["text"])
            self.assertEqual(section["alertedProblems"],
                             [source_freshness._problem_key(self.GONYU)])
            self.assertEqual(section["alertDecision"], "incident")
            self.assertEqual(section["incidentSince"], "2026-09-25T09:25:00Z")

            code, send, section = run(stale, 26)
            self.assertEqual(code, 1)  # Forge vede în continuare jobul eșuat
            send.assert_not_called()
            self.assertEqual(section["alertDecision"], "muted")
            self.assertEqual(section["lastAlertAt"], "2026-09-25T09:25:00Z")
            self.assertEqual(section["staleSources"], [self.GONYU])

            # O rulare manuală fără --alert nu atinge memoria alertelor.
            code, send, section = run(fresh, 26, extra=())
            send.assert_not_called()
            self.assertEqual(section["alertedProblems"],
                             [source_freshness._problem_key(self.GONYU)])

            code, send, section = run(fresh, 27)
            self.assertEqual(code, 0)
            send.assert_called_once()
            self.assertEqual(send.call_args.args[1]["subject"], "[Danube] data sources recovered")
            self.assertEqual(section["alertDecision"], "recovery")

            code, send, section = run(fresh, 28)
            send.assert_not_called()

    def test_alert_config_reuses_backup_env_without_requiring_s3_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_file = self._alert_env(root)
            alert = offsite_backup.load_alert_config(config_file)
            self.assertEqual(alert.recipient, "daniel@0x730.com")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_file = self._alert_env(root)
            text = config_file.read_text(encoding="utf-8")
            config_file.write_text(
                text.replace("DANUBE_BACKUP_ALERT_TO=daniel@0x730.com\n", ""),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                offsite_backup.BackupError, "backup_alert_configuration_missing"
            ):
                offsite_backup.load_alert_config(config_file)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_file = self._alert_env(
                root, extra={"DANUBE_BACKUP_TEM_REGION": "fr-par"}
            )
            with self.assertRaisesRegex(
                offsite_backup.BackupError, "backup_alert_configuration_legacy"
            ):
                offsite_backup.load_alert_config(config_file)

    def test_base_url_rejects_credentials_paths_and_other_schemes(self):
        good = source_freshness._validated_base_url("http://127.0.0.1:7300")
        self.assertEqual(good, "http://127.0.0.1:7300")
        for url in (
            "ftp://127.0.0.1", "http://user:pw@127.0.0.1:7300",
            "http://127.0.0.1:7300/api", "http://127.0.0.1:7300/?x=1",
        ):
            with self.assertRaises(source_freshness.FreshnessError):
                source_freshness._validated_base_url(url)


if __name__ == "__main__":
    unittest.main()
