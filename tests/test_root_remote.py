"""Root-level Drive paths use the same sync safeguards as folder paths.

All databases, credentials, applications, and remote operations are isolated mocks.
Run with: python3 -m unittest discover -s tests -p 'test_root_remote.py' -v
"""

import hashlib
from pathlib import Path

from support import LAUNCHER_REMOTE, LauncherCase, InstallerCase
import test_launcher as launcher


ROOT_REMOTE = "gdrive:Passwords.kdbx"
UUID = "12345678-1234-1234-1234-123456789abc"
BACKUP_PATTERN = r"gdrive:KeePass-Backups/Passwords-\d{8}T\d{6}Z-[a-f0-9-]{36}\.kdbx"


class RootRemoteTests(LauncherCase):
    # Reuse the isolated harness without rediscovering the folder suite here.

    def setUp(self):
        super().setUp()
        self.env["TEST_REMOTE"] = ROOT_REMOTE
        self.cloud = self.root / "cloud/gdrive/Passwords.kdbx"
        self.config = self.root / "config/keesyncxc/config"
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.write_remote(ROOT_REMOTE)
        self.state = self.data / (hashlib.sha256(ROOT_REMOTE.encode()).hexdigest() + ".md5")
        self.transaction = Path(str(self.state) + ".transaction")
        self.conflict = Path(str(self.state) + ".conflict")

    def write_remote(self, remote):
        self.config.write_text(f"{remote}\n{self.local}\n")

    def assert_root_journal(self):
        fields = self.transaction.read_text().splitlines()
        self.assertEqual(len(fields), 6)
        self.assertEqual(fields[:3], ["in-place-v2", self.digest("original"), self.digest("local edit")])
        self.assertEqual(fields[3], self.cloud_id())
        self.assertRegex(fields[4], "^" + BACKUP_PATTERN + "$")
        self.assertEqual(Path(fields[5]).parent, self.data / "backups")
        self.assertEqual(Path(fields[5]).read_text(), "local edit")

    def test_initial_download_uses_root_file_and_unchanged_remote_identity(self):
        self.cloud.write_text("cloud database")
        self.assert_success(self.run_launcher())
        self.assertEqual(self.local.read_text(), "cloud database")
        self.assertEqual(self.state.read_text().strip(), self.digest("cloud database"))
        self.assertEqual(self.state.name, hashlib.sha256(ROOT_REMOTE.encode()).hexdigest() + ".md5")
        self.assertEqual(self.remote_operations("copyto")[0][1], ROOT_REMOTE)
        self.assertTrue(self.remote_operations("lsf"))
        listings = [args for args in self.remote_operations("lsf")
                    if args[args.index("--format") + 1] == "p"]
        identities = [args for args in self.remote_operations("lsf")
                      if args[args.index("--format") + 1] == "ih"]
        self.assertTrue(all(args[1] == "gdrive:" for args in listings))
        self.assertTrue(identities)
        self.assertTrue(all(args[1] == ROOT_REMOTE for args in identities))
        folder_state = self.data / (hashlib.sha256(LAUNCHER_REMOTE.encode()).hexdigest() + ".md5")
        self.assertFalse(folder_state.exists())

    def test_changed_local_upload_preserves_id_and_copies_backup_in_separate_folder(self):
        self.seed(local="local edit")
        original_id = self.cloud_id()
        self.assert_success(self.run_launcher())
        self.assertEqual(self.cloud.read_text(), "local edit")
        self.assertEqual([file.read_text() for file in self.cloud_backups()], ["original"])
        uploads = [args for args in self.remote_operations("copyto")
                   if not args[1].startswith("gdrive:")]
        self.assertEqual(len(uploads), 1)
        self.assertNotEqual(uploads[0][1], str(self.local))
        self.assertEqual(uploads[0][2], ROOT_REMOTE)
        self.assertEqual(self.cloud_id(), original_id)
        backups = [args for args in self.remote_operations("copyto") if args[1] == ROOT_REMOTE]
        self.assertEqual(len(backups), 1)
        self.assertRegex(backups[0][2], "^" + BACKUP_PATTERN + "$")
        self.assertEqual(self.cloud_backups()[0].parent, self.cloud.parent / "KeePass-Backups")
        self.assertFalse(list(self.cloud.parent.glob("Passwords-*.kdbx")))
        self.assertEqual(self.remote_operations("mkdir"),
                         [["mkdir", "gdrive:KeePass-Backups", "--retries", "2"]])
        self.assertFalse(self.remote_operations("moveto"))
        self.assertEqual(self.state.read_text().strip(), self.digest("local edit"))
        self.assertFalse(self.transaction.exists())

    test_cloud_change_is_downloaded_and_local_backed_up = (
        launcher.LauncherTests.test_remote_update_is_downloaded_and_local_backed_up)
    test_conflict_cancel_preserves_both_versions = (
        launcher.LauncherTests.test_conflict_cancel_preserves_both_versions_without_upload)
    test_conflict_adopt_cloud_then_publish_reapplied_edit = (
        launcher.LauncherTests.test_open_cloud_preserves_local_then_syncs_edits_against_new_baseline)
    test_conflict_review_then_explicitly_publish_local = (
        launcher.LauncherTests.test_reference_edits_can_be_explicitly_published_after_close)

    def test_failed_root_update_keeps_id_and_requires_explicit_retry(self):
        self.seed(local="local edit")
        original_id = self.cloud_id()
        self.assert_blocked(self.run_launcher("update"))
        self.assert_root_journal()
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertEqual([file.read_text() for file in self.cloud_backups()], ["original"])
        self.assert_blocked(self.run_launcher())
        self.assertEqual(self.cloud.read_text(), "original")
        self.assertEqual(self.state.read_text().strip(), self.digest("original"))
        self.assertFalse(self.transaction.exists())
        self.assertTrue(self.conflict.exists())
        self.assert_success(self.run_launcher(choices="3\n"))
        self.assertEqual(self.cloud.read_text(), "local edit")
        self.assertEqual(self.cloud_id(), original_id)
        self.assertFalse(self.transaction.exists())
        self.assertFalse(self.conflict.exists())

    def test_root_post_update_state_failure_recovers_without_reupload(self):
        self.seed(local="local edit")
        self.assert_blocked(self.run_launcher("replace_state"))
        self.assertEqual(self.cloud.read_text(), "local edit")
        self.assert_root_journal()
        uploads_before = len(self.remote_operations("copyto"))
        self.assert_success(self.run_launcher())
        self.assertEqual(self.state.read_text().strip(), self.digest("local edit"))
        self.assertFalse(self.transaction.exists())
        self.assertEqual(len(self.remote_operations("copyto")), uploads_before)

    test_completed_root_update_recovers_recorded_backup_without_transfer = (
        launcher.LauncherTests.test_completed_update_recovers_recorded_backup_without_transfer)

    test_unfinished_root_update_retains_backup_and_requires_decision = (
        launcher.LauncherTests.test_unfinished_update_retains_backup_and_requires_decision)

    test_failed_root_backup_directory_creation_preserves_canonical = (
        launcher.LauncherTests.test_failed_backup_directory_creation_never_uploads_or_writes_a_transaction)

    test_unchanged_root_session_does_not_create_remote_backup_folder = (
        launcher.LauncherTests.test_unchanged_session_does_not_create_remote_backup_folder)

    def test_invalid_remote_paths_fail_before_cloud_or_database_changes(self):
        self.seed(local="local edit")
        baseline = self.state.read_text()
        for remote in ("kpDB.kdbx", "gdrive:", "gdrive:Passwords.kdbx/", "gdrive:/Passwords.kdbx",
                       "gdrive:./Passwords.kdbx", "gdrive:../Passwords.kdbx",
                       "gdrive:KeeSyncXC/./Passwords.kdbx", "gdrive:KeeSyncXC/../Passwords.kdbx",
                       "-gdrive:Passwords.kdbx"):
            with self.subTest(remote=remote):
                self.write_remote(remote)
                self.assert_blocked(self.run_launcher())
                self.assertEqual(self.local.read_text(), "local edit")
                self.assertEqual(self.cloud.read_text(), "original")
                self.assertEqual(self.state.read_text(), baseline)
                self.assertFalse(self.events("rclone"))

    def test_recovery_rejects_and_preserves_cross_parent_or_remote_paths(self):
        self.seed(local="local edit")
        backups = self.data / "backups"
        backups.mkdir()
        snapshot = backups / "upload-retained.kdbx"
        snapshot.write_text("local edit")
        name = f"Passwords-20261101T000000Z-{UUID}.kdbx"
        for backup in ("gdrive:" + name,
                       "gdrive:KeeSyncXC/KeePass-Backups/" + name,
                       "gdrive:Other-Backups/" + name,
                       "archive:KeePass-Backups/" + name):
            with self.subTest(backup=backup):
                journal = "\n".join(("in-place-v2", self.digest("original"),
                                     self.digest("local edit"), self.cloud_id(),
                                     backup, str(snapshot))) + "\n"
                self.transaction.write_text(journal)
                self.assert_blocked(self.run_launcher())
                self.assertEqual(self.transaction.read_text(), journal)
                self.assertEqual(snapshot.read_text(), "local edit")
                self.assertEqual(self.local.read_text(), "local edit")
                self.assertEqual(self.cloud.read_text(), "original")
                self.assertFalse(self.events("rclone"))


