"""Configured cloud backup names are respected by publication and recovery."""

import hashlib
from pathlib import Path
import unittest

from support import LAUNCHER_REMOTE, LauncherCase


class BackupFolderTests(LauncherCase):
    def configure(self, folder="Vault copies", remote=LAUNCHER_REMOTE):
        self.folder = folder
        self.env["TEST_REMOTE"] = remote
        self.cloud = self.root / "cloud" / remote.replace(":", "/", 1)
        self.cloud.parent.mkdir(parents=True, exist_ok=True)
        self.state = self.data / (hashlib.sha256(remote.encode()).hexdigest() + ".md5")
        self.transaction = Path(str(self.state) + ".transaction")
        self.conflict = Path(str(self.state) + ".conflict")
        self.config = self.root / "config/keesyncxc/config"
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(f"{remote}\n{self.local}\n{folder}\n")
        parent = remote.rsplit("/", 1)[0] + "/" if "/" in remote else remote.split(":", 1)[0] + ":"
        return parent + folder

    def check_upload(self, remote):
        backup_directory = self.configure(remote=remote)
        self.seed(local="local edit")
        original_id = self.cloud_id()
        self.assert_success(self.run_launcher())
        self.assertEqual(self.cloud.read_text(), "local edit")
        self.assertEqual(self.cloud_id(), original_id)
        backups = list((self.cloud.parent / self.folder).glob("Passwords-*.kdbx"))
        self.assertEqual([file.read_text() for file in backups], ["original"])
        self.assertFalse((self.cloud.parent / "KeePass-Backups").exists())
        self.assertEqual(self.remote_operations("mkdir"),
                         [["mkdir", backup_directory, "--retries", "2"]])
        backup_copies = [args for args in self.remote_operations("copyto") if args[1] == remote]
        self.assertEqual(len(backup_copies), 1)
        self.assertTrue(backup_copies[0][2].startswith(backup_directory + "/Passwords-"))
        listings = [args[1] for args in self.remote_operations("lsf")
                    if args[args.index("--format") + 1] == "p"]
        self.assertIn(backup_directory, listings)

    def test_folder_database_uses_custom_backup_name_with_spaces(self):
        self.check_upload(LAUNCHER_REMOTE)

    def test_root_database_uses_custom_backup_name_with_spaces(self):
        self.check_upload("gdrive:Passwords.kdbx")

    def test_completed_custom_folder_update_recovers_without_another_transfer(self):
        destination = self.configure()
        self.seed(local="local edit")
        self.assert_blocked(self.run_launcher("replace_state"))
        fields = self.transaction.read_text().splitlines()
        self.assertTrue(fields[4].startswith(destination + "/Passwords-"))
        copies = self.remote_operations("copyto")
        directories = self.remote_operations("mkdir")
        self.assert_success(self.run_launcher())
        self.assertFalse(self.transaction.exists())
        self.assertEqual(self.state.read_text().strip(), self.digest("local edit"))
        self.assertEqual(self.remote_operations("copyto"), copies)
        self.assertEqual(self.remote_operations("mkdir"), directories)

    def test_changing_saved_folder_cannot_redirect_pending_transaction_recovery(self):
        self.configure()
        self.seed(local="local edit")
        self.assert_blocked(self.run_launcher("update_response_lost"))
        journal = self.transaction.read_bytes()
        self.configure(folder="Other copies")
        before = self.events("rclone")
        self.assert_blocked(self.run_launcher())
        self.assertEqual(self.transaction.read_bytes(), journal)
        self.assertEqual(self.events("rclone"), before)
        self.assertEqual(self.cloud.read_text(), "local edit")
        self.assertFalse((self.cloud.parent / "Other copies").exists())

    def test_conflict_information_reports_custom_backup_folder_without_cloud_access(self):
        destination = self.configure()
        self.seed(local="local edit", cloud="cloud edit")
        self.conflict.write_text("pending conflict\n")
        result = self.run_launcher(choices="5\n4\n")
        self.assert_blocked(result)
        self.assertIn(destination, result.stdout + result.stderr)
        self.assertFalse(self.events("rclone"))
        self.assertEqual(self.cloud.read_text(), "cloud edit")
        self.assertEqual(self.local.read_text(), "local edit")

    def test_invalid_saved_backup_names_fail_before_cloud_access(self):
        self.configure()
        self.seed(local="local edit")
        for folder in ("", ".", "..", "some/folder", "gdrive:copies", "Passwords.kdbx",
                       "copies\tother", "copies\rother", "copies\nextra line"):
            with self.subTest(folder=folder):
                self.config.write_text(f"{LAUNCHER_REMOTE}\n{self.local}\n{folder}\n")
                self.assert_blocked(self.run_launcher())
                self.assertFalse(self.events("rclone"))
                self.assertEqual(self.cloud.read_text(), "original")
                self.assertEqual(self.local.read_text(), "local edit")


if __name__ == "__main__":
    unittest.main()
