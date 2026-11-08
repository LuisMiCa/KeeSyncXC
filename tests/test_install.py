"""Installer integration tests without package, browser, or cloud access.

Run with: python3 -m unittest discover -s tests -v
"""

import hashlib
from pathlib import Path
import stat
import unittest

from support import SCRIPT, InstallerCase, INSTALLER_REMOTE as REMOTE


# Keep the launcher's realistic transfer mocks for the installed-launcher test,
# while adding the two configuration commands and an inert package manager.


class InstallerTests(InstallerCase):
    def test_help_has_no_configuration_or_runtime_side_effects(self):
        self.write_config()
        self.config.write_text("malformed configuration\n")
        result = self.run_script("--help", answers="", PATH="/nonexistent")
        self.assert_success(result)
        self.assertTrue(result.stdout.startswith("KeeSyncXC v1.0.0\n"))
        self.assertIn("--install", result.stdout)
        self.assertIn("--version", result.stdout)
        self.assertFalse(self.state_dir.exists())
        self.assertFalse(self.installed.exists())
        self.assertFalse(self.events())

    def test_version_ignores_invalid_configuration_and_missing_dependencies(self):
        self.write_config()
        self.config.write_text("malformed configuration\n")
        result = self.run_script("--version", answers="", PATH="/nonexistent", DISPLAY=":mock")
        self.assert_success(result)
        self.assertEqual(result.stdout, "KeeSyncXC v1.0.0\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.config.read_text(), "malformed configuration\n")
        self.assertFalse(self.state_dir.exists())
        self.assertFalse(self.installed.exists())
        self.assertFalse(self.desktop.exists())
        self.assertFalse(self.events())

    def test_unknown_argument_fails_without_creating_runtime_state(self):
        result = self.run_script("--unknown-option", answers="")
        self.assert_failure(result)
        self.assertFalse(self.state_dir.exists())
        self.assertFalse(self.config.exists())
        self.assertFalse(self.events())

    def test_install_as_root_is_rejected_before_installation(self):
        result = self.run_script("--install", TEST_UID="0")
        self.assert_failure(result)
        self.assertIn("regular user", result.stderr)
        self.assertFalse(self.state_dir.exists())
        self.assertFalse(self.config.exists())
        self.assertFalse(self.installed.exists())
        self.assertFalse(self.events())

    def test_install_writes_private_config_executable_and_menu_entry(self):
        result = self.run_script("--install")
        self.assert_success(result)
        self.assertEqual(self.config.read_text(), f"{REMOTE}\n{self.local}\nKeePass-Backups\n")
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.installed.stat().st_mode), 0o700)
        self.assertEqual(self.installed.read_bytes(), SCRIPT.read_bytes())
        menu = self.desktop.read_text()
        self.assertIn("[Desktop Entry]", menu)
        self.assertIn("Name=KeeSyncXC\n", menu)
        self.assertIn("Type=Application", menu)
        self.assertIn("Terminal=true", menu)
        self.assertIn(str(self.installed), menu)
        self.assertIn('Exec=/bin/bash "', menu)
        self.assertIn('" --desktop\n', menu)
        self.assertIn("listremotes", self.operations())
        self.assertIn("md5sum", self.operations())
        self.assertNotIn("config", self.operations())
        self.assertFalse(self.events("sudo"))
        self.assertFalse(self.events("apt-get"))
        self.assertFalse(self.local.exists())
        self.assertFalse(self.state.exists())
        self.assertFalse(Path(str(self.state) + ".id").exists())
        self.assert_no_database_changes()
        events_before = self.events()
        version = self.run_script("--version", answers="", script=self.installed, PATH="/nonexistent")
        self.assert_success(version)
        self.assertEqual(version.stdout, "KeeSyncXC v1.0.0\n")
        self.assertEqual(version.stderr, "")
        self.assertEqual(self.events(), events_before)

    def test_reinstall_refuses_a_different_id_even_when_cloud_contents_match(self):
        original_config = self.write_config()
        self.state_dir.mkdir(parents=True)
        original_info = self.cloud.stat()
        original_id = f"id-{original_info.st_dev}-{original_info.st_ino}"
        identity = Path(str(self.state) + ".id")
        identity.write_text(original_id + "\n")
        replacement = self.cloud.with_name("replacement.kdbx")
        replacement.write_bytes(self.cloud.read_bytes())
        replacement.replace(self.cloud)
        result = self.run_script("--install")
        self.assert_failure(result)
        self.assertEqual(self.config.read_text(), original_config)
        self.assertEqual(identity.read_text(), original_id + "\n")
        self.assertFalse(self.installed.exists())
        self.assert_no_database_changes()

    def test_bare_root_filename_is_saved_with_the_default_remote(self):
        remote = "gdrive:kpDB.kdbx"
        self.cloud = self.cloud.rename(self.root / "cloud/gdrive/kpDB.kdbx")
        original_id = self.cloud.stat().st_ino
        result = self.run_script("--install", answers="kpDB.kdbx\n\n\nn\n", TEST_REMOTE=remote)
        self.assert_success(result)
        self.assertEqual(self.config.read_text(), f"{remote}\n{self.local}\nKeePass-Backups\n")
        self.assertIn(remote, result.stdout + result.stderr)
        self.assertEqual(self.cloud.stat().st_ino, original_id)
        self.assertTrue(self.installed.is_file())
        self.assertFalse(self.local.exists())
        state = self.state_dir / (hashlib.sha256(remote.encode()).hexdigest() + ".md5")
        self.assertFalse(state.exists())
        self.assertFalse(Path(str(state) + ".id").exists())
        paths = [event["arguments"][1] for event in self.events("rclone")
                 if event["arguments"][0] in ("lsf", "md5sum")]
        self.assertTrue(paths)
        self.assertTrue(all(path in ("gdrive:", remote) for path in paths))
        self.assert_no_database_changes()

    def test_bare_root_filename_reuses_remote_name_from_saved_settings(self):
        self.write_config(remote="archive:Vault/Previous.kdbx")
        remote = "archive:kpDB.kdbx"
        target = self.root / "cloud/archive/kpDB.kdbx"
        target.parent.mkdir(parents=True)
        self.cloud = self.cloud.rename(target)
        result = self.run_script("--install", answers="kpDB.kdbx\n\n\nn\n", TEST_REMOTE=remote)
        self.assert_success(result)
        self.assertEqual(self.config.read_text(), f"{remote}\n{self.local}\nKeePass-Backups\n")
        self.assertIn(remote, result.stdout + result.stderr)
        paths = [event["arguments"][1] for event in self.events("rclone")
                 if event["arguments"][0] in ("lsf", "md5sum")]
        self.assertTrue(paths)
        self.assertTrue(all(path in ("archive:", remote) for path in paths))
        self.assert_no_database_changes()

    def test_relative_folder_filename_is_saved_with_the_default_remote(self):
        result = self.run_script("--install", answers="KeePass/kpDB.kdbx\n\n\nn\n")
        self.assert_success(result)
        self.assertEqual(self.config.read_text(), f"{REMOTE}\n{self.local}\nKeePass-Backups\n")
        self.assert_no_database_changes()

    def test_inferred_invalid_paths_fail_before_dependencies_or_cloud_checks(self):
        self.isolate_path({"keepassxc", "rclone"})
        for relative in ("/kpDB.kdbx", "./kpDB.kdbx", "../kpDB.kdbx",
                         "KeeSyncXC//kpDB.kdbx", "KeeSyncXC/./kpDB.kdbx", "KeeSyncXC/../kpDB.kdbx"):
            with self.subTest(relative=relative):
                result = self.run_script("--install", answers=f"{relative}\n\n\nn\n")
                self.assert_failure(result)
                self.assertIn("REMOTE must name a file", result.stdout + result.stderr)
                self.assertFalse(self.events())
                self.assertFalse(self.config.exists())
                self.assertFalse(self.installed.exists())
                self.assertFalse(self.state_dir.exists())
                self.assert_no_database_changes()

    def test_new_remote_invokes_native_configuration_and_checks_cloud_file(self):
        result = self.run_script("--install", TEST_REMOTES="")
        self.assert_success(result)
        self.assertEqual(self.operations().count("config"), 1)
        self.assertTrue((self.root / "configured").exists())
        self.assertIn("md5sum", self.operations())
        self.assert_no_database_changes()

    def test_existing_remote_reconfiguration_requires_an_explicit_yes(self):
        result = self.run_script("--install", answers="\n\n\ny\n")
        self.assert_success(result)
        self.assertEqual(self.operations().count("config"), 1)
        self.assert_no_database_changes()

    def test_custom_backup_folder_is_saved_and_displayed_without_creating_it(self):
        result = self.run_script("--install", answers="\nVault copies\n\nn\n")
        self.assert_success(result)
        self.assertEqual(self.config.read_text(), f"{REMOTE}\n{self.local}\nVault copies\n")
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        self.assertIn("Cloud backups: gdrive:KeePass/Vault copies", result.stdout + result.stderr)
        self.assertFalse((self.cloud.parent / "Vault copies").exists())
        self.assert_no_database_changes()

    def test_setup_asks_for_cloud_backup_folder_before_local_path(self):
        selected_local = self.home / "My vault/Selected.kdbx"
        result = self.run_script("--install", answers=f"\nVault copies\n{selected_local}\nn\n")
        self.assert_success(result)
        output = result.stdout + result.stderr
        self.assertIn(f"Default: {REMOTE}", output)
        self.assertLess(output.index("Google Drive database ("),
                        output.index("Google Drive backup folder name"))
        self.assertLess(output.index("Google Drive backup folder name"),
                        output.index("Local database path"))
        self.assertEqual(self.config.read_text(), f"{REMOTE}\n{selected_local}\nVault copies\n")
        self.assertFalse(selected_local.exists())
        self.assert_no_database_changes()

    def test_saved_root_database_is_labeled_separately_from_the_built_in_default(self):
        remote = "gdrive:kpDB.kdbx"
        expected = self.write_config(remote=remote, backup_folder="Vault copies")
        self.cloud = self.cloud.rename(self.root / "cloud/gdrive/kpDB.kdbx")
        result = self.run_script("--install", TEST_REMOTE=remote)
        self.assert_success(result)
        output = result.stdout + result.stderr
        self.assertIn(f"Saved setting: {remote}", output)
        self.assertIn(REMOTE, output)
        self.assertNotIn(f"Default: {remote}", output)
        self.assertEqual(self.config.read_text(), expected)
        self.assertIn("Cloud backups: gdrive:Vault copies", output)
        self.assert_no_database_changes()

    def test_built_in_default_can_be_selected_explicitly_over_a_saved_root_path(self):
        self.write_config(remote="gdrive:kpDB.kdbx")
        result = self.run_script("--install", answers=f"{REMOTE}\n\n\nn\n")
        self.assert_success(result)
        self.assertEqual(self.config.read_text(), f"{REMOTE}\n{self.local}\nKeePass-Backups\n")
        self.assert_no_database_changes()

    def test_reinstall_retains_custom_backup_folder_when_prompt_is_blank(self):
        expected = self.write_config(backup_folder="Vault copies")
        result = self.run_script("--install")
        self.assert_success(result)
        self.assertEqual(self.config.read_text(), expected)
        self.assertIn("Vault copies", result.stdout + result.stderr)
        self.assert_no_database_changes()

    def test_reinstall_preserves_previously_saved_cloud_paths(self):
        remote = "gdrive:KeeSyncXC/kpDB.kdbx"
        expected = self.write_config(remote=remote, backup_folder="KeeSyncXC-Backups")
        destination = self.root / "cloud/gdrive/KeeSyncXC/kpDB.kdbx"
        destination.parent.mkdir(parents=True)
        self.cloud = self.cloud.rename(destination)
        result = self.run_script("--install", TEST_REMOTE=remote)
        self.assert_success(result)
        self.assertEqual(self.config.read_text(), expected)
        self.assertIn('Cloud backups: gdrive:KeeSyncXC/KeeSyncXC-Backups', result.stdout)
        self.assert_no_database_changes()

    def test_invalid_backup_names_fail_before_packages_or_cloud_access(self):
        self.isolate_path({"keepassxc", "rclone"})
        for folder in (".", "..", "Vault/copies", "/copies", "copies/", "gdrive:copies",
                       "kpDB.kdbx", "copies\tother", "copies\r"):
            with self.subTest(folder=folder):
                result = self.run_script("--install", answers=f"\n{folder}\n\nn\n")
                self.assert_failure(result)
                self.assertFalse(self.events())
                self.assertFalse(self.config.exists())
                self.assertFalse(self.installed.exists())
                self.assertFalse(self.state_dir.exists())
                self.assert_no_database_changes()

    def test_changing_backup_name_cannot_bypass_pending_recovery(self):
        original = self.write_config(backup_folder="Current copies")
        self.state_dir.mkdir(parents=True)
        for suffix in (".transaction", ".conflict"):
            with self.subTest(suffix=suffix):
                pending = Path(str(self.state) + suffix)
                pending.write_text("pending recovery\n")
                result = self.run_script("--install", answers="\nOther copies\n\nn\n")
                self.assert_failure(result)
                self.assertEqual(self.config.read_text(), original)
                self.assertEqual(pending.read_text(), "pending recovery\n")
                self.assertFalse(self.installed.exists())
                self.assertFalse(self.events("rclone"))
                self.assert_no_database_changes()
                pending.unlink()

    def test_missing_cloud_database_stops_before_installing(self):
        self.cloud.unlink()
        result = self.run_script("--install")
        self.assert_failure(result)
        self.assertFalse(self.installed.exists())
        self.assertFalse(self.config.exists())
        self.assertFalse(self.desktop.exists())
        self.assertFalse(self.local.exists())
        self.assertFalse(self.events("keepassxc"))
        self.assertNotIn("copyto", self.operations())
        self.assertNotIn("moveto", self.operations())

    def test_cloud_authentication_failure_preserves_previous_installation(self):
        config = self.write_config()
        self.installed.parent.mkdir(parents=True)
        self.installed.write_text("previous launcher\n")
        result = self.run_script("--install", TEST_FAULT="offline")
        self.assert_failure(result)
        self.assertEqual(self.config.read_text(), config)
        self.assertEqual(self.installed.read_text(), "previous launcher\n")
        self.assert_no_database_changes()

    def test_configuration_cancellation_stops_before_installing(self):
        result = self.run_script("--install", TEST_REMOTES="", TEST_FAULT="config")
        self.assert_failure(result)
        self.assertFalse(self.config.exists())
        self.assertFalse(self.installed.exists())
        self.assert_no_database_changes()

    def test_eof_cancellation_preserves_existing_configuration(self):
        config = self.write_config()
        for answers in ("", "\n", "\n\n", "\n\n\n"):
            with self.subTest(answers=answers):
                result = self.run_script("--install", answers=answers)
                self.assert_failure(result)
                self.assertEqual(self.config.read_text(), config)
                self.assertFalse(self.installed.exists())
                self.assert_no_database_changes()

    def test_malformed_config_is_read_as_data_and_never_executed(self):
        sentinel = self.root / "executed-config"
        self.config.parent.mkdir(parents=True)
        contents = f"$(touch {sentinel})\n{self.local}\nextra line\n"
        self.config.write_text(contents)
        result = self.run_script(answers="")
        self.assert_failure(result)
        self.assertFalse(sentinel.exists())
        self.assertEqual(self.config.read_text(), contents)
        self.assertFalse(self.events())
        self.assertFalse(self.state_dir.exists())

    def test_two_line_shell_assignments_are_not_sourced(self):
        sentinel = self.root / "executed-config"
        self.config.parent.mkdir(parents=True)
        contents = f'REMOTE="$(touch {sentinel})"\nLOCAL="{self.local}"\n'
        self.config.write_text(contents)
        result = self.run_script(answers="")
        self.assert_failure(result)
        self.assertFalse(sentinel.exists())
        self.assertEqual(self.config.read_text(), contents)
        self.assertFalse(self.events())

    def test_first_install_refuses_a_different_existing_local_database(self):
        self.local.parent.mkdir(parents=True)
        self.local.write_text("unsynchronized local changes")
        result = self.run_script("--install")
        self.assert_failure(result)
        self.assertEqual(self.local.read_text(), "unsynchronized local changes")
        self.assertFalse(self.installed.exists())
        self.assertFalse(self.config.exists())
        self.assertFalse(self.state.exists())
        self.assert_no_database_changes()

    def test_first_install_preserves_a_local_database_matching_drive(self):
        self.local.parent.mkdir(parents=True)
        self.local.write_text("cloud database")
        result = self.run_script("--install")
        self.assert_success(result)
        self.assertEqual(self.local.read_text(), "cloud database")
        self.assertFalse(self.state.exists())
        self.assert_no_database_changes()

    def test_first_install_accepts_matching_local_database_with_backslashes(self):
        self.local = self.home / r"Documents\KeeSyncXC" / r"Pass\words.kdbx"
        self.local.parent.mkdir(parents=True)
        self.local.write_text("cloud database")
        result = self.run_script("--install", answers=f"\n\n{self.local}\nn\n")
        self.assert_success(result)
        self.assertEqual(self.config.read_text().splitlines()[1], str(self.local))
        self.assertEqual(self.local.read_text(), "cloud database")
        self.assertFalse(self.state.exists())
        self.assert_no_database_changes()

    def test_invalid_baseline_stops_reinstallation_without_overwriting_edits(self):
        config = self.write_config()
        self.local.parent.mkdir(parents=True)
        self.local.write_text("unsynchronized local changes")
        self.state_dir.mkdir(parents=True)
        self.state.write_text("invalid baseline\n")
        result = self.run_script("--install")
        self.assert_failure(result)
        self.assertEqual(self.config.read_text(), config)
        self.assertEqual(self.local.read_text(), "unsynchronized local changes")
        self.assertEqual(self.state.read_text(), "invalid baseline\n")
        self.assertFalse(self.installed.exists())
        self.assert_no_database_changes()

    def test_changing_paths_cannot_bypass_an_unresolved_conflict(self):
        config = self.write_config()
        self.state_dir.mkdir(parents=True)
        conflict = Path(str(self.state) + ".conflict")
        conflict.write_text("Unresolved conflict\n")
        alternate = self.home / "another location/Passwords.kdbx"
        result = self.run_script("--install", answers=f"\n\n{alternate}\nn\n")
        self.assert_failure(result)
        self.assertEqual(self.config.read_text(), config)
        self.assertEqual(conflict.read_text(), "Unresolved conflict\n")
        self.assertFalse(self.installed.exists())
        self.assertFalse(self.events("rclone"))
        self.assert_no_database_changes()

    def test_reinstall_retains_unsynced_edits_baseline_and_history(self):
        config = self.write_config()
        self.local.parent.mkdir(parents=True)
        self.local.write_text("unsynchronized local changes")
        self.state_dir.mkdir(parents=True)
        baseline = hashlib.md5(b"cloud database").hexdigest() + "\n"
        self.state.write_text(baseline)
        backups = self.state_dir / "backups"
        backups.mkdir()
        history = backups / "old-safety-copy.kdbx"
        history.write_text("previous database")
        result = self.run_script("--install")
        self.assert_success(result)
        self.assertEqual(self.config.read_text(), config)
        self.assertEqual(self.local.read_text(), "unsynchronized local changes")
        self.assertEqual(self.state.read_text(), baseline)
        self.assertEqual(history.read_text(), "previous database")
        self.assert_no_database_changes()

    def check_changed_local_with_baseline(self, contents):
        config = self.write_config()
        self.local.parent.mkdir(parents=True)
        self.local.write_text("unsynchronized original local changes")
        self.state_dir.mkdir(parents=True)
        baseline = hashlib.md5(b"cloud database").hexdigest() + "\n"
        self.state.write_text(baseline)
        alternate = self.home / "selected vault/Passwords.kdbx"
        alternate.parent.mkdir()
        alternate.write_text(contents)
        result = self.run_script("--install", answers=f"\n\n{alternate}\nn\n")
        self.assertEqual(self.local.read_text(), "unsynchronized original local changes")
        self.assertEqual(alternate.read_text(), contents)
        self.assertEqual(self.state.read_text(), baseline)
        self.assert_no_database_changes()
        return result, config, alternate


    def test_changed_local_cannot_use_old_baseline_to_authorize_different_contents(self):
        result, config, _ = self.check_changed_local_with_baseline("different unsynchronized database")
        self.assert_failure(result)
        self.assertEqual(self.config.read_text(), config)
        self.assertFalse(self.installed.exists())

    def test_changed_local_matching_drive_is_accepted_without_overwriting_previous_local(self):
        result, _, alternate = self.check_changed_local_with_baseline("cloud database")
        self.assert_success(result)
        self.assertEqual(self.config.read_text(), f"{REMOTE}\n{alternate}\nKeePass-Backups\n")
        self.assertTrue(self.installed.exists())

    def test_installed_launcher_can_reinstall_itself(self):
        self.assert_success(self.run_script("--install"))
        before = self.installed.read_bytes()
        result = self.run_script("--install", script=self.installed)
        self.assert_success(result)
        self.assertEqual(self.installed.read_bytes(), before)
        self.assert_no_database_changes()

    def test_directory_at_installed_executable_destination_is_rejected(self):
        config = self.write_config()
        self.installed.mkdir(parents=True)
        marker = self.installed / "preserved-marker"
        marker.write_text("existing directory contents")
        result = self.run_script("--install")
        self.assert_failure(result)
        self.assertEqual(self.config.read_text(), config)
        self.assertTrue(self.installed.is_dir())
        self.assertEqual(list(self.installed.iterdir()), [marker])
        self.assertEqual(marker.read_text(), "existing directory contents")
        self.assertFalse(list(self.installed.parent.glob(".keesyncxc-install.*")))
        self.assert_no_database_changes()

    def test_no_argument_installed_launcher_uses_saved_remote_and_local_paths(self):
        remote = "archive:Vault/Another.kdbx"
        local = self.home / "selected vault/Another.kdbx"
        cloud = self.root / "cloud/archive/Vault/Another.kdbx"
        cloud.parent.mkdir(parents=True)
        cloud.write_text("selected cloud database")
        answers = f"{remote}\n\n{local}\nn\n"
        self.assert_success(self.run_script("--install", answers=answers))
        result = self.run_script(script=self.installed, answers="")
        self.assert_success(result)
        self.assertEqual(local.read_text(), "selected cloud database")
        self.assertFalse(self.local.exists())
        launches = self.events("keepassxc")
        self.assertEqual(len(launches), 1)
        self.assertEqual(launches[0]["arguments"][-1], str(local))
        transfers = [event["arguments"] for event in self.events("rclone")
                     if event["arguments"][0] == "copyto"]
        self.assertEqual(len(transfers), 1)
        self.assertEqual(transfers[0][1], remote)

    def test_only_missing_dependencies_are_installed_through_fake_sudo(self):
        self.isolate_path({"keepassxc", "rclone"})
        result = self.run_script("--install")
        self.assert_success(result)
        commands = [event["arguments"] for event in self.events("sudo")]
        self.assertEqual(len(commands), 2, commands)
        self.assertEqual(commands[0], ["apt-get", "update"])
        install = commands[1]
        self.assertEqual(install[:4], ["apt-get", "install", "-y", "--"])
        self.assertEqual(set(install[4:]), {"keepassxc", "rclone"})
        self.assertFalse(self.events("apt-get"), "Package manager must be invoked through sudo")
        self.assert_no_database_changes()

    def test_package_install_failure_stops_before_remote_or_local_changes(self):
        self.isolate_path({"keepassxc"})
        result = self.run_script("--install", TEST_FAULT="packages")
        self.assert_failure(result)
        self.assertFalse(self.config.exists())
        self.assertFalse(self.installed.exists())
        self.assertFalse(self.events("rclone"))
        self.assert_no_database_changes()


if __name__ == "__main__":
    unittest.main()
