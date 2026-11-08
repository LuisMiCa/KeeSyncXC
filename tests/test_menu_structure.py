"""Task-oriented menus and synchronization without opening a database.

Use the isolated homes, fake Drive, and inert KeePassXC from the existing
integration harnesses. No test opens an application or contacts Google Drive.
"""

import fcntl
import hashlib
from pathlib import Path
import subprocess
import unittest

from support import SCRIPT, INSTALLER_REMOTE, LauncherCase, InstallerCase


class MenuStructureTests(InstallerCase):
    def assert_no_external_actions(self):
        self.assertFalse(self.events(), "Read-only menu navigation ran an external action")

    def test_every_main_category_and_help_can_be_opened_and_left(self):
        result = self.run_script("--menu", answers="2\n0\n3\n0\n4\n0\n5\n0\nh\n0\n")
        self.assert_success(result)
        output = result.stdout + result.stderr
        for label in ("Open database", "Status and troubleshooting", "Backups and recovery",
                      "Settings and database selection", "Installation and advanced maintenance",
                      "Synchronize without opening", "Restore a local backup", "Switch cloud database",
                      "Move local database", "Google Drive connection", "Inspect a cloud backup", "Help"):
            self.assertIn(label, output)
        self.assertFalse(self.config.exists())
        self.assertFalse(self.state_dir.exists())
        self.assert_no_external_actions()

    def test_nested_read_only_actions_return_to_their_submenu(self):
        self.write_config()
        before = self.config.read_bytes()
        result = self.run_script("--menu", answers="2\n1\n0\n3\n1\n0\n4\n1\n0\n0\n")
        self.assert_success(result)
        output = result.stdout + result.stderr
        self.assertIn("Launcher status", output)
        self.assertIn("Local backups", output)
        self.assertIn(INSTALLER_REMOTE, output)
        for label in ("Status and troubleshooting", "Backups and recovery",
                      "Settings and database selection"):
            self.assertGreaterEqual(output.count(label), 2, output)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(self.state_dir.exists())
        self.assert_no_external_actions()

    def test_invalid_submenu_choice_redisplays_that_submenu(self):
        result = self.run_script("--menu", answers="2\ninvalid\n0\n0\n")
        self.assert_success(result)
        output = result.stdout + result.stderr
        self.assertGreaterEqual(output.count("Status and troubleshooting"), 3, output)
        self.assertNotIn("Action did not complete", output)
        self.assert_no_external_actions()

    def test_switch_cancellation_returns_quietly_to_settings(self):
        self.prepare_installation()
        before = self.config.read_bytes()
        result = self.run_script("--menu", answers="4\n5\nn\n0\n0\n")
        self.assert_success(result)
        output = result.stdout + result.stderr
        self.assertIn("Cancelled", output)
        self.assertNotIn("Action did not complete", output)
        self.assertGreaterEqual(output.count("Settings and database selection"), 2, output)
        self.assertEqual(self.config.read_bytes(), before)
        self.assert_no_external_actions()

    def test_uninstall_cancellation_returns_quietly_to_maintenance(self):
        self.prepare_installation()
        result = self.run_script("--menu", answers="5\n3\n1\nn\n0\n0\n")
        self.assert_success(result)
        output = result.stdout + result.stderr
        self.assertIn("Cancelled", output)
        self.assertNotIn("Action did not complete", output)
        self.assertGreaterEqual(output.count("Installation and advanced maintenance"), 2, output)
        self.assertTrue(self.installed.exists())
        self.assertTrue(self.config.exists())
        self.assert_no_external_actions()

    def test_connection_check_is_read_only_for_database_and_sync_state(self):
        self.write_config()
        self.local.parent.mkdir(parents=True)
        self.local.write_text("local unsynchronized work")
        before = self.config.read_bytes()
        result = self.run_script("--check-connection", answers="")
        self.assert_success(result)
        self.assertTrue(self.events("rclone"))
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(self.local.read_text(), "local unsynchronized work")
        self.assertEqual(self.cloud.read_text(), "cloud database")
        self.assertFalse(self.state.exists())
        self.assertFalse(self.events("keepassxc"))
        self.assertFalse({"copyto", "moveto", "mkdir", "delete", "deletefile"} & set(self.operations()))

    def test_failed_connection_check_does_not_initialize_sync_state(self):
        self.write_config()
        result = self.run_script("--check-connection", answers="", TEST_FAULT="offline")
        self.assert_failure(result)
        self.assertFalse(self.state.exists())
        self.assertFalse(self.local.exists())
        self.assertFalse(self.events("keepassxc"))
        self.assertFalse({"copyto", "moveto", "mkdir"} & set(self.operations()))

    def test_new_prompting_modes_open_a_terminal_before_changing_any_files(self):
        self.mock_terminal()
        modes = ("--backup-folder", "--move-local", "--connection", "--inspect-backup", "--inspect-cloud-backup",
                 "--restore-backup", "--repair", "--dependencies", "--clear-local",
                 "--uninstall", "--uninstall-keep-data", "--switch-database", "--cleanup-age", "--cleanup-count")
        for mode in modes:
            with self.subTest(mode=mode):
                result = self.run_from_file_manager(mode)
                self.assert_success(result)
                dispatched = "--reset" if mode == "--switch-database" else mode
                self.assertEqual(self.events()[-1], {
                    "command": "x-terminal-emulator",
                    "arguments": ["-e", "/bin/bash", str(SCRIPT), dispatched]})
                self.assertFalse(self.config.exists())
                self.assertFalse(self.state_dir.exists())
        self.assertEqual(len(self.events()), len(modes))


