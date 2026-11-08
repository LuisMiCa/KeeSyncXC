"""Drive names may change or collide; the pinned object must stay authoritative.

The shared fake Drive models duplicate objects independently of POSIX names.
No real remote, credentials, or graphical application is used.
"""

import hashlib
from pathlib import Path
import shutil
import unittest

from support import SCRIPT, LauncherCase


class IdentityTests(LauncherCase):
    def setUp(self):
        super().setUp()
        self.config = self.root / "config/keesyncxc/config"
        self.identity = Path(str(self.state) + ".id")
        self.env.pop("TEST_ACTIVE_REMOTE", None)

    def configure(self, remote):
        self.env["TEST_REMOTE"] = remote
        self.config.write_text(f"{remote}\n{self.local}\nKeePass-Backups\n")
        self.cloud = self.root / "cloud" / remote.replace(":", "/", 1)
        self.cloud.parent.mkdir(parents=True, exist_ok=True)
        self.state = self.data / (hashlib.sha256(remote.encode()).hexdigest() + ".md5")
        self.identity = Path(str(self.state) + ".id")
        self.transaction = Path(str(self.state) + ".transaction")
        self.conflict = Path(str(self.state) + ".conflict")

    def rename(self, filename):
        self.cloud = self.cloud.rename(self.cloud.with_name(filename))
        remote = self.env["TEST_REMOTE"]
        parent = remote.rsplit("/", 1)[0] + "/" if "/" in remote else remote.split(":", 1)[0] + ":"
        active = parent + filename
        self.env["TEST_ACTIVE_REMOTE"] = active
        return active

    def assert_no_cloud_writes(self):
        self.assertFalse(self.remote_operations("mkdir"))
        self.assertFalse([args for args in self.remote_operations("copyto")
                          if args[2].startswith("gdrive:")])
        self.assertFalse(self.remote_operations("moveto"))
        self.assertFalse(self.remote_operations("deletefile"))

    def assert_no_transfers(self):
        self.assertFalse(self.remote_operations("copyto"))
        self.assertFalse(self.remote_operations("backend"))
        self.assert_no_cloud_writes()

    def prepare_desktop(self):
        self.installed = self.root / ".local/bin/keesyncxc"
        self.installed.parent.mkdir(parents=True)
        shutil.copyfile(SCRIPT, self.installed)
        self.installed.chmod(0o700)
        entry = self.root / "data/applications/keesyncxc.desktop"
        entry.parent.mkdir(parents=True)
        entry.write_text("[Desktop Entry]\nType=Application\nTerminal=true\n")

    def test_unpinned_duplicate_names_cannot_bind_an_arbitrary_object(self):
        self.seed()
        duplicate = self.duplicate()
        self.assert_blocked(self.run_launcher())
        self.assertFalse(self.identity.exists())
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertEqual(duplicate.read_text(), "unrelated duplicate")
        self.assert_no_transfers()

    def test_pinned_duplicate_matching_local_opens_with_warning(self):
        self.seed()
        identity = self.pin()
        duplicate = self.duplicate()
        result = self.run_launcher()
        self.assert_success(result)
        self.assertTrue((self.root / "launched").exists())
        self.assertIn("duplicate", (result.stdout + result.stderr).lower())
        self.assertIn(identity, result.stdout + result.stderr)
        self.assertEqual(duplicate.read_text(), "unrelated duplicate")
        self.assert_no_transfers()

    def test_pinned_duplicate_download_uses_exact_id_and_absolute_local_destination(self):
        self.seed(cloud="new contents of the pinned object")
        identity = self.pin()
        duplicate = self.duplicate("wrong database; never download this")
        result = self.run_launcher()
        self.assert_success(result)
        self.assertEqual(self.local.read_text(), "new contents of the pinned object")
        self.assertEqual(self.state.read_text().strip(), self.digest(self.local.read_text()))
        self.assertEqual(duplicate.read_text(), "wrong database; never download this")
        commands = self.remote_operations("backend")
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][:4], ["backend", "copyid", "gdrive:", identity])
        self.assertTrue(Path(commands[0][4]).is_absolute())
        self.assertFalse(commands[0][4].endswith("/"))
        self.assertIn("--drive-skip-shortcuts", commands[0])
        self.assertEqual(commands[0][commands[0].index("--retries") + 1], "2")
        self.assert_no_cloud_writes()

    def test_pinned_duplicate_pending_local_upload_is_preserved_without_cloud_transfer(self):
        self.seed(local="pending local changes")
        self.pin()
        duplicate = self.duplicate()
        baseline = self.state.read_text()
        result = self.run_launcher()
        self.assert_blocked(result)
        self.assertIn("duplicate", (result.stdout + result.stderr).lower())
        self.assertIn("upload", (result.stdout + result.stderr).lower())
        self.assertEqual(self.local.read_text(), "pending local changes")
        self.assertEqual(self.state.read_text(), baseline)
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertEqual(duplicate.read_text(), "unrelated duplicate")
        self.assert_no_transfers()

    def test_editing_with_known_duplicates_keeps_edits_and_blocks_cloud_upload(self):
        self.seed()
        self.pin()
        self.duplicate()
        baseline = self.state.read_text()
        result = self.run_launcher(edit="saved local edit")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / "launched").exists())
        self.assertEqual(self.local.read_text(), "saved local edit")
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertEqual(self.state.read_text(), baseline)
        self.assert_no_transfers()

    def test_pinned_same_parent_rename_warns_and_preserves_saved_path_and_records(self):
        self.seed()
        identity = self.pin()
        old_path = self.env["TEST_REMOTE"]
        saved_config = self.config.read_text()
        active = self.rename("Renamed database.kdbx")
        result = self.run_launcher()
        self.assert_success(result)
        output = result.stdout + result.stderr
        self.assertIn("renamed", output.lower())
        self.assertIn(old_path, output)
        self.assertIn(active, output)
        self.assertEqual(self.config.read_text(), saved_config)
        self.assertEqual(self.identity.read_text().strip(), identity)
        self.assertEqual(list(self.data.glob("*.md5")), [self.state])
        self.assert_no_cloud_writes()

    def test_pinned_renamed_file_accepts_upload_without_rebinding_or_renaming(self):
        self.seed()
        identity = self.pin()
        saved_config = self.config.read_text()
        active = self.rename("Renamed ; password database.kdbx")
        result = self.run_launcher(edit="edited renamed database")
        self.assert_success(result)
        self.assertEqual(self.cloud.read_text(), "edited renamed database")
        self.assertEqual(self.cloud_id(), identity)
        uploads = [args for args in self.remote_operations("copyto") if not args[1].startswith("gdrive:")]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0][2], active)
        backups = list((self.cloud.parent / "KeePass-Backups").glob("Passwords-*.kdbx"))
        self.assertEqual([file.read_text() for file in backups], ["original"])
        self.assertEqual(self.config.read_text(), saved_config)
        self.assertEqual(self.identity.read_text().strip(), identity)
        self.assertFalse(self.transaction.exists())
        self.assertFalse(self.remote_operations("moveto"))

    def test_root_file_rename_accepts_download_and_upload_to_same_object(self):
        self.configure("gdrive:Passwords.kdbx")
        self.seed(cloud="cloud update")
        identity = self.pin()
        active = self.rename("Root renamed.kdbx")
        result = self.run_launcher(edit="edited root database")
        self.assert_success(result)
        self.assertEqual(self.cloud.read_text(), "edited root database")
        self.assertEqual(self.cloud_id(), identity)
        uploads = [args for args in self.remote_operations("copyto") if not args[1].startswith("gdrive:")]
        self.assertEqual([args[2] for args in uploads], [active])
        self.assertTrue(list((self.cloud.parent / "KeePass-Backups").glob("Passwords-*.kdbx")))
        self.assertEqual(self.config.read_text().splitlines()[0], "gdrive:Passwords.kdbx")

    def test_rename_to_duplicate_name_uses_pinned_id_for_download(self):
        self.seed(cloud="renamed pinned database")
        identity = self.pin()
        self.rename("Renamed.kdbx")
        duplicate = self.duplicate("wrong renamed object")
        result = self.run_launcher()
        self.assert_success(result)
        self.assertEqual(self.local.read_text(), "renamed pinned database")
        self.assertEqual(duplicate.read_text(), "wrong renamed object")
        self.assertEqual(self.remote_operations("backend")[0][3], identity)
        self.assert_no_cloud_writes()

    def test_pinned_object_missing_from_parent_stops_before_offline_editing(self):
        self.seed()
        self.pin()
        destination = self.cloud.parent / "elsewhere" / self.cloud.name
        destination.parent.mkdir()
        self.cloud.rename(destination)
        baseline = self.state.read_text()
        self.assert_blocked(self.run_launcher())
        self.assertEqual(self.state.read_text(), baseline)
        self.assertEqual(self.local.read_text(), "original")
        self.assert_no_transfers()

    def test_name_replacement_cannot_replace_pinned_object_even_when_bytes_match(self):
        self.seed()
        identity = self.pin()
        replacement = self.cloud.with_suffix(".new")
        replacement.write_bytes(self.cloud.read_bytes())
        replacement.replace(self.cloud)
        self.assert_blocked(self.run_launcher())
        self.assertEqual(self.identity.read_text().strip(), identity)
        self.assert_no_transfers()

    def test_repeated_pinned_identity_in_listing_is_rejected(self):
        self.seed()
        self.pin()
        self.assert_blocked(self.run_launcher("metadata_duplicate"))
        self.assert_no_transfers()

    def test_invalid_pinned_hash_is_rejected_before_offline_editing(self):
        self.seed()
        self.pin()
        self.assert_blocked(self.run_launcher("metadata_invalid_hash"))
        self.assert_no_transfers()

    def test_composite_shortcut_metadata_is_rejected(self):
        self.seed()
        self.pin()
        self.assert_blocked(self.run_launcher("metadata_shortcut"))
        self.assert_no_transfers()

    def test_transient_metadata_failure_keeps_existing_offline_editing_available(self):
        self.seed()
        self.pin()
        result = self.run_launcher("offline_metadata", edit="offline saved edit")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / "launched").exists())
        self.assertEqual(self.local.read_text(), "offline saved edit")
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertEqual(self.state.read_text().strip(), self.digest("original"))
        self.assert_no_transfers()

    def test_duplicate_introduced_during_editing_is_detected_before_cloud_backup(self):
        self.seed()
        self.pin()
        result = self.run_launcher("duplicate_during_edit", edit="saved local edit")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / "launched").exists())
        self.assertIn("duplicate", (result.stdout + result.stderr).lower())
        self.assertEqual(self.local.read_text(), "saved local edit")
        self.assertEqual(self.cloud.read_text(), "original")
        self.assert_no_transfers()

    def test_duplicate_introduced_after_backup_stops_before_database_upload(self):
        self.seed(local="pending local edit")
        self.pin()
        baseline = self.state.read_text()
        result = self.run_launcher("duplicate_after_backup")
        self.assert_blocked(result)
        self.assertIn("duplicate", (result.stdout + result.stderr).lower())
        self.assertEqual(self.local.read_text(), "pending local edit")
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertEqual(self.state.read_text(), baseline)
        self.assertEqual([file.read_text() for file in self.cloud_backups()], ["original"])
        uploads = [args for args in self.remote_operations("copyto") if not args[1].startswith("gdrive:")]
        self.assertFalse(uploads)
        self.assertFalse(self.transaction.exists())

    def test_completed_upload_recovers_after_rename_with_original_backup_basename(self):
        backup = self.seed_in_place_transaction(published=True)
        identity = self.pin()
        saved_config = self.config.read_text()
        self.rename("Renamed after upload.kdbx")
        result = self.run_launcher()
        self.assert_success(result)
        self.assertFalse(self.transaction.exists())
        self.assertEqual(self.state.read_text().strip(), self.digest("local edit"))
        self.assertEqual(self.identity.read_text().strip(), identity)
        self.assertEqual(self.config.read_text(), saved_config)
        self.assertEqual(backup.read_text(), "original")
        self.assertTrue(backup.name.startswith("Passwords-"))
        self.assert_no_transfers()

    def test_unpublished_upload_recovery_can_publish_after_same_parent_rename(self):
        backup = self.seed_in_place_transaction()
        identity = self.pin()
        saved_config = self.config.read_text()
        active = self.rename("Renamed during interruption.kdbx")
        result = self.run_launcher(choices="3\n")
        self.assert_success(result)
        self.assertFalse(self.transaction.exists())
        self.assertFalse(self.conflict.exists())
        self.assertEqual(self.cloud.read_text(), "local edit")
        self.assertEqual(self.cloud_id(), identity)
        self.assertEqual(self.state.read_text().strip(), self.digest("local edit"))
        self.assertEqual(self.config.read_text(), saved_config)
        self.assertEqual(backup.read_text(), "original")
        self.assertTrue(backup.name.startswith("Passwords-"))
        updates = [args for args in self.remote_operations("copyto") if not args[1].startswith("gdrive:")]
        self.assertEqual([args[2] for args in updates], [active])
        self.assertTrue(list((self.data / "backups").glob("interrupted-upload-*.txt")))

    def test_rename_warning_keeps_desktop_terminal_open_for_review(self):
        self.seed()
        self.pin()
        self.rename("Renamed visible warning.kdbx")
        self.prepare_desktop()
        output = self.assert_terminal_holds()
        self.assertIn("renamed", output.lower())
        self.assertIn("Finished.", output)
        self.assertIn("file-ID warnings above", output)
        self.assertNotIn("contents could not be confirmed", output)
        self.assert_no_cloud_writes()

    def test_duplicate_warning_keeps_desktop_terminal_open_for_review(self):
        self.seed()
        identity = self.pin()
        self.duplicate()
        self.prepare_desktop()
        output = self.assert_terminal_holds()
        self.assertIn("duplicate", output.lower())
        self.assertIn(identity, output)
        self.assertIn("Finished.", output)
        self.assertIn("file-ID warnings above", output)
        self.assertNotIn("contents could not be confirmed", output)
        self.assert_no_transfers()


if __name__ == "__main__":
    unittest.main()
