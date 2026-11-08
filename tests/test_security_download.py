"""Download permissions stay private even if remote metadata is applied.

The fake deliberately applies mode 0644 despite RCLONE_METADATA=false to model
per-remote global overrides and verify the independent local publication check.
It rejects --metadata flags to model older supported rclone versions. No real
rclone, cloud remote, or database is used.
"""

import os
from pathlib import Path
import shutil
import unittest

from support import SCRIPT, LauncherCase, InstallerCase


class SecureDownloadTests(LauncherCase):
    def setUp(self):
        super().setUp()
        self.identity = Path(str(self.state) + ".id")
        self.env["RCLONE_METADATA"] = "true"
        self.env["TEST_APPLY_DOWNLOAD_METADATA"] = "1"
        (self.binaries / "chmod").symlink_to(self.mock)

    def assert_no_staging(self):
        self.assertFalse(list(self.local.parent.glob(".keesyncxc-download.*")))

    def assert_download_private(self, operation):
        transfer = self.remote_operations(operation)
        self.assertEqual(len(transfer), 1)
        self.assertFalse(any(argument == "--metadata" or argument.startswith("--metadata=")
                             for argument in transfer[0]))
        for event in self.events("rclone"):
            expected = "false" if event["arguments"][0] in ("copyto", "backend") else "true"
            self.assertEqual(event["metadata_environment"], expected)
        observed = self.events("download-security")
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]["parent_mode"], 0o700)
        self.assertEqual(observed[0]["parent_owner"], self.local.stat().st_uid)
        self.assertEqual(observed[0]["metadata_environment"], "false")
        destination = Path(observed[0]["destination"])
        self.assertEqual(destination.name, "database.kdbx")
        self.assertEqual(destination.parent.parent, self.local.parent)
        self.assertEqual(self.local.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.state.read_text().strip(), self.digest(self.local.read_text()))
        self.assert_no_staging()

    def test_metadata_cannot_make_downloaded_database_readable_by_other_users(self):
        self.seed(cloud="remote replacement")
        self.assert_success(self.run_launcher())
        self.assertEqual(self.local.read_text(), "remote replacement")
        self.assert_download_private("copyto")

    def test_first_download_uses_private_staging_and_final_mode(self):
        self.cloud.write_text("first remote database")
        self.assert_success(self.run_launcher())
        self.assertEqual(self.local.read_text(), "first remote database")
        self.assert_download_private("copyto")

    def test_pinned_duplicate_download_has_the_same_permission_protection(self):
        self.seed(cloud="pinned remote replacement")
        pinned = self.pin()
        other = self.duplicate("unrelated database")
        self.assert_success(self.run_launcher())
        self.assertEqual(self.local.read_text(), "pinned remote replacement")
        self.assertEqual(other.read_text(), "unrelated database")
        self.assertEqual(self.remote_operations("backend")[0][3], pinned)
        self.assert_download_private("backend")

    def test_failed_permission_check_preserves_working_database_and_baseline(self):
        for fault in ("download_chmod", "download_chmod_ignored", "stage_chmod"):
            with self.subTest(fault=fault):
                self.seed(cloud="remote replacement")
                baseline = self.state.read_text()
                result = self.run_launcher(fault)
                self.assert_blocked(result)
                self.assertEqual(self.local.read_text(), "original")
                self.assertEqual(self.state.read_text(), baseline)
                self.assert_no_staging()

    def test_failed_transfer_and_verification_remove_private_staging(self):
        for fault in ("download", "partial_download", "corrupt_download", "download_hash",
                      "local_backup", "replace_local"):
            with self.subTest(fault=fault):
                self.seed(cloud="remote replacement")
                baseline = self.state.read_text()
                self.assert_blocked(self.run_launcher(fault))
                self.assertEqual(self.local.read_text(), "original")
                self.assertEqual(self.state.read_text(), baseline)
                self.assert_no_staging()

    def test_pinned_duplicate_failed_permission_check_preserves_both_copies(self):
        self.seed(cloud="pinned remote replacement")
        self.pin()
        other = self.duplicate("unrelated database")
        baseline = self.state.read_text()
        self.assert_blocked(self.run_launcher("download_chmod"))
        self.assertEqual(self.local.read_text(), "original")
        self.assertEqual(other.read_text(), "unrelated database")
        self.assertEqual(self.state.read_text(), baseline)
        self.assert_no_staging()

    def test_state_write_failure_leaves_secured_database_and_no_staging(self):
        self.seed(cloud="remote replacement")
        baseline = self.state.read_text()
        self.assert_blocked(self.run_launcher("replace_state"))
        self.assertEqual(self.local.read_text(), "remote replacement")
        self.assertEqual(self.local.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.state.read_text(), baseline)
        self.assert_no_staging()