class SynchronizeOnlyTests(LauncherCase):
    def run_sync(self, choices="", fault="", **settings):
        return subprocess.run(["bash", str(SCRIPT), "--sync"],
                              input=choices, text=True, capture_output=True,
                              env=dict(self.env, TEST_FAULT=fault, **settings), timeout=20)

    def assert_no_editor(self):
        self.assertFalse(self.events("keepassxc"))
        self.assertFalse((self.root / "launched").exists())

    def test_initial_sync_downloads_and_establishes_baseline_without_editor(self):
        self.cloud.write_text("first cloud database")
        result = self.run_sync()
        self.assert_success(result)
        self.assertEqual(self.local.read_text(), "first cloud database")
        self.assertEqual(self.state.read_text().strip(), self.digest("first cloud database"))
        self.assert_no_editor()

    def test_matching_copies_sync_without_upload_or_editor(self):
        self.seed()
        result = self.run_sync()
        self.assert_success(result)
        self.assertEqual(self.local.read_text(), "original")
        self.assertFalse(self.remote_operations("copyto"))
        self.assert_no_editor()

    def test_remote_only_edit_is_downloaded_with_local_safety_copy(self):
        self.seed(cloud="other device changes")
        result = self.run_sync()
        self.assert_success(result)
        self.assertEqual(self.local.read_text(), "other device changes")
        self.assertEqual(self.local_backups(), ["original"])
        self.assertEqual(self.state.read_text().strip(), self.digest("other device changes"))
        self.assert_no_editor()

    def test_local_only_edit_updates_same_drive_object_and_keeps_cloud_backup(self):
        self.seed(local="saved local changes")
        original_id = self.cloud_id()
        result = self.run_sync()
        self.assert_success(result)
        self.assertEqual(self.cloud.read_text(), "saved local changes")
        self.assertEqual(self.cloud_id(), original_id)
        self.assertEqual([p.read_text() for p in self.cloud_backups()], ["original"])
        self.assertEqual(self.state.read_text().strip(), self.digest("saved local changes"))
        self.assertFalse(self.transaction.exists())
        self.assert_no_editor()

    def test_offline_sync_reports_incomplete_without_opening_offline_editor(self):
        self.seed(local="unsynchronized changes")
        result = self.run_sync(fault="offline")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.local.read_text(), "unsynchronized changes")
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertEqual(self.state.read_text().strip(), self.digest("original"))
        self.assertNotIn("Finished. Synchronization completed.", result.stdout)
        self.assert_no_editor()

    def test_failed_download_preserves_local_and_baseline_without_editor(self):
        self.seed(cloud="other device changes")
        result = self.run_sync(fault="corrupt_download")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.local.read_text(), "original")
        self.assertEqual(self.state.read_text().strip(), self.digest("original"))
        self.assert_no_editor()

    def test_cancel_alias_leaves_conflict_and_both_copies_intact(self):
        self.seed(local="local changes", cloud="cloud changes")
        result = self.run_sync(choices="0\n")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.local.read_text(), "local changes")
        self.assertEqual(self.cloud.read_text(), "cloud changes")
        self.assertTrue(self.conflict.exists())
        self.assertEqual(self.state.read_text().strip(), self.digest("original"))
        self.assert_no_editor()

    def test_help_and_review_choices_cannot_open_editor_in_sync_mode(self):
        self.seed(local="local changes", cloud="cloud changes")
        result = self.run_sync(choices="h\n1\n0\n")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        output = result.stdout + result.stderr
        self.assertIn("Conflict help", output)
        self.assertGreaterEqual(output.count("Synchronization conflict"), 3, output)
        self.assertTrue(self.conflict.exists())
        self.assertEqual(self.cloud.read_text(), "cloud changes")
        self.assert_no_editor()

    def test_cloud_conflict_choice_adopts_cloud_without_opening_editor(self):
        self.seed(local="local changes", cloud="cloud changes")
        result = self.run_sync(choices="2\n")
        self.assert_success(result)
        self.assertEqual(self.local.read_text(), "cloud changes")
        self.assertIn("local changes", self.local_backups())
        self.assertEqual(self.state.read_text().strip(), self.digest("cloud changes"))
        self.assertFalse(self.conflict.exists())
        self.assert_no_editor()

    def test_publish_conflict_choice_keeps_cloud_backup_without_opening_editor(self):
        self.seed(local="local changes", cloud="cloud changes")
        identity = self.cloud_id()
        result = self.run_sync(choices="3\n")
        self.assert_success(result)
        self.assertEqual(self.cloud.read_text(), "local changes")
        self.assertEqual(self.cloud_id(), identity)
        self.assertEqual([p.read_text() for p in self.cloud_backups()], ["cloud changes"])
        self.assertEqual(self.state.read_text().strip(), self.digest("local changes"))
        self.assertFalse(self.conflict.exists())
        self.assert_no_editor()

    def test_completed_interrupted_upload_is_recovered_without_editor(self):
        backup = self.seed_in_place_transaction(published=True)
        result = self.run_sync()
        self.assert_success(result)
        self.assertEqual(backup.read_text(), "original")
        self.assertEqual(self.cloud.read_text(), "local edit")
        self.assertEqual(self.state.read_text().strip(), self.digest("local edit"))
        self.assertFalse(self.transaction.exists())
        self.assert_no_editor()

    def test_sync_uses_the_same_lock_as_editor_sessions(self):
        self.seed(local="local changes")
        lock = self.data / "launcher.lock"
        with lock.open("w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_sync()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertFalse(self.events("rclone"))
        self.assert_no_editor()


class FocusedManagementTests(InstallerCase):
    def prepare_working_database(self):
        self.write_config()
        self.local.parent.mkdir(parents=True)
        self.local.write_text("current local database")
        self.state_dir.mkdir(parents=True)
        self.state.write_text(hashlib.md5(b"cloud database").hexdigest() + "\n")
        identity = Path(str(self.state) + ".id")
        identity.write_text("original-drive-id\n")
        return identity

    def make_backup(self, name="local-20000101T000000Z-Backup12.kdbx"):
        directory = self.state_dir / "backups"
        directory.mkdir(parents=True, exist_ok=True)
        backup = directory / name
        backup.write_text("historical backup database")
        return backup

    def assert_cloud_untouched(self):
        self.assertEqual(self.cloud.read_text(), "cloud database")
        self.assertFalse(self.events("rclone"))

    def test_backup_folder_edit_updates_only_saved_settings(self):
        self.prepare_working_database()
        before = self.state.read_bytes()
        result = self.run_script("--backup-folder", answers="Older vault copies\n")
        self.assert_success(result)
        self.assertEqual(self.config.read_text(),
                         f"{INSTALLER_REMOTE}\n{self.local}\nOlder vault copies\n")
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(self.local.read_text(), "current local database")
        self.assert_cloud_untouched()

    def test_cloud_backup_listing_uses_saved_folder_without_transferring_or_mutating_data(self):
        identity = self.prepare_working_database()
        self.write_config(backup_folder="Vault copies")
        directory = self.cloud.parent / "Vault copies"
        directory.mkdir()
        backup = directory / "kpDB-20261101T000000Z-preserved.kdbx"
        backup.write_text("historical cloud database")
        before = {path: path.read_bytes()
                  for path in (self.config, self.local, self.state, identity, self.cloud, backup)}
        result = self.run_script("--cloud-backups", answers="")
        self.assert_success(result)
        self.assertIn(backup.name, result.stdout)
        self.assertEqual(self.operations(), ["lsf"])
        arguments = self.events("rclone")[0]["arguments"]
        self.assertEqual(arguments[1], "gdrive:KeePass/Vault copies")
        self.assertIn("--files-only", arguments)
        self.assertEqual(arguments[arguments.index("--format") + 1], "stp")
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertFalse(self.events("keepassxc"))

    def test_offline_cloud_backup_listing_fails_without_creating_sync_state(self):
        self.write_config()
        before = self.config.read_bytes()
        result = self.run_script("--cloud-backups", answers="", TEST_FAULT="offline")
        self.assert_failure(result)
        self.assertEqual(self.operations(), ["lsf"])
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(self.state_dir.exists())
        self.assertFalse(self.local.exists())
        self.assertFalse(self.events("keepassxc"))

    def test_repair_refreshes_launcher_and_shortcut_without_changing_selection_or_cloud(self):
        identity = self.prepare_working_database()
        self.write_config(backup_folder="Existing backup folder")
        before = {path: path.read_bytes() for path in (self.config, self.local, self.state, identity)}
        result = self.run_script("--repair", answers="", TEST_FAULT="offline")
        self.assert_success(result)
        self.assertEqual(self.installed.read_bytes(), SCRIPT.read_bytes())
        self.assertIn("--desktop", self.desktop.read_text())
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertFalse(self.events("keepassxc"))
        self.assertFalse(self.events("sudo"))
        self.assert_cloud_untouched()

    def test_dependencies_only_installs_missing_tools_without_configuration_or_database_actions(self):
        self.isolate_path({"rclone", "keepassxc"})
        result = self.run_script("--dependencies", answers="")
        self.assert_success(result)
        self.assertEqual([event["arguments"] for event in self.events("sudo")],
                         [["apt-get", "update"], ["apt-get", "install", "-y", "--", "keepassxc", "rclone"]])
        self.assertTrue((self.binaries / "rclone").exists())
        self.assertTrue((self.binaries / "keepassxc").exists())
        self.assertFalse(self.config.exists())
        self.assertFalse(self.installed.exists())
        self.assertFalse(self.state_dir.exists())
        self.assertFalse(self.local.exists())
        self.assertFalse(self.events("keepassxc"))
        self.assert_cloud_untouched()

    def mock_file_manager(self):
        (self.binaries / "xdg-open").symlink_to(self.mock)

    def test_open_backup_folder_dispatches_exact_path_without_database_or_cloud_actions(self):
        self.prepare_working_database()
        backup = self.make_backup()
        self.mock_file_manager()
        before = {path: path.read_bytes() for path in (self.config, self.local, self.state, backup)}
        result = self.run_script("--open-backups", answers="")
        self.assert_success(result)
        self.assertEqual(self.events(), [{"command": "xdg-open", "arguments": [str(backup.parent)]}])
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assert_cloud_untouched()

    def test_open_backup_folder_refuses_symlink_before_file_manager_dispatch(self):
        self.prepare_working_database()
        self.mock_file_manager()
        outside = self.root / "outside backups"
        outside.mkdir()
        (self.state_dir / "backups").symlink_to(outside, target_is_directory=True)
        result = self.run_script("--open-backups", answers="")
        self.assert_failure(result)
        self.assertFalse(self.events())
        self.assert_cloud_untouched()

    def test_invalid_backup_folder_preserves_saved_settings(self):
        self.prepare_working_database()
        before = self.config.read_bytes()
        for folder in ("../copies", "kpDB.kdbx", "nested/copies"):
            with self.subTest(folder=folder):
                result = self.run_script("--backup-folder", answers=folder + "\n")
                self.assert_failure(result)
                self.assertEqual(self.config.read_bytes(), before)
                self.assert_cloud_untouched()

    def test_move_local_preserves_sync_history_and_updates_config_after_verified_copy(self):
        identity = self.prepare_working_database()
        state_before, identity_before = self.state.read_bytes(), identity.read_bytes()
        target = self.home / "another directory/moved vault.kdbx"
        result = self.run_script("--move-local", answers=f"{target}\ny\n")
        self.assert_success(result)
        self.assertEqual(target.read_text(), "current local database")
        self.assertFalse(self.local.exists())
        self.assertEqual(self.config.read_text(),
                         f"{INSTALLER_REMOTE}\n{target}\nKeePass-Backups\n")
        self.assertEqual(self.state.read_bytes(), state_before)
        self.assertEqual(identity.read_bytes(), identity_before)
        self.assertFalse(list(target.parent.glob(".keesyncxc-move.*")))
        self.assert_cloud_untouched()

    def test_move_refuses_existing_destination_without_changing_either_file(self):
        self.prepare_working_database()
        target = self.home / "occupied.kdbx"
        target.write_text("another database")
        before = self.config.read_bytes()
        result = self.run_script("--move-local", answers=f"{target}\ny\n")
        self.assert_failure(result)
        self.assertEqual(target.read_text(), "another database")
        self.assertEqual(self.local.read_text(), "current local database")
        self.assertEqual(self.config.read_bytes(), before)
        self.assert_cloud_untouched()

    def test_move_copy_failure_keeps_source_and_original_settings(self):
        self.prepare_working_database()
        (self.binaries / "cp").symlink_to(self.mock)
        before = self.config.read_bytes()
        target = self.home / "destination.kdbx"
        result = self.run_script("--move-local", answers=f"{target}\ny\n",
                                 TEST_FAULT="local_backup")
        self.assert_failure(result)
        self.assertEqual(self.local.read_text(), "current local database")
        self.assertFalse(target.exists())
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(list(target.parent.glob(".keesyncxc-move.*")))
        self.assert_cloud_untouched()

    def test_restore_preserves_current_copy_and_retains_baseline_for_next_sync(self):
        identity = self.prepare_working_database()
        backup = self.make_backup()
        before = {path: path.read_bytes() for path in (self.config, self.state, identity)}
        result = self.run_script("--restore-backup", answers="1\ny\n")
        self.assert_success(result)
        self.assertEqual(self.local.read_text(), "historical backup database")
        self.assertEqual(backup.read_text(), "historical backup database")
        copies = [p.read_text() for p in backup.parent.glob("local-*.kdbx") if p != backup]
        self.assertEqual(copies, ["current local database"])
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertFalse(Path(str(self.state) + ".conflict").exists())
        self.assertFalse(self.events("keepassxc"))
        self.assert_cloud_untouched()

    def test_restore_cancellation_keeps_current_database_and_existing_backup(self):
        self.prepare_working_database()
        backup = self.make_backup()
        result = self.run_script("--restore-backup", answers="1\nn\n")
        self.assertIn("Cancelled", result.stdout + result.stderr)
        self.assertEqual(self.local.read_text(), "current local database")
        self.assertEqual(list(backup.parent.iterdir()), [backup])
        self.assert_cloud_untouched()

    def test_restore_requires_a_confirmed_baseline(self):
        self.prepare_working_database()
        self.make_backup()
        self.state.unlink()
        result = self.run_script("--restore-backup", answers="1\ny\n")
        self.assert_failure(result)
        self.assertEqual(self.local.read_text(), "current local database")
        self.assertFalse(self.state.exists())
        self.assert_cloud_untouched()

    def test_settings_changes_and_restore_refuse_pending_recovery(self):
        self.prepare_working_database()
        self.make_backup()
        before = self.config.read_bytes()
        for suffix in (".conflict", ".transaction"):
            pending = Path(str(self.state) + suffix)
            pending.write_text("unresolved recovery record\n")
            for argument, answers in (("--backup-folder", "Other copies\n"),
                                      ("--move-local", str(self.home / "target.kdbx") + "\ny\n"),
                                      ("--restore-backup", "1\ny\n")):
                with self.subTest(argument=argument, suffix=suffix):
                    result = self.run_script(argument, answers=answers)
                    self.assert_failure(result)
                    self.assertEqual(self.local.read_text(), "current local database")
                    self.assertEqual(self.config.read_bytes(), before)
                    self.assertEqual(pending.read_text(), "unresolved recovery record\n")
                    self.assert_cloud_untouched()
            pending.unlink()

    def test_backup_inspection_opens_selected_copy_without_synchronization(self):
        self.prepare_working_database()
        backup = self.make_backup()
        baseline = self.state.read_bytes()
        result = self.run_script("--inspect-backup", answers="1\n",
                                 TEST_EDITS='["changes in inspected backup"]')
        self.assert_success(result)
        self.assertEqual(self.events("keepassxc")[0]["arguments"], [str(backup)])
        self.assertEqual(backup.read_text(), "changes in inspected backup")
        self.assertEqual(self.local.read_text(), "current local database")
        self.assertEqual(self.state.read_bytes(), baseline)
        self.assert_cloud_untouched()

    def test_inventory_shows_recovery_archives_without_following_symlinks_or_printing_settings(self):
        self.prepare_working_database()
        backup = self.make_backup()
        archive = backup.parent / "reset-Archive1"
        archive.mkdir()
        (archive / "config").write_text("private archived configuration\n")
        (archive / self.state.name).write_text("private archived sync state\n")
        outside = self.root / "outside-archive"
        outside.mkdir()
        (outside / "config").write_text("private outside configuration\n")
        symlink = backup.parent / "reset-Symlink1"
        symlink.symlink_to(outside, target_is_directory=True)
        result = self.run_script("--backups", answers="")
        self.assert_success(result)
        output = result.stdout + result.stderr
        self.assertIn(str(archive), output)
        self.assertIn(str(backup), output)
        self.assertIn("bytes", output)
        self.assertIn("local safety copy", output)
        self.assertNotIn(str(symlink), output)
        self.assertNotIn("private archived", output)
        self.assertNotIn("private outside", output)
        self.assert_cloud_untouched()


if __name__ == "__main__":
    unittest.main()