class RootInstallerTests(InstallerCase):
    def setUp(self):
        super().setUp()
        self.env["TEST_REMOTE"] = ROOT_REMOTE
        original = self.cloud
        self.cloud = self.root / "cloud/gdrive/Passwords.kdbx"
        original.rename(self.cloud)
        self.state = self.state_dir / (hashlib.sha256(ROOT_REMOTE.encode()).hexdigest() + ".md5")

    def test_install_accepts_and_preserves_root_database_path(self):
        result = self.run_script("--install", answers=f"{ROOT_REMOTE}\n\n\nn\n")
        self.assert_success(result)
        self.assertEqual(self.config.read_text(), f"{ROOT_REMOTE}\n{self.local}\nKeePass-Backups\n")
        self.assertTrue(self.installed.is_file())
        self.assertTrue(self.desktop.is_file())
        listings = [event["arguments"] for event in self.events("rclone")
                    if event["arguments"][0] == "lsf"
                    and event["arguments"][event["arguments"].index("--format") + 1] == "p"]
        self.assertTrue(listings)
        self.assertTrue(all(args[1] == "gdrive:" for args in listings))
        self.assertFalse(self.state.exists())
        self.assert_no_database_changes()

    def test_reinstall_keeps_existing_two_line_paths_and_adds_default_backup_name(self):
        expected = f"{ROOT_REMOTE}\n{self.local}\n"
        self.config.parent.mkdir(parents=True)
        self.config.write_text(expected)
        before = self.cloud.stat()
        self.assert_success(self.run_script("--install"))
        self.assertEqual(self.config.read_text(), expected + "KeePass-Backups\n")
        self.assertEqual(len(self.config.read_text().splitlines()), 3)
        self.assertEqual(self.cloud.stat().st_ino, before.st_ino)
        self.assertFalse((self.cloud.parent / "KeePass-Backups").exists())
        self.assert_no_database_changes()
