"""Shared isolated fixtures, with no test cases of their own."""

import errno
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "keesyncxc.sh"
FAKE_COMMANDS = Path(__file__).with_name("fake_commands.py")
LAUNCHER_REMOTE = "gdrive:KeeSyncXC/Passwords.kdbx"
INSTALLER_REMOTE = "gdrive:KeePass/kpDB.kdbx"


class ScriptCase(unittest.TestCase):
    def setUp(self):
        previous_umask = os.umask(0o022)
        self.addCleanup(os.umask, previous_umask)
        self.temp = tempfile.TemporaryDirectory(prefix="keesyncxc-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def install_mocks(self, *commands):
        self.binaries = self.root / "bin"
        self.binaries.mkdir()
        self.mock = self.binaries / "mock.py"
        shutil.copyfile(FAKE_COMMANDS, self.mock)
        self.mock.chmod(0o755)
        for command in commands:
            (self.binaries / command).symlink_to(self.mock)

    def isolated_environment(self, home, config, remote):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("TEST_") and key not in ("DISPLAY", "WAYLAND_DISPLAY")}
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(self.root / config),
                   XDG_DATA_HOME=str(self.root / "data"),
                   XDG_STATE_HOME=str(self.root / "state"),
                   TEST_ROOT=str(self.root), TEST_BIN=str(self.binaries),
                   TEST_REMOTE=remote, PATH=str(self.binaries) + ":/usr/bin:/bin")
        return env

    def mock_terminal(self):
        """Record terminal dispatch without opening a graphical window."""
        (self.binaries / "x-terminal-emulator").symlink_to(self.mock)

    def events(self, command=None):
        file = self.root / "events.jsonl"
        events = [json.loads(line) for line in file.read_text().splitlines()] if file.exists() else []
        return [event for event in events if command is None or event["command"] == command]

    def remote_operations(self, operation):
        return [event["arguments"] for event in self.events("rclone")
                if event["arguments"][0] == operation]

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_blocked(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "launched").exists())
        self.assertNotIn("Finished.", result.stdout)

    def operations(self):
        return [event["arguments"][0] for event in self.events("rclone")]

    def assert_failure(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    @staticmethod
    def wait_for_file(path, process, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists():
                return
            if process.poll() is not None:
                raise AssertionError(f"Launcher exited before reaching {path.name}")
            time.sleep(0.02)
        raise AssertionError(f"Launcher did not reach {path.name}")

    @staticmethod
    def read_available(fd, timeout=0.1):
        output = bytearray()
        while select.select([fd], [], [], timeout)[0]:
            try:
                chunk = os.read(fd, 65536)
            except OSError as error:
                if error.errno == errno.EIO:  # PTY slave has closed.
                    break
                raise
            if not chunk:
                break
            output.extend(chunk)
            timeout = 0
        return output.decode()

    def assert_terminal_holds(self, *, choices="", expected_status=0, script=None, **settings):
        master, slave = pty.openpty()
        process = subprocess.Popen(["/bin/bash", str(script or self.installed), "--desktop"],
                                   stdin=slave, stdout=slave, stderr=slave,
                                   env=dict(self.env, **settings))
        os.close(slave)
        try:
            if choices:
                os.write(master, choices.encode())
            output = ""
            deadline = time.monotonic() + 15
            while "Press Enter" not in output and time.monotonic() < deadline:
                output += self.read_available(master, timeout=0.2)
                if process.poll() is not None:
                    break
            self.assertIn("Press Enter", output)
            self.assertNotIn("Auto-closing", output)
            self.assertIsNone(process.poll(), output)
            os.write(master, b"\n")
            self.assertEqual(process.wait(timeout=5), expected_status, output)
            return output
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            os.close(master)

    def run_from_file_manager(self, *arguments, script=SCRIPT, **settings):
        return subprocess.run(["/bin/bash", str(script), *arguments],
                              stdin=subprocess.DEVNULL, text=True, capture_output=True,
                              env=dict(self.env, DISPLAY=":mock", **settings), timeout=20)


class LauncherCase(ScriptCase):
    @classmethod
    def setUpClass(cls):
        if not SCRIPT.is_file():
            raise AssertionError(f"Launcher under test does not exist: {SCRIPT}")

    def setUp(self):
        super().setUp()
        self.local = self.root / "Documents/KeeSyncXC/Passwords.kdbx"
        self.cloud = self.root / "cloud/gdrive/KeeSyncXC/Passwords.kdbx"
        self.data = self.root / "state/keesyncxc"
        digest = hashlib.sha256(LAUNCHER_REMOTE.encode()).hexdigest()
        self.state = self.data / (digest + ".md5")
        self.transaction = Path(str(self.state) + ".transaction")
        self.conflict = Path(str(self.state) + ".conflict")
        for directory in (self.local.parent, self.cloud.parent, self.data):
            directory.mkdir(parents=True)
        self.install_mocks("rclone", "keepassxc", "pgrep", "zenity", "cp", "mv", "mktemp", "md5sum", "timeout")
        self.env = self.isolated_environment(self.root, "config", LAUNCHER_REMOTE)
        self.config = self.root / "config/keesyncxc/config"
        self.config.parent.mkdir(parents=True)
        self.config.write_text(f"{LAUNCHER_REMOTE}\n{self.local}\nKeePass-Backups\n")

    def seed(self, local="original", cloud="original", baseline="original"):
        self.local.write_text(local)
        self.cloud.write_text(cloud)
        self.state.write_text(self.digest(baseline) + "\n")

    @staticmethod
    def digest(contents):
        return hashlib.md5(contents.encode()).hexdigest()

    def cloud_id(self):
        info = self.cloud.stat()
        return f"id-{info.st_dev}-{info.st_ino}"

    def seed_in_place_transaction(self, *, published=False):
        """Represent an interrupted update with its backup and local snapshot."""
        self.seed(local="local edit")
        remote = self.env["TEST_REMOTE"]
        prefix = remote.rsplit("/", 1)[0] + "/" if "/" in remote else "gdrive:"
        backup_name = "Passwords-20261101T000000Z-12345678-1234-1234-1234-123456789abc.kdbx"
        directory = self.cloud.parent / "KeePass-Backups"
        prefix += "KeePass-Backups/"
        directory.mkdir(exist_ok=True)
        backup = directory / backup_name
        backup.write_bytes(self.cloud.read_bytes())
        snapshots = self.data / "backups"
        snapshots.mkdir(exist_ok=True)
        snapshot = snapshots / "upload-retained.kdbx"
        snapshot.write_text("local edit")
        original_id = self.cloud_id()
        if published:
            self.cloud.write_text("local edit")
        self.transaction.write_text("\n".join(("in-place-v2", self.digest("original"),
                                               self.digest("local edit"), original_id,
                                               prefix + backup_name, str(snapshot))) + "\n")
        return backup

    def run_launcher(self, fault="", edit=None, choices="", edits=None, **settings):
        env = dict(self.env, TEST_FAULT=fault, **settings)
        if edit is not None:
            edits = [edit]
        if edits is not None:
            env["TEST_EDITS"] = json.dumps(edits)
        return subprocess.run(["bash", str(SCRIPT), "--launch"], env=env, text=True,
                              input=choices, capture_output=True, timeout=20)

    def local_backups(self):
        return [file.read_text() for file in (self.data / "backups").glob("*")]

    def cloud_backups(self):
        return sorted((self.cloud.parent / "KeePass-Backups").glob("Passwords-*.kdbx"))

    def pin(self):
        identity = self.cloud_id()
        self.identity.write_text(identity + "\n")
        return identity

    def duplicate(self, contents="unrelated duplicate", *, name=None):
        entries_file = self.root / "duplicate_files.json"
        entries = json.loads(entries_file.read_text()) if entries_file.exists() else []
        backing = self.root / "duplicate-objects" / str(len(entries))
        backing.parent.mkdir(exist_ok=True)
        backing.write_text(contents)
        remote = self.env.get("TEST_ACTIVE_REMOTE", self.env["TEST_REMOTE"])
        parent = remote.rsplit("/", 1)[0] if "/" in remote else remote.split(":", 1)[0] + ":"
        entries.append({"directory": parent, "name": name or self.cloud.name, "file": str(backing)})
        entries_file.write_text(json.dumps(entries))
        return backing


class InstallerCase(ScriptCase):
    def setUp(self):
        super().setUp()
        self.home = self.root / "home with spaces"
        self.home.mkdir()
        self.local = self.home / "Documents/KeeSyncXC/Passwords.kdbx"
        self.cloud = self.root / "cloud/gdrive/KeePass/kpDB.kdbx"
        self.cloud.parent.mkdir(parents=True)
        self.cloud.write_text("cloud database")
        self.config = self.root / "configuration/keesyncxc/config"
        self.desktop = self.root / "data/applications/keesyncxc.desktop"
        self.installed = self.home / ".local/bin/keesyncxc"
        self.state_dir = self.root / "state/keesyncxc"
        self.state = self.state_dir / (hashlib.sha256(INSTALLER_REMOTE.encode()).hexdigest() + ".md5")
        self.install_mocks("rclone", "keepassxc", "pgrep", "zenity", "sudo", "apt-get", "id", "timeout")
        self.env = self.isolated_environment(self.home, "configuration", INSTALLER_REMOTE)

    def run_script(self, *arguments, answers="\n\n\nn\n", script=None, **settings):
        return subprocess.run(["/bin/bash", str(script or SCRIPT), *arguments],
                              input=answers, text=True, capture_output=True,
                              env=dict(self.env, **settings), timeout=20)

    def write_config(self, remote=INSTALLER_REMOTE, local=None, backup_folder="KeePass-Backups"):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        contents = f"{remote}\n{local or self.local}\n"
        if backup_folder is not None:
            contents += backup_folder + "\n"
        self.config.write_text(contents)
        return contents

    def assert_no_database_changes(self):
        self.assertEqual(self.cloud.read_text(), "cloud database")
        self.assertFalse(self.events("keepassxc"))
        self.assertFalse({"copyto", "moveto", "delete", "deletefile", "mkdir"} & set(self.operations()))

    def isolate_path(self, missing):
        """Expose real utilities except selected dependencies; apt stays mocked."""
        for directory in (Path("/usr/bin"), Path("/bin")):
            for file in directory.iterdir():
                destination = self.binaries / file.name
                if file.name not in missing and not destination.exists():
                    destination.symlink_to(file)
        for command in missing:
            destination = self.binaries / command
            if destination.exists():
                destination.unlink()
        self.env["PATH"] = str(self.binaries)

    def prepare_installation(self):
        self.write_config()
        self.installed.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SCRIPT, self.installed)
        self.installed.chmod(0o700)
        self.desktop.parent.mkdir(parents=True, exist_ok=True)
        self.desktop.write_text("[Desktop Entry]\nType=Application\n")

    def assert_no_external_actions(self):
        for command in ("rclone", "keepassxc", "sudo", "apt-get"):
            self.assertFalse(self.events(command), f"Unexpected {command} command")
