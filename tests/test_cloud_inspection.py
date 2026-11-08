"""Cloud backup inspection downloads private copies without synchronization."""

import hashlib
import json
from pathlib import Path
import stat
import unittest

from support import InstallerCase


class CloudInspectionTests(InstallerCase):
    def setUp(self):
        super().setUp()
        self.write_config()
        self.local.parent.mkdir(parents=True)
        self.local.write_text("working database")
        self.state_dir.mkdir(parents=True)
        self.state.write_text(hashlib.md5(b"cloud database").hexdigest() + "\n")
        self.identity = Path(str(self.state) + ".id")
        info = self.cloud.stat()
        self.identity.write_text(f"id-{info.st_dev}-{info.st_ino}\n")
        self.directory = self.cloud.parent / "KeePass-Backups"
        self.directory.mkdir()
        self.cloud_backup = self.directory / "kpDB-20261101T000000Z-backup.kdbx"
        self.cloud_backup.write_text("historical cloud database")
        self.before = {path: path.read_bytes() for path in
                       (self.config, self.local, self.state, self.identity, self.cloud, self.cloud_backup)}
        self.backups = self.state_dir / "backups"

    def copies(self):
        return sorted(self.backups.glob("cloud-*.kdbx"))

    def assert_originals_unchanged(self):
        for path, contents in self.before.items():
            self.assertEqual(path.read_bytes(), contents, str(path))
        self.assert_no_cloud_writes()

    def assert_no_cloud_writes(self):
        self.assertFalse({"mkdir", "moveto", "delete", "deletefile", "copyto"} & set(self.operations()))

    def assert_no_inspection_artifacts(self):
        self.assertFalse(self.copies())
        self.assertFalse(list(self.backups.glob(".keesyncxc-download.*")))
        self.assertFalse(self.events("keepassxc"))

    def test_menu_downloads_verifies_and_opens_private_copy_without_synchronizing(self):
        result = self.run_script("--menu", answers="3\n8\n1\n0\n0\n")
        self.assert_success(result)
        copy, = self.copies()
        self.assertEqual(copy.read_text(), "historical cloud database")
        self.assertEqual(stat.S_IMODE(copy.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(copy.parent.stat().st_mode), 0o700)
        self.assertEqual(self.events("keepassxc")[0]["arguments"], [str(copy)])
        self.assertIn("2026-11-05 00:00:00", result.stderr)
        self.assertIn("bytes", result.stderr)
        self.assertIn("Local inspection copy", result.stdout)
        self.assertFalse(list(self.backups.glob(".keesyncxc-download.*")))
        self.assertEqual(self.operations(), ["lsf", "backend", "lsf"])
        self.assert_originals_unchanged()

    def test_saved_edits_affect_only_retained_inspection_copy(self):
        result = self.run_script("--inspect-cloud-backup", answers="1\n", TEST_EDITS='["inspection edits"]')
        self.assert_success(result)
        self.assertEqual(self.copies()[0].read_text(), "inspection edits")
        self.assertIn("Edits were saved to the local inspection copy", result.stdout)
        self.assert_originals_unchanged()

    def test_repeated_inspection_reuses_unchanged_local_copy_without_download(self):
        self.assert_success(self.run_script("--inspect-cloud-backup", answers="1\n"))
        copy, = self.copies()
        (self.root / "events.jsonl").unlink()
        result = self.run_script("--inspect-cloud-backup", answers="1\n")
        self.assert_success(result)
        self.assertIn("Matching local MD5 found", result.stdout)
        self.assertEqual(self.copies(), [copy])
        self.assertEqual(self.operations(), ["lsf", "lsf"])
        self.assertEqual(self.events("keepassxc")[0]["arguments"], [str(copy)])
        self.assertFalse(list(self.backups.glob(".keesyncxc-download.*")))
        self.assert_originals_unchanged()

    def test_identical_remote_files_share_an_existing_inspection_copy(self):
        other = self.directory / "zz-another-backup.kdbx"
        other.write_bytes(self.cloud_backup.read_bytes())
        self.before[other] = other.read_bytes()
        self.assert_success(self.run_script("--inspect-cloud-backup", answers="1\n"))
        copy, = self.copies()
        (self.root / "events.jsonl").unlink()
        self.assert_success(self.run_script("--inspect-cloud-backup", answers="2\n"))
        self.assertEqual(self.copies(), [copy])
        self.assertNotIn("backend", self.operations())
        self.assert_originals_unchanged()

    def test_edited_inspection_copy_with_same_size_does_not_match(self):
        self.assert_success(self.run_script("--inspect-cloud-backup", answers="1\n"))
        edited, = self.copies()
        edited.write_bytes(b"X" + edited.read_bytes()[1:])
        self.before[edited] = edited.read_bytes()
        (self.root / "events.jsonl").unlink()
        self.assert_success(self.run_script("--inspect-cloud-backup", answers="1\n"))
        self.assertEqual(len(self.copies()), 2)
        self.assertIn("backend", self.operations())
        opened = Path(self.events("keepassxc")[0]["arguments"][-1])
        self.assertNotEqual(opened, edited)
        self.assertEqual(opened.read_bytes(), self.cloud_backup.read_bytes())
        self.assert_originals_unchanged()

    def test_matching_working_database_is_copied_before_inspection_edits(self):
        self.local.write_bytes(self.cloud_backup.read_bytes())
        self.before[self.local] = self.local.read_bytes()
        self.assert_success(self.run_script("--inspect-cloud-backup", answers="1\n", TEST_EDITS='["inspection edits"]'))
        self.assertNotIn("backend", self.operations())
        copy, = self.copies()
        self.assertEqual(copy.read_text(), "inspection edits")
        self.assertEqual(stat.S_IMODE(copy.stat().st_mode), 0o600)
        self.assertNotEqual(self.events("keepassxc")[0]["arguments"], [str(self.local)])
        self.assert_originals_unchanged()

    def test_matching_local_backup_is_copied_without_changing_original_permissions(self):
        self.backups.mkdir()
        original = self.backups / "local-20000101T000000Z-Ab12Cd34.kdbx"
        original.write_bytes(self.cloud_backup.read_bytes())
        original.chmod(0o640)
        self.before[original] = original.read_bytes()
        self.assert_success(self.run_script("--inspect-cloud-backup", answers="1\n", TEST_EDITS='["local inspection edits"]'))
        self.assertNotIn("backend", self.operations())
        self.assertEqual(self.copies()[0].read_text(), "local inspection edits")
        self.assertEqual(stat.S_IMODE(original.stat().st_mode), 0o640)
        self.assert_originals_unchanged()

    def test_symlinked_inspection_copy_is_skipped(self):
        self.backups.mkdir()
        outside = self.root / "outside.kdbx"
        outside.write_bytes(self.cloud_backup.read_bytes())
        link = self.backups / "cloud-20000101T000000Z-Cached12.kdbx"
        link.symlink_to(outside)
        self.assert_success(self.run_script("--inspect-cloud-backup", answers="1\n", TEST_EDITS='["inspection edits"]'))
        self.assertIn("backend", self.operations())
        self.assertTrue(link.is_symlink())
        self.assertEqual(outside.read_bytes(), self.cloud_backup.read_bytes())
        self.assert_originals_unchanged()

    def test_hardlink_to_working_database_is_never_opened_as_inspection_copy(self):
        self.local.write_bytes(self.cloud_backup.read_bytes())
        self.before[self.local] = self.local.read_bytes()
        self.backups.mkdir()
        linked = self.backups / "cloud-20000101T000000Z-Cached12.kdbx"
        linked.hardlink_to(self.local)
        self.before[linked] = linked.read_bytes()
        self.assert_success(self.run_script("--inspect-cloud-backup", answers="1\n", TEST_EDITS='["inspection edits"]'))
        self.assertNotIn("backend", self.operations())
        self.assertNotEqual(self.events("keepassxc")[0]["arguments"], [str(linked)])
        self.assert_originals_unchanged()

    def test_unreadable_local_md5_falls_back_to_cloud_download(self):
        self.backups.mkdir()
        cached = self.backups / "cloud-20000101T000000Z-Cached12.kdbx"
        cached.write_bytes(self.cloud_backup.read_bytes())
        cached.chmod(0o600)
        self.before[cached] = cached.read_bytes()
        command = self.binaries / "md5sum"
        command.write_text('#!/bin/bash\ncase "${@: -1}" in */cloud-20000101T000000Z-Cached12.kdbx) exit 1 ;; esac\nexec /usr/bin/md5sum "$@"\n')
        command.chmod(0o700)
        self.assert_success(self.run_script("--inspect-cloud-backup", answers="1\n"))
        self.assertIn("backend", self.operations())
        self.assert_originals_unchanged()

    def test_reused_copy_changed_during_remote_verification_is_not_opened(self):
        self.assert_success(self.run_script("--inspect-cloud-backup", answers="1\n"))
        copy, = self.copies()
        (self.root / "events.jsonl").unlink()
        self.env["TEST_FAULT"] = "cached_copy_changed"
        result = self.run_script("--inspect-cloud-backup", answers="1\n", TEST_CACHE=str(copy))
        self.assert_failure(result)
        self.assertIn("inspection copy changed", result.stderr)
        self.assertNotIn("backend", self.operations())
        self.assertFalse(self.events("keepassxc"))
        self.assertFalse(list(self.backups.glob(".keesyncxc-download.*")))
        self.assert_originals_unchanged()

    def test_selected_remote_id_is_rechecked_even_when_a_local_copy_matches(self):
        self.assert_success(self.run_script("--inspect-cloud-backup", answers="1\n"))
        (self.root / "events.jsonl").unlink()
        self.env["TEST_FAULT"] = "inspection_remote_removed"
        result = self.run_script("--inspect-cloud-backup", answers="1\n", TEST_INSPECT_REMOTE=str(self.cloud_backup))
        self.assert_failure(result)
        self.assertNotIn("backend", self.operations())
        self.assertFalse(self.events("keepassxc"))
        self.assertFalse(list(self.backups.glob(".keesyncxc-download.*")))
        self.assertEqual(self.local.read_text(), "working database")
        self.assert_no_cloud_writes()

    def test_cancellation_blank_and_eof_never_download_or_open(self):
        for answers in ("0\n", "\n", ""):
            with self.subTest(answers=answers):
                result = self.run_script("--inspect-cloud-backup", answers=answers)
                self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
                self.assertNotIn("backend", self.operations())
                self.assert_no_inspection_artifacts()
                self.assert_originals_unchanged()

    def test_help_and_invalid_input_return_to_same_backup_selection(self):
        result = self.run_script("--inspect-cloud-backup", answers="invalid\nh\n9\n1\n")
        self.assert_success(result)
        self.assertIn("Inspect a cloud backup help", result.stderr)
        self.assertIn("Help returns to the same menu", result.stderr)
        self.assertEqual(self.operations().count("lsf"), 2)
        self.assert_originals_unchanged()

    def test_empty_folder_and_non_database_files_offer_nothing(self):
        self.cloud_backup.unlink()
        self.before.pop(self.cloud_backup)
        (self.directory / "notes.txt").write_text("notes")
        (self.directory / "nested.kdbx").mkdir()
        result = self.run_script("--inspect-cloud-backup", answers="1\n")
        self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
        self.assertIn("No cloud database backups", result.stderr)
        self.assert_no_inspection_artifacts()
        self.assert_originals_unchanged()

    def test_offline_listing_stops_without_download_or_editor(self):
        result = self.run_script("--inspect-cloud-backup", answers="1\n", TEST_FAULT="offline")
        self.assert_failure(result)
        self.assert_no_inspection_artifacts()
        self.assert_originals_unchanged()

    def test_failed_or_corrupt_download_is_removed_without_opening(self):
        for fault in ("download", "corrupt_download"):
            with self.subTest(fault=fault):
                self.assert_failure(self.run_script("--inspect-cloud-backup", answers="1\n", TEST_FAULT=fault))
                self.assert_no_inspection_artifacts()
                self.assert_originals_unchanged()

    def test_cloud_backup_changed_during_download_is_not_opened(self):
        self.env["TEST_FAULT"] = "cloud_backup_changed"
        self.assert_failure(self.run_script("--inspect-cloud-backup", answers="1\n"))
        self.assert_no_inspection_artifacts()
        self.assertEqual(self.local.read_text(), "working database")
        self.assert_no_cloud_writes()

    def test_cloud_backup_removed_during_download_is_not_opened(self):
        self.env["TEST_FAULT"] = "cloud_backup_removed"
        self.assert_failure(self.run_script("--inspect-cloud-backup", answers="1\n"))
        self.assert_no_inspection_artifacts()
        self.assertEqual(self.local.read_text(), "working database")
        self.assert_no_cloud_writes()

    def test_same_name_and_contents_with_replaced_id_are_rejected(self):
        self.env["TEST_FAULT"] = "cloud_backup_replaced"
        self.assert_failure(self.run_script("--inspect-cloud-backup", answers="1\n"))
        self.assert_no_inspection_artifacts()
        self.assert_originals_unchanged()

    def test_duplicate_names_download_the_selected_file_id(self):
        extra = self.root / "duplicate-objects/backup"
        extra.parent.mkdir()
        extra.write_text("selected duplicate backup")
        (self.root / "duplicate_files.json").write_text(json.dumps([
            {"directory": "gdrive:KeePass/KeePass-Backups", "name": self.cloud_backup.name, "file": str(extra)}]))
        self.assert_success(self.run_script("--inspect-cloud-backup", answers="2\n"))
        self.assertEqual(self.copies()[0].read_text(), "selected duplicate backup")
        args = self.events("rclone")[1]["arguments"]
        self.assertEqual(args[0:3], ["backend", "copyid", "gdrive:"])
        self.assertEqual(args[3], f"id-{extra.stat().st_dev}-{extra.stat().st_ino}")
        self.assert_originals_unchanged()

    def test_csv_filenames_with_controls_quotes_commas_and_unicode_are_safe(self):
        self.before.pop(self.cloud_backup)
        name = 'Cópia 日本語 🔐, "quoted" \\ newline\n\x1b[2J\r\u202e.kdbx'
        self.cloud_backup = self.cloud_backup.rename(self.directory / name)
        self.before[self.cloud_backup] = self.cloud_backup.read_bytes()
        result = self.run_script("--inspect-cloud-backup", answers="1\n", LC_ALL="C")
        self.assert_success(result)
        self.assertIn('Cópia 日本語 🔐, "quoted" \\ newline', result.stderr)
        self.assertIn(r"\x0A\x1B[2J\x0D\u202E.kdbx", result.stderr)
        self.assertNotRegex(result.stdout + result.stderr,
                            r"[\x00-\x09\x0b-\x1f\x7f-\x9f\u202a-\u202e]")
        self.assert_originals_unchanged()

    def test_invalid_csv_metadata_stops_before_download(self):
        for listing in ('"unterminated', 'id,2,date,"name.kdbx"suffix,' + "a" * 32,
                        'shortcut/id,2,date,name.kdbx,' + "a" * 32,
                        'id,-2,date,name.kdbx,' + "a" * 32,
                        'id,2,date,name.kdbx,not-a-hash'):
            with self.subTest(listing=listing):
                self.assert_failure(self.run_script("--inspect-cloud-backup", answers="1\n", TEST_LISTING=listing))
                self.assertNotIn("backend", self.operations())
                self.assert_no_inspection_artifacts()
                self.assert_originals_unchanged()

    def test_unsecured_download_is_not_opened(self):
        command = self.binaries / "chmod"
        command.write_text('#!/bin/bash\ncase "${@: -1}" in */database.kdbx) exit 1 ;; esac\nexec /usr/bin/chmod "$@"\n')
        command.chmod(0o700)
        self.assert_failure(self.run_script("--inspect-cloud-backup", answers="1\n"))
        self.assert_no_inspection_artifacts()
        self.assert_originals_unchanged()

    def test_editor_failure_keeps_verified_inspection_copy(self):
        self.assert_failure(self.run_script("--inspect-cloud-backup", answers="1\n", TEST_APP_STATUS="3"))
        self.assertEqual(self.copies()[0].read_text(), "historical cloud database")
        self.assert_originals_unchanged()

    def test_inspection_does_not_need_working_database_or_sync_baseline(self):
        for path in (self.local, self.state, self.identity):
            path.unlink()
            self.before.pop(path)
        self.assert_success(self.run_script("--inspect-cloud-backup", answers="1\n"))
        self.assertFalse(self.local.exists())
        self.assertFalse(self.state.exists())
        self.assertFalse(self.identity.exists())
        self.assert_originals_unchanged()

    def test_pending_conflict_upload_and_restore_records_are_preserved(self):
        for suffix in (".conflict", ".transaction", ".restore"):
            path = Path(str(self.state) + suffix)
            path.write_text("pending record")
            self.before[path] = path.read_bytes()
        self.assert_success(self.run_script("--inspect-cloud-backup", answers="1\n"))
        self.assert_originals_unchanged()

    def test_custom_root_backup_folder_is_used(self):
        self.write_config(remote="gdrive:Passwords.kdbx", backup_folder="Vault copies")
        self.before[self.config] = self.config.read_bytes()
        destination = self.root / "cloud/gdrive/Vault copies"
        self.directory.rename(destination)
        self.before.pop(self.cloud_backup)
        self.cloud_backup = destination / self.cloud_backup.name
        self.before[self.cloud_backup] = self.cloud_backup.read_bytes()
        self.assert_success(self.run_script("--inspect-cloud-backup", answers="1\n"))
        self.assertEqual(self.events("rclone")[0]["arguments"][1], "gdrive:Vault copies")
        self.assert_originals_unchanged()

    def test_retained_copy_can_be_restored_but_needs_publication_approval(self):
        self.assert_success(self.run_script("--inspect-cloud-backup", answers="1\n"))
        self.assert_success(self.run_script("--restore-backup", answers="1\ny\n"))
        self.assertEqual(self.local.read_text(), "historical cloud database")
        self.assertTrue(Path(str(self.state) + ".restore").exists())
        result = self.run_script("--sync", answers="n\n")
        self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
        self.assertEqual(self.cloud.read_text(), "cloud database")
        self.assert_no_cloud_writes()

    def test_pruning_keeps_inspection_copies_and_full_cleanup_removes_them(self):
        self.assert_success(self.run_script("--inspect-cloud-backup", answers="1\n"))
        copy = self.copies()[0]
        self.assert_success(self.run_script("--cleanup-count", answers="0\ny\n"))
        self.assertTrue(copy.exists())
        stage = self.backups / ".keesyncxc-download.Ab12Cd34"
        stage.mkdir(mode=0o700)
        (stage / "database.kdbx").write_text("abandoned inspection")
        self.assert_success(self.run_script("--clear-local", answers="y\n"))
        self.assertFalse(copy.exists())
        self.assertFalse(stage.exists())
        self.assertEqual(self.cloud_backup.read_text(), "historical cloud database")
        self.assert_no_cloud_writes()


if __name__ == "__main__":
    unittest.main()
