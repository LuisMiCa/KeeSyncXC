"""Readable terminal output without real packages, cloud access, or applications."""

import errno
import os
import pty
import re
import select
import subprocess
import unittest

from support import SCRIPT, INSTALLER_REMOTE, LauncherCase, InstallerCase


ANSI = re.compile(r"\x1b\[[0-9;]*m")


class SetupOutputTests(InstallerCase):
    def test_setup_has_ordered_steps_summary_and_plain_captured_output(self):
        self.env.pop("NO_COLOR", None)
        result = self.run_script("--install", TERM="xterm-256color")
        self.assert_success(result)
        self.assertNotRegex(result.stdout + result.stderr, ANSI)
        self.assertEqual(re.findall(r"\[([1-5])/5\]", result.stdout),
                         ["1", "2", "3", "4", "5"])
        self.assertIn("[ OK ]", result.stdout)
        self.assertIn("Installation complete", result.stdout)
        self.assertIn(str(self.installed), result.stdout)
        self.assertIn(str(self.local), result.stdout)
        self.assertIn(INSTALLER_REMOTE, result.stdout)
        self.assertIn("Cloud backups: gdrive:KeePass/KeePass-Backups", result.stdout)
        # User-facing formatting must never enter the saved data format.
        self.assertEqual(self.config.read_text(),
                         f"{INSTALLER_REMOTE}\n{self.local}\nKeePass-Backups\n")

    def test_rclone_configuration_output_is_labeled_and_returns_to_setup(self):
        self.env["TEST_CONFIG_OUTPUT"] = "Mock rclone menu"
        result = self.run_script("--install", TEST_REMOTES="")
        self.assert_success(result)
        stages = ["rclone connection setup", "rclone output begins", "Mock rclone menu",
                  "rclone output ends", "Back in the KeeSyncXC installer", "[4/5]"]
        positions = [result.stdout.index(stage) for stage in stages]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("Choose n (New remote)", result.stdout)
        self.assertIn("s sets a password", result.stdout)
        self.assertIn("Setup complete", result.stdout)
        self.assertGreaterEqual(result.stdout.count("+==========================================================+"), 2)

    def test_rclone_configuration_failure_still_prints_the_closing_boundary(self):
        result = self.run_script("--install", TEST_REMOTES="", TEST_FAULT="config")
        self.assert_failure(result)
        self.assertIn("rclone output begins", result.stdout)
        self.assertIn("rclone output ends", result.stdout)
        self.assertLess(result.stdout.index("rclone output begins"),
                        result.stdout.index("rclone output ends"))
        self.assertGreaterEqual(result.stdout.count("+==========================================================+"), 2)
        self.assertIn("rclone configuration failed", result.stderr)
        self.assertNotIn("Installation complete", result.stdout)

    @staticmethod
    def read_terminal(master):
        result = bytearray()
        while select.select([master], [], [], 0.1)[0]:
            try:
                data = os.read(master, 65536)
            except OSError as error:
                if error.errno == errno.EIO:
                    break
                raise
            if not data:
                break
            result.extend(data)
        return result.decode()

    def run_terminal(self, argument="--status", *, terminal_stream="stdout", **settings):
        environment = dict(self.env, TERM="xterm-256color")
        environment.pop("NO_COLOR", None)
        environment.update(settings)
        master, slave = pty.openpty()
        process = None
        try:
            process = subprocess.Popen(
                ["/bin/bash", str(SCRIPT), argument],
                stdin=subprocess.DEVNULL,
                stdout=slave if terminal_stream == "stdout" else subprocess.PIPE,
                stderr=slave if terminal_stream == "stderr" else subprocess.PIPE,
                env=environment,
            )
            os.close(slave)
            slave = None
            captured_stdout, captured_stderr = process.communicate(timeout=10)
            return (process.returncode, self.read_terminal(master),
                    (captured_stdout or b"").decode(),
                    (captured_stderr or b"").decode())
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if slave is not None:
                os.close(slave)
            os.close(master)

    def test_interactive_terminal_has_color_and_plain_redirected_error(self):
        status, terminal, stdout, stderr = self.run_terminal()
        self.assertEqual(status, 0, terminal + stdout + stderr)
        self.assertRegex(terminal, ANSI)
        self.assertIn("Installation", terminal)
        self.assertNotRegex(stderr, ANSI)

        status, terminal, stdout, stderr = self.run_terminal("--unknown-option")
        self.assertNotEqual(status, 0)
        self.assertNotRegex(stderr, ANSI)
        self.assertIn("[FAIL]", stderr)

    def test_no_color_disables_styling_even_when_empty(self):
        status, terminal, stdout, stderr = self.run_terminal(NO_COLOR="")
        self.assertEqual(status, 0, terminal + stdout + stderr)
        self.assertIn("Installation", terminal)
        self.assertNotRegex(terminal + stdout + stderr, ANSI)

    def test_dumb_terminal_has_no_styling(self):
        status, terminal, stdout, stderr = self.run_terminal(TERM="dumb")
        self.assertEqual(status, 0, terminal + stdout + stderr)
        self.assertIn("Installation", terminal)
        self.assertNotRegex(terminal + stdout + stderr, ANSI)

    def test_error_colors_the_stderr_terminal_when_stdout_is_redirected(self):
        status, terminal, stdout, stderr = self.run_terminal(
            "--unknown-option", terminal_stream="stderr")
        self.assertNotEqual(status, 0)
        self.assertIn("[FAIL]", terminal)
        self.assertRegex(terminal, ANSI)
        self.assertNotRegex(stdout, ANSI)
        self.assertEqual(stderr, "")


