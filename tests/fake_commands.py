#!/usr/bin/env python3
"""Inert command replacements configured through TEST_FAULT and TEST_* options."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

root = Path(os.environ["TEST_ROOT"])
fault = os.environ.get("TEST_FAULT", "")
name = Path(sys.argv[0]).name
args = sys.argv[1:]

def log(**event):
    with (root / "events.jsonl").open("a") as output:
        output.write(json.dumps(event) + "\n")

def reject(message):
    print("Simulated failure: " + message, file=sys.stderr)
    sys.exit(1)

def count(key):
    file = root / key
    value = int(file.read_text()) + 1 if file.exists() else 1
    file.write_text(str(value))
    return value

def mapped(path):
    if ":" in path and not path.startswith("/"):
        remote, relative = path.split(":", 1)
        return root / "cloud" / remote / relative
    return Path(path)

def canonical(path):
    return path in (os.environ.get("TEST_REMOTE", "gdrive:KeeSyncXC/Passwords.kdbx"),
                    os.environ.get("TEST_ACTIVE_REMOTE", ""))

def drive_id(path):
    file = mapped(path)
    return "id-{}-{}".format(file.stat().st_dev, file.stat().st_ino)

def replace_identity(path, contents=None):
    file = mapped(path)
    replacement = file.with_name(file.name + ".replacement")
    replacement.write_bytes(file.read_bytes() if contents is None else contents.encode())
    replacement.replace(file)

def duplicate_entries():
    file = root / "duplicate_files.json"
    return json.loads(file.read_text()) if file.exists() else []

def add_duplicate(path):
    # Drive permits duplicate names; keep extra objects outside the POSIX cloud
    # tree and inject their names/IDs into listings of the matching directory.
    remote, relative = path.split(":", 1)
    parent, _, filename = relative.rpartition("/")
    directory = remote + ":" + parent
    entries = duplicate_entries()
    backing = root / "duplicate-objects" / str(len(entries))
    backing.parent.mkdir(exist_ok=True)
    backing.write_text("different duplicate database")
    entries.append({"directory": directory, "name": filename, "file": str(backing)})
    (root / "duplicate_files.json").write_text(json.dumps(entries))

def apply_download_metadata(destination):
    if not os.environ.get("TEST_APPLY_DOWNLOAD_METADATA"):
        return
    destination = mapped(destination)
    log(command="download-security", destination=str(destination),
        parent_mode=destination.parent.stat().st_mode & 0o777,
        parent_owner=destination.parent.stat().st_uid,
        metadata_environment=os.environ.get("RCLONE_METADATA"))
    destination.chmod(0o644)
    if fault == "partial_download":
        reject("download interrupted after writing payload")

if name in ("xdg-open", "x-terminal-emulator"):
    log(command=name, arguments=args)
    sys.exit(0)
if name == "id" and args == ["-u"]:
    print(os.environ.get("TEST_UID", "1000"))
    sys.exit(0)
if name in ("sudo", "apt-get"):
    log(command=name, arguments=args)
    apt_args = args[1:] if name == "sudo" else args
    if name == "sudo" and (not args or args[0] != "apt-get"):
        reject("unexpected privileged command")
    if fault == "packages":
        reject("package installation")
    if "install" in apt_args:
        mapping = {"keepassxc": ["keepassxc"], "rclone": ["rclone"],
                   "zenity": ["zenity"], "util-linux": ["flock"],
                   "procps": ["pgrep"], "coreutils": ["sha256sum", "md5sum", "timeout"]}
        binaries = Path(os.environ["TEST_BIN"])
        for package in apt_args:
            for command in mapping.get(package, []):
                destination = binaries / command
                if not destination.exists():
                    destination.symlink_to(binaries / "mock.py")
    sys.exit(0)
if name == "pgrep":
    sys.exit(0 if os.environ.get("TEST_APP_RUNNING") else 1)
if name == "timeout":
    log(command=name, arguments=args)
    results = json.loads(os.environ.get("TEST_CONNECTIVITY", "[0]"))
    result = results[min(count("connectivity_count") - 1, len(results) - 1)]
    if result == "stall":
        # Exercise the real deadline with a stalled command, without networking.
        os.execv("/usr/bin/timeout", ["/usr/bin/timeout", *args[:2], "/bin/sleep", "30"])
    sys.exit(int(result))
if name == "keepassxc":
    index = count("launch_count") - 1
    database = Path(args[-1])
    (root / "launched").touch()
    log(command=name, arguments=args, contents=database.read_text())
    edits = json.loads(os.environ.get("TEST_EDITS", "[]"))
    if index < len(edits) and edits[index] is not None:
        database.write_text(edits[index])
    cloud_edits = json.loads(os.environ.get("TEST_APP_CLOUD_EDITS", "[]"))
    if index < len(cloud_edits) and cloud_edits[index] is not None:
        mapped(os.environ.get("TEST_REMOTE", "gdrive:KeeSyncXC/Passwords.kdbx")).write_text(cloud_edits[index])
    if fault == "duplicate_during_edit":
        add_duplicate(os.environ.get("TEST_ACTIVE_REMOTE") or os.environ["TEST_REMOTE"])
    sys.exit(int(os.environ.get("TEST_APP_STATUS", "0")))
if name == "zenity":
    if "--question" in args:
        log(command=name, arguments=args)
        if "TEST_CONFIRM_EDIT" in os.environ:
            Path(os.environ["TEST_LOCAL"]).write_text(os.environ["TEST_CONFIRM_EDIT"])
        sys.exit(int(os.environ.get("TEST_CONFIRM_STATUS", "0")))
    if "--text-info" in args:
        log(command=name, arguments=args, input=sys.stdin.read())
        sys.exit(int(os.environ.get("TEST_GUI_INFO_STATUS", "0")))
    log(command=name, arguments=args)
    choices = json.loads(os.environ.get("TEST_GUI_CHOICES", "[]"))
    if "--list" in args or "--forms" in args:
        index = count("gui_count") - 1
        if index >= len(choices):
            sys.exit(1)
        print(choices[index])
    sys.exit(0)
if name == "rclone":
    if os.environ.get("TEST_NETWORK_GATE") and not (root / "allow-network").exists():
        (root / "network-waiting").touch()
        deadline = time.monotonic() + 15
        while not (root / "allow-network").exists():
            if time.monotonic() > deadline:
                reject("test network gate timed out")
            time.sleep(0.02)
    if os.environ.get("TEST_APPLY_DOWNLOAD_METADATA"):
        if any(arg == "--metadata" or arg.startswith("--metadata=") for arg in args):
            reject("metadata flag is unavailable in older supported rclone versions")
        log(command=name, arguments=args, metadata_environment=os.environ.get("RCLONE_METADATA"))
    else:
        log(command=name, arguments=args)
    operation = args[0]
    if operation == "listremotes":
        print("gdrive:\narchive:" if (root / "configured").exists()
              else os.environ.get("TEST_REMOTES", "gdrive:\narchive:"))
        sys.exit(0)
    if operation == "config":
        if "TEST_CONFIG_OUTPUT" in os.environ:
            print(os.environ["TEST_CONFIG_OUTPUT"])
        if fault == "config":
            reject("configuration cancelled")
        (root / "configured").touch()
        sys.exit(0)
    if fault == "offline":
        reject("offline")
    if operation == "mkdir":
        if fault == "backup_mkdir":
            reject("backup directory creation")
        mapped(args[1]).mkdir(parents=True, exist_ok=True)
    elif operation == "md5sum":
        if canonical(args[1]):
            number = count("remote_reads")
            if number == 2 and fault == "changed_remote":
                mapped(args[1]).write_text("other device edit")
            if number == 2 and fault == "offline_recheck":
                reject("connection lost before upload")
        file = mapped(args[1])
        if not file.is_file():
            reject("file does not exist")
        digest = hashlib.md5(file.read_bytes()).hexdigest()
        print(digest + "  " + file.name)
    elif operation == "copyto":
        source, dest = args[1:3]
        for path in (source, dest):
            if not path.startswith("gdrive:"):
                continue
            directory, _, filename = path.rpartition("/")
            if not directory:
                remote, filename = path.split(":", 1)
                directory = remote + ":"
            if any(entry["directory"] == directory and entry["name"] == filename
                   for entry in duplicate_entries()):
                reject("ambiguous path transfer must use the pinned ID")
        if fault == "upload" and not source.startswith("gdrive:"):
            reject("upload")
        updating = not source.startswith("gdrive:") and canonical(dest)
        backing_up = canonical(source) and dest.startswith("gdrive:")
        if fault == "remote_backup" and backing_up:
            reject("remote backup copy")
        if fault == "cloud_before_backup" and backing_up:
            mapped(source).write_text("edit just before backup copy")
        if fault == "update" and updating:
            reject("updating existing file")
        if fault == "delete_during_update" and updating:
            mapped(dest).unlink()
            reject("file disappeared during update")
        if fault == "download" and not dest.startswith("gdrive:"):
            reject("download")
        if not mapped(source).is_file():
            reject("source does not exist")
        if "--ignore-existing" in args and mapped(dest).exists():
            sys.exit(0)
        if updating and os.environ.get("RCLONE_BACKUP_DIR"):
            backup_dir = (args[args.index("--backup-dir") + 1] if "--backup-dir" in args
                          else os.environ["RCLONE_BACKUP_DIR"])
            if backup_dir and mapped(dest).exists():
                mapped(dest).rename(mapped(dest).with_name("environment-backup.kdbx"))
        shutil.copyfile(mapped(source), mapped(dest))
        if source.startswith("gdrive:") and not dest.startswith("gdrive:"):
            apply_download_metadata(dest)
        if backing_up:
            if fault == "cloud_after_backup":
                mapped(source).write_text("other device edit")
            if fault == "identity_after_backup":
                replace_identity(source)
            if fault == "corrupt_backup":
                mapped(dest).write_text("corrupted backup")
            if fault == "duplicate_after_backup":
                add_duplicate(source)
        if updating:
            if fault == "update_response_lost":
                reject("response lost after successful update")
            if fault == "corrupt_update":
                mapped(dest).write_text("corrupted update")
            if fault == "update_replaces_id":
                replace_identity(dest)
        if fault == "corrupt_download" and not dest.startswith("gdrive:"):
            mapped(dest).write_text("corrupted transfer")
    elif operation == "backend" and args[1] == "copyid":
        remote, identity, destination = args[2:5]
        candidates = list((root / "cloud" / remote.rstrip(":")).rglob("*"))
        candidates += [Path(entry["file"]) for entry in duplicate_entries()]
        matches = [file for file in candidates if file.is_file() and drive_id(str(file)) == identity]
        if len(matches) != 1:
            reject("pinned file ID missing or ambiguous")
        if fault == "download":
            reject("download")
        shutil.copyfile(matches[0], mapped(destination))
        apply_download_metadata(destination)
        if fault == "corrupt_download":
            mapped(destination).write_text("corrupted transfer")
        if fault == "cloud_backup_changed":
            matches[0].write_text("changed cloud backup")
        if fault == "cloud_backup_removed":
            matches[0].unlink()
        if fault == "cloud_backup_replaced":
            replace_identity(str(matches[0]))
    elif operation == "lsf":
        if fault == "inspection_remote_removed" and count("inspection_listings") == 2:
            Path(os.environ["TEST_INSPECT_REMOTE"]).unlink()
        target = mapped(args[1])
        if not target.exists():
            print("")
            sys.exit(0)
        files = [target] if target.is_file() else sorted(target.iterdir())
        if "--files-only" in args:
            files = [file for file in files if file.is_file()]
        fields = args[args.index("--format") + 1] if "--format" in args else "p"
        if fields == "istph" and fault == "cached_copy_changed" and count("inspection_listings") == 2:
            Path(os.environ["TEST_CACHE"]).write_text("changed during verification")
        extra = [(Path(entry["file"]), entry["name"]) for entry in duplicate_entries()
                 if entry["directory"] == args[1]] if target.is_dir() else []
        if fields == "istph" and "--csv" in args:
            if "TEST_LISTING" in os.environ:
                print(os.environ["TEST_LISTING"])
                sys.exit(0)
            import csv
            writer = csv.writer(sys.stdout, lineterminator="\n")
            for file, filename in [(file, file.name) for file in files] + extra:
                writer.writerow([drive_id(str(file)), file.stat().st_size,
                                 "2026-11-05 00:00:00", filename,
                                 hashlib.md5(file.read_bytes()).hexdigest()])
        elif fields == "stp" and "--csv" in args:
            import csv
            writer = csv.writer(sys.stdout, lineterminator="\n")
            for file in files:
                writer.writerow([file.stat().st_size, "2026-11-05 00:00:00", file.name])
        elif fields in ("ih", "ihp"):
            if fault == "offline_metadata":
                reject("metadata unavailable")
            records = []
            for file, filename in [(file, file.name) for file in files if file.is_file()] + extra:
                identity = "shortcut/id" if fault == "metadata_bad_id" else drive_id(str(file))
                if fault == "metadata_shortcut":
                    identity += "\tshortcut-id"
                digest = hashlib.md5(file.read_bytes()).hexdigest()
                if fault == "metadata_invalid_hash":
                    digest = "not-an-md5"
                record = identity + ";" + digest
                records.append(record + ";" + filename if fields == "ihp" else record)
            if fault == "metadata_duplicate":
                records = records * 2
            print("\n".join(records))
        else:
            print("\n".join([file.name for file in files] + [filename for _, filename in extra]))
    else:
        reject("unexpected rclone command " + operation)
    sys.exit(0)
if name == "cp" and fault == "local_backup":
    reject("local backup")
if name == "cp" and fault in ("corrupt_local_backup", "change_local_during_backup"):
    source, destination = mapped(args[-2]), mapped(args[-1])
    shutil.copyfile(source, destination)
    if destination.parent.name == "backups" and destination.name.startswith("local-"):
        if fault == "corrupt_local_backup":
            destination.write_text("corrupted local backup")
        else:
            source.write_text("local edit during backup")
    sys.exit(0)
if name == "mv":
    if fault == "replace_local" and args[-1].endswith("Passwords.kdbx"):
        reject("local replacement")
    if fault == "replace_state" and args[-1].endswith(".md5"):
        reject("state replacement")
    if fault == "replace_conflict" and args[-1].endswith(".conflict"):
        reject("conflict marker replacement")
if name == "mktemp":
    if fault == "state_temp" and ".sync-state." in args[-1]:
        reject("state temporary file")
    if fault == "download_temp" and ".keesyncxc-download." in args[-1]:
        reject("download temporary file")
if name == "md5sum":
    if fault == "local_hash" and args[-1].endswith("Passwords.kdbx"):
        reject("local hash")
    if fault == "download_hash" and ".keesyncxc-download." in args[-1]:
        reject("download hash")
if name == "chmod" and ".keesyncxc-download." in args[-1]:
    if args[-1].endswith("/database.kdbx"):
        if fault == "download_chmod":
            reject("download permission change")
        if fault == "download_chmod_ignored":
            sys.exit(0)
    elif fault == "stage_chmod":
        reject("staging permission change")
real = shutil.which(name, path="/usr/bin:/bin")
os.execv(real, [real] + args)
