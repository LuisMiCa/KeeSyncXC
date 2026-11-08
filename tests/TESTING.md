# Testing KeeSyncXC

Use the automated checks first, then exercise the real application with a disposable database in a separate Google Drive folder. The manual checks below upload and change test files, so use dummy entries throughout. See the [README](../README.md) for setup, configuration, commands, file locations, and security guidance.

## Automated checks

On Linux, use Bash, Python 3, GNU coreutils, and util-linux (`flock`). The suite uses only the Python standard library; KeePassXC, rclone, Zenity, and package-manager commands are replaced with mocks. No cloud credentials are needed. From the repository directory, run:

```bash
bash -n keesyncxc.sh
bash keesyncxc.sh --version
python3 -B -m unittest discover -s tests -v
```

Expected: the syntax check exits with status `0`, the version command prints `KeeSyncXC v1.0.0`, and every regression test passes with a final `OK`. The [GitHub Actions workflow](../.github/workflows/ci.yml) runs the syntax check and regression tests on Ubuntu for pushes and pull requests. The regression tests use temporary homes and fake commands; they do not open KeePassXC or access Google Drive. They cover setup and maintenance as well as synchronization, conflict handling, file-ID preservation and mismatches, transfer failures, corruption, cloud changes during uploads, and interrupted-upload recovery. Desktop checks use mocks and a pseudo-terminal; actual terminal windows and desktop-menu behavior require the manual checks below. Passing these checks does not establish live KeeAnywhere restricted-mode compatibility.

To run a single module from the repository root, use its module name without the `tests/` prefix:

```bash
PYTHONPATH=tests python3 -B -m unittest -v test_identity
```

Shared fixtures live in [tests/support.py](support.py). Inherit from `LauncherCase` for synchronization scenarios or `InstallerCase` for setup and management scenarios. These base classes provide temporary homes, fake commands, event logs, and common assertions; keep test cases in the `test_*.py` modules so discovery runs each case once.

The fake command implementations live in [tests/fake_commands.py](fake_commands.py) and are copied into each test's temporary workspace. Select an existing failure with `self.run_launcher(fault="corrupt_download")` or `self.run_script("--sync", TEST_FAULT="offline")`. Other `TEST_*` options control editor changes, graphical answers, metadata, and delayed network requests. Add new scenarios as explicit options in that file instead of replacing mock source text inside a test.

| Test module | Coverage |
| --- | --- |
| [tests/test_install.py](test_install.py) | Dependency installation through fake package commands, rclone setup, full remote paths and filenames without remote prefixes, cloud backup folder names, prompt order, saved settings, file permissions, shortcuts, reinstalling, and preservation of existing data, including local paths with backslashes. |
| [tests/test_management.py](test_management.py) | Automatic first-run setup, incomplete installations, menu actions, offline status and backup listing, age/count backup retention, full local cleanup, uninstall choices and cancellation, installed-script self-removal, and backing up before choosing another database, including paths with backslashes. |
| [tests/test_menu_structure.py](test_menu_structure.py) | Nested menu navigation and cancellation, read-only connection checks, synchronization without an editor including conflicts and recovery, backup-folder changes, verified database moves, local backup restoration and inspection, and recovery archive inventory. |
| [tests/test_menu_actions.py](test_menu_actions.py) | Move and restoration failures, corrupt copies, configuration-write failures, overlapping paths, symlink refusal, backup selection, and a destination appearing during a move. |
| [tests/test_restore_publication.py](test_restore_publication.py) | Restored-copy upload confirmation, inspection reminders, declined/blank/EOF answers, graphical cancellation, offline edits, conflicts, upload recovery, unsafe records, failed approval-record writes, and maintenance preservation/cleanup. |
| [tests/test_cloud_inspection.py](test_cloud_inspection.py) | Cloud backup selection, local MD5 reuse, protected working/backup copies, CSV filenames, exact-ID downloads and duplicate names, checksums and permissions, transfer failures and cloud changes, retained local edits, independent restoration, pruning, and full cleanup. |
| [tests/test_desktop.py](test_desktop.py) | Terminal-enabled shortcut, graphical double-click routing, immediate startup output before a delayed offline check, desktop setup and launch success/failure, review prompts after edits, and a three-second countdown for unchanged, synchronized sessions. Pseudo-terminal checks preserve exit status; captured launches exit without waiting. |
| [tests/test_output.py](test_output.py) | Ordered setup stages and session progress, rclone output boundaries, offline and conflict feedback, unchanged saved settings, terminal colors, `NO_COLOR`, and plain captured output. |
| [tests/test_launcher.py](test_launcher.py) | Downloads, in-place uploads, checksums (including GNU checksum output for backslashes in local/state paths), file IDs, conflict choices, offline sessions, transfer failures, and interrupted-upload recovery. |
| [tests/test_identity.py](test_identity.py) | Same-folder renamed files, pinned duplicate names, exact-ID downloads, blocked ambiguous uploads, unpinned duplicate rejection, and identity/name changes during backup or upload checks. |
| [tests/test_root_remote.py](test_root_remote.py) | Installation and saved settings for `gdrive:Passwords.kdbx`, root listings, `KeePass-Backups` paths, downloads, uploads, conflict choices, interrupted-upload recovery, invalid paths, and rejection of recovery paths in another folder or remote. |
| [tests/test_backup_folder.py](test_backup_folder.py) | Custom cloud backup folder names for root and nested databases, uploads, interrupted-upload recovery, offline status, and conflict information. |
| [tests/test_security_lock.py](test_security_lock.py) | Nontruncating locks, symlink/hard-link/FIFO/directory rejection, private lock permissions, held-lock exclusion, unsafe storage and ancestor permissions, symlinked storage ancestors, and private creation of new state and backup directories. Ownership rejection is also checked when the runner can change file ownership. |
| [tests/test_security_download.py](test_security_download.py) | Download metadata settings, private staging, database permissions, failed permission changes, transfer and verification failures, and staging cleanup. |
| [tests/test_security_output.py](test_security_output.py) | Escaped control characters in cloud/local backup listings and command-line errors, newline-safe cloud records, ordinary Unicode filenames, and cloud-listing failure handling. |

