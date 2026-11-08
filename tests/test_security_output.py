"""Untrusted filenames remain readable terminal data, using inert local fixtures."""

import os
from pathlib import Path
import subprocess
import unittest

from support import SCRIPT, InstallerCase


class SafeFilenameOutputTests(InstallerCase):
    def run_script(self, *arguments, answers="", **settings):
        script = Path(os.environ.get("TEST_SECURITY_SCRIPT", str(SCRIPT)))
        result = subprocess.run(
            ["/bin/bash", str(script), *arguments], input=answers.encode(),
            capture_output=True, env=dict(self.env, **settings), timeout=20)
        # Decode without universal-newline conversion, which would hide raw CR.
        result.stdout = result.stdout.decode()
        result.stderr = result.stderr.decode()
        return result

    def assert_safe_output(self, result):
        self.assertNotRegex(result.stdout + result.stderr,
                            r"[\x00-\x09\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")

    def test_local_inventory_escapes_controls_newlines_and_direction_markers(self):
        self.write_config()
        directory = self.state_dir / "backups"
        directory.mkdir(parents=True)
        name = "unsafe-\x1b[2J\r\b\t\n[ OK ] forged\x7f\u009b31m\u202e.kdbx"
        backup = directory / name
        backup.write_text("encrypted fixture")
        before = backup.read_bytes()
        result = self.run_script("--backups")
        self.assert_success(result)
        self.assert_safe_output(result)
        self.assertIn(r"unsafe-\x1B[2J\x0D\x08\x09\x0A[ OK ] forged\x7F\x9B31m\u202E.kdbx",
                      result.stdout)
        self.assertNotIn("\n[ OK ] forged", result.stdout)
        self.assertEqual(backup.read_bytes(), before)
        self.assertFalse(self.events())

    def test_local_inventory_keeps_ordinary_unicode_spaces_and_backslashes(self):
        self.write_config()
        directory = self.state_dir / "backups"
        directory.mkdir(parents=True)
        name = 'Cópia 日本語 🔐 with spaces \\ "quoted",semicolon;.kdbx'
        backup = directory / name
        backup.write_text("encrypted fixture")
        result = self.run_script("--backups", LC_ALL="C")
        self.assert_success(result)
        self.assert_safe_output(result)
        self.assertIn(name, result.stdout)
        self.assertFalse(self.events())

    def test_cloud_csv_listing_preserves_records_and_escapes_filename_newlines(self):
        self.write_config()
        directory = self.cloud.parent / "KeePass-Backups"
        directory.mkdir()
        names = ['Cópia 日本語 🔐 with spaces \\ "quoted",comma;.kdbx',
                 'attack-\x1b[2J\r\b\n[ OK ] forged\u009b31m.kdbx']
        for name in names:
            (directory / name).write_text("encrypted cloud fixture")
        before = {path: path.read_bytes() for path in [self.config, self.cloud, *directory.iterdir()]}
        result = self.run_script("--cloud-backups", LC_ALL="C")
        self.assert_success(result)
        self.assert_safe_output(result)
        self.assertIn(names[0], result.stdout)
        self.assertIn(r"attack-\x1B[2J\x0D\x08\x0A[ OK ] forged\x9B31m.kdbx", result.stdout)
        self.assertNotIn("\n[ OK ] forged", result.stdout)
        records = [line for line in result.stdout.splitlines() if "2026-11-05 00:00:00" in line]
        self.assertEqual(len(records), 2)
        self.assertTrue(all(" | " in line for line in records))
        self.assertEqual(self.operations(), ["lsf"])
        self.assertIn("--csv", self.events("rclone")[0]["arguments"])
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertFalse(self.state_dir.exists())
        self.assertFalse(self.events("keepassxc"))

    def test_cloud_listing_failure_is_propagated_without_success_footer_or_state(self):
        self.write_config()
        before = self.config.read_bytes()
        result = self.run_script("--cloud-backups", TEST_FAULT="offline")
        self.assert_failure(result)
        self.assert_safe_output(result)
        self.assertIn("Cannot list cloud backups", result.stderr)
        self.assertNotIn("No database was transferred or modified", result.stdout)
        self.assertEqual(self.operations(), ["lsf"])
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(self.state_dir.exists())
        self.assertFalse(self.local.exists())

    def test_hostile_cli_argument_is_data_in_the_error_message(self):
        result = self.run_script("--unknown-\x1b[2J\r\nforged")
        self.assert_failure(result)
        self.assert_safe_output(result)
        self.assertIn(r"\x1B[2J\x0D\x0Aforged", result.stderr)
        self.assertFalse(self.events())


if __name__ == "__main__":
    unittest.main()