class SessionOutputTests(LauncherCase):
    def test_session_stages_keep_progress_and_summary_in_order(self):
        self.seed()
        self.env.pop("NO_COLOR", None)
        result = self.run_launcher(edit="saved edit", TERM="xterm-256color")
        self.assert_success(result)
        self.assertNotRegex(result.stdout + result.stderr, ANSI)
        stages = ["Before editing", "KeePassXC session", "After editing", "Session complete"]
        positions = [result.stdout.index(stage) for stage in stages]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("[WAIT]", result.stdout)
        self.assertIn("[ OK ]", result.stdout)
        self.assertIn("Finished.", result.stdout)
        self.assertEqual(self.state.read_text(), self.digest("saved edit") + "\n")
        self.assertEqual(self.local.read_text(), "saved edit")
        self.assertEqual(self.cloud.read_text(), "saved edit")

    def test_offline_session_is_visibly_deferred_and_never_reports_success(self):
        self.seed()
        result = self.run_launcher("offline", edit="offline edit")
        self.assertNotEqual(result.returncode, 0)
        output = result.stdout + result.stderr
        self.assertIn("[WARN]", output)
        self.assertIn("[FAIL]", output)
        self.assertIn("Local database is available offline", output)
        self.assertIn("Post-close sync deferred", output)
        self.assertNotIn("Session complete", output)
        self.assertNotIn("Finished.", output)
        self.assertEqual(self.local.read_text(), "offline edit")
        self.assertEqual(self.cloud.read_text(), "original")

    def test_conflict_options_stay_readable_and_choice_is_not_decorated(self):
        self.seed(local="local edit", cloud="cloud edit")
        result = self.run_launcher(choices="2\n")
        self.assert_success(result)
        self.assertIn("conflict", result.stderr.lower())
        self.assertIn("Choice [0-3, H]:", result.stderr)
        self.assertIn("1) Open my local changes", result.stderr)
        self.assertIn("H) More information", result.stderr)
        self.assertEqual(self.local.read_text(), "cloud edit")
        self.assertEqual(self.cloud.read_text(), "cloud edit")
        self.assertEqual(self.state.read_text(), self.digest("cloud edit") + "\n")


if __name__ == "__main__":
    unittest.main()