class DownloadStageCleanupTests(InstallerCase):
    def prepare_cleanup(self):
        self.prepare_installation()
        self.local.parent.mkdir(parents=True)
        self.local.write_text("working database")

    def stage(self, suffix, payload=True):
        stage = self.local.parent / (".keesyncxc-download." + suffix)
        stage.mkdir(mode=0o700)
        if payload:
            (stage / "database.kdbx").write_text("abandoned download")
        return stage

    def test_full_cleanup_removes_confirmed_download_stages_and_legacy_temps(self):
        self.prepare_cleanup()
        stage = self.stage("Ab12Cd34")
        payload = stage / "database.kdbx"
        empty = self.stage("Empty001", payload=False)
        legacy = self.local.parent / ".keesyncxc-download.Legacy01"
        legacy.write_text("legacy temporary database")
        result = self.run_script("--clear-local", answers="y\n")
        self.assert_success(result)
        for path in (stage, empty, legacy):
            self.assertFalse(path.exists(), str(path))
        self.assertIn(str(payload), result.stdout)
        self.assertIn(str(legacy), result.stdout)
        self.assertTrue(self.installed.is_file())
        self.assert_no_external_actions()

    def test_full_cleanup_cancellation_keeps_download_stages_and_legacy_temps(self):
        self.prepare_cleanup()
        stage = self.stage("Ab12Cd34")
        empty = self.stage("Empty001", payload=False)
        legacy = self.local.parent / ".keesyncxc-download.Legacy01"
        legacy.write_text("legacy temporary database")
        result = self.run_script("--clear-local", answers="\n")
        self.assert_failure(result)
        self.assertEqual((stage / "database.kdbx").read_text(), "abandoned download")
        self.assertTrue(empty.is_dir())
        self.assertEqual(legacy.read_text(), "legacy temporary database")
        self.assertEqual(self.local.read_text(), "working database")
        self.assertTrue(self.config.exists())
        self.assert_no_external_actions()

    def test_full_cleanup_keeps_entire_stages_containing_unknown_entries_or_links(self):
        self.prepare_cleanup()
        unknown = self.stage("Unknown1")
        (unknown / ".unexpected").write_text("unknown hidden file")
        nested = self.stage("Nested01")
        (nested / "unknown-directory").mkdir()
        unrecognized = self.stage("WrongLength")
        outside = self.root / "outside-database.kdbx"
        outside.write_text("outside database")
        symlink_payload = self.stage("LinkPay1", payload=False)
        (symlink_payload / "database.kdbx").symlink_to(outside)
        hardlink_payload = self.stage("Hardlnk1", payload=False)
        os.link(outside, hardlink_payload / "database.kdbx")
        outside_dir = self.root / "outside-stage"
        outside_dir.mkdir()
        (outside_dir / "database.kdbx").write_text("outside stage database")
        symlink_dir = self.local.parent / ".keesyncxc-download.LinkDir1"
        symlink_dir.symlink_to(outside_dir, target_is_directory=True)
        result = self.run_script("--clear-local", answers="y\n")
        self.assert_success(result)
        for stage in (unknown, nested, unrecognized, symlink_payload, hardlink_payload):
            self.assertTrue(stage.is_dir(), str(stage))
            self.assertTrue((stage / "database.kdbx").exists())
            self.assertNotIn(str(stage / "database.kdbx"), result.stdout)
        self.assertEqual((unknown / ".unexpected").read_text(), "unknown hidden file")
        self.assertTrue((nested / "unknown-directory").is_dir())
        self.assertTrue(symlink_dir.is_symlink())
        self.assertTrue((symlink_payload / "database.kdbx").is_symlink())
        self.assertEqual(outside.read_text(), "outside database")
        self.assertEqual((outside_dir / "database.kdbx").read_text(), "outside stage database")
        self.assert_no_external_actions()

    def test_full_cleanup_keeps_executing_source_inside_download_stage(self):
        self.prepare_cleanup()
        stage = self.stage("Source01", payload=False)
        source = stage / "database.kdbx"
        shutil.copyfile(SCRIPT, source)
        before = source.read_bytes()
        result = self.run_script("--clear-local", answers="y\n", script=source)
        self.assert_success(result)
        self.assertEqual(source.read_bytes(), before)
        self.assertTrue(stage.is_dir())
        self.assertNotIn(str(source), result.stdout)
        self.assertTrue(self.installed.is_file())
        self.assert_no_external_actions()


if __name__ == "__main__":
    unittest.main()
