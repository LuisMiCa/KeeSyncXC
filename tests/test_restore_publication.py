"""Restored databases require consent before any cloud write, across processes."""

import hashlib
from pathlib import Path
import shutil
import unittest

from support import SCRIPT, INSTALLER_REMOTE, InstallerCase


class RestorePublicationTests(InstallerCase):
    def prepare_restore(self, contents="restored database", current="cloud database"):
        self.write_config()
        self.local.parent.mkdir(parents=True)
        self.local.write_text(current)
        self.state_dir.mkdir(parents=True)
        self.state.write_text(hashlib.md5(b"cloud database").hexdigest() + "\n")
        self.identity = Path(str(self.state) + ".id")
        info = self.cloud.stat()
        self.identity.write_text(f"id-{info.st_dev}-{info.st_ino}\n")
        self.review = Path(str(self.state) + ".restore")
        self.transaction = Path(str(self.state) + ".transaction")
        self.conflict = Path(str(self.state) + ".conflict")
        self.backups = self.state_dir / "backups"
        self.backups.mkdir()
        self.backup = self.backups / "local-20000101T000000Z-Ab12Cd34.kdbx"
        self.backup.write_text(contents)
        baseline, identity = self.state.read_bytes(), self.identity.read_bytes()
        result = self.run_script("--restore-backup", answers="1\ny\n")
        self.assert_success(result)
        self.assertEqual(self.review.read_text().splitlines(),
                         ["restore-review-v1", str(self.local), hashlib.md5(contents.encode()).hexdigest()])
        self.assertEqual(self.state.read_bytes(), baseline)
        self.assertEqual(self.identity.read_bytes(), identity)
        self.assertEqual(self.local.read_text(), contents)
        safety = [p.read_text() for p in self.backups.glob("local-*.kdbx") if p != self.backup]
        self.assertEqual(safety, [current])
        self.assertFalse(self.events("rclone"))
        self.assertFalse(self.events("keepassxc"))

    def assert_no_cloud_writes(self):
        self.assertFalse({"mkdir", "moveto", "deletefile", "delete"} & set(self.operations()))
        for event in self.events("rclone"):
            args = event["arguments"]
            if args[0] == "copyto":
                self.assertFalse(args[2].startswith("gdrive:"), args)

    def clear_events(self):
        (self.root / "events.jsonl").unlink(missing_ok=True)

    def test_decline_blank_and_eof_keep_both_databases_and_ask_on_each_run(self):
        self.prepare_restore(current="unsynchronized work")
        for mode in ("--sync", "--launch"):
            for answers in ("n\n", "\n", ""):
                with self.subTest(mode=mode, answers=answers):
                    result = self.run_script(mode, answers=answers)
                    self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
                    self.assertIn("Inspect the backup", result.stdout)
                    self.assertIn("Publish the restored local database", result.stderr)
                    self.assertEqual(self.local.read_text(), "restored database")
                    self.assertEqual(self.cloud.read_text(), "cloud database")
                    self.assertTrue(self.review.exists())
                    self.assertFalse(self.transaction.exists())
                    self.assertFalse(self.events("keepassxc"))
                    self.assert_no_cloud_writes()

    def test_yes_keeps_cloud_backup_and_publishes_before_opening_editor(self):
        self.prepare_restore()
        result = self.run_script("--launch", answers="yes\n")
        self.assert_success(result)
        self.assertEqual(result.stderr.count("[y/N]"), 1)
        self.assertEqual(self.cloud.read_text(), "restored database")
        self.assertFalse(self.review.exists())
        cloud_backups = list((self.cloud.parent / "KeePass-Backups").glob("*.kdbx"))
        self.assertEqual([p.read_text() for p in cloud_backups], ["cloud database"])
        events = self.events()
        upload = next(i for i, event in enumerate(events) if event["command"] == "rclone"
                      and event["arguments"][0] == "copyto" and event["arguments"][2] == INSTALLER_REMOTE)
        editor = next(i for i, event in enumerate(events) if event["command"] == "keepassxc")
        self.assertLess(upload, editor)

    def test_successful_publication_clears_review_for_later_ordinary_edits(self):
        self.prepare_restore()
        self.assert_success(self.run_script("--sync", answers="y\n"))
        self.assertFalse(self.review.exists())
        self.local.write_text("later ordinary edit")
        result = self.run_script("--sync", answers="")
        self.assert_success(result)
        self.assertNotIn("Publish the restored local database", result.stderr)
        self.assertEqual(self.cloud.read_text(), "later ordinary edit")
        self.assertFalse(self.events("keepassxc"))

    def test_conflict_publication_still_requires_restoration_confirmation(self):
        self.prepare_restore()
        self.cloud.write_text("another device edit")
        result = self.run_script("--sync", answers="3\nn\n")
        self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
        self.assertEqual(self.cloud.read_text(), "another device edit")
        self.assertTrue(self.review.exists())
        self.assertTrue(self.conflict.exists())
        self.assert_no_cloud_writes()
        self.assert_success(self.run_script("--sync", answers="3\ny\n"))
        self.assertEqual(self.cloud.read_text(), "restored database")
        self.assertFalse(self.review.exists())
        self.assertFalse(self.conflict.exists())

    def test_adopting_cloud_after_conflict_clears_review_without_upload(self):
        self.prepare_restore()
        self.cloud.write_text("another device edit")
        result = self.run_script("--sync", answers="2\n")
        self.assert_success(result)
        self.assertEqual(self.local.read_text(), "another device edit")
        self.assertFalse(self.review.exists())
        self.assertNotIn("Publish the restored local database", result.stderr)
        self.assert_no_cloud_writes()

    def test_cloud_only_change_downloads_without_publication_confirmation(self):
        self.prepare_restore(contents="cloud database", current="unsynchronized work")
        self.cloud.write_text("another device edit")
        self.assert_success(self.run_script("--sync", answers=""))
        self.assertEqual(self.local.read_text(), "another device edit")
        self.assertFalse(self.review.exists())
        self.assert_no_cloud_writes()

    def test_matching_copies_clear_review_without_prompt_or_transfer(self):
        self.prepare_restore()
        self.cloud.write_text("restored database")
        self.assert_success(self.run_script("--sync", answers=""))
        self.assertFalse(self.review.exists())
        self.assertNotIn("copyto", self.operations())
        self.assert_no_cloud_writes()

    def test_offline_edits_keep_the_publication_gate_after_restart(self):
        self.prepare_restore()
        result = self.run_script("--launch", answers="", TEST_FAULT="offline",
                                 TEST_EDITS='["edited restored database"]')
        self.assert_failure(result)
        self.assertEqual(self.local.read_text(), "edited restored database")
        self.assertTrue(self.review.exists())
        result = self.run_script("--sync", answers="n\n")
        self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
        self.assertEqual(self.cloud.read_text(), "cloud database")
        self.assert_success(self.run_script("--sync", answers="y\n"))
        self.assertEqual(self.cloud.read_text(), "edited restored database")

    def test_failed_upload_attempt_requires_new_confirmation(self):
        self.prepare_restore()
        self.assert_failure(self.run_script("--sync", answers="y\n", TEST_FAULT="backup_mkdir"))
        self.assertTrue(self.review.exists())
        self.clear_events()
        self.assertEqual(self.run_script("--sync", answers="n\n").returncode, 130)
        self.assert_no_cloud_writes()
        self.assert_success(self.run_script("--sync", answers="y\n"))

    def test_completed_interrupted_publication_recovers_without_another_approval(self):
        self.prepare_restore()
        self.assert_failure(self.run_script("--sync", answers="y\n", TEST_FAULT="update_response_lost"))
        self.assertTrue(self.transaction.exists())
        self.assertTrue(self.review.exists())
        self.clear_events()
        self.assert_success(self.run_script("--sync", answers=""))
        self.assertFalse(self.review.exists())
        self.assertFalse(self.transaction.exists())
        self.assertEqual(self.cloud.read_text(), "restored database")
        self.assert_no_cloud_writes()

    def test_unpublished_interrupted_attempt_keeps_review_through_conflict_recovery(self):
        self.prepare_restore()
        self.assert_failure(self.run_script("--sync", answers="y\n", TEST_FAULT="update"))
        self.clear_events()
        result = self.run_script("--sync", answers="3\nn\n")
        self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
        self.assertTrue(self.review.exists())
        self.assertEqual(self.cloud.read_text(), "cloud database")
        self.assert_no_cloud_writes()

    def test_invalid_review_record_stops_before_cloud_access_or_editor(self):
        self.prepare_restore()
        for contents in ("", "invalid\n", f"restore-review-v1\n{self.local}\ninvalid\n",
                         "restore-review-v1\n/another/database.kdbx\n" + "a" * 32 + "\n"):
            with self.subTest(contents=contents):
                self.review.write_text(contents)
                self.assert_failure(self.run_script("--launch", answers="y\n"))
                self.assertFalse(self.events("rclone"))
                self.assertFalse(self.events("keepassxc"))
                self.assertEqual(self.cloud.read_text(), "cloud database")

    def test_symlinked_review_record_is_preserved_and_stops_before_cloud_access(self):
        self.prepare_restore()
        outside = self.root / "outside-record"
        self.review.rename(outside)
        before = outside.read_bytes()
        self.review.symlink_to(outside)
        self.assert_failure(self.run_script("--sync", answers="y\n"))
        self.assertTrue(self.review.is_symlink())
        self.assertEqual(outside.read_bytes(), before)
        self.assertFalse(self.events("rclone"))

    def test_restore_stops_if_review_record_cannot_be_saved(self):
        self.write_config()
        self.local.parent.mkdir(parents=True)
        self.local.write_text("original database")
        self.state_dir.mkdir(parents=True)
        self.state.write_text("a" * 32 + "\n")
        backups = self.state_dir / "backups"
        backups.mkdir()
        (backups / "local-20000101T000000Z-Ab12Cd34.kdbx").write_text("older backup")
        command = self.binaries / "mv"
        command.write_text('#!/bin/bash\ncase "${@: -1}" in *.md5.restore) exit 1 ;; esac\nexec /usr/bin/mv "$@"\n')
        command.chmod(0o700)
        self.assert_failure(self.run_script("--restore-backup", answers="1\ny\n"))
        self.assertEqual(self.local.read_text(), "original database")
        self.assertFalse(Path(str(self.state) + ".restore").exists())

    def test_pending_review_blocks_pruning_move_and_reconfiguration(self):
        self.prepare_restore()
        for option, answers in (("--cleanup-age", "0\ny\n"),
                                ("--move-local", f"{self.home / 'moved.kdbx'}\ny\n"),
                                ("--backup-folder", "Other copies\n"),
                                ("--install", "\nOther copies\n\nn\n")):
            with self.subTest(option=option):
                self.assert_failure(self.run_script(option, answers=answers))
                self.assertTrue(self.review.exists())
                self.assertEqual(self.local.read_text(), "restored database")
                self.assertFalse(self.events("rclone"))

    def test_clear_local_recognizes_and_removes_review_record(self):
        self.prepare_restore()
        self.assert_success(self.run_script("--clear-local", answers="y\n"))
        self.assertFalse(self.review.exists())
        self.assertFalse(self.local.exists())
        self.assertFalse(self.events("rclone"))

    def test_basic_uninstall_retains_review_with_preserved_database(self):
        self.prepare_restore()
        self.installed.parent.mkdir(parents=True)
        shutil.copyfile(SCRIPT, self.installed)
        self.installed.chmod(0o700)
        self.desktop.parent.mkdir(parents=True)
        self.desktop.write_text("[Desktop Entry]\n")
        self.assert_success(self.run_script("--uninstall-keep-data", answers="y\n"))
        self.assertTrue(self.review.exists())
        self.assertEqual(self.local.read_text(), "restored database")

    def test_switch_archives_pending_review_with_previous_selection(self):
        self.prepare_restore()
        self.assert_failure(self.run_script("--switch-database", answers="y\n"))
        archives = list(self.backups.glob("reset-*"))
        self.assertEqual(len(archives), 1)
        self.assertTrue((archives[0] / self.review.name).is_file())
        self.assertFalse(self.review.exists())
        self.assertFalse(self.events("rclone"))

    def test_graphical_confirmation_defaults_to_cancel_and_reminds_to_inspect(self):
        self.prepare_restore()
        self.env["TEST_CONFIRM_STATUS"] = "1"
        result = self.run_script("--sync", answers="", DISPLAY=":test")
        self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
        args = self.events("zenity")[0]["arguments"]
        self.assertIn("--default-cancel", args)
        self.assertTrue(any("Inspect the backup" in arg for arg in args))
        self.assert_no_cloud_writes()
        self.assert_success(self.run_script("--sync", answers="", DISPLAY=":test", TEST_CONFIRM_STATUS="0"))
        self.assertFalse(self.review.exists())

    def test_editing_while_approving_cannot_publish_an_older_snapshot(self):
        self.prepare_restore()
        self.env["TEST_CONFIRM_EDIT"] = "edited while inspecting"
        result = self.run_script("--sync", answers="", DISPLAY=":test", TEST_LOCAL=str(self.local))
        self.assert_failure(result)
        self.assertIn("changed while awaiting approval", result.stderr)
        self.assertTrue(self.review.exists())
        self.assertEqual(self.local.read_text(), "edited while inspecting")
        self.assertEqual(self.cloud.read_text(), "cloud database")
        self.assert_no_cloud_writes()
        self.assert_success(self.run_script("--sync", answers="y\n"))
        self.assertEqual(self.cloud.read_text(), "edited while inspecting")


if __name__ == "__main__":
    unittest.main()
