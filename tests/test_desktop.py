"""Menu and file-manager feedback without a graphical terminal or real cloud."""

import hashlib
import json
import os
import pty
import shutil
import subprocess
import time
import unittest

from support import SCRIPT, InstallerCase


class DesktopTests(InstallerCase):
    def prepare_installation(self):
        self.write_config()
        self.installed.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SCRIPT, self.installed)
        self.installed.chmod(0o700)
        self.desktop.parent.mkdir(parents=True, exist_ok=True)
        self.desktop.write_text("[Desktop Entry]\nType=Application\nTerminal=true\n")

    def assert_terminal_dispatch(self, result, script=SCRIPT):
        self.assert_success(result)
        self.assertEqual(self.events(), [{"command": "x-terminal-emulator", "arguments":
                                         ["-e", "/bin/bash", str(script), "--desktop"]}])
        self.assertFalse(self.events("rclone"))
        self.assertFalse(self.events("keepassxc"))

    def test_double_click_first_run_opens_terminal_before_setup_or_cloud_access(self):
        self.mock_terminal()
        self.assert_terminal_dispatch(self.run_from_file_manager())
        self.assertFalse(self.config.exists())
        self.assertFalse(self.state_dir.exists())

    def test_double_click_installed_launcher_opens_terminal_before_network_access(self):
        self.prepare_installation()
        self.mock_terminal()
        self.assert_terminal_dispatch(self.run_from_file_manager(script=self.installed),
                                      script=self.installed)
        self.assertFalse(self.local.exists())
        self.assertFalse(self.state_dir.exists())

    def test_double_click_invalid_configuration_opens_terminal_before_reporting_error(self):
        self.prepare_installation()
        self.config.write_text("invalid configuration\n")
        self.mock_terminal()
        self.assert_terminal_dispatch(self.run_from_file_manager(script=self.installed),
                                      script=self.installed)
        self.assertEqual(self.config.read_text(), "invalid configuration\n")

    def test_desktop_option_without_terminal_opens_terminal_before_checks(self):
        self.mock_terminal()
        self.assert_terminal_dispatch(self.run_from_file_manager("--desktop"))

    def test_double_click_preserves_script_path_with_spaces_and_shell_characters(self):
        self.mock_terminal()
        script = self.root / "launcher folder" / "launch 'vault' $(never-run).sh"
        script.parent.mkdir()
        shutil.copyfile(SCRIPT, script)
        self.assert_terminal_dispatch(self.run_from_file_manager(script=script), script=script)

    def test_double_click_wayland_session_opens_terminal(self):
        self.mock_terminal()
        env = dict(self.env, WAYLAND_DISPLAY="wayland-mock")
        result = subprocess.run(["/bin/bash", str(SCRIPT)],
                                stdin=subprocess.DEVNULL, text=True, capture_output=True,
                                env=env, timeout=20)
        self.assert_terminal_dispatch(result)

    def test_piped_setup_answers_in_graphical_session_do_not_open_a_terminal(self):
        self.mock_terminal()
        result = self.run_script(DISPLAY=":mock")
        self.assert_success(result)
        self.assertFalse(self.events("x-terminal-emulator"))
        self.assertTrue(self.config.is_file())
        self.assertTrue(self.installed.is_file())

    def test_graphical_status_remains_offline_without_opening_terminal(self):
        self.mock_terminal()
        result = self.run_from_file_manager("--status")
        self.assert_success(result)
        self.assertFalse(self.events())

    def test_explicit_launch_in_graphical_session_keeps_direct_mode(self):
        self.prepare_installation()
        self.mock_terminal()
        result = self.run_from_file_manager("--launch", script=self.installed)
        self.assert_success(result)
        self.assertFalse(self.events("x-terminal-emulator"))
        self.assertEqual(len(self.events("keepassxc")), 1)

    def test_desktop_child_report_guard_prevents_opening_a_second_terminal(self):
        self.prepare_installation()
        self.config.write_text("invalid configuration\n")
        self.mock_terminal()
        result = self.run_from_file_manager(KEESYNCXC_DESKTOP_REPORT="1")
        self.assert_failure(result)
        self.assertIn("Invalid configuration", result.stderr)
        self.assertFalse(self.events())

    def test_existing_terminal_output_with_redirected_input_does_not_open_another(self):
        self.mock_terminal()
        for terminal_stream in ("stdout", "stderr"):
            with self.subTest(terminal_stream=terminal_stream):
                master, slave = pty.openpty()
                process = subprocess.Popen(
                    ["/bin/bash", str(SCRIPT)], stdin=subprocess.DEVNULL,
                    stdout=slave if terminal_stream == "stdout" else subprocess.PIPE,
                    stderr=slave if terminal_stream == "stderr" else subprocess.PIPE,
                    env=dict(self.env, DISPLAY=":mock"))
                os.close(slave)
                try:
                    stdout, stderr = process.communicate(timeout=10)
                    output = self.read_available(master) + (stdout or b"").decode() + (stderr or b"").decode()
                    self.assertNotEqual(process.returncode, 0, output)
                    self.assertIn("Action cancelled", output)
                    self.assertFalse(self.events("x-terminal-emulator"))
                    self.assertFalse(self.events("rclone"))
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)
                    os.close(master)

    def test_missing_terminal_reports_graphical_error_before_setup_or_sync(self):
        self.isolate_path({"x-terminal-emulator"})
        result = self.run_from_file_manager()
        self.assert_failure(result)
        dialogs = self.events("zenity")
        self.assertEqual(len(dialogs), 1)
        self.assertIn("--error", dialogs[0]["arguments"])
        self.assertIn("terminal", " ".join(dialogs[0]["arguments"]).lower())
        self.assertFalse(self.events("rclone"))
        self.assertFalse(self.events("keepassxc"))
        self.assertFalse(self.config.exists())
        self.assertFalse(self.state_dir.exists())

    def test_desktop_first_run_still_installs_without_opening_database(self):
        result = self.run_script("--desktop")
        self.assert_success(result)
        self.assertIn("Starting KeeSyncXC...", result.stdout)
        self.assertIn("starting installation", result.stdout)
        self.assertLess(result.stdout.index("Starting KeeSyncXC"),
                        result.stdout.index("starting installation"))
        self.assertTrue(self.installed.is_file())
        self.assertTrue(self.config.is_file())
        self.assertFalse(self.events("keepassxc"))
        self.assertNotIn("copyto", self.operations())
        self.assertNotIn("Press Enter", result.stdout)

    def test_nonterminal_desktop_success_runs_sync_without_pause_or_countdown(self):
        self.prepare_installation()
        result = self.run_script("--desktop", script=self.installed, answers="")
        self.assert_success(result)
        self.assertEqual(self.local.read_text(), "cloud database")
        self.assertEqual(len(self.events("keepassxc")), 1)
        self.assertIn("Starting launcher checks...", result.stdout)
        self.assertIn("Opening KeePassXC:", result.stdout)
        self.assertIn("Waiting for KeePassXC to fully exit", result.stdout)
        self.assertIn("checking Google Drive for saved changes", result.stdout)
        self.assertIn("Finished.", result.stdout)
        self.assertNotIn("Press Enter", result.stdout)
        self.assertNotIn("Auto-closing", result.stdout)

    def test_nonterminal_saved_changes_sync_without_pause_or_countdown(self):
        self.prepare_installation()
        result = self.run_script("--desktop", script=self.installed, answers="",
                                 TEST_EDITS='["saved edit"]')
        self.assert_success(result)
        self.assertEqual(self.local.read_text(), "saved edit")
        self.assertEqual(self.cloud.read_text(), "saved edit")
        self.assertEqual(self.state.read_text(), hashlib.md5(b"saved edit").hexdigest() + "\n")
        self.assertNotIn("Press Enter", result.stdout)
        self.assertNotIn("Auto-closing", result.stdout)

    def test_invalid_configuration_reports_error_without_pausing_a_pipe(self):
        self.prepare_installation()
        self.config.write_text("invalid configuration\n")
        result = self.run_script("--desktop", answers="")
        self.assert_failure(result)
        self.assertIn("Starting KeeSyncXC...", result.stdout)
        self.assertIn("Invalid configuration", result.stderr)
        self.assertNotIn("Press Enter", result.stdout + result.stderr)
        self.assertFalse(self.events())
        self.assertFalse(self.state_dir.exists())

    def test_feedback_is_visible_while_the_first_network_request_is_waiting(self):
        self.prepare_installation()
        self.local.parent.mkdir(parents=True, exist_ok=True)
        self.local.write_text("cloud database")
        self.state_dir.mkdir(parents=True)
        self.state.write_text(hashlib.md5(b"cloud database").hexdigest() + "\n")
        self.env["TEST_NETWORK_GATE"] = "1"
        process = subprocess.Popen(["/bin/bash", str(self.installed), "--desktop"],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   env=dict(self.env, TEST_FAULT="offline"))
        try:
            self.wait_for_file(self.root / "network-waiting", process)
            early = self.read_available(process.stdout.fileno())
            self.assertIsNone(process.poll())
            self.assertIn("Starting KeeSyncXC...", early)
            self.assertIn("Checking Google Drive...", early)
            self.assertIn("may take a while", early)
            self.assertFalse(self.events("keepassxc"))
            (self.root / "allow-network").touch()
            remaining, _ = process.communicate(timeout=20)
            output = early + remaining.decode()
            self.assertNotEqual(process.returncode, 0, output)
            self.assertIn("Local database is available offline", output)
            self.assertIn("Post-close sync deferred", output)
            self.assertIn("Opening KeePassXC:", output)
            self.assertEqual(len(self.events("keepassxc")), 1)
            self.assertEqual(self.cloud.read_text(), "cloud database")
            self.assertNotIn("Press Enter", output)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()

    def child_failure_mock(self, status=37):
        child = self.binaries / "bash"
        child.write_text(f"#!/bin/sh\nprintf 'Child failed visibly\\n' >&2\nexit {status}\n")
        child.chmod(0o755)

    def test_nonterminal_failure_preserves_child_status_without_prompting(self):
        self.child_failure_mock()
        result = self.run_script("--desktop", answers="")
        self.assertEqual(result.returncode, 37, result.stdout + result.stderr)
        self.assertIn("Child failed visibly", result.stderr)
        self.assertNotIn("Press Enter", result.stdout + result.stderr)
        self.assertFalse(self.events())

    def test_terminal_failure_stays_visible_until_enter_and_preserves_status(self):
        self.child_failure_mock()
        master, slave = pty.openpty()
        process = subprocess.Popen(["/bin/bash", str(SCRIPT), "--desktop"],
                                   stdin=slave, stdout=slave, stderr=slave, env=self.env)
        os.close(slave)
        try:
            output = ""
            deadline = time.monotonic() + 5
            while "Press Enter" not in output and time.monotonic() < deadline:
                output += self.read_available(master, timeout=0.2)
                if process.poll() is not None:
                    break
            self.assertIn("Starting KeeSyncXC...", output)
            self.assertIn("Child failed visibly", output)
            self.assertIn("exit status 37", output)
            self.assertIn("Press Enter", output)
            self.assertIsNone(process.poll(), output)
            os.write(master, b"\n")
            self.assertEqual(process.wait(timeout=5), 37)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            os.close(master)

    def test_unchanged_synchronized_terminal_session_closes_after_visible_countdown(self):
        self.prepare_installation()
        master, slave = pty.openpty()
        process = subprocess.Popen(["/bin/bash", str(self.installed), "--desktop"],
                                   stdin=slave, stdout=slave, stderr=slave, env=self.env)
        os.close(slave)
        try:
            output = ""
            countdown_started = None
            deadline = time.monotonic() + 15
            while process.poll() is None and time.monotonic() < deadline:
                output += self.read_available(master, timeout=0.2)
                if countdown_started is None and "Auto-closing in 3 seconds" in output:
                    countdown_started = time.monotonic()
            output += self.read_available(master)
            self.assertEqual(process.poll(), 0, output)
            self.assertIn("Finished.", output)
            for seconds in (3, 2, 1):
                self.assertIn(f"Auto-closing in {seconds} second", output)
            self.assertIsNotNone(countdown_started, output)
            self.assertGreaterEqual(time.monotonic() - countdown_started, 2.7)
            self.assertNotIn("Press Enter", output)
            self.assertEqual(len(self.events("keepassxc")), 1)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            os.close(master)

    def test_saved_editor_changes_keep_terminal_open_after_successful_upload(self):
        self.prepare_installation()
        output = self.assert_terminal_holds(TEST_EDITS='["saved edit"]')
        self.assertIn("Finished.", output)
        self.assertEqual(self.local.read_text(), "saved edit")
        self.assertEqual(self.cloud.read_text(), "saved edit")
        self.assertEqual(self.state.read_text(), hashlib.md5(b"saved edit").hexdigest() + "\n")

    def test_reference_editor_changes_remain_sticky_when_cloud_is_adopted_later(self):
        self.prepare_installation()
        self.local.parent.mkdir(parents=True)
        self.local.write_text("local edit")
        self.cloud.write_text("cloud edit")
        self.state_dir.mkdir(parents=True)
        self.state.write_text(hashlib.md5(b"original").hexdigest() + "\n")
        output = self.assert_terminal_holds(choices="1\n2\n",
                                            TEST_EDITS=json.dumps(["reference edit", None]))
        self.assertIn("Finished.", output)
        self.assertEqual(len(self.events("keepassxc")), 2)
        self.assertEqual(self.local.read_text(), "cloud edit")
        self.assertEqual(self.cloud.read_text(), "cloud edit")

    def test_success_without_a_verified_session_report_keeps_terminal_open(self):
        self.prepare_installation()
        self.child_failure_mock(status=0)
        self.assert_terminal_holds()

    def test_malformed_success_report_keeps_terminal_open(self):
        self.prepare_installation()
        child = self.binaries / "bash"
        child.write_text("#!/bin/sh\nprintf 'synced-unchanged\\nextra\\n' >&8\nexit 0\n")
        child.chmod(0o755)
        self.assert_terminal_holds()

    def test_first_run_setup_keeps_terminal_open_for_review(self):
        output = self.assert_terminal_holds(script=SCRIPT, choices="\n\n\nn\n")
        self.assertIn("Installation complete", output)
        self.assertTrue(self.config.is_file())
        self.assertTrue(self.installed.is_file())
        self.assertFalse(self.events("keepassxc"))


if __name__ == "__main__":
    unittest.main()
