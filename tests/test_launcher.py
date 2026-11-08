"""Regression tests using isolated homes and fake KeePassXC/rclone programs.

Run with: python3 -m unittest discover -s tests -v
No real database, graphical application, or cloud remote is accessed.
"""

from pathlib import Path
import unittest

from support import LauncherCase, LAUNCHER_REMOTE as REMOTE


class LauncherTests(LauncherCase):
    def test_initial_download(self):
        self.cloud.write_text("cloud database")
        result = self.run_launcher()
        self.assert_success(result)
        self.assertEqual(self.local.read_bytes(), self.cloud.read_bytes())
        self.assertEqual(self.state.read_text().strip(), self.digest("cloud database"))

    def test_matching_local_filename_with_backslash_stays_synchronized(self):
        self.local = self.local.with_name(r"Pass\words.kdbx")
        config = self.root / "config/keesyncxc/config"
        config.write_text(f"{REMOTE}\n{self.local}\nKeePass-Backups\n")
        self.seed()
        result = self.run_launcher()
        self.assert_success(result)
        self.assertEqual(self.local.read_text(), "original")
        self.assertEqual(self.state.read_text(), self.digest("original") + "\n")
        self.assertFalse(self.conflict.exists())
        self.assertFalse(self.remote_operations("copyto"))

    def test_download_and_edit_with_backslash_in_local_directory(self):
        self.local = self.local.parent / r"vault\folder" / self.local.name
        config = self.root / "config/keesyncxc/config"
        config.write_text(f"{REMOTE}\n{self.local}\nKeePass-Backups\n")
        self.cloud.write_text("cloud database")
        result = self.run_launcher(edit="local edit")
        self.assert_success(result)
        self.assertEqual(self.local.read_text(), "local edit")
        self.assertEqual(self.cloud.read_text(), "local edit")
        self.assertEqual(self.state.read_text(), self.digest("local edit") + "\n")
        self.assertEqual([file.read_text() for file in self.cloud_backups()], ["cloud database"])
        self.assertFalse(self.transaction.exists())

    def test_upload_with_backslash_in_state_directory(self):
        state_home = self.root / r"state\storage"
        self.env["XDG_STATE_HOME"] = str(state_home)
        self.data = state_home / "keesyncxc"
        self.data.mkdir(parents=True)
        self.state = self.data / self.state.name
        self.transaction = Path(str(self.state) + ".transaction")
        self.conflict = Path(str(self.state) + ".conflict")
        self.seed(local="local edit")
        result = self.run_launcher()
        self.assert_success(result)
        self.assertEqual(self.cloud.read_text(), "local edit")
        self.assertEqual(self.state.read_text(), self.digest("local edit") + "\n")
        self.assertEqual([file.read_text() for file in self.cloud_backups()], ["original"])
        self.assertFalse(self.transaction.exists())

    def test_remote_update_is_downloaded_and_local_backed_up(self):
        self.seed(cloud="cloud edit")
        result = self.run_launcher()
        self.assert_success(result)
        self.assertEqual(self.local.read_text(), "cloud edit")
        self.assertEqual(self.local_backups(), ["original"])

    def test_local_edit_updates_existing_drive_file_and_copies_cloud_backup(self):
        self.seed(local="local edit")
        original_id = self.cloud_id()
        result = self.run_launcher()
        self.assert_success(result)
        self.assertEqual(self.cloud.read_text(), "local edit")
        self.assertEqual([file.read_text() for file in self.cloud_backups()], ["original"])
        uploads = [args for args in self.remote_operations("copyto")
                   if not args[1].startswith("gdrive:")]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0][2], self.env["TEST_REMOTE"])
        self.assertNotEqual(uploads[0][1], str(self.local), "Upload must use a stable snapshot")
        self.assertFalse(self.remote_operations("moveto"))
        self.assertEqual(self.cloud_id(), original_id)
        backup = self.cloud_backups()[0]
        self.assertEqual(backup.parent, self.cloud.parent / "KeePass-Backups")
        self.assertFalse(list(self.cloud.parent.glob("Passwords-*.kdbx")))
        self.assertEqual(self.remote_operations("mkdir"),
                         [["mkdir", "gdrive:KeeSyncXC/KeePass-Backups", "--retries", "2"]])
        operations = [event["arguments"][0] for event in self.events("rclone")]
        self.assertLess(operations.index("mkdir"), operations.index("copyto"))
        self.assertNotEqual(backup.stat().st_ino, self.cloud.stat().st_ino)
        self.assertIn("--ignore-times", uploads[0])
        self.assertIn("--ignore-checksum", uploads[0])
        self.assertIn("--ignore-size", uploads[0])
        self.assertEqual(Path(str(self.state) + ".id").read_text().strip(), original_id)
        self.assertEqual(self.state.read_text().strip(), self.digest("local edit"))
        self.assertFalse(self.transaction.exists())

    def test_download_failures_preserve_local_and_baseline(self):
        for fault in ("download_temp", "download", "corrupt_download", "download_hash",
                      "local_backup", "replace_local", "local_hash"):
            with self.subTest(fault=fault):
                self.seed(cloud="new cloud database")
                baseline = self.state.read_text()
                result = self.run_launcher(fault)
                self.assert_blocked(result)
                self.assertEqual(self.local.read_text(), "original")
                self.assertEqual(self.cloud.read_text(), "new cloud database")
                self.assertEqual(self.state.read_text(), baseline)
                self.assertFalse(list(self.local.parent.glob(".keesyncxc-download.*")))

    def test_state_failures_preserve_baseline_and_stop_launch(self):
        for fault in ("state_temp", "replace_state"):
            with self.subTest(fault=fault):
                self.seed(cloud="cloud edit")
                baseline = self.state.read_text()
                result = self.run_launcher(fault)
                self.assert_blocked(result)
                self.assertEqual(self.state.read_text(), baseline)
                self.assertEqual(self.local.read_text(), "cloud edit")
                self.assertFalse(list(self.data.glob(".sync-state.*")))

    def test_initial_download_recovers_after_state_write_failure(self):
        self.cloud.write_text("cloud database")
        self.assert_blocked(self.run_launcher("replace_state"))
        self.assertFalse(self.state.exists())
        self.assert_success(self.run_launcher())
        self.assertEqual(self.state.read_text().strip(), self.digest("cloud database"))

    def test_initial_setup_refuses_a_different_existing_local_database(self):
        self.local.write_text("unsynchronized local database")
        self.cloud.write_text("cloud database")
        self.assert_blocked(self.run_launcher())
        self.assertEqual(self.local.read_text(), "unsynchronized local database")
        self.assertEqual(self.cloud.read_text(), "cloud database")
        self.assertFalse(self.state.exists())

    def assert_failed_transfer_preserves_versions(self, fault):
        self.seed(local="local edit")
        baseline = self.state.read_text()
        result = self.run_launcher(fault)
        self.assert_blocked(result)
        self.assertEqual(self.local.read_text(), "local edit")
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertEqual(self.state.read_text(), baseline)


    def test_failed_upload_preserves_both_versions(self):
        self.assert_failed_transfer_preserves_versions("upload")

    def test_failed_backup_copy_preserves_both_versions(self):
        self.assert_failed_transfer_preserves_versions("remote_backup")

    def test_failed_backup_directory_creation_never_uploads_or_writes_a_transaction(self):
        self.seed(local="local edit")
        original_id = self.cloud_id()
        baseline = self.state.read_text()
        self.assert_blocked(self.run_launcher("backup_mkdir"))
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertEqual(self.cloud_id(), original_id)
        self.assertEqual(self.local.read_text(), "local edit")
        self.assertEqual(self.state.read_text(), baseline)
        self.assertFalse(self.transaction.exists())
        self.assertFalse(self.remote_operations("copyto"))
        self.assertFalse(self.remote_operations("moveto"))
        self.assertFalse((self.cloud.parent / "KeePass-Backups").exists())

    def test_unchanged_session_does_not_create_remote_backup_folder(self):
        self.seed()
        self.assert_success(self.run_launcher())
        self.assertFalse(self.remote_operations("mkdir"))
        self.assertFalse((self.cloud.parent / "KeePass-Backups").exists())

    def test_remote_change_before_upload_does_not_write_cloud(self):
        self.seed(local="local edit")
        result = self.run_launcher("changed_remote", choices="4\n")
        self.assert_blocked(result)
        self.assertEqual(self.cloud.read_text(), "other device edit")
        self.assertEqual(self.local.read_text(), "local edit")
        self.assertFalse(self.remote_operations("copyto"))
        self.assertFalse(self.remote_operations("moveto"))

    def test_remote_change_after_backup_is_detected_before_updating_canonical(self):
        self.seed(local="local edit")
        baseline = self.state.read_text()
        result = self.run_launcher("cloud_after_backup", choices="4\n")
        self.assert_blocked(result)
        self.assertEqual(self.cloud.read_text(), "other device edit")
        self.assertEqual(self.state.read_text(), baseline)
        self.assertFalse(self.remote_operations("moveto"))
        self.assertEqual([file.read_text() for file in self.cloud_backups()], ["original"])
        updates = [args for args in self.remote_operations("copyto")
                   if args[2] == self.env["TEST_REMOTE"]]
        self.assertFalse(updates)

    def test_corrupt_backup_prevents_cloud_update(self):
        self.seed(local="local edit")
        result = self.run_launcher("corrupt_backup")
        self.assert_blocked(result)
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertEqual(self.state.read_text().strip(), self.digest("original"))
        self.assertFalse(self.remote_operations("moveto"))

    def test_conflict_cancel_preserves_both_versions_without_upload(self):
        self.seed(local="local edit", cloud="cloud edit")
        result = self.run_launcher(choices="4\n")
        self.assert_blocked(result)
        self.assertEqual(self.local.read_text(), "local edit")
        self.assertEqual(self.cloud.read_text(), "cloud edit")
        self.assertFalse(self.remote_operations("copyto"))
        self.assertFalse(self.remote_operations("moveto"))

    def test_conflict_without_input_exits_safely(self):
        self.seed(local="local edit", cloud="cloud edit")
        self.assert_blocked(self.run_launcher())
        self.assertEqual(self.local.read_text(), "local edit")
        self.assertEqual(self.cloud.read_text(), "cloud edit")
        self.assertFalse(self.remote_operations("moveto"))

    def test_cancelled_conflict_requires_explicit_resolution_on_restart(self):
        self.seed(local="local edit", cloud="cloud edit")
        self.assert_blocked(self.run_launcher(choices="4\n"))
        self.assertTrue(self.conflict.exists())
        # A later cloud revision matching the old baseline must not silently
        # authorize uploading a local copy that was already in conflict.
        self.cloud.write_text("original")
        self.assert_blocked(self.run_launcher())
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertEqual(self.local.read_text(), "local edit")
        self.assertFalse(self.remote_operations("moveto"))

    def test_conflict_backup_failure_still_requires_resolution_on_restart(self):
        self.seed(local="local edit", cloud="cloud edit")
        self.assert_blocked(self.run_launcher("local_backup"))
        self.assertTrue(self.conflict.exists())
        self.assertEqual(self.local.read_text(), "local edit")
        self.assertEqual(self.cloud.read_text(), "cloud edit")
        self.cloud.write_text("original")
        self.assert_blocked(self.run_launcher())
        self.assertTrue(self.conflict.exists())
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertEqual(self.local.read_text(), "local edit")
        self.assertEqual(self.state.read_text().strip(), self.digest("original"))
        self.assertFalse(self.remote_operations("copyto"))
        self.assertFalse(self.remote_operations("moveto"))

    def test_reference_edits_return_to_menu_without_automatic_upload(self):
        self.seed(local="local edit", cloud="cloud edit")
        result = self.run_launcher(choices="1\n4\n", edit="revised local edit")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.events("keepassxc")), 1)
        self.assertEqual(self.local.read_text(), "revised local edit")
        self.assertEqual(self.cloud.read_text(), "cloud edit")
        self.assertEqual(self.state.read_text().strip(), self.digest("original"))
        self.assertFalse(self.remote_operations("copyto"))
        self.assertFalse(self.remote_operations("moveto"))

    def test_reference_edits_can_be_explicitly_published_after_close(self):
        self.seed(local="local edit", cloud="cloud edit")
        result = self.run_launcher(choices="1\n3\n", edit="revised local edit")
        self.assert_success(result)
        self.assertEqual(len(self.events("keepassxc")), 1)
        self.assertEqual(self.cloud.read_text(), "revised local edit")
        self.assertEqual(self.local.read_text(), "revised local edit")
        self.assertEqual([file.read_text() for file in self.cloud_backups()], ["cloud edit"])
        self.assertEqual(self.state.read_text().strip(), self.digest("revised local edit"))

    def test_post_close_conflict_allows_reference_edit_and_explicit_overwrite(self):
        self.seed()
        result = self.run_launcher(
            choices="1\n3\n", edits=["session edit", "reviewed session edit"],
            TEST_APP_CLOUD_EDITS='["edit from another device"]')
        self.assert_success(result)
        self.assertEqual([event["contents"] for event in self.events("keepassxc")],
                         ["original", "session edit"])
        self.assertEqual(self.local.read_text(), "reviewed session edit")
        self.assertEqual(self.cloud.read_text(), "reviewed session edit")
        self.assertEqual([file.read_text() for file in self.cloud_backups()],
                         ["edit from another device"])

    def test_explicit_overwrite_still_rejects_a_further_cloud_change(self):
        self.seed(local="local edit", cloud="cloud edit")
        result = self.run_launcher("cloud_after_backup", choices="3\n4\n")
        self.assert_blocked(result)
        self.assertEqual(self.cloud.read_text(), "other device edit")
        self.assertFalse(self.remote_operations("moveto"))
        self.assertEqual(self.state.read_text().strip(), self.digest("original"))

    def test_open_cloud_preserves_local_then_syncs_edits_against_new_baseline(self):
        self.seed(local="local edit", cloud="cloud edit")
        result = self.run_launcher(choices="2\n", edit="cloud with reapplied change")
        self.assert_success(result)
        self.assertEqual([event["contents"] for event in self.events("keepassxc")], ["cloud edit"])
        self.assertIn("local edit", self.local_backups())
        self.assertEqual(self.local.read_text(), "cloud with reapplied change")
        self.assertEqual(self.cloud.read_text(), "cloud with reapplied change")
        self.assertEqual(self.state.read_text().strip(), self.digest("cloud with reapplied change"))

    def test_failed_cloud_adoption_returns_to_menu_preserving_local(self):
        self.seed(local="local edit", cloud="cloud edit")
        result = self.run_launcher("download", choices="2\n4\n")
        self.assert_blocked(result)
        self.assertEqual(self.local.read_text(), "local edit")
        self.assertEqual(self.cloud.read_text(), "cloud edit")
        self.assertEqual(self.state.read_text().strip(), self.digest("original"))

    def test_graphical_conflict_menu_can_publish_current_local_version(self):
        self.seed(local="local edit", cloud="cloud edit")
        result = self.run_launcher(DISPLAY=":test", TEST_GUI_CHOICES='["3"]')
        self.assert_success(result)
        self.assertTrue(self.events("zenity"))
        self.assertEqual(self.cloud.read_text(), "local edit")
        self.assertFalse(self.events("keepassxc"))

    def test_graphical_conflict_menu_cancel_is_safe(self):
        self.seed(local="local edit", cloud="cloud edit")
        result = self.run_launcher(DISPLAY=":test", TEST_GUI_CHOICES="[]")
        self.assert_blocked(result)
        self.assertTrue(self.events("zenity"))
        self.assertEqual(self.local.read_text(), "local edit")
        self.assertEqual(self.cloud.read_text(), "cloud edit")
        self.assertFalse(self.remote_operations("moveto"))

    def assert_information_preserved_conflict(self, result, marker):
        self.assert_blocked(result)
        self.assertEqual(self.local.read_text(), "local edit")
        self.assertEqual(self.cloud.read_text(), "cloud edit")
        self.assertEqual(self.state.read_text().strip(), self.digest("original"))
        self.assertEqual(self.conflict.read_text(), marker)
        self.assertFalse(self.local_backups())
        self.assertFalse(self.cloud_backups())
        self.assertFalse(self.events("rclone"))


    def assert_information_locations(self, information):
        self.assertIn(str(self.local), information)
        self.assertIn(REMOTE, information)
        self.assertIn(str(self.data / "backups"), information)
        self.assertIn(REMOTE.rsplit("/", 1)[0] + "/KeePass-Backups", information)
        self.assertIn("local-", information)
        self.assertIn("upload-", information)
        self.assertIn("Passwords-", information)


    def test_terminal_more_information_explains_locations_and_returns_to_menu(self):
        self.seed(local="local edit", cloud="cloud edit")
        marker = "An unresolved conflict is waiting for a decision.\n"
        self.conflict.write_text(marker)
        result = self.run_launcher(choices="5\n4\n")
        self.assert_information_preserved_conflict(result, marker)
        information = result.stdout + result.stderr
        self.assert_information_locations(information)
        self.assertGreaterEqual(information.count("H) More information"), 2)
        self.assertFalse(self.events("zenity"))

    def test_graphical_more_information_close_returns_to_menu_without_changes(self):
        self.seed(local="local edit", cloud="cloud edit")
        marker = "An unresolved conflict is waiting for a decision.\n"
        self.conflict.write_text(marker)
        result = self.run_launcher(DISPLAY=":test", TEST_GUI_CHOICES='["5", "4"]',
                                   TEST_GUI_INFO_STATUS="1")
        self.assert_information_preserved_conflict(result, marker)
        dialogs = self.events("zenity")
        self.assertEqual(len(dialogs), 3)
        self.assertIn("--list", dialogs[0]["arguments"])
        self.assertIn("--text-info", dialogs[1]["arguments"])
        self.assertIn("--list", dialogs[2]["arguments"])
        self.assertTrue(any("More information" in argument
                            for argument in dialogs[0]["arguments"]))
        self.assert_information_locations(dialogs[1]["input"])

    def test_post_update_state_failure_recovers_on_next_launch(self):
        self.seed(local="local edit")
        original_id = self.cloud_id()
        self.assert_blocked(self.run_launcher("replace_state"))
        self.assertEqual(self.cloud.read_text(), "local edit")
        self.assertTrue(self.transaction.exists())
        uploads_before = len(self.remote_operations("copyto"))
        self.assert_success(self.run_launcher())
        self.assertEqual(self.state.read_text().strip(), self.digest("local edit"))
        self.assertFalse(self.transaction.exists())
        self.assertEqual(self.cloud_id(), original_id)
        self.assertEqual(len(self.remote_operations("copyto")), uploads_before,
                         "Completed update should recover without another transfer")

    def test_failed_update_leaves_canonical_and_journal_for_explicit_retry(self):
        self.seed(local="local edit")
        original_id = self.cloud_id()
        self.assert_blocked(self.run_launcher("update"))
        journal = self.transaction.read_text().splitlines()
        self.assertEqual(journal[:4], ["in-place-v2", self.digest("original"),
                                      self.digest("local edit"), original_id])
        self.assertEqual(len(journal), 6)
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertEqual(self.cloud_id(), original_id)

        self.assertEqual(Path(journal[5]).read_text(), "local edit")
        copies_before = len(self.remote_operations("copyto"))
        self.assert_blocked(self.run_launcher(choices="4\n"))
        self.assertFalse(self.transaction.exists())
        self.assertTrue(self.conflict.exists())
        self.assertEqual(len(self.remote_operations("copyto")), copies_before)
        self.assert_success(self.run_launcher(choices="3\n"))
        self.assertEqual(self.cloud.read_text(), "local edit")
        self.assertEqual(self.cloud_id(), original_id)

    def test_environment_backup_options_cannot_move_the_canonical_file(self):
        self.seed(local="local edit")
        original_id = self.cloud_id()
        result = self.run_launcher(RCLONE_BACKUP_DIR="gdrive:Backups", RCLONE_SUFFIX="-old")
        self.assert_success(result)
        self.assertEqual(self.cloud_id(), original_id)
        self.assertEqual(self.cloud.read_text(), "local edit")
        self.assertFalse((self.cloud.parent / "environment-backup.kdbx").exists())

    def test_lost_update_response_recovers_without_another_upload(self):
        self.seed(local="local edit")
        original_id = self.cloud_id()
        self.assert_blocked(self.run_launcher("update_response_lost"))
        self.assertEqual(self.cloud.read_text(), "local edit")
        self.assertTrue(self.transaction.exists())
        copies_before = len(self.remote_operations("copyto"))
        self.assert_success(self.run_launcher())
        self.assertEqual(self.cloud_id(), original_id)
        self.assertFalse(self.transaction.exists())
        self.assertEqual(len(self.remote_operations("copyto")), copies_before)

    def test_recovery_does_not_confirm_publication_without_the_recorded_backup(self):
        self.seed(local="local edit")
        self.assert_blocked(self.run_launcher("update_response_lost"))
        backup = self.cloud_backups()[0]
        backup.unlink()
        backup.parent.rmdir()
        copies_before = len(self.remote_operations("copyto"))
        mkdirs_before = len(self.remote_operations("mkdir"))
        self.assert_blocked(self.run_launcher(choices="4\n"))
        self.assertEqual(self.cloud.read_text(), "local edit")
        self.assertEqual(self.local.read_text(), "local edit")
        self.assertEqual(self.state.read_text().strip(), self.digest("original"))
        self.assertEqual(len(self.remote_operations("copyto")), copies_before)
        self.assertEqual(len(self.remote_operations("mkdir")), mkdirs_before)
        self.assertFalse(backup.parent.exists())

    def test_corrupt_update_preserves_snapshot_backup_and_requires_decision(self):
        self.seed(local="local edit")
        self.assert_blocked(self.run_launcher("corrupt_update"))
        self.assertEqual(self.cloud.read_text(), "corrupted update")
        self.assertTrue(self.transaction.exists())
        copies_before = len(self.remote_operations("copyto"))
        self.assert_blocked(self.run_launcher(choices="4\n"))
        self.assertEqual(self.cloud.read_text(), "corrupted update")
        self.assertEqual(self.local.read_text(), "local edit")
        self.assertEqual([file.read_text() for file in self.cloud_backups()], ["original"])
        self.assertEqual(self.state.read_text().strip(), self.digest("original"))
        self.assertTrue(self.conflict.exists())
        self.assertFalse(self.transaction.exists())
        self.assertEqual(len(self.remote_operations("copyto")), copies_before)

    def test_unexpected_new_id_after_update_retains_journal_and_stops_recovery(self):
        self.seed(local="local edit")
        original_id = self.cloud_id()
        self.assert_blocked(self.run_launcher("update_replaces_id"))
        self.assertEqual(self.cloud.read_text(), "local edit")
        self.assertNotEqual(self.cloud_id(), original_id)
        journal = self.transaction.read_text()
        copies_before = len(self.remote_operations("copyto"))
        self.assert_blocked(self.run_launcher())
        self.assertEqual(self.transaction.read_text(), journal)
        self.assertEqual(self.state.read_text().strip(), self.digest("original"))
        self.assertEqual(len(self.remote_operations("copyto")), copies_before)
        self.assertFalse(self.remote_operations("moveto"))

    def test_missing_canonical_after_update_failure_is_never_recreated(self):
        self.seed(local="local edit")
        self.assert_blocked(self.run_launcher("delete_during_update"))
        self.assertFalse(self.cloud.exists())
        journal = self.transaction.read_text()
        copies_before = len(self.remote_operations("copyto"))
        self.assert_blocked(self.run_launcher())
        self.assertFalse(self.cloud.exists())
        self.assertEqual(self.transaction.read_text(), journal)
        self.assertEqual(self.local.read_text(), "local edit")
        self.assertEqual([file.read_text() for file in self.cloud_backups()], ["original"])
        self.assertEqual(len(self.remote_operations("copyto")), copies_before)
        self.assertFalse(self.remote_operations("moveto"))

    def test_pinned_identity_rejects_replacement_even_with_identical_bytes(self):
        self.seed()
        identity = Path(str(self.state) + ".id")
        original_id = self.cloud_id()
        identity.write_text(original_id + "\n")
        replacement = self.cloud.with_name("replacement.kdbx")
        replacement.write_bytes(self.cloud.read_bytes())
        replacement.replace(self.cloud)
        self.assert_blocked(self.run_launcher())
        self.assertEqual(identity.read_text(), original_id + "\n")
        self.assertFalse(self.remote_operations("copyto"))

    def test_conflict_cloud_adoption_cannot_bypass_a_known_identity_change(self):
        self.seed(local="local edit", cloud="cloud edit")
        original_id = self.cloud_id()
        identity = Path(str(self.state) + ".id")
        identity.write_text(original_id + "\n")
        marker = "Both versions need an explicit decision.\n"
        self.conflict.write_text(marker)
        replacement = self.cloud.with_name("replacement.kdbx")
        replacement.write_bytes(self.cloud.read_bytes())
        replacement.replace(self.cloud)
        self.assert_blocked(self.run_launcher(choices="2\n4\n"))
        self.assertEqual(self.local.read_text(), "local edit")
        self.assertEqual(self.cloud.read_text(), "cloud edit")
        self.assertEqual(self.state.read_text().strip(), self.digest("original"))
        self.assertEqual(identity.read_text(), original_id + "\n")
        self.assertEqual(self.conflict.read_text(), marker)
        self.assertFalse(self.remote_operations("copyto"))
        self.assertFalse(self.local_backups())

    def test_ambiguous_metadata_is_rejected_before_transfers(self):
        self.seed(local="local edit")
        self.assert_blocked(self.run_launcher("metadata_duplicate"))
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertFalse(self.remote_operations("copyto"))

    def test_shortcut_style_identity_is_rejected_before_transfers(self):
        self.seed(local="local edit")
        self.assert_blocked(self.run_launcher("metadata_bad_id"))
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertFalse(self.remote_operations("copyto"))

    def test_unsupported_transaction_records_are_preserved_and_not_executed(self):
        self.seed(local="local edit")
        name = "Passwords-20261101T000000Z-12345678-1234-1234-1234-123456789abc.kdbx"
        prefix = self.env["TEST_REMOTE"].rsplit("/", 1)[0] + "/"
        fields = (self.digest("original"), self.digest("local edit"), self.cloud_id(),
                  prefix + "KeePass-Backups/" + name, str(self.data / "backups/upload-retained.kdbx"))
        records = (
            "\n".join(("future-v999", *fields)) + "\n",
            "\n".join(("in-place-v1", *fields[:3], prefix + name, fields[4])) + "\n",
            "\n".join((fields[0], fields[1], prefix + ".keesyncxc-pending-12345678-1234-1234-1234-123456789abc.kdbx",
                       prefix + name, fields[4])) + "\n",
        )
        for contents in records:
            with self.subTest(contents=contents):
                self.transaction.write_text(contents)
                self.assert_blocked(self.run_launcher())
                self.assertEqual(self.transaction.read_text(), contents)
                self.assertFalse(self.events("rclone"))

    def test_completed_update_recovers_recorded_backup_without_transfer(self):
        backup = self.seed_in_place_transaction(published=True)
        original_id = self.cloud_id()
        self.assert_success(self.run_launcher())
        self.assertEqual(backup.parent, self.cloud.parent / "KeePass-Backups")
        self.assertEqual(backup.read_text(), "original")
        self.assertEqual(self.cloud.read_text(), "local edit")
        self.assertEqual(self.cloud_id(), original_id)
        self.assertEqual(self.state.read_text().strip(), self.digest("local edit"))
        self.assertFalse(self.transaction.exists())
        self.assertFalse(self.remote_operations("mkdir"))
        self.assertFalse(self.remote_operations("copyto"))
        self.assertFalse(self.remote_operations("moveto"))

    def test_unfinished_update_retains_backup_and_requires_decision(self):
        backup = self.seed_in_place_transaction()
        original_id = self.cloud_id()
        self.assert_blocked(self.run_launcher(choices="4\n"))
        self.assertEqual(backup.parent, self.cloud.parent / "KeePass-Backups")
        self.assertEqual(backup.read_text(), "original")
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertEqual(self.cloud_id(), original_id)
        self.assertEqual(self.local.read_text(), "local edit")
        self.assertFalse(self.transaction.exists())
        self.assertTrue(self.conflict.exists())
        self.assertFalse(self.remote_operations("mkdir"))
        self.assertFalse(self.remote_operations("copyto"))
        self.assertFalse(self.remote_operations("moveto"))

    def test_recovery_rejects_wrong_backup_folder_and_foreign_remote(self):
        self.seed(local="local edit")
        snapshots = self.data / "backups"
        snapshots.mkdir()
        snapshot = snapshots / "upload-retained.kdbx"
        snapshot.write_text("local edit")
        prefix = self.env["TEST_REMOTE"].rsplit("/", 1)[0] + "/"
        name = "Passwords-20261101T000000Z-12345678-1234-1234-1234-123456789abc.kdbx"
        for backup in (prefix + name,
                       prefix + "Other-Backups/" + name,
                       "archive:KeeSyncXC/KeePass-Backups/" + name):
            with self.subTest(backup=backup):
                journal = "\n".join(("in-place-v2", self.digest("original"), self.digest("local edit"),
                                     self.cloud_id(), backup, str(snapshot))) + "\n"
                self.transaction.write_text(journal)
                self.assert_blocked(self.run_launcher())
                self.assertEqual(self.transaction.read_text(), journal)
                self.assertEqual(self.cloud.read_text(), "original")
                self.assertEqual(self.local.read_text(), "local edit")
                self.assertFalse(self.events("rclone"))

    def test_malformed_recovery_journal_is_not_executed_or_discarded(self):
        self.seed(local="local edit")
        sentinel = self.root / "executed-journal"
        contents = f"in-place-v2\n$(touch {sentinel})\ninvalid\ninvalid\ninvalid\ninvalid\n"
        self.transaction.write_text(contents)
        self.assert_blocked(self.run_launcher())
        self.assertFalse(sentinel.exists())
        self.assertEqual(self.transaction.read_text(), contents)
        self.assertEqual(self.local.read_text(), "local edit")
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertFalse(self.remote_operations("moveto"))

    def test_recovery_keeps_unexpected_contents_in_the_canonical_file(self):
        self.seed_in_place_transaction()
        self.cloud.write_text("unexpected replacement")
        result = self.run_launcher(choices="4\n")
        self.assert_blocked(result)
        self.assertEqual(self.cloud.read_text(), "unexpected replacement")
        self.assertEqual(self.local.read_text(), "local edit")
        self.assertEqual([file.read_text() for file in self.cloud_backups()], ["original"])
        self.assertEqual(self.state.read_text().strip(), self.digest("original"))
        self.assertFalse(self.remote_operations("moveto"))
        self.assertTrue(self.conflict.exists())
        self.assertFalse(self.transaction.exists())

    def test_cloud_change_before_backup_copy_is_preserved_and_detected(self):
        self.seed(local="local edit")
        result = self.run_launcher("cloud_before_backup", choices="4\n")
        self.assert_blocked(result)
        self.assertEqual(self.cloud.read_text(), "edit just before backup copy")
        self.assertEqual(self.local.read_text(), "local edit")
        self.assertEqual(self.state.read_text().strip(), self.digest("original"))
        self.assertFalse(self.remote_operations("moveto"))
        self.assertEqual([file.read_text() for file in self.cloud_backups()],
                         ["edit just before backup copy"])

    def test_same_bytes_with_new_identity_after_backup_are_not_updated(self):
        self.seed(local="local edit")
        original_id = self.cloud_id()
        result = self.run_launcher("identity_after_backup", choices="4\n")
        self.assert_blocked(result)
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertNotEqual(self.cloud_id(), original_id)
        self.assertEqual(self.local.read_text(), "local edit")
        self.assertEqual(self.state.read_text().strip(), self.digest("original"))
        self.assertEqual([file.read_text() for file in self.cloud_backups()], ["original"])
        self.assertFalse([args for args in self.remote_operations("copyto")
                          if args[2] == self.env["TEST_REMOTE"]])
        self.assertFalse(self.remote_operations("moveto"))

    def test_conflict_marker_failure_during_recovery_retains_journal(self):
        self.seed_in_place_transaction()
        journal = self.transaction.read_text()
        self.assert_blocked(self.run_launcher("replace_conflict"))
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertEqual(self.transaction.read_text(), journal)
        self.assertFalse(self.conflict.exists())
        self.assertEqual(self.local.read_text(), "local edit")
        self.assertEqual(self.state.read_text().strip(), self.digest("original"))
        moves_before = len(self.remote_operations("moveto"))
        uploads_before = len(self.remote_operations("copyto"))
        self.assert_blocked(self.run_launcher())
        self.assertTrue(self.conflict.exists())
        self.assertFalse(self.transaction.exists())
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertEqual(len(self.remote_operations("moveto")), moves_before)
        self.assertEqual(len(self.remote_operations("copyto")), uploads_before)

    def test_exit_upload_failure_reports_failure_and_preserves_edits(self):
        self.seed()
        result = self.run_launcher("upload", edit="saved during session")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.root / "launched").exists())
        self.assertEqual(self.local.read_text(), "saved during session")
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertNotIn("Finished.", result.stdout)

    def test_application_failure_is_reported_after_successful_final_sync(self):
        self.seed()
        result = self.run_launcher(edit="saved before app failure", TEST_APP_STATUS="42")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.events("keepassxc")), 1)
        self.assertEqual(self.local.read_text(), "saved before app failure")
        self.assertEqual(self.cloud.read_text(), "saved before app failure")
        self.assertEqual(self.state.read_text().strip(), self.digest("saved before app failure"))
        self.assertIn("KeePassXC exited with an error", result.stdout + result.stderr)
        self.assertNotIn("Finished.", result.stdout)

    def test_offline_launch_is_allowed_but_deferred_exit_is_nonzero(self):
        self.seed()
        result = self.run_launcher("offline", edit="offline edit")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.root / "launched").exists())
        self.assertEqual(self.local.read_text(), "offline edit")
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertIn("Post-close sync deferred", result.stdout + result.stderr)
        self.assertNotIn("Finished.", result.stdout)

    def test_connection_loss_before_upload_allows_offline_session_and_retry(self):
        self.seed(local="local edit")
        result = self.run_launcher("offline_recheck")
        self.assert_success(result)
        self.assertTrue((self.root / "launched").exists())
        self.assertEqual(self.cloud.read_text(), "local edit")

    def test_invalid_state_cannot_authorize_offline_launch(self):
        self.seed()
        self.state.write_text("invalid state")
        self.assert_blocked(self.run_launcher("offline"))

    def test_missing_local_database_cannot_silently_restore_over_editing_state(self):
        self.seed()
        self.local.unlink()
        self.assert_blocked(self.run_launcher())
        self.assertFalse(self.local.exists())
        self.assertEqual(self.cloud.read_text(), "original")


if __name__ == "__main__":
    unittest.main()
