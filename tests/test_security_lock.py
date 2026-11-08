"""Lock and storage safety checks using disposable files and fake applications."""

import fcntl
import hashlib
import os
import stat
import subprocess
import unittest

from support import SCRIPT, InstallerCase


class SecureLockTests(InstallerCase):
    def prepare_database(self):
        self.saved_config = self.write_config()
        self.local.parent.mkdir(parents=True)
        self.local.write_text("cloud database")
        self.state_dir.mkdir(parents=True, mode=0o700)
        self.state_dir.parent.chmod(0o755)
        self.state_dir.chmod(0o700)
        self.state.write_text(hashlib.md5(b"cloud database").hexdigest() + "\n")
        self.backups = self.state_dir / "backups"
        self.backups.mkdir(mode=0o700)
        self.lock = self.state_dir / "launcher.lock"

    def run_action(self, option, answers="\n"):
        # The short timeout also catches accidentally opening a planted FIFO.
        return subprocess.run(["/bin/bash", str(SCRIPT), option],
                              input=answers, text=True, capture_output=True,
                              env=self.env, timeout=5)

    def assert_preserved_without_external_actions(self):
        self.assertEqual(self.local.read_text(), "cloud database")
        self.assertEqual(self.cloud.read_text(), "cloud database")
        self.assertEqual(self.config.read_text(), self.saved_config)
        self.assertFalse(self.events("rclone"))
        self.assertFalse(self.events("keepassxc"))

    def assert_storage_refused(self, option):
        result = self.run_action(option)
        self.assert_failure(result)
        self.assert_preserved_without_external_actions()
        return result

    def test_symlinked_lock_never_truncates_database_in_launch_sync_or_cancelled_cleanup(self):
        self.prepare_database()
        self.lock.symlink_to(self.local)
        for option in ("--launch", "--sync", "--clear-local"):
            with self.subTest(option=option):
                self.assert_storage_refused(option)
                self.assertTrue(self.lock.is_symlink())

    def test_hardlinked_lock_never_truncates_database_in_launch_sync_or_cancelled_cleanup(self):
        self.prepare_database()
        self.local.chmod(0o600)
        self.lock.hardlink_to(self.local)
        for option in ("--launch", "--sync", "--clear-local"):
            with self.subTest(option=option):
                self.assert_storage_refused(option)
                self.assertEqual(self.lock.stat().st_nlink, 2)

    def test_dangling_lock_symlink_does_not_create_its_target(self):
        self.prepare_database()
        target = self.root / "absent database.kdbx"
        self.lock.symlink_to(target)
        self.assert_storage_refused("--launch")
        self.assertFalse(target.exists())

    def test_fifo_lock_is_rejected_without_blocking(self):
        self.prepare_database()
        os.mkfifo(self.lock, mode=0o600)
        for option in ("--launch", "--sync", "--clear-local"):
            with self.subTest(option=option):
                self.assert_storage_refused(option)
                self.assertTrue(stat.S_ISFIFO(self.lock.lstat().st_mode))

    def test_directory_lock_is_rejected_without_external_actions(self):
        self.prepare_database()
        self.lock.mkdir(mode=0o700)
        for option in ("--launch", "--sync", "--clear-local"):
            with self.subTest(option=option):
                self.assert_storage_refused(option)
                self.assertTrue(self.lock.is_dir())

    def test_shared_lock_permissions_are_rejected(self):
        self.prepare_database()
        self.lock.write_text("existing lock contents")
        for mode in (0o640, 0o660, 0o604, 0o666):
            self.lock.chmod(mode)
            for option in ("--launch", "--clear-local"):
                with self.subTest(mode=oct(mode), option=option):
                    self.assert_storage_refused(option)
                    self.assertEqual(self.lock.read_text(), "existing lock contents")

    def test_existing_regular_private_lock_contents_survive_launch_and_cancelled_cleanup(self):
        self.prepare_database()
        self.lock.write_text("existing lock contents")
        self.lock.chmod(0o600)
        for option in ("--launch", "--sync", "--clear-local"):
            with self.subTest(option=option):
                result = self.run_action(option)
                if option == "--clear-local":
                    self.assertIn("Cancelled", result.stdout + result.stderr)
                    self.assertEqual(self.local.read_text(), "cloud database")
                else:
                    self.assert_success(result)
                self.assertEqual(self.lock.read_text(), "existing lock contents")

    def test_held_private_lock_blocks_launch_sync_and_maintenance(self):
        self.prepare_database()
        self.lock.touch(mode=0o600)
        with self.lock.open("r+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for option in ("--launch", "--sync", "--clear-local"):
                with self.subTest(option=option):
                    result = self.assert_storage_refused(option)
                    self.assertIn("Another copy", result.stdout + result.stderr)

    def test_writable_state_or_backup_directory_is_rejected_without_chmod(self):
        self.prepare_database()
        for directory in (self.state_dir, self.backups):
            for mode in (0o770, 0o707, 0o777):
                directory.chmod(mode)
                try:
                    for option in ("--launch", "--sync", "--clear-local"):
                        with self.subTest(path=str(directory), mode=oct(mode), option=option):
                            self.assert_storage_refused(option)
                            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), mode)
                            self.assertFalse(self.lock.exists())
                finally:
                    directory.chmod(0o700)

    def test_writable_state_ancestor_is_rejected_without_changing_it(self):
        self.prepare_database()
        ancestor = self.state_dir.parent
        ancestor.chmod(0o777)
        for option in ("--launch", "--sync", "--clear-local"):
            with self.subTest(option=option):
                self.assert_storage_refused(option)
                self.assertEqual(stat.S_IMODE(ancestor.stat().st_mode), 0o777)
                self.assertFalse(self.lock.exists())

    def test_owned_readable_storage_is_privatised_without_changing_parent_permissions(self):
        self.prepare_database()
        ancestor = self.state_dir.parent
        for directory in (ancestor, self.state_dir, self.backups):
            directory.chmod(0o755)
        self.assert_success(self.run_action("--launch"))
        self.assertEqual(stat.S_IMODE(ancestor.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(self.state_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.backups.stat().st_mode), 0o700)

    def test_symlinked_state_ancestor_is_rejected_before_lock_creation(self):
        self.prepare_database()
        ancestor = self.state_dir.parent
        actual = self.root / "actual state"
        ancestor.rename(actual)
        ancestor.symlink_to(actual, target_is_directory=True)
        for option in ("--launch", "--sync", "--clear-local"):
            with self.subTest(option=option):
                self.assert_storage_refused(option)
                self.assertFalse(self.lock.exists())

    def test_symlinked_backup_directory_is_rejected_before_lock_creation(self):
        self.prepare_database()
        actual = self.root / "actual backups"
        self.backups.rename(actual)
        self.backups.symlink_to(actual, target_is_directory=True)
        for option in ("--launch", "--sync", "--clear-local"):
            with self.subTest(option=option):
                self.assert_storage_refused(option)
                self.assertFalse(self.lock.exists())

    def test_fresh_storage_creates_private_directories_and_regular_private_lock(self):
        self.write_config()
        result = self.run_action("--launch")
        self.assert_success(result)
        for directory in (self.state_dir, self.state_dir / "backups"):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual(directory.stat().st_uid, os.geteuid())
        lock = self.state_dir / "launcher.lock"
        self.assertTrue(lock.is_file())
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)
        self.assertEqual(lock.stat().st_uid, os.geteuid())
        self.assertEqual(lock.stat().st_nlink, 1)
        self.assertEqual(self.local.read_text(), "cloud database")

    @unittest.skipUnless(os.geteuid() == 0, "Changing file ownership requires root")
    def test_foreign_owned_lock_and_state_are_rejected(self):
        self.prepare_database()
        self.lock.touch(mode=0o600)
        for target in (self.lock, self.state_dir, self.backups):
            os.chown(target, 1, -1)
            try:
                for option in ("--launch", "--clear-local"):
                    with self.subTest(path=str(target), option=option):
                        self.assert_storage_refused(option)
            finally:
                os.chown(target, os.geteuid(), -1)


if __name__ == "__main__":
    unittest.main()
