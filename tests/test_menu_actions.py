"""Safety checks for settings changes and local backup recovery."""

import hashlib
from pathlib import Path
import unittest

from support import SCRIPT, InstallerCase


class MenuActionSafetyTests(InstallerCase):
    def prepare_database(self):
        self.original_config = self.write_config()
        self.local.parent.mkdir(parents=True, exist_ok=True)
        self.local.write_text("working database")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state.write_text(hashlib.md5(b"last shared contents").hexdigest() + "\n")
        self.identity = Path(str(self.state) + ".id")
        self.identity.write_text("original-drive-id\n")
        self.backups = self.state_dir / "backups"
        self.backups.mkdir()
        self.backup = self.backups / "local-20000101T000000Z-Ab12Cd34.kdbx"
        self.backup.write_text("older backup")

    def install_command(self, name, contents):
        command = self.binaries / name
        command.write_text("#!/bin/bash\n" + contents)
        command.chmod(0o700)

    def assert_original_preserved(self):
        self.assertEqual(self.local.read_text(), "working database")
        self.assertEqual(self.config.read_text(), self.original_config)
        self.assertEqual(self.state.read_text(), hashlib.md5(b"last shared contents").hexdigest() + "\n")
        self.assertEqual(self.identity.read_text(), "original-drive-id\n")
        self.assertFalse(self.events("rclone"))
        self.assertFalse(self.events("keepassxc"))

    def test_move_rejects_symlinked_parent_and_dangling_target(self):
        self.prepare_database()
        actual = self.home / "actual destination"
        actual.mkdir()
        link = self.home / "linked destination"
        link.symlink_to(actual, target_is_directory=True)
        dangling = self.home / "dangling.kdbx"
        dangling.symlink_to(self.home / "absent.kdbx")
        for target in (link / "moved.kdbx", dangling):
            with self.subTest(target=target):
                result = self.run_script("--move-local", answers=f"{target}\ny\n")
                self.assert_failure(result)
                self.assert_original_preserved()
        self.assertFalse((actual / "moved.kdbx").exists())
        self.assertTrue(dangling.is_symlink())

    def test_move_rejects_relative_and_launcher_storage_targets(self):
        self.prepare_database()
        for target in ("relative.kdbx", self.state_dir / "moved.kdbx",
                       self.config.parent / "moved.kdbx", SCRIPT):
            with self.subTest(target=target):
                result = self.run_script("--move-local", answers=f"{target}\ny\n")
                self.assert_failure(result)
                self.assert_original_preserved()

    def test_move_failed_settings_commit_keeps_original_and_verified_new_copy(self):
        self.prepare_database()
        target = self.home / "moved/Vault.kdbx"
        self.install_command("mv", '''if [[ "${@: -1}" == "$TEST_FAILED_CONFIG" ]]; then exit 1; fi
exec /usr/bin/mv "$@"
''')
        result = self.run_script("--move-local", answers=f"{target}\ny\n",
                                 TEST_FAILED_CONFIG=str(self.config))
        self.assert_failure(result)
        self.assert_original_preserved()
        self.assertEqual(target.read_text(), "working database")

    def test_move_rejects_corrupted_copy_without_publishing_target(self):
        self.prepare_database()
        target = self.home / "moved/Vault.kdbx"
        self.install_command("cp", '''/usr/bin/cp "$@" || exit $?
printf 'corrupted contents' >"${@: -1}"
''')
        result = self.run_script("--move-local", answers=f"{target}\ny\n")
        self.assert_failure(result)
        self.assert_original_preserved()
        self.assertFalse(target.exists())

    def test_move_refuses_destination_directory_appearing_during_copy(self):
        self.prepare_database()
        target = self.home / "moved/Vault.kdbx"
        self.install_command("cp", '''/usr/bin/cp "$@" || exit $?
mkdir -- "$TEST_APPEARING_TARGET"
''')
        result = self.run_script("--move-local", answers=f"{target}\ny\n",
                                 TEST_APPEARING_TARGET=str(target))
        self.assert_failure(result)
        self.assert_original_preserved()
        self.assertTrue(target.is_dir())
        self.assertEqual(list(target.iterdir()), [])

    def test_move_cannot_remove_a_hardlink_to_the_running_script(self):
        self.prepare_database()
        script_copy = self.root / "source.sh"
        script_copy.write_bytes(SCRIPT.read_bytes())
        self.local.unlink()
        self.local.hardlink_to(script_copy)
        before = self.local.read_bytes()
        result = self.run_script("--move-local", script=script_copy,
                                 answers=f"{self.home / 'moved.kdbx'}\ny\n")
        self.assert_failure(result)
        self.assertEqual(self.local.read_bytes(), before)
        self.assertEqual(self.config.read_text(), self.original_config)
        self.assertTrue(script_copy.is_file())

    def test_restore_rejects_corrupt_restoration_after_verified_safety_copy(self):
        self.prepare_database()
        self.install_command("cp", '''/usr/bin/cp "$@" || exit $?
case "${@: -1}" in */.keesyncxc-download.*) printf 'corrupt restore' >"${@: -1}" ;; esac
''')
        result = self.run_script("--restore-backup", answers="1\ny\n")
        self.assert_failure(result)
        self.assert_original_preserved()
        self.assertEqual(self.backup.read_text(), "older backup")
        safety = [p for p in self.backups.glob("local-*.kdbx") if p != self.backup]
        self.assertEqual(len(safety), 1)
        self.assertEqual(safety[0].read_text(), "working database")

    def test_restore_refuses_symlinked_baseline_without_reading_cloud(self):
        self.prepare_database()
        outside = self.root / "outside-baseline"
        self.state.replace(outside)
        self.state.symlink_to(outside)
        result = self.run_script("--restore-backup", answers="1\ny\n")
        self.assert_failure(result)
        self.assert_original_preserved()
        self.assertTrue(self.state.is_symlink())
        self.assertEqual(list(self.backups.iterdir()), [self.backup])

    def test_settings_and_recovery_refuse_symlinked_state_ancestor_before_lock_write(self):
        self.prepare_database()
        state_root = self.state_dir.parent
        actual = self.root / "actual state"
        state_root.rename(actual)
        state_root.symlink_to(actual, target_is_directory=True)
        for option, answers in (("--backup-folder", "New copies\n"),
                                ("--move-local", f"{self.home / 'moved.kdbx'}\ny\n"),
                                ("--restore-backup", "1\ny\n")):
            with self.subTest(option=option):
                result = self.run_script(option, answers=answers)
                self.assert_failure(result)
                self.assert_original_preserved()
                self.assertFalse((self.state_dir / "launcher.lock").exists())

    def test_backup_selector_excludes_symlinks_and_hardlinks_to_working_file(self):
        self.prepare_database()
        self.backup.unlink()
        self.backup.symlink_to(self.local)
        hardlink = self.backups / "upload-20000101T000000Z-Zz12Aa34.kdbx"
        hardlink.hardlink_to(self.local)
        for option in ("--inspect-backup", "--restore-backup"):
            with self.subTest(option=option):
                result = self.run_script(option, answers="1\ny\n")
                self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
                self.assertIn("No recognized local database backups", result.stdout + result.stderr)
                self.assert_original_preserved()


if __name__ == "__main__":
    unittest.main()
