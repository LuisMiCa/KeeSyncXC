"""First-run and maintenance tests without real package or cloud access."""

import fcntl
import os
from pathlib import Path
import time
import unittest

from support import SCRIPT, INSTALLER_REMOTE, InstallerCase


class ManagementTests(InstallerCase):
    def old_backup(self, name, contents="retained database"):
        backups = self.state_dir / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        path = backups / name
        path.write_text(contents)
        old = time.time() - 100 * 86400
        os.utime(path, (old, old))
        return path

    def test_first_run_installs_without_opening_or_downloading_database(self):
        result = self.run_script()
        self.assert_success(result)
        self.assertTrue(self.config.is_file())
        self.assertTrue(self.installed.is_file())
        self.assertTrue(self.desktop.is_file())
        self.assertFalse(self.local.exists())
        self.assertFalse(self.state.exists())
        self.assertFalse(self.events("keepassxc"))
        self.assertNotIn("copyto", self.operations())
        self.assertNotIn("moveto", self.operations())

    def check_partial_installation(self, missing):
        self.prepare_installation()
        missing.unlink()
        result = self.run_script()
        self.assert_success(result)
        self.assertTrue(self.config.is_file())
        self.assertTrue(self.installed.is_file())
        self.assertTrue(self.desktop.is_file())
        self.assertFalse(self.local.exists())
        self.assertFalse(self.events("keepassxc"))
        self.assertNotIn("copyto", self.operations())

    def test_missing_executable_reruns_setup(self):
        self.check_partial_installation(self.installed)

    def test_missing_menu_entry_reruns_setup(self):
        self.check_partial_installation(self.desktop)

    def test_missing_saved_configuration_reruns_setup(self):
        self.check_partial_installation(self.config)

    def test_first_run_eof_cancels_without_installation(self):
        result = self.run_script(answers="")
        self.assert_failure(result)
        self.assertFalse(self.config.exists())
        self.assertFalse(self.installed.exists())
        self.assertFalse(self.desktop.exists())
        self.assert_no_external_actions()

    def test_explicit_launch_supports_manual_setup_without_installation(self):
        result = self.run_script("--launch", answers="")
        self.assert_success(result)
        self.assertEqual(self.local.read_text(), "cloud database")
        self.assertEqual(len(self.events("keepassxc")), 1)
        self.assertFalse(self.config.exists())
        self.assertFalse(self.installed.exists())
        self.assertFalse(self.desktop.exists())
        self.assertFalse(self.events("sudo"))

    def test_status_and_backup_listing_are_read_only_without_an_installation(self):
        for argument in ("--status", "--backups"):
            with self.subTest(argument=argument):
                result = self.run_script(argument, answers="")
                self.assert_success(result)
                self.assertTrue(result.stdout.strip())
                self.assertFalse(self.config.exists())
                self.assertFalse(self.installed.exists())
                self.assertFalse(self.desktop.exists())
                self.assertFalse(self.state_dir.exists())
                self.assertFalse(self.local.parent.exists())
                self.assert_no_external_actions()

    def test_status_reports_saved_paths_and_pending_recovery_offline(self):
        self.write_config()
        self.state_dir.mkdir(parents=True)
        self.state.write_text("d41d8cd98f00b204e9800998ecf8427e\n")
        conflict = Path(str(self.state) + ".conflict")
        conflict.write_text("saved conflict\n")
        transaction = Path(str(self.state) + ".transaction")
        transaction.write_text("saved recovery record\n")
        before = {p: p.read_bytes() for p in self.state_dir.iterdir()}
        result = self.run_script("--status", answers="", TEST_FAULT="offline")
        self.assert_success(result)
        output = result.stdout + result.stderr
        self.assertIn(INSTALLER_REMOTE, output)
        self.assertIn(str(self.local), output)
        self.assertIn("Pending conflict", output)
        self.assertIn("Interrupted upload", output)
        self.assertEqual({p: p.read_bytes() for p in self.state_dir.iterdir()}, before)
        self.assert_no_external_actions()

    def test_status_reports_malformed_configuration_without_executing_it(self):
        self.write_config()
        marker = self.root / "executed-config"
        contents = f"$(touch {marker})\n{self.local}\nextra line\ninvalid fourth line\n"
        self.config.write_text(contents)
        result = self.run_script("--status", answers="")
        self.assert_failure(result)
        self.assertIn("configuration", (result.stdout + result.stderr).lower())
        self.assertEqual(self.config.read_text(), contents)
        self.assertFalse(marker.exists())
        self.assertFalse(self.state_dir.exists())
        self.assert_no_external_actions()

    def test_status_reports_custom_cloud_backup_destination_without_cloud_access(self):
        self.write_config(backup_folder="Vault copies")
        before = self.config.read_bytes()
        result = self.run_script("--status", answers="", TEST_FAULT="offline")
        self.assert_success(result)
        self.assertIn("Cloud backups: gdrive:KeePass/Vault copies", result.stdout + result.stderr)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(self.state_dir.exists())
        self.assert_no_external_actions()

    def test_status_rejects_invalid_saved_backup_names_without_cloud_access(self):
        for folder in ("", "../copies", "kpDB.kdbx"):
            with self.subTest(folder=folder):
                expected = self.write_config(backup_folder=folder)
                result = self.run_script("--status", answers="")
                self.assert_failure(result)
                self.assertEqual(self.config.read_text(), expected)
                self.assertFalse(self.state_dir.exists())
                self.assert_no_external_actions()

    def test_backup_listing_includes_regular_files_without_following_symlinks(self):
        generated = self.old_backup("local-20000101T000000Z-Ab12Cd34.kdbx")
        recovery = self.old_backup("interrupted-upload-Ab12Cd34.txt", "recovery record")
        outside = self.root / "outside.kdbx"
        outside.write_text("outside file")
        link = generated.parent / "upload-20000101T000000Z-Ab12Cd34.kdbx"
        link.symlink_to(outside)
        result = self.run_script("--backups", answers="")
        self.assert_success(result)
        self.assertIn(str(generated), result.stdout)
        self.assertIn(str(recovery), result.stdout)
        self.assertNotIn(str(link), result.stdout)
        self.assertFalse((self.state_dir / "launcher.lock").exists())
        self.assert_no_external_actions()

    def test_cleanup_cancellation_previews_and_preserves_candidates(self):
        self.write_config()
        candidate = self.old_backup("local-20000101T000000Z-Ab12Cd34.kdbx")
        result = self.run_script("--cleanup", answers="1\n\n\n")
        self.assertIn("Cancelled", result.stdout + result.stderr)
        self.assertIn(str(candidate), result.stdout + result.stderr)
        self.assertEqual(candidate.read_text(), "retained database")
        self.assert_no_external_actions()

    def test_cleanup_only_removes_old_generated_backups_after_confirmation(self):
        self.write_config()
        local = self.old_backup("local-20000101T000000Z-Ab12Cd34.kdbx")
        upload = self.old_backup("upload-20000101T000000Z-Zz12Aa34.kdbx")
        preserved = [
            self.old_backup("local-20000101T000000Z-short.kdbx"),
            self.old_backup("local-20000101T000000Z-Ab12Cd34.txt"),
            self.old_backup("arbitrary.kdbx"),
            self.old_backup("interrupted-upload-Ab12Cd34.txt", "recovery record"),
            self.old_backup("local-20000101T000000Z-Fresh123.kdbx"),
        ]
        os.utime(preserved[-1], None)
        outside = self.root / "outside.kdbx"
        outside.write_text("outside database")
        symlink = local.parent / "local-20000101T000000Z-Symlink1.kdbx"
        symlink.symlink_to(outside)
        generated_directory = local.parent / "local-20000101T000000Z-Direct01.kdbx"
        generated_directory.mkdir()
        self.local.parent.mkdir(parents=True)
        self.local.write_text("active local database")
        hardlink = local.parent / "upload-20000101T000000Z-Hardlink.kdbx"
        os.link(self.local, hardlink)
        old = time.time() - 100 * 86400
        os.utime(hardlink, (old, old))
        result = self.run_script("--cleanup", answers="1\n30\ny\n")
        self.assert_success(result)
        self.assertFalse(local.exists())
        self.assertFalse(upload.exists())
        self.assertTrue(all(path.is_file() for path in preserved))
        self.assertTrue(symlink.is_symlink())
        self.assertTrue(generated_directory.is_dir())
        self.assertEqual(outside.read_text(), "outside database")
        self.assertEqual(self.local.read_text(), "active local database")
        self.assertTrue(hardlink.is_file())
        self.assert_no_external_actions()

    def test_cleanup_does_not_remove_active_database_inside_backup_directory(self):
        active = self.old_backup("local-20000101T000000Z-Active12.kdbx", "active database")
        self.write_config(local=active)
        result = self.run_script("--cleanup", answers="1\n1\ny\n")
        self.assert_success(result)
        self.assertEqual(active.read_text(), "active database")
        self.assert_no_external_actions()

    def test_cleanup_rejects_invalid_age_without_deleting_files(self):
        self.write_config()
        candidate = self.old_backup("local-20000101T000000Z-Ab12Cd34.kdbx")
        for answer in ("-1", "36501", "1.5", "no", "30; touch injected"):
            with self.subTest(answer=answer):
                result = self.run_script("--cleanup", answers="1\n" + answer + "\ny\n")
                self.assert_failure(result)
                self.assertEqual(candidate.read_text(), "retained database")
                self.assert_no_external_actions()

    def test_cleanup_eof_preserves_files(self):
        self.write_config()
        candidate = self.old_backup("local-20000101T000000Z-Ab12Cd34.kdbx")
        for answers in ("", "1\n", "1\n30\n"):
            with self.subTest(answers=answers):
                result = self.run_script("--cleanup", answers=answers)
                self.assertEqual(candidate.read_text(), "retained database")
                self.assert_no_external_actions()

    def test_cleanup_refuses_pending_conflicts_or_uploads_for_any_remote(self):
        self.write_config()
        candidate = self.old_backup("local-20000101T000000Z-Ab12Cd34.kdbx")
        for suffix in (".conflict", ".transaction"):
            with self.subTest(suffix=suffix):
                pending = self.state_dir / ("another-remote.md5" + suffix)
                pending.write_text("pending recovery\n")
                result = self.run_script("--cleanup", answers="1\n1\ny\n")
                self.assert_failure(result)
                self.assertEqual(candidate.read_text(), "retained database")
                self.assertEqual(pending.read_text(), "pending recovery\n")
                pending.unlink()
                self.assert_no_external_actions()

    def test_cleanup_obeys_running_launcher_lock(self):
        self.write_config()
        candidate = self.old_backup("local-20000101T000000Z-Ab12Cd34.kdbx")
        with (self.state_dir / "launcher.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_script("--cleanup", answers="1\n1\ny\n")
        self.assert_failure(result)
        self.assertEqual(candidate.read_text(), "retained database")
        self.assert_no_external_actions()

    def test_cleanup_refuses_symlinked_backup_directory(self):
        self.write_config()
        outside = self.root / "outside backups"
        outside.mkdir()
        candidate = outside / "local-20000101T000000Z-Ab12Cd34.kdbx"
        candidate.write_text("outside database")
        old = time.time() - 100 * 86400
        os.utime(candidate, (old, old))
        self.state_dir.mkdir(parents=True)
        (self.state_dir / "backups").symlink_to(outside)
        result = self.run_script("--cleanup", answers="1\n1\ny\n")
        self.assert_failure(result)
        self.assertEqual(candidate.read_text(), "outside database")
        self.assert_no_external_actions()

    def test_cleanup_menu_cancellation_does_not_create_state(self):
        result = self.run_script("--cleanup", answers="0\n")
        self.assert_success(result)
        self.assertFalse(self.state_dir.exists())
        self.assert_no_external_actions()

    def test_cleanup_keeps_newest_combined_backups_with_stable_ties(self):
        self.write_config()
        oldest = self.old_backup("local-20000101T000000Z-Oldest01.kdbx")
        tied_a = self.old_backup("local-20000101T000000Z-Aaaaaaaa.kdbx")
        tied_z = self.old_backup("upload-20000101T000000Z-Zzzzzzzz.kdbx")
        newest = self.old_backup("upload-20000101T000000Z-Newest01.kdbx")
        switch = self.old_backup("switch-20000101T000000Z-Switch01.kdbx")
        archive = self.old_backup("interrupted-upload-Ab12Cd34.txt", "recovery")
        now = int(time.time())
        for path, mtime in ((oldest, now - 300), (tied_a, now - 200),
                            (tied_z, now - 200), (newest, now - 100)):
            os.utime(path, (mtime, mtime))
        result = self.run_script("--cleanup", answers="2\n2\ny\n")
        self.assert_success(result)
        self.assertTrue(newest.exists())
        self.assertTrue(tied_a.exists())
        self.assertFalse(oldest.exists())
        self.assertFalse(tied_z.exists())
        self.assertTrue(switch.exists())
        self.assertTrue(archive.exists())
        self.assert_no_external_actions()

    def test_cleanup_zero_count_removes_all_eligible_backups(self):
        self.write_config()
        old = self.old_backup("local-20000101T000000Z-Ab12Cd34.kdbx")
        new = self.old_backup("upload-20000101T000000Z-Newest01.kdbx")
        os.utime(new, None)
        result = self.run_script("--cleanup", answers="2\n0\ny\n")
        self.assert_success(result)
        self.assertFalse(old.exists())
        self.assertFalse(new.exists())
        self.assert_no_external_actions()

    def test_cleanup_zero_days_includes_recent_past_backups(self):
        self.write_config()
        old = self.old_backup("local-20000101T000000Z-Ab12Cd34.kdbx")
        result = self.run_script("--cleanup", answers="1\n0\ny\n")
        self.assert_success(result)
        self.assertFalse(old.exists())
        self.assert_no_external_actions()

    def test_cleanup_rejects_invalid_retention_counts(self):
        self.write_config()
        candidate = self.old_backup("local-20000101T000000Z-Ab12Cd34.kdbx")
        for count in ("-1", "100001", "1.5", "hello", "2; touch injected"):
            with self.subTest(count=count):
                result = self.run_script("--cleanup", answers=f"2\n{count}\ny\n")
                self.assert_failure(result)
                self.assertTrue(candidate.exists())
        self.assert_no_external_actions()

    def test_cleanup_count_refuses_pending_recovery(self):
        self.write_config()
        candidate = self.old_backup("local-20000101T000000Z-Ab12Cd34.kdbx")
        pending = self.state_dir / ("another-remote.md5.transaction")
        pending.write_text("unresolved")
        result = self.run_script("--cleanup", answers="2\n0\ny\n")
        self.assert_failure(result)
        self.assertTrue(candidate.exists())
        self.assertEqual(pending.read_text(), "unresolved")
        self.assert_no_external_actions()

    def prepare_full_cleanup(self):
        self.prepare_installation()
        self.local.parent.mkdir(parents=True)
        self.local.write_text("local working database")
        backup = self.old_backup("local-20000101T000000Z-Ab12Cd34.kdbx")
        switch = self.old_backup("switch-20000101T000000Z-Switch01.kdbx")
        recovery = self.old_backup("interrupted-upload-Ab12Cd34.txt", "recovery")
        self.state.write_text("baseline")
        identity = Path(str(self.state) + ".id")
        identity.write_text("old-drive-id")
        conflict = Path(str(self.state) + ".conflict")
        conflict.write_text("pending conflict")
        transaction = Path(str(self.state) + ".transaction")
        transaction.write_text("pending upload")
        archive = backup.parent / "reset-Ab12Cd34"
        archive.mkdir()
        archived_config = archive / "config"
        archived_config.write_text("archived settings")
        archived_record = archive / self.state.name
        archived_record.write_text("archived baseline")
        return [self.local, backup, switch, recovery, self.state, identity,
                conflict, transaction, archived_config, archived_record]

    def test_full_cleanup_previews_working_database_and_defaults_to_no(self):
        files = self.prepare_full_cleanup()
        before = {path: path.read_bytes() for path in files}
        result = self.run_script("--clear-local", answers="\n")
        self.assertIn("Working database (will be deleted)", result.stdout)
        self.assertIn("conflict and interrupted-upload", result.stdout)
        for path, contents in before.items():
            self.assertIn(str(path), result.stdout)
            self.assertEqual(path.read_bytes(), contents)
        self.assertTrue(self.config.exists())
        self.assert_no_external_actions()

    def test_full_cleanup_removes_only_known_files_and_retains_launcher(self):
        files = self.prepare_full_cleanup()
        unknown = self.state_dir / "unknown.txt"
        unknown.write_text("unknown state data")
        unknown_backup = self.state_dir / "backups/do-not-delete.kdbx"
        unknown_backup.write_text("unknown backup")
        archive_unknown = self.state_dir / "backups/reset-Ab12Cd34/unknown.txt"
        archive_unknown.write_text("unknown recovery file")
        outside = self.root / "outside.kdbx"
        outside.write_text("outside database")
        link = self.state_dir / "backups/unknown-link.kdbx"
        link.symlink_to(outside)
        credentials = self.root / "configuration/rclone/rclone.conf"
        credentials.parent.mkdir(parents=True)
        credentials.write_text("credentials")
        result = self.run_script("--clear-local", answers="y\n")
        self.assert_success(result)
        self.assertTrue(all(not path.exists() for path in files))
        self.assertFalse(self.config.exists())
        self.assertTrue(self.installed.exists())
        self.assertTrue(self.desktop.exists())
        self.assertTrue((self.state_dir / "launcher.lock").is_file())
        self.assertTrue(unknown.exists())
        self.assertTrue(unknown_backup.exists())
        self.assertTrue(archive_unknown.exists())
        self.assertTrue(link.is_symlink())
        self.assertEqual(outside.read_text(), "outside database")
        self.assertEqual(credentials.read_text(), "credentials")
        self.assertEqual(self.cloud.read_text(), "cloud database")
        self.assert_no_external_actions()

    def test_full_cleanup_removes_only_empty_known_directories(self):
        self.prepare_full_cleanup()
        result = self.run_script("--clear-local", answers="y\n")
        self.assert_success(result)
        self.assertFalse((self.state_dir / "backups").exists())
        self.assertFalse(self.config.parent.exists())
        self.assertFalse(self.local.parent.exists())
        self.assertTrue(self.state_dir.is_dir())
        self.assert_no_external_actions()

    def test_full_cleanup_removes_exact_generated_temps_and_keeps_unknowns(self):
        self.prepare_full_cleanup()
        locations = ((self.state_dir, ".sync-state.Ab12Cd34"),
                     (self.state_dir, ".launcher-record.Ab12Cd34"),
                     (self.config.parent, ".config.Ab12Cd34"),
                     (self.installed.parent, ".keesyncxc-install.Ab12Cd34"),
                     (self.desktop.parent, ".keesyncxc-install.Ab12Cd34"),
                     (self.local.parent, ".keesyncxc-download.Ab12Cd34"))
        generated = []
        unknown = []
        for directory, name in locations:
            path = directory / name
            path.write_text("abandoned temporary file")
            generated.append(path)
            unrelated = directory / (name + "-unrelated")
            unrelated.write_text("keep unknown file")
            unknown.append(unrelated)
        outside = self.root / "outside.txt"
        outside.write_text("outside data")
        symlink = self.config.parent / ".config.Symlink1"
        symlink.symlink_to(outside)
        directory = self.state_dir / ".sync-state.Direct01"
        directory.mkdir()
        result = self.run_script("--clear-local", answers="y\n")
        self.assert_success(result)
        self.assertTrue(all(not path.exists() for path in generated))
        self.assertTrue(all(path.read_text() == "keep unknown file" for path in unknown))
        self.assertTrue(symlink.is_symlink())
        self.assertTrue(directory.is_dir())
        self.assertEqual(outside.read_text(), "outside data")
        for path in generated:
            self.assertIn(str(path), result.stdout)
        self.assert_no_external_actions()

    def test_full_cleanup_keeps_executing_source_with_temp_filename(self):
        self.prepare_full_cleanup()
        source = self.config.parent / ".config.Source01"
        source.write_bytes(SCRIPT.read_bytes())
        before = source.read_bytes()
        result = self.run_script("--clear-local", answers="y\n", script=source)
        self.assert_success(result)
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(self.config.exists())
        self.assertTrue(self.installed.exists())
        self.assert_no_external_actions()

    def test_full_cleanup_refuses_symlinked_local_database(self):
        self.prepare_installation()
        outside = self.root / "outside.kdbx"
        outside.write_text("outside database")
        self.local.parent.mkdir(parents=True)
        self.local.symlink_to(outside)
        result = self.run_script("--clear-local", answers="y\n")
        self.assert_failure(result)
        self.assertTrue(self.local.is_symlink())
        self.assertTrue(self.config.exists())
        self.assertEqual(outside.read_text(), "outside database")
        self.assert_no_external_actions()

    def test_full_cleanup_with_missing_config_does_not_guess_default_database(self):
        self.local.parent.mkdir(parents=True)
        self.local.write_text("unconfigured database")
        backup = self.old_backup("local-20000101T000000Z-Ab12Cd34.kdbx")
        result = self.run_script("--clear-local", answers="y\n")
        self.assert_success(result)
        self.assertEqual(self.local.read_text(), "unconfigured database")
        self.assertFalse(backup.exists())
        self.assert_no_external_actions()

    def test_full_cleanup_refuses_malformed_config_and_preserves_data(self):
        self.prepare_full_cleanup()
        self.config.write_text("invalid settings\n")
        result = self.run_script("--clear-local", answers="y\n")
        self.assert_failure(result)
        self.assertTrue(self.local.exists())
        self.assertEqual(self.config.read_text(), "invalid settings\n")
        self.assert_no_external_actions()

    def test_full_uninstall_removes_known_local_data_after_one_confirmation(self):
        files = self.prepare_full_cleanup()
        result = self.run_script("--uninstall", answers="3\ny\n", script=self.installed)
        self.assert_success(result)
        self.assertEqual(result.stderr.count('[y/N]'), 1)
        self.assertEqual(result.stdout.count(str(self.local)), 1)
        self.assertNotIn('Installation files selected for removal', result.stdout)
        self.assertTrue(all(not path.exists() for path in files))
        self.assertFalse(self.installed.exists())
        self.assertFalse(self.desktop.exists())
        self.assertFalse(self.config.exists())
        self.assertTrue((self.state_dir / "launcher.lock").exists())
        self.assertEqual(self.cloud.read_text(), "cloud database")
        self.assert_no_external_actions()

    def test_full_uninstall_cancellation_preserves_installation_and_database(self):
        self.prepare_full_cleanup()
        result = self.run_script("--uninstall", answers="3\nn\n")
        self.assertEqual(result.returncode, 130)
        self.assertIn("Cancelled", result.stdout)
        self.assertTrue(self.local.exists())
        self.assertTrue(self.installed.exists())
        self.assertTrue(self.config.exists())
        self.assert_no_external_actions()

    def test_uninstall_launcher_only_choice_keeps_local_data(self):
        self.prepare_full_cleanup()
        result = self.run_script("--uninstall", answers="1\ny\n")
        self.assert_success(result)
        self.assertTrue(self.local.exists())
        self.assertTrue(self.state.exists())
        self.assertFalse(self.config.exists())
        self.assertFalse(self.installed.exists())
        self.assert_no_external_actions()

    def test_uninstall_local_data_only_choice_keeps_launcher(self):
        files = self.prepare_full_cleanup()
        result = self.run_script("--uninstall", answers="2\ny\n", script=self.installed)
        self.assert_success(result)
        self.assertEqual(result.stderr.count('[y/N]'), 1)
        self.assertTrue(all(not path.exists() for path in files))
        self.assertFalse(self.config.exists())
        self.assertTrue(self.installed.exists())
        self.assertTrue(self.desktop.exists())
        self.assertEqual(self.cloud.read_text(), "cloud database")
        self.assert_no_external_actions()

    def test_uninstall_every_choice_defaults_to_no_at_deletion_preview(self):
        files = self.prepare_full_cleanup() + [self.installed, self.desktop, self.config]
        before = {path: path.read_bytes() for path in files}
        for choice in (1, 2, 3):
            for confirmation in ("n\n", "\n", ""):
                with self.subTest(choice=choice, confirmation=confirmation):
                    result = self.run_script("--uninstall", answers=f"{choice}\n{confirmation}")
                    self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
                    for path, contents in before.items():
                        self.assertEqual(path.read_bytes(), contents)
        self.assert_no_external_actions()

    def test_uninstall_help_and_invalid_choices_never_select_deletion(self):
        self.prepare_full_cleanup()
        before = self.config.read_bytes()
        result = self.run_script("--uninstall", answers="invalid\ny\nh\n0\n")
        self.assertEqual(result.returncode, 130)
        output = result.stdout + result.stderr
        self.assertIn('Uninstall and local-data removal help', output)
        self.assertIn('Help returns to the same menu', output)
        self.assertIn('Choose 0, 1, 2, 3, or H', output)
        self.assertNotIn('[y/N]', output)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertTrue(self.installed.exists())
        self.assertTrue(self.local.exists())
        self.assert_no_external_actions()

    def test_uninstall_keep_data_flag_bypasses_menu_and_preserves_data(self):
        files = self.prepare_full_cleanup()
        result = self.run_script("--uninstall-keep-data", answers="y\n", script=self.installed)
        self.assert_success(result)
        self.assertNotIn('Choice [0-3, H]', result.stderr)
        self.assertEqual(result.stderr.count('[y/N]'), 1)
        self.assertTrue(all(path.exists() for path in files))
        self.assertFalse(self.installed.exists())
        self.assertFalse(self.desktop.exists())
        self.assertFalse(self.config.exists())
        self.assert_no_external_actions()

    def test_reset_cancellation_preserves_current_selection(self):
        self.prepare_full_cleanup()
        before = self.config.read_bytes()
        backups_before = list((self.state_dir / "backups").glob("switch-*"))
        result = self.run_script("--reset", answers="n\n")
        self.assertIn("Cancelled", result.stdout)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(self.local.read_text(), "local working database")
        self.assertEqual(list((self.state_dir / "backups").glob("switch-*")), backups_before)
        self.assert_no_external_actions()

    def test_reset_preserves_verified_backup_and_archives_recovery_on_setup_eof(self):
        self.prepare_installation()
        self.local.parent.mkdir(parents=True)
        self.local.write_text("unsynced local working database")
        self.state_dir.mkdir(parents=True)
        self.state.write_text("old baseline")
        identity = Path(str(self.state) + ".id")
        identity.write_text("old-drive-id")
        transaction = Path(str(self.state) + ".transaction")
        transaction.write_text("unresolved upload")
        conflict = Path(str(self.state) + ".conflict")
        conflict.write_text("unresolved conflict")
        old_config = self.config.read_bytes()
        result = self.run_script("--reset", answers="y\n")
        self.assert_failure(result)
        self.assertIn("Enter value (required)", result.stderr)
        self.assertFalse(self.local.exists())
        self.assertFalse(self.config.exists())
        for record in (self.state, identity, transaction, conflict):
            self.assertFalse(record.exists())
        backups = list((self.state_dir / "backups").glob("switch-*.kdbx"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "unsynced local working database")
        archives = list((self.state_dir / "backups").glob("reset-*"))
        self.assertEqual(len(archives), 1)
        self.assertEqual((archives[0] / "config").read_bytes(), old_config)
        self.assertEqual((archives[0] / transaction.name).read_text(), "unresolved upload")
        self.assertIn(str(archives[0]), result.stdout)
        self.assertIn(str(backups[0]), result.stdout)
        self.assertTrue(self.installed.exists())
        self.assert_no_external_actions()

    def test_reset_accepts_replaced_remote_id_as_new_selection_without_upload(self):
        self.prepare_installation()
        self.local.parent.mkdir(parents=True)
        self.local.write_text("old unsynced local database")
        self.state_dir.mkdir(parents=True)
        self.state.write_text("a" * 32)
        identity = Path(str(self.state) + ".id")
        identity.write_text("previous-drive-id")
        result = self.run_script("--reset", answers=f"y\n{INSTALLER_REMOTE}\n\n\nn\n")
        self.assert_success(result)
        self.assertFalse(self.local.exists())
        self.assertFalse(self.state.exists())
        self.assertFalse(identity.exists())
        self.assertEqual(self.config.read_text().splitlines()[0], INSTALLER_REMOTE)
        backups = list((self.state_dir / "backups").glob("switch-*.kdbx"))
        self.assertEqual(backups[0].read_text(), "old unsynced local database")
        self.assertEqual(self.cloud.read_text(), "cloud database")
        self.assertFalse({"copyto", "moveto", "delete", "deletefile", "mkdir"} & set(self.operations()))
        self.assertFalse(self.events("keepassxc"))

    def test_reset_verifies_database_and_config_paths_with_backslashes(self):
        self.local = self.local.with_name(r"Pass\words.kdbx")
        config_home = self.root / r"configuration\settings"
        self.env["XDG_CONFIG_HOME"] = str(config_home)
        self.config = config_home / "keesyncxc/config"
        self.prepare_installation()
        self.local.parent.mkdir(parents=True)
        self.local.write_text("unsynced local database")
        self.state_dir.mkdir(parents=True)
        self.state.write_text("old baseline")
        original_config = self.config.read_bytes()
        result = self.run_script("--reset", answers="y\n")
        self.assert_failure(result)
        self.assertIn("Enter value (required)", result.stderr)
        self.assertFalse(self.local.exists())
        self.assertFalse(self.config.exists())
        self.assertFalse(self.state.exists())
        backups = list((self.state_dir / "backups").glob("switch-*.kdbx"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "unsynced local database")
        archives = list((self.state_dir / "backups").glob("reset-*"))
        self.assertEqual(len(archives), 1)
        self.assertEqual((archives[0] / "config").read_bytes(), original_config)
        self.assertEqual((archives[0] / self.state.name).read_text(), "old baseline")
        self.assert_no_external_actions()

    def test_reset_refuses_running_launcher_without_creating_backup(self):
        self.prepare_installation()
        self.state_dir.mkdir(parents=True)
        with (self.state_dir / "launcher.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_script("--reset", answers="y\n")
        self.assert_failure(result)
        self.assertTrue(self.config.exists())
        self.assertFalse((self.state_dir / "backups").exists())
        self.assert_no_external_actions()

    def test_reset_copy_failure_keeps_database_and_active_records(self):
        self.prepare_installation()
        self.local.parent.mkdir(parents=True)
        self.local.write_text("unsynced local database")
        self.state_dir.mkdir(parents=True)
        self.state.write_text("old baseline")
        original_config = self.config.read_bytes()
        (self.binaries / "cp").symlink_to(self.mock)
        result = self.run_script("--reset", answers="y\n", TEST_FAULT="local_backup")
        self.assert_failure(result)
        self.assertEqual(self.local.read_text(), "unsynced local database")
        self.assertEqual(self.config.read_bytes(), original_config)
        self.assertEqual(self.state.read_text(), "old baseline")
        self.assert_no_external_actions()

    def test_reset_corrupt_backup_keeps_database_and_active_records(self):
        self.prepare_installation()
        self.local.parent.mkdir(parents=True)
        self.local.write_text("unsynced local database")
        original_config = self.config.read_bytes()
        copy_mock = self.binaries / "cp"
        copy_mock.write_text("#!/usr/bin/python3\nimport sys\nfrom pathlib import Path\n"
                             "Path(sys.argv[-1]).write_text('corrupt backup')\n")
        copy_mock.chmod(0o755)
        result = self.run_script("--reset", answers="y\n")
        self.assert_failure(result)
        self.assertIn("verification failed", result.stdout + result.stderr)
        self.assertEqual(self.local.read_text(), "unsynced local database")
        self.assertEqual(self.config.read_bytes(), original_config)
        self.assert_no_external_actions()

    def test_reset_without_local_database_can_configure_another_remote(self):
        self.prepare_installation()
        result = self.run_script("--reset", answers=f"y\n{INSTALLER_REMOTE}\n\n\nn\n")
        self.assert_success(result)
        self.assertIn("Working database is absent", result.stdout)
        self.assertTrue(self.config.exists())
        self.assertFalse(self.local.exists())
        self.assertFalse(list((self.state_dir / "backups").glob("switch-*.kdbx")))
        self.assertEqual(len(list((self.state_dir / "backups").glob("reset-*"))), 1)
        self.assertFalse({"copyto", "moveto", "delete", "deletefile", "mkdir"} & set(self.operations()))

    def test_destructive_maintenance_refuses_running_keepassxc(self):
        self.prepare_full_cleanup()
        self.env["TEST_APP_RUNNING"] = "1"
        for option, answers in (("--clear-local", "y\n"), ("--uninstall", "1\ny\n"),
                                ("--uninstall", "2\ny\n"), ("--uninstall", "3\ny\n"),
                                ("--reset", "y\n")):
            with self.subTest(option=option):
                result = self.run_script(option, answers=answers)
                self.assert_failure(result)
                self.assertIn("Fully quit KeePassXC", result.stdout + result.stderr)
                self.assertTrue(self.config.exists())
                self.assertTrue(self.local.exists())
        self.assert_no_external_actions()

    def test_uninstall_cancellation_preserves_installation(self):
        self.prepare_installation()
        before = {p: p.read_bytes() for p in (self.installed, self.config, self.desktop)}
        for answers in ("", "\n", "0\n", "q\n"):
            with self.subTest(answers=answers):
                result = self.run_script("--uninstall", answers=answers)
                self.assertEqual(result.returncode, 130)
                self.assertIn("Cancelled", result.stdout + result.stderr)
                self.assertNotIn('[y/N]', result.stderr)
                for path, contents in before.items():
                    self.assertEqual(path.read_bytes(), contents)
        self.assert_no_external_actions()

    def test_uninstall_preserves_databases_credentials_packages_state_and_source(self):
        self.prepare_installation()
        self.write_config(backup_folder="Vault copies")
        self.local.parent.mkdir(parents=True)
        self.local.write_text("local database")
        backup = self.old_backup("local-20000101T000000Z-Ab12Cd34.kdbx")
        self.state.write_text("retained synchronization state\n")
        credentials = self.root / "configuration/rclone/rclone.conf"
        credentials.parent.mkdir(parents=True)
        credentials.write_text("retained OAuth credentials\n")
        source = SCRIPT.read_bytes()
        result = self.run_script("--uninstall", answers="1\ny\n", script=self.installed)
        self.assert_success(result)
        self.assertFalse(self.installed.exists())
        self.assertFalse(self.config.exists())
        self.assertFalse(self.desktop.exists())
        self.assertEqual(self.local.read_text(), "local database")
        self.assertEqual(self.cloud.read_text(), "cloud database")
        self.assertEqual(self.state.read_text(), "retained synchronization state\n")
        self.assertEqual(backup.read_text(), "retained database")
        self.assertEqual(credentials.read_text(), "retained OAuth credentials\n")
        self.assertEqual(SCRIPT.read_bytes(), source)
        self.assert_no_external_actions()

    def test_uninstall_works_with_malformed_configuration_and_missing_app_dependencies(self):
        self.prepare_installation()
        self.config.write_text("malformed configuration\n")
        self.isolate_path({"rclone", "keepassxc", "zenity", "pgrep"})
        result = self.run_script("--uninstall", answers="1\nyes\n")
        self.assert_success(result)
        self.assertFalse(self.installed.exists())
        self.assertFalse(self.config.exists())
        self.assertFalse(self.desktop.exists())
        self.assert_no_external_actions()

    def test_uninstall_obeys_running_launcher_lock(self):
        self.prepare_installation()
        self.state_dir.mkdir(parents=True)
        with (self.state_dir / "launcher.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_script("--uninstall", answers="1\ny\n")
        self.assert_failure(result)
        self.assertTrue(self.installed.exists())
        self.assertTrue(self.config.exists())
        self.assertTrue(self.desktop.exists())
        self.assert_no_external_actions()

    def test_menu_handles_invalid_choices_and_eof_without_installing(self):
        for argument in ("--menu", "--interactive"):
            with self.subTest(argument=argument):
                result = self.run_script(argument, answers="invalid\n")
                self.assert_success(result)
                output = (result.stdout + result.stderr).lower()
                self.assertIn("install", output)
                self.assertIn("maintenance", output)
                self.assertIn("backups and recovery", output)
                self.assertFalse(self.config.exists())
                self.assertFalse(self.state_dir.exists())
                self.assert_no_external_actions()

    def test_menu_returns_after_read_only_actions(self):
        result = self.run_script("--menu", answers="2\n1\n0\n3\n1\n0\n0\n")
        self.assert_success(result)
        self.assertGreaterEqual((result.stdout + result.stderr).lower().count("installation and advanced maintenance"), 3)
        self.assertFalse(self.state_dir.exists())
        self.assert_no_external_actions()

    def test_menu_launch_runs_first_install_then_returns_to_menu(self):
        result = self.run_script("--menu", answers="1\n\n\n\nn\n0\n")
        self.assert_success(result)
        self.assertTrue(self.config.is_file())
        self.assertTrue(self.installed.is_file())
        self.assertTrue(self.desktop.is_file())
        self.assertFalse(self.local.exists())
        self.assertFalse(self.events("keepassxc"))

    def test_menu_continues_after_a_failed_action(self):
        self.write_config()
        self.config.write_text("malformed configuration\n")
        result = self.run_script("--menu", answers="2\n1\n0\n0\n")
        self.assert_success(result)
        output = (result.stdout + result.stderr).lower()
        self.assertIn("configuration", output)
        self.assertGreaterEqual(output.count("status and troubleshooting"), 2)
        self.assert_no_external_actions()

    def test_menu_uninstall_cancellation_returns_to_menu(self):
        self.prepare_installation()
        result = self.run_script("--menu", answers="5\n3\n0\n0\n0\n")
        self.assert_success(result)
        self.assertTrue(self.installed.exists())
        self.assertTrue(self.config.exists())
        self.assertTrue(self.desktop.exists())
        self.assertGreaterEqual((result.stdout + result.stderr).lower().count("uninstall"), 2)
        self.assert_no_external_actions()

    def test_menu_exits_after_successful_uninstall(self):
        self.prepare_installation()
        result = self.run_script("--menu", answers="5\n3\n1\ny\n", script=self.installed)
        self.assert_success(result)
        self.assertFalse(self.installed.exists())
        self.assertFalse(self.config.exists())
        self.assertFalse(self.desktop.exists())
        self.assertNotIn("Action did not complete", result.stdout)
        self.assert_no_external_actions()

    def test_menu_exits_after_complete_uninstall(self):
        files = self.prepare_full_cleanup()
        result = self.run_script("--menu", answers="5\n3\n3\ny\n", script=self.installed)
        self.assert_success(result)
        self.assertTrue(all(not path.exists() for path in files))
        self.assertFalse(self.installed.exists())
        self.assertFalse(self.desktop.exists())
        self.assertNotIn('Action did not complete', result.stdout)
        self.assert_no_external_actions()

    def test_menu_returns_to_maintenance_after_deleting_only_local_data(self):
        files = self.prepare_full_cleanup()
        result = self.run_script("--menu", answers="5\n3\n2\ny\n0\n0\n", script=self.installed)
        self.assert_success(result)
        self.assertTrue(all(not path.exists() for path in files))
        self.assertTrue(self.installed.exists())
        self.assertTrue(self.desktop.exists())
        self.assertGreaterEqual(result.stderr.count('Installation and advanced maintenance'), 2)
        self.assertNotIn('Action did not complete', result.stdout)
        self.assert_no_external_actions()


if __name__ == "__main__":
    unittest.main()