Root-path tests use the same isolated fake commands as the folder tests. They do not verify KeeAnywhere authorization or real-account interoperability; see the [compatibility notes](../README.md#keeanywhere-compatibility).

## Isolated disposable setup

Prepare the manual tests in one Bash terminal. Fully quit KeePassXC, including its tray process, and confirm that your authenticated rclone remote is named `gdrive` (adjust the remote below if needed). Find the authentication file before isolating the launcher's configuration:

```bash
rclone config file
```

Set `RCLONE_CONFIG` to the absolute filename printed by that command:

```bash
export RCLONE_CONFIG='/absolute/path/printed/above/rclone.conf'
```

This keeps rclone using your authenticated remote when the checks below change `XDG_CONFIG_HOME` or `HOME`. Do not put credentials in the launcher configuration.

```bash
export KEESYNCXC_TEST_ROOT="$(mktemp -d /tmp/keesyncxc-test.XXXXXXXX)"
export KEESYNCXC_TEST_REMOTE="gdrive:KeeSyncXC-Test/${KEESYNCXC_TEST_ROOT##*/}/Passwords.kdbx"
export XDG_CONFIG_HOME="$KEESYNCXC_TEST_ROOT/manual-config"
cp -- keesyncxc.sh "$KEESYNCXC_TEST_ROOT/launcher.sh"
mkdir -p -- "$KEESYNCXC_TEST_ROOT/local"
printf 'Test workspace: %s\nTest remote: %s\n' "$KEESYNCXC_TEST_ROOT" "$KEESYNCXC_TEST_REMOTE"
```

Edit **only the copied script**, at the printed workspace path, and replace its configuration assignments with:

```bash
REMOTE="${KEESYNCXC_TEST_REMOTE:?}"
LOCAL="${KEESYNCXC_TEST_ROOT:?}/local/Passwords.kdbx"
REMOTE_BACKUP_FOLDER='KeePass-Backups'
```

The script uses these defaults when no saved launcher configuration exists, so setting `REMOTE` and `LOCAL` environment variables alone does not override them. The manual synchronization commands below use `--launch` to skip automatic installation and isolate configuration with `XDG_CONFIG_HOME` and synchronization state with `XDG_STATE_HOME`. The explicit `RCLONE_CONFIG` keeps authentication available.

Open KeePassXC directly and create a new database at `$KEESYNCXC_TEST_ROOT/seed.kdbx`, using the actual printed path in the save dialog. Give it a test password and a dummy entry named `Seed`. Save and fully quit KeePassXC. Upload this initial database:

```bash
rclone mkdir "${KEESYNCXC_TEST_REMOTE%/*}"
rclone copyto "$KEESYNCXC_TEST_ROOT/seed.kdbx" "$KEESYNCXC_TEST_REMOTE"
```

Define these helpers in the same terminal:

```bash
TEST_LOCAL="$KEESYNCXC_TEST_ROOT/local/Passwords.kdbx"
TEST_DATA="$KEESYNCXC_TEST_ROOT/state/keesyncxc"
TEST_STATE="$TEST_DATA/$(printf '%s' "$KEESYNCXC_TEST_REMOTE" | sha256sum | cut -d' ' -f1).md5"

run_launcher() {
    XDG_CONFIG_HOME="$KEESYNCXC_TEST_ROOT/manual-config" \
        XDG_STATE_HOME="$KEESYNCXC_TEST_ROOT/state" \
        bash "$KEESYNCXC_TEST_ROOT/launcher.sh" --launch
    local result=$?
    printf 'Launcher exit status: %s\n' "$result"
    return "$result"
}

check_sync() {
    md5sum -- "$TEST_LOCAL"
    rclone md5sum "$KEESYNCXC_TEST_REMOTE"
    cat -- "$TEST_STATE"
    printf 'Recorded Drive file ID: '
    cat -- "$TEST_STATE.id"
    rclone lsjson "$KEESYNCXC_TEST_REMOTE" --stat --hash
}
```

The helpers isolate the launcher settings and synchronization state. `HOME` and `XDG_DATA_HOME` are also isolated for the installation checks below, so the installed script and application-menu shortcut go into the disposable workspace. The authenticated rclone file remains outside that workspace through `RCLONE_CONFIG`.

## Installation and management checks

### First run and saved settings

Before testing normal synchronization, you can check the installer with the same disposable cloud database. Have all dependencies installed first; isolating `HOME` does not isolate `sudo apt-get`. Use a disposable Linux Mint, Ubuntu, or Debian machine to test package installation itself.

```bash
export KEESYNCXC_INSTALL_ROOT="$KEESYNCXC_TEST_ROOT/install"
KEESYNCXC_INSTALL_BACKUP_FOLDER='Test Cloud Backups'
mkdir -p -- "$KEESYNCXC_INSTALL_ROOT/home"
KEESYNCXC_INSTALL_BEFORE=$(rclone md5sum "$KEESYNCXC_TEST_REMOTE")
printf 'Installer cloud path: %s\nBackup folder name: %s\nInstaller local path: %s\n' \
    "$KEESYNCXC_TEST_REMOTE" "$KEESYNCXC_INSTALL_BACKUP_FOLDER" \
    "$KEESYNCXC_INSTALL_ROOT/local/Passwords.kdbx"

run_install_copy() {
    HOME="$KEESYNCXC_INSTALL_ROOT/home" \
        XDG_CONFIG_HOME="$KEESYNCXC_INSTALL_ROOT/config" \
        XDG_DATA_HOME="$KEESYNCXC_INSTALL_ROOT/data" \
        XDG_STATE_HOME="$KEESYNCXC_INSTALL_ROOT/state" \
        bash "$KEESYNCXC_TEST_ROOT/launcher.sh" "$@"
}

run_install_copy
```

With no options and no installation yet, the script must start setup automatically. Enter the printed cloud path, backup folder name, and local path, in that order, then decline reconfiguring the existing remote. The resolved cloud backup path must end in `/Test Cloud Backups`. These checks reuse your authenticated remote to read the disposable database; do not change its credentials. Test new-remote configuration with a separate disposable rclone config instead. Expect a successful installation and no KeePassXC window. Verify the installed files and preserved cloud contents:

```bash
test -x "$KEESYNCXC_INSTALL_ROOT/home/.local/bin/keesyncxc"
test -f "$KEESYNCXC_INSTALL_ROOT/data/applications/keesyncxc.desktop"
cat "$KEESYNCXC_INSTALL_ROOT/data/applications/keesyncxc.desktop"
printf '%s\n%s\n%s\n' "$KEESYNCXC_TEST_REMOTE" \
    "$KEESYNCXC_INSTALL_ROOT/local/Passwords.kdbx" \
    "$KEESYNCXC_INSTALL_BACKUP_FOLDER" \
    | cmp - "$KEESYNCXC_INSTALL_ROOT/config/keesyncxc/config"
test ! -e "$KEESYNCXC_INSTALL_ROOT/local/Passwords.kdbx"
test "$KEESYNCXC_INSTALL_BEFORE" = "$(rclone md5sum "$KEESYNCXC_TEST_REMOTE")"
```

Each check should exit with status `0`. The shortcut must contain `Terminal=true` and an `Exec` command that runs the installed launcher through `/bin/bash` with `--desktop`. The config file must contain exactly the full `remote:path`, local path, and backup folder name on separate lines, without shell assignments. Installation must not create a synchronization baseline, download the database, or create the cloud backup folder. Run `run_install_copy --install` again: the cloud prompt must distinguish the script's built-in default from the current saved setting. Accept the saved setting with Enter; expect the same settings, an updated executable and shortcut, no editor, and unchanged cloud contents. Repeat with the path `${KEESYNCXC_TEST_REMOTE#*:}` at the cloud prompt: expect the installer to use the shown remote name and save the same full remote path. To check a missing cloud file, enter a nonexistent `.kdbx` filename during another explicit `--install` attempt; expect failure without creating or uploading that file. Keep installation tests separate from the `manual-config` and `state` directories used below.

Reconfigure the cloud backup folder to another single name such as `More Test Backups`. Expect the new value as the default on the next installation and in the offline status path. No cloud folder or backup should be created or moved during setup. Restore `Test Cloud Backups` afterward. Names containing `/`, `:`, control characters, or consisting of `.` or `..` must be rejected, as must the database's own filename. The automated tests also check that pending conflicts and upload recovery prevent changing this folder name.

The saved settings must take precedence over the copied script's default paths on later launches. For automatic setup, an installation is complete only when the saved config, executable installed launcher, and menu shortcut exist. To check repair, move one of these disposable files aside and invoke `run_install_copy` without options; expect setup again and no editor. Repeat for each file, restoring a complete installation between checks. The automated management suite also covers these cases.

Check `bash keesyncxc.sh --help` as well: it should identify KeeSyncXC v1.0.0 and describe automatic setup, explicit launch, installation, and maintenance without requiring rclone authentication or opening an editing session. `bash keesyncxc.sh --version` should print only `KeeSyncXC v1.0.0`, including when configuration is invalid or dependencies are unavailable. The installed `keesyncxc --version` command should report the same version. The automated setup and maintenance tests cover rejected input and failures without changing real packages, user files, or Google Drive.

### Readable terminal output

During the isolated installation, check that five numbered stages distinguish database locations, system tools, the Google Drive connection, database verification, and installing the launcher. The location prompts must ask for the cloud database, backup folder name, and local database, in that order. An unconfigured original source script must offer `gdrive:KeePass/kpDB.kdbx` as its cloud default; the disposable copy offers its explicitly set test path. A saved root path must appear as a saved setting, with the built-in default shown separately, and must remain selected when you press Enter. Changing that selection requires entering another path; setup must never move the cloud file automatically. The selected cloud database, local database, and cloud backup locations must be easy to find together. Any package installation or rclone configuration must be introduced clearly, with the external tool's own prompts still visible. Full-width, labelled start and end borders must clearly enclose rclone's own output. After saving a connection and quitting rclone, expect a visible return to the launcher. The completion summary must show how to launch the installed application.

Check `[INFO]`, `[ OK ]`, `[WAIT]`, `[WARN]`, and `[FAIL]` labels where applicable: completed checks must be distinguishable from actions still waiting, and a failure must explain the problem and next step. Normal sessions must separate synchronization before editing, the editor session, and synchronization after editing. Use the offline and conflict checks below to verify that these states remain understandable without color.

Open `--menu`, enter and leave each submenu, then return to the main menu. Check that each menu has a complete box around its title and options, with the choice prompt below it. The main menu must use `=` horizontal borders and `||` sides, and show no cloud/local path summary before the box on entry or return. Submenus keep their single-line borders. Maintenance option 3 must read **Uninstall launcher and/or remove local data**. Main-menu **H** must briefly explain each option using the same **1–5**, **H**, and **0** labels and explain that each submenu has its own help. Open **H** in each submenu to check that its detailed help returns to that submenu. Use **0** to return and invalid input to confirm that the current submenu remains active. Cancelling an action should return quietly without a generic failure warning. Scroll upward to confirm previous menus and action output remain visible. Repeat in a narrow terminal to check text wrapping and aligned borders. During the disposable conflict scenarios below, check the same framing around terminal conflict choices.

On an interactive terminal with color support and `NO_COLOR` unset, check that headings, menu borders, and status labels have readable colors. Repeat status with color disabled, then capture its output:

```bash
NO_COLOR=1 run_install_copy --status
TERM=dumb run_install_copy --status
run_install_copy --status > "$KEESYNCXC_TEST_ROOT/status-output.txt"
cat -- "$KEESYNCXC_TEST_ROOT/status-output.txt"
```

All three outputs must remain readable without ANSI color escapes, spinners, or terminal-control characters. Redirected status must still list the configured locations and must not contact Drive. The new prompt order must not change the saved configuration order: cloud database, local database, then backup folder name. Exit codes and synchronization behavior must remain unchanged by output formatting.

### Feedback from the desktop shortcut

Test the actual application-menu entry on a disposable desktop account whose launcher uses the test database. Refresh that account's installed script and shortcut with `bash keesyncxc.sh --repair` from the updated source first. Do not launch the production menu entry for these checks.

1. Open **KeeSyncXC** from the application menu. Expect a terminal to appear with a startup message before any network check finishes. It must show the Drive check, any download or upload, and KeePassXC opening. Leave the terminal open during editing; it must explain that synchronization waits for KeePassXC to fully quit. Close KeePassXC's tray process too if necessary. Quit without changing the database. After the final check confirms matching local and cloud copies, expect `Auto-closing in 3 seconds`, then `2` and `1` seconds, followed by terminal closure. The countdown must last about three seconds.
2. Launch again, add and save a dummy entry, then fully quit KeePassXC. Expect a successful backup and upload, matching hashes, and a terminal that remains open with an Enter prompt so you can review the result. No automatic-closing countdown should appear. Press Enter to close it.
3. After a successful synchronization, disconnect the network and launch from the menu again. The startup and connection-check messages must remain visible while rclone retries. Expect offline editing to open after the retries. Save a dummy change and fully quit while still offline. Expect the deferred synchronization error to remain visible with an Enter prompt. Press Enter to close the terminal, reconnect, and launch again to synchronize the saved local change. Also check an offline session without editing: lack of a confirmed cloud match must prevent automatic closure.
4. With KeePassXC already running independently, open the menu entry. Expect a visible error and an Enter prompt rather than a disappearing window. Fully quit the independent process afterward. Use the automated pseudo-terminal tests for exit-code preservation and failure prompts without a real desktop.
5. Create a disposable conflict and launch from the menu. Expect the conflict choices in the visible terminal. The separate graphical-dialog check below still applies to direct launches whose standard input is not a terminal.
6. On an incomplete disposable installation, launch from the menu and finish setup. Expect the installation summary to stay visible with an Enter prompt. No closing countdown should appear because setup has not checked that the local and cloud databases match. Headless or piped-input `--desktop` runs without an interactive terminal must finish without an Enter wait or countdown, on success or failure.
7. In the file manager, double-click the executable disposable script and choose **Run**. With `DISPLAY` or `WAYLAND_DISPLAY` set and no existing terminal streams, expect `x-terminal-emulator` to open a terminal before any setup detection or network check, then the same session feedback and closing behavior as the menu shortcut. Repeat on an incomplete disposable installation and expect setup in the terminal. Choosing **Display** only opens the source text; choosing **Run in Terminal** uses the file manager's terminal. File-manager script execution settings must permit **Run** for this check.
8. Use the automated mocks to check a missing `x-terminal-emulator`: graphical no-option and `--desktop` launches must stop before cloud access and show a Zenity error when available. Explicit `--launch`, headless runs, piped answers, and existing terminal sessions must not gain an extra terminal window.
9. Rename a disposable, already pinned cloud database within the same Drive folder and launch through the shortcut. Expect a warning showing the configured and current cloud paths. The terminal must wait for Enter after an unchanged session so the warning remains visible. Restore the original name afterward. Duplicate-name warnings must also prevent automatic closure; the isolated identity tests cover that case without creating real duplicate cloud files.

Keep the terminal open until the final synchronization completes. Closing it can interrupt an upload; use the deterministic interrupted-upload tests below to check recovery rather than terminating a live transfer.

### Menus, settings, and uninstall

Use the isolated installation to check the management commands:

```bash
run_install_copy --status
run_install_copy --backups
run_install_copy --menu
```

Status, settings, and local backup listing must work offline without opening KeePassXC, changing files, or creating a cloud backup folder. Status must display the configured `Cloud backups` path ending in `/Test Cloud Backups`. Local inventory must include dates, sizes, upload snapshots, switch backups, and recovery archives when present.

Check these main menu branches; `--interactive` must show the same structure:

| Branch | Actions to verify |
| --- | --- |
| **1: Open database** | Synchronize, open KeePassXC, then synchronize on full exit. |
| **2: Status and troubleshooting** | Offline status, read-only Drive connection check, synchronization without opening KeePassXC. |
| **3: Backups and recovery** | Local inventory, open folder, read-only cloud listing, inspect local or cloud backup, age pruning, count pruning, restore backup. |
| **4: Settings and database selection** | Show settings, change cloud backup folder, move local database, open rclone configuration, switch cloud database. |
| **5: Installation and advanced maintenance** | Repair launcher/shortcut, check/install tools, uninstall menu (launcher only, local data only, or both), delete local database/backups/setup. |
| **H: Help**, **0: Quit** | Main Help shows the general guide; status, backups, settings, and maintenance Help each explain only that submenu's actions and return there. Quit exits; every submenu offers Back. |

Check **H** in `--cleanup` for age/count pruning and retention guidance, and in each backup selector for inspection or restoration guidance, returning to the same selection prompt. Inspection Help must explain that edits affect only the backup without synchronization; restoration Help must explain the verified safety copy, preserved baseline/Drive ID, and later synchronization, and both must mention backups from previous database selections.

In **Installation and advanced maintenance**, option 3 must open the same numbered menu as `--uninstall`: 1 removes the launcher and settings while keeping local data, 2 deletes local data and setup while keeping the launcher, and 3 removes both. **H** in maintenance must explain how to reach complete uninstall; **H** inside the uninstall menu must explain all three choices and return to that menu. Each action must show one file preview and require one Yes/No confirmation, defaulting to No. **0**, blank input, and end-of-input must cancel without deleting files. Option 4 in maintenance still opens local-data deletion directly. KeePassXC, rclone, credentials, cloud files, and source files remain installed or preserved.

Run `run_install_copy --settings` and `run_install_copy --backup-folder`. Change only the backup folder name, verify the saved remote and local path remain unchanged, and restore the original name. This must work offline without installing tools, opening rclone configuration, moving old cloud backups, or creating a cloud folder. Invalid names and pending conflicts or upload transactions must prevent the change. Run `run_install_copy --repair` and verify that only the installed script and shortcut refresh, retaining saved settings, databases, and state without contacting Drive. With no saved configuration, repair should enter full setup. `--install` remains the full setup/reconfiguration wizard. Use mocks or a disposable machine for `--dependencies`; it may invoke `sudo apt-get`. Test `--connection` only with a disposable rclone configuration, since it opens rclone's own editable wizard.

Run `run_install_copy --check-connection` and `run_install_copy --cloud-backups` before the first upload. Both must use read-only requests, preserve all local synchronization records, and avoid creating the backup folder. A missing folder should be reported clearly. Repeat after an upload creates backups and confirm the expected folder contents appear. Neither action may open KeePassXC or transfer database contents.

Run `run_install_copy --uninstall-keep-data` and decline first; all installed files must remain. Confirm it on a second run; only the installed executable, menu shortcut, and saved configuration may disappear. Databases, state including pinned IDs, and backups must remain. This flag must bypass the numbered uninstall menu and never offer local-data deletion. Reinstall for subsequent checks.

Run `run_install_copy --uninstall`, choose **1**, and decline the deletion confirmation; all installed files must remain. Run it again, choose **1**, and confirm once. Only the installed executable, menu shortcut, and saved launcher configuration must disappear. The source copy, rclone authentication, system packages, and any databases, state including recorded file IDs, and backups must remain. Run `run_install_copy` without options again and expect setup to return, finishing without opening KeePassXC. Once installation is complete, a later invocation without options must launch normally; fully quit the editor before continuing. Repeat with `"$KEESYNCXC_INSTALL_ROOT/home/.local/bin/keesyncxc"` as the Bash target inside `run_install_copy`, retaining all four environment overrides, to verify that the isolated installed script can uninstall itself.

### Back up and choose another database

Use only the isolated installation and disposable cloud files. Launch once to establish its local copy and state, then fully quit KeePassXC. Add a local-only dummy entry by opening its working database directly and saving without the launcher. Prepare another test cloud database under a different name in the same disposable Drive folder.

Run `run_install_copy --switch-database` (also `--reset`), or select **Settings and database selection → Switch cloud database**. Before clearing the active selection, it must create and verify a `switch-*.kdbx` backup containing the local-only edit and archive the active settings and records under `backups/reset-*/`. Confirm the operation and select the other disposable cloud database in the new setup. The previous local path should be offered as the new download destination. Setup must not upload, modify either cloud database, or open KeePassXC; the next launch downloads the newly selected database.

Repeat with a nonexistent new cloud filename so setup fails. The verified switch backup and archived configuration, baseline, pinned ID, and any active recovery records must remain available for investigation. Cancelling before the switch must preserve the active selection and working database. Records for unrelated targets must remain; selecting one with an existing baseline and a missing local working copy must fail safely and request restoration. Ordinary age/count backup pruning must never select switch backups or archives.

### Move and restore the local database

Use only an established disposable installation with KeePassXC fully closed. Record the database hash, baseline, pinned Drive ID, and cloud hash. Run `run_install_copy --move-local`, choose an unused absolute destination outside launcher directories, and confirm. The destination must match the original hash, the old local path must disappear only after verification, and the saved local path must change. The baseline, Drive ID, and cloud contents must remain unchanged. Launch again to verify normal use at the new location. Cancelling, an existing destination, or a failed copy/verification must preserve the original database and settings. Missing settings, a pending conflict, or an interrupted upload must block the move. Moving a configured local file without a baseline must preserve the absence of that baseline.

After generating recognizable local backups, run `run_install_copy --inspect-backup`. Select a backup and verify it opens in KeePassXC after a warning explaining that edits affect that copy. Save a dummy entry and fully quit. Only the selected backup may change; the working database, baseline, cloud, and pinned ID must remain unchanged, and no synchronization may follow. Selecting **0** instead of a backup must preserve all files.

When disposable cloud backups exist, select **Backups and recovery → 8: Inspect a cloud backup**, or run `run_install_copy --inspect-cloud-backup`. Check filenames, modification dates, and sizes, open **H** and return to the same selection, and cancel once. Then select a backup. Expect a verified `cloud-*.kdbx` copy in the private local backup folder and KeePassXC opening that copy. Save a dummy edit and fully quit. The cloud backup, working database, baseline, pinned ID, and any pending recovery/review records must remain unchanged, with no synchronization afterward. The local inspection copy must retain the edit and appear in local inventory and restoration choices. Inspect an unchanged cloud backup twice and verify the same matching local inspection copy is reused without a download or duplicate file. A matching working database or other recognized backup must supply a separate copy before opening; editing that inspection copy must preserve the original. Same-size edits with a different MD5, unreadable candidates, and symlinks must not be reused. The selected cloud ID/checksum and the local copy must still be rechecked before opening, even when no download occurs. Ordinary pruning must retain it; full local cleanup must recognize it and abandoned private inspection stages. Test empty folders, duplicate names, hostile filenames, failed or corrupt downloads, cloud changes during transfer, and editor failure with the isolated mock suite.

Run `run_install_copy --restore-backup`. Choose a recognized regular `.kdbx` backup with visibly different test contents. Before confirming, check that the warning reminds you to inspect before publication and explains that a later upload needs separate approval. Confirm and expect a verified safety backup of the current working database, replacement with the selected contents, preservation of the baseline and pinned ID, and a `.md5.restore` review record. There must be no cloud access or automatic editor launch. Inspect the restored local contents directly and fully quit KeePassXC.

Run `--sync` and decline the publication confirmation; repeat with blank input and end-of-input. Both databases and the review record must remain, with no cloud backup creation or upload. Restart and confirm it asks again. Approve once; expect a verified cloud backup followed by publication and removal of the review record. With both sides changed, choose local publication from the conflict menu and verify it still asks for restoration approval. Offline edits and failed uploads must retain this requirement; a successfully recovered completed upload may clear it without another upload. If both copies already match, or only Drive changed while the restored copy equals the baseline, synchronization must finish without publication approval. Normal edits after successful publication must not keep prompting.

Check restoration cancellation, a missing baseline, an unrecognized path or symlink, unsafe review records, and pending conflict/upload recovery with mocks or disposable records. A failure to save the review record must preserve the working database. Pending review blocks pruning, moving, and reconfiguration; basic uninstall retains it, a database switch archives it, and full local cleanup removes it.

## Synchronization checks

### Hashes, backups, and state files

Run `check_sync` after each successful synchronization. All three hashes and the JSON `Hashes.MD5` must match. The JSON `ID` must match the recorded Drive file ID and remain unchanged across uploads. A normal completed run prints `Finished.` and returns `0`; cancellation, unresolved errors, and synchronization still deferred on exit return nonzero. The script's internal status values are not its public exit-code contract.

Inspect backups with:

```bash
ls -l -- "$TEST_DATA/backups"
rclone lsf "${KEESYNCXC_TEST_REMOTE%/*}/KeePass-Backups" --files-only
```

Local safety copies are named `local-*.kdbx`, upload snapshots are `upload-*.kdbx`, and cloud backups are `Passwords-*.kdbx` inside the `KeePass-Backups` subfolder. The cloud listing can report a missing folder before the first backup is needed; setup, downloads, and unchanged sessions must not create it. To verify backup contents, open a local backup directly, or download a cloud backup to another filename and open it. Fully quit KeePassXC afterward.

State filenames identify the configured remote by its SHA-256 hash; the `.md5` file's contents are the last confirmed shared database MD5. Inspect these artifacts as the scenarios create them:

| Artifact | Expected role |
| --- | --- |
| `"$TEST_DATA/launcher.lock"` | Coordinates editing, installation, and maintenance. The file can remain after exit; the held lock prevents overlapping sessions. |
| `"$TEST_STATE"` | Last confirmed synchronization baseline, checked by `check_sync`. |
| `"$TEST_STATE.id"` | Actual Drive file ID, established on the first online launch. Ordinary backup pruning and basic uninstall retain it; full local cleanup deliberately removes it. |
| `"$TEST_STATE.conflict"` | Pending conflict that must survive cancellation until explicitly resolved. |
| `"$TEST_STATE.transaction"` | Interrupted upload recovery record; preserve it while recovery is pending. |
| `"$TEST_DATA/backups/interrupted-upload-*.txt"` | Archived recovery records from handled interrupted uploads. |
| `"$TEST_DATA/backups/switch-*.kdbx"` | Verified database copies created before switching the active cloud database. Ordinary backup pruning preserves them. |
| `"$TEST_DATA/backups/reset-*/"` | Saved active configuration and synchronization records from switching databases. Ordinary backup pruning preserves them. |

### Cleanup protections

Use only disposable backups. Invoke the copied script with the usual manual configuration and state directories and `--cleanup` instead of `--launch`. Expect a submenu with **1: Older than a chosen age**, **2: Keep the newest copies**, and **0: Back**. The same pruning actions appear as **5** and **6** in **Backups and recovery**. Full local deletion must be separate under advanced maintenance or the explicit `--clear-local` option.

For age pruning, expect a default of 30 days; for newest-count pruning, expect 10 copies. Both accept zero. Count retention applies to the combined eligible `local-*.kdbx` and `upload-*.kdbx` list, sorted by modification time with filenames breaking ties. Every deletion must show its exact candidate list and ask for confirmation defaulting to **No**. Declining keeps every file; confirming removes only the selected generated backup/snapshot files. Unknown filenames, symlinks, the current local database, pinned IDs, recovery records, and switch backups/archives must remain. A held launcher lock or any pending `.conflict` or `.transaction` record anywhere in this state directory must prevent ordinary pruning. Do not alter real recovery records to make pruning proceed.

Check `run_install_copy --clear-local`, also **Installation and advanced maintenance → Delete local database, backups, and setup**, only with the isolated installation, so the synchronization scenarios below remain usable. Its preview must explicitly include the configured working database, recognized generated backups and recovery/switch archives, synchronization records including recorded IDs, saved configuration, and generated launcher temporary files in their expected locations. The working database path must appear only once in the preview. Decline once and verify preservation. On confirmation, those listed files must disappear even if disposable conflicts or interrupted uploads were pending. Unknown files and symlinks remain; the installed launcher and shortcut remain, and the next invocation starts setup. The shared lock and its state directory can remain. Repeat through `--uninstall` choice **2** for the same result. With a fresh disposable installation, choose **3** for complete uninstall: it must show one preview and require one confirmation, and the installed launcher and shortcut must also be removed. Declining or reaching end-of-input at any deletion confirmation must preserve all selected files.

All maintenance deletion must refuse to overlap a running launcher or KeePassXC. No cleanup or uninstall option may contact or remove cloud files, rclone credentials, packages, or the source checkout. Automated tests exercise these cases with isolated files.

### Editing and conflict scenarios

Some checks inspect Drive while the launcher is waiting for KeePassXC to exit. Use a second terminal for those commands, first setting these variables with the actual workspace path printed during setup (and the same remote name if you changed `gdrive`):

```bash
export KEESYNCXC_TEST_ROOT='/tmp/REPLACE-WITH-YOUR-TEST-WORKSPACE'
export KEESYNCXC_TEST_REMOTE="gdrive:KeeSyncXC-Test/${KEESYNCXC_TEST_ROOT##*/}/Passwords.kdbx"
export XDG_CONFIG_HOME="$KEESYNCXC_TEST_ROOT/manual-config"
export RCLONE_CONFIG='/absolute/path/printed/above/rclone.conf'
```

Perform these manual checks in order. Unless a step says otherwise, keep the network connected and fully quit KeePassXC between launches.

1. **First download.** Run `run_launcher` while the test local database and test state are still absent. KeePassXC must open the downloaded database containing `Seed`. Quit without editing. Expect status `0`, matching hashes, a new state file, and a recorded cloud file ID at `"$TEST_STATE.id"`. The cloud source must remain unchanged, and the launcher must not create `KeePass-Backups` yet.

2. **No changes.** Record the backup filenames, run the launcher again, and quit without editing. Expect `Local and Drive copies match.`, status `0`, and no new upload snapshots or cloud backups. The state file may be rewritten with the same hash.

3. **Save, tray, and full exit.** Launch, add `Saved-through-launcher`, and save. While KeePassXC remains running, inspect the cloud with `rclone md5sum "$KEESYNCXC_TEST_REMOTE"` in the second terminal; it must still have the old hash. If closing the window sends KeePassXC to the tray, the launcher must keep waiting and the cloud must remain unchanged. Fully quit through KeePassXC's Quit/Exit action. Expect matching hashes, an unchanged cloud file ID, a retained local upload snapshot, and an automatically created `KeePass-Backups` subfolder containing a dated cloud backup of the database before this edit. If your settings quit on window close, test the save-while-open behavior and then exit normally.

4. **Local change before launch.** With no launcher running, open `"$TEST_LOCAL"` directly in KeePassXC, add `Local-before-launch`, save, and fully quit. Run the launcher. Expect the local change to upload **before** the editing session opens, preserving the previous cloud version as a backup. Quit without editing and confirm matching hashes.

5. **Cloud-only change.** With KeePassXC closed, run the commands below. Add `Cloud-only` in the downloaded copy, save, and fully quit before uploading it. Then run the launcher. Expect the cloud version to download before opening, the previous local contents to be preserved in a `local-*.kdbx` backup, and `Cloud-only` to appear. Quit and confirm matching hashes.

   ```bash
   rclone copyto "$KEESYNCXC_TEST_REMOTE" "$KEESYNCXC_TEST_ROOT/cloud-edit.kdbx"
   keepassxc "$KEESYNCXC_TEST_ROOT/cloud-edit.kdbx"
   rclone copyto "$KEESYNCXC_TEST_ROOT/cloud-edit.kdbx" "$KEESYNCXC_TEST_REMOTE"
   ```

6. **Create a conflict and inspect information.** Starting with matching hashes, open the local database directly, add `Conflict-local`, save, and fully quit. Independently download and edit the cloud copy using the commands from step 5, adding `Conflict-cloud`, then upload it. Run the launcher. Expect a conflict menu and a `"$TEST_STATE.conflict"` marker; each working copy must still contain its own distinct edit. Choose **H: Explain conflict choices and recovery**. Verify that the explanation includes the configured local path, remote path, backup locations, and resolution choices, then returns to the menu.

7. **Reference editing.** In that menu, choose **1: Review/edit local changes**. Confirm `Conflict-local` is present, add `Reference-edit`, save, and fully quit. Expect the conflict menu to appear again. The cloud must still contain `Conflict-cloud` and must not contain `Reference-edit`; no automatic upload should have occurred.

8. **Cancel and retry.** Choose **0: Cancel and resolve later**. Expect a nonzero exit, both versions preserved, and the conflict marker retained. Run the launcher again. Expect the conflict menu again, before an editing session opens. Terminal input **4** also cancels, and **5** also opens help.

9. **Adopt the cloud version.** Choose **2: Use the latest cloud database**. Expect a local backup containing `Conflict-local` and `Reference-edit`, and an editing session containing `Conflict-cloud`. Add `After-cloud-adoption`, save, and fully quit. Expect normal synchronization, matching hashes, and removal of the conflict marker. The newly saved cloud edit must upload successfully.

10. **Explicitly overwrite the cloud.** Create another conflict as in step 6, using new distinct entry names. Choose **3: Publish local changes over the cloud database**. Check the printed local, cloud, and backup locations; selecting this choice authorizes publication without a second confirmation. Expect the current local version to become the cloud version, the previous cloud version to remain in a dated cloud backup, matching hashes, and removal of the conflict marker. This choice finishes after publication; it does not open another editing session.

11. **Conflict detected after editing.** Start synchronized. Download the cloud database to `"$KEESYNCXC_TEST_ROOT/concurrent-cloud.kdbx"`, open that downloaded copy directly, add a distinct entry, save, and fully quit. Leave it unuploaded for now. Run the launcher, add and save a different local entry, and leave KeePassXC open. In the second terminal, upload the prepared cloud edit with `rclone copyto "$KEESYNCXC_TEST_ROOT/concurrent-cloud.kdbx" "$KEESYNCXC_TEST_REMOTE"`. Fully quit the launcher-managed KeePassXC. Expect the conflict menu after exit and preservation of both edits. Cancel to check preservation, then rerun and resolve with choice 2 or 3 before continuing. This simulates another device changing Drive during the editing session; backup and upload timing failures are covered by the regression tests. There is still a window between checking Drive and writing, so use one editing device at a time outside this deliberate conflict test.

12. **Offline editing and retry.** Start synchronized, disconnect the network, and run the launcher. After rclone's retries, expect the previously synchronized local database to open with an offline/deferred message. Add `Offline-edit`, save, and fully quit while still offline. Expect a nonzero exit and `Post-close sync deferred`; the local edit and previous baseline must remain. Reconnect and run again. If the cloud was unchanged, expect the saved local edit to upload before opening. Quit and check matching hashes. If another device changed the cloud, expect a conflict instead.

13. **Overlapping launchers.** Run the launcher and leave KeePassXC open. In the second terminal, with the exported variables set as described above, run:

    ```bash
    XDG_CONFIG_HOME="$KEESYNCXC_TEST_ROOT/manual-config" \
        XDG_STATE_HOME="$KEESYNCXC_TEST_ROOT/state" \
        bash "$KEESYNCXC_TEST_ROOT/launcher.sh" --launch
    ```

    Expect the second invocation to fail with `Another copy of this launcher is already running.` It must not open a second session or change either database. Fully quit the first session.

14. **KeePassXC already running independently.** Open the test database directly, then invoke `run_launcher`. Expect a nonzero exit and `KeePassXC is already running`, without synchronization. Fully quit the independently opened application before continuing.

15. **Real graphical conflict dialogs.** Create a conflict again. From the graphical desktop session, run `run_launcher </dev/null` so standard input is not a terminal. With `zenity` installed and `DISPLAY` or `WAYLAND_DISPLAY` set, expect the graphical conflict menu. Test More information, close that information window to return to the menu, and cancel. Expect preservation and a nonzero exit. Rerun to try reference editing, then cancel again so the conflict remains pending for the next check. The automated suite mocks these dialogs, so this checks the actual desktop integration.

16. **No available conflict interface.** With a conflict still pending, run the following command. Expect a nonzero exit without editing or publishing, and the conflict marker retained. Resolve the conflict in the normal terminal before continuing.

    ```bash
    env -u DISPLAY -u WAYLAND_DISPLAY \
        XDG_CONFIG_HOME="$KEESYNCXC_TEST_ROOT/manual-config" \
        XDG_STATE_HOME="$KEESYNCXC_TEST_ROOT/state" \
        bash "$KEESYNCXC_TEST_ROOT/launcher.sh" --launch </dev/null
    ```

17. **Synchronize without an editor.** With the copies synchronized, invoke the copied script with the same isolated configuration and state directories but use `--sync`. Expect status `0`, no KeePassXC window, and matching hashes. Repeat after a local-only change and a cloud-only change, then check the usual backups, baseline, and unchanged Drive ID. Create a conflict again and run `--sync`: **1** must explain that local review requires an editing launch and redisplay the choices without opening KeePassXC; **2** must adopt the cloud copy and finish without opening it; **3** must show the publication locations and publish without opening the editor. Cancellation and an offline connection must return nonzero with local work preserved. Use **Status and troubleshooting → Synchronize without opening KeePassXC** to verify the same behavior through the menu.

### Optional real Drive root check

After completing the folder checks, fully quit KeePassXC and use a separate configuration, state directory, local destination, and unique cloud filename for a root-level check. Keep the authentication and disposable seed database from the setup above. First upload that seed under the unique root filename:

```bash
export KEESYNCXC_ROOT_TEST_REMOTE="gdrive:KeeSyncXC-Root-Test-${KEESYNCXC_TEST_ROOT##*/}.kdbx"
printf 'Root test remote: %s\n' "$KEESYNCXC_ROOT_TEST_REMOTE"
rclone copyto "$KEESYNCXC_TEST_ROOT/seed.kdbx" "$KEESYNCXC_ROOT_TEST_REMOTE" --immutable
```

Continue only if the upload succeeds. Do not substitute the filename of a production database. Configure and launch this separate test:

```bash
mkdir -p -- "$KEESYNCXC_TEST_ROOT/root-config/keesyncxc"
printf '%s\n%s\n%s\n' "$KEESYNCXC_ROOT_TEST_REMOTE" \
    "$KEESYNCXC_TEST_ROOT/root-local.kdbx" \
    'KeePass-Backups' \
    > "$KEESYNCXC_TEST_ROOT/root-config/keesyncxc/config"
XDG_CONFIG_HOME="$KEESYNCXC_TEST_ROOT/root-config" \
    XDG_STATE_HOME="$KEESYNCXC_TEST_ROOT/root-state" \
    bash "$KEESYNCXC_TEST_ROOT/launcher.sh" --launch
```

Expect the seed database to download and open. Add a dummy entry, save, and fully quit. Expect successful publication, matching local/cloud hashes, an unchanged Drive file ID, and a dated cloud backup under `gdrive:KeePass-Backups/`. The working database must stay in `gdrive:`. Compare `rclone lsjson "$KEESYNCXC_ROOT_TEST_REMOTE" --stat --hash` with the `.md5.id` record under the separate `root-state/keesyncxc` directory. Inspect the backup with:

```bash
rclone lsf 'gdrive:KeePass-Backups' --files-only
```

Use the exact backup path printed by the launcher to distinguish this test's backup from any other databases' backups in that shared folder. This checks real rclone root paths, not KeeAnywhere compatibility. Retain the printed database and backup paths so you can remove only these test files afterward.

### Optional KeeAnywhere restricted-mode check

This check needs KeePass with KeeAnywhere on the other device. Use a different disposable root file and fresh launcher state; do not reuse a production database or an object created by the earlier rclone tests. Print a unique filename:

```bash
export KEESYNCXC_ANYWHERE_TEST_REMOTE="gdrive:KeeAnywhere-Launcher-Test-${KEESYNCXC_TEST_ROOT##*/}.kdbx"
printf 'Create this test file through KeeAnywhere: %s\n' "$KEESYNCXC_ANYWHERE_TEST_REMOTE"
```

In KeePass, create a disposable database with a dummy entry named `Anywhere-seed`, then use **File → Save As → Save to Cloud Drive** and KeeAnywhere's **Google Drive (restricted)** account to save it to the printed root filename. Close and reopen it through KeeAnywhere to establish that access works before testing the launcher. Fully quit KeePass.

On the launcher machine, record the original Drive ID and prepare isolated settings:

```bash
rclone lsjson "$KEESYNCXC_ANYWHERE_TEST_REMOTE" --stat --hash
mkdir -p -- "$KEESYNCXC_TEST_ROOT/anywhere-config/keesyncxc"
printf '%s\n%s\n%s\n' "$KEESYNCXC_ANYWHERE_TEST_REMOTE" \
    "$KEESYNCXC_TEST_ROOT/anywhere-local.kdbx" \
    'KeePass-Backups' \
    > "$KEESYNCXC_TEST_ROOT/anywhere-config/keesyncxc/config"
XDG_CONFIG_HOME="$KEESYNCXC_TEST_ROOT/anywhere-config" \
    XDG_STATE_HOME="$KEESYNCXC_TEST_ROOT/anywhere-state" \
    bash "$KEESYNCXC_TEST_ROOT/launcher.sh" --launch
rclone lsjson "$KEESYNCXC_ANYWHERE_TEST_REMOTE" --stat --hash
```

During that editing session, add `Launcher-round-trip`, save, and fully quit KeePassXC. The before/after Drive IDs must match, the database MD5 must change, and a dated backup in `gdrive:KeePass-Backups/` must preserve the initial contents. The working database must stay in the root. Close and reopen KeeAnywhere's picker, open the same root filename, and confirm `Launcher-round-trip` appears. A backup created by rclone may be invisible in the restricted picker; inspect its exact printed path with rclone instead.

Add `Anywhere-round-trip` through KeeAnywhere, save, and fully quit KeePass. Run the same isolated launcher command again and confirm that entry downloads and appears in KeePassXC. Quit and recheck the hashes and ID. Record the application versions and exact error text if either direction fails. The fake-command suite cannot establish these live authorization results.

## Invalid configuration and failure scenarios

The following checks deliberately make the disposable setup invalid. Perform them after the normal checks, restoring the test files as instructed.

| Check | Instructions | Expected result |
| --- | --- | --- |
| First use with a different existing local file | After a successful sync, edit and save only the test local database directly. Invoke the copied script with `XDG_STATE_HOME="$KEESYNCXC_TEST_ROOT/fresh-state"` instead of the usual state directory. | Nonzero exit before opening; the existing local database and cloud version remain unchanged. The message explains that the cloud must be the initial source. Return to the normal state directory and run the launcher to publish the pending local edit. |
| First use with a matching local file | With matching local/cloud hashes, invoke the copied script using a new state directory such as `XDG_STATE_HOME="$KEESYNCXC_TEST_ROOT/matching-state"`. Quit without editing. | The launcher accepts the matching local copy, writes the baseline in that state directory, and completes successfully. |
| Offline first use | Disconnect the network and invoke the copied script with another unused state directory. | Nonzero exit; no editor opens, because the local file has no established baseline in that directory. Reconnect afterward. |
| Missing previously synchronized local file | With no application open, move `"$TEST_LOCAL"` to `"$KEESYNCXC_TEST_ROOT/held.kdbx"`, then run the normal launcher. Restore it to its original path afterward. | Nonzero exit before opening; the launcher asks for restoration instead of silently downloading over missing editing state. Cloud and baseline remain unchanged. |
| Invalid baseline | Copy `"$TEST_STATE"` to `"$KEESYNCXC_TEST_ROOT/saved-state.md5"`, replace the test state contents with `invalid`, and run the launcher. Restore the saved state file afterward. | Nonzero exit with `Invalid sync state`; no editor opens and neither database changes. |

### Renames, duplicates, and pinned file IDs

With a successfully synchronized disposable database and KeePassXC closed, rename the cloud file in Drive without moving it to another folder. Run the launcher again. Expect a warning with both full paths and continued synchronization of the original ID. The saved remote path, state filename, pinned ID, and original cloud backup filename prefix must remain unchanged. Restore the original filename before continuing the other manual checks: their path-based `check_sync` commands assume that name.

Run the isolated identity cases with:

```bash
PYTHONPATH=tests python3 -B -m unittest -v test_identity
```

| Case | Expected result |
| --- | --- |
| Same-folder rename of the pinned object | Follow its new name for the session, warn, and retain the original configuration and state key. |
| Duplicate filename with an existing pinned ID | Load only the recorded object; exact-ID downloads require rclone 1.54 or newer. Matching local copies may open, but every upload is blocked until the pinned name is unambiguous. |
| Duplicate filename before an ID has been recorded | Reject the selection without binding to an arbitrary object. |
| Pinned object replaced or missing from a successful parent listing | Stop before editing or uploading, even when a replacement has the same contents. Preserve the pin and recovery records. |
| Temporary folder-listing failure | Preserve the normal offline behavior for an established local copy. |
| Duplicate or identity change during backup/upload preparation | Recheck name uniqueness and ID, then stop writing if the destination is no longer safe. |

Installation must also reject a known-ID mismatch and must not create a new ID record during setup. The launcher does not search other folders for a moved object. Use `--reset` only when deliberately selecting another location or replacement after saving local work.

Path-based `rclone md5sum` and `lsjson --stat` checks cannot establish which object was chosen when names are duplicated. For manual investigation, run `rclone lsjson "${KEESYNCXC_TEST_REMOTE%/*}" --files-only --hash` and compare each entry's ID to the saved pin. Download the pinned object to a separate local investigation file with `rclone backend copyid` if needed; avoid uploading by an ambiguous path. These operations are covered in [rclone's Drive command documentation](https://rclone.org/drive/#copyid).

### Interrupted-upload recovery

For failures during publication, run the deterministic tests instead of interrupting a live upload. These modules cover in-place uploads and interrupted-upload recovery:

```bash
PYTHONPATH=tests python3 -B -m unittest -v test_launcher test_root_remote
```

Expected recovery behavior:

| Simulated failure | Expected result |
| --- | --- |
| Cloud backup folder creation or backup transfer fails, or the backup hash is wrong | The original cloud database is not overwritten; the local upload snapshot remains available. |
| Cloud changes during backup or before the final upload | No deliberate rename or replacement of the configured database; the other cloud edit is preserved when detected before writing, and a conflict is required. |
| Upload fails and the original ID still has the previous contents | Restart verifies the backup, archives the transaction, and requires an explicit conflict choice before retrying. |
| Publication completes but saving the baseline fails | Restart verifies the published copy's ID and MD5 and the backup, records the correct baseline, and clears the transaction without another upload. |
| In-place recovery finds unexpected contents under the original ID | The versions are preserved, the transaction is archived, and an explicit conflict choice is required. |
| Recovery finds the configured cloud object missing or under a different ID | The journal remains for investigation; the launcher must not silently create a replacement or bind to another object. |
| Invalid or unsupported transaction record | The launcher opens no editor, leaves the record untouched, and never executes its contents. |

Transaction records contain exactly six data lines: `in-place-v2`, previous MD5, new MD5, original Drive ID, a backup path inside the configured backup folder, and local snapshot path. Recovery must reject unsupported formats, invalid hashes or IDs, and paths outside their expected directories rather than interpreting records as shell code.

## Remove the disposable test setup

When testing is complete, fully quit KeePass and KeePassXC and inspect any retained backups. Remove only the disposable workspace printed at setup and its corresponding cloud test subfolder, including its own `KeePass-Backups`, when you no longer need them. If you performed the optional root or KeeAnywhere check, remove only their uniquely named root databases and the exact test backup paths you recorded. The shared `gdrive:KeePass-Backups` folder can also contain production backups; do not remove the whole folder. Keep the original launcher configuration and production database separate from this cleanup.
