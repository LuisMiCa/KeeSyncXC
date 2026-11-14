# KeeSyncXC

KeeSyncXC is a Bash launcher for Linux that keeps a KeePassXC password database synchronized with Google Drive. Open your database through KeeSyncXC: it synchronizes before starting KeePassXC and again after you save and fully quit the editor.

The launcher uses rclone to transfer the encrypted `.kdbx` file, keeps local and cloud backups, and verifies checksums and the original Google Drive file ID after uploads. When both copies change, it asks you to choose how to resolve the conflict. After an initial synchronization, you can also edit offline and synchronize when the connection returns.

Interactive setup and an application-menu shortcut handle everyday launches. Built-in menus let you synchronize without opening the editor, inspect and restore backups, move the local database, and manage the installation. Each saved configuration connects one local database to one cloud file.

## Contents

- [Disclaimer](#disclaimer)
- [Requirements](#requirements)
- [Installation](#installation)
- [Everyday use](#everyday-use)
- [Commands](#commands)
- [Configuration and manual use](#configuration-and-manual-use)
- [Interactive menu and maintenance](#interactive-menu-and-maintenance)
- [Normal synchronization](#normal-synchronization)
- [Resolving a conflict](#resolving-a-conflict)
- [How publication protects the files](#how-publication-protects-the-files)
- [State and backups](#state-and-backups)
- [Renamed files and duplicate names](#renamed-files-and-duplicate-names)
- [KeeAnywhere compatibility](#keeanywhere-compatibility)
- [Security](#security)
- [Development](#development)
- [License](#license)
- [Version](#version)

See also the [testing guide](tests/TESTING.md), [contribution guide](CONTRIBUTING.md), and [changelog](CHANGELOG.md).

## Disclaimer

KeeSyncXC is a **vibe coded project**, developed with AI assistance. It is provided **as is**, without warranty, under the [MIT License](LICENSE). Bugs or synchronization failures may cause data loss or overwrite changes.

Review the code, test with a disposable database, and keep independent backups before using it with real passwords. Automated tests and recovery features cannot guarantee data safety or security.

## Requirements

- A Linux desktop with Bash and GNU coreutils. Automatic dependency installation supports Linux Mint, Ubuntu, and Debian through `apt-get` and `sudo`.
- KeePassXC 2.7.6 or newer, rclone with its Google Drive backend, `flock` (util-linux), and `pgrep` (procps). Setup also installs Zenity for graphical messages and conflict choices.
- A terminal for setup and desktop launches. Double-click launches require `x-terminal-emulator` to show progress.
- A Google account, your own [Google OAuth client ID and secret for rclone](https://rclone.org/drive/#making-your-own-client-id), and an existing `.kdbx` database in Google Drive.

Other Linux distributions require manual dependency installation. Cloud backup inspection and duplicate-name downloads by file ID require rclone 1.54 or newer. Python 3 is needed only for the regression tests. This launcher uses Drive-specific file identity and metadata; other rclone storage backends are outside its supported workflow.

## Installation

Download and extract this repository, or clone it, then run the source-script commands below from the repository root.

- [ ] **Prepare your Google credentials and database.** Follow rclone's [Google Drive client ID guide](https://rclone.org/drive/#making-your-own-client-id) to create your own OAuth client ID and secret. Keep them ready for rclone configuration. Create a KeePassXC database and upload it to Google Drive first if you do not already have one; the installer requires an existing cloud `.kdbx` file.
- [ ] **Run the script in a terminal**, from this project directory:

  ```bash
  bash keesyncxc.sh
  ```

  Run as your regular desktop user. The installer uses `sudo apt-get` when it needs missing packages; do not run the whole script with `sudo`. Automatic package installation supports Linux Mint, Ubuntu, and Debian. It checks KeePassXC, rclone, Zenity, util-linux, procps, and GNU coreutils.
- [ ] **Follow the prompts.** Choose the cloud database first, the cloud backup folder name second, and the local database path third. The default cloud database is `gdrive:KeePass/kpDB.kdbx`. For a database in the Drive root, enter `kpDB.kdbx`; the installer uses the remote name shown in the prompt, such as `gdrive`, and saves `gdrive:kpDB.kdbx`. You can also enter a full path to choose the remote explicitly. Do not put a slash immediately after the colon. For backups, accept `KeePass-Backups` or enter a folder name such as `kpDB Backups`; setup shows the resulting cloud path. The installer reuses an existing rclone connection or opens [rclone's connection setup](#connect-google-drive-in-rclone) below. Google account authorization requires your interaction. If you also use KeeAnywhere, read the [compatibility notes](#keeanywhere-compatibility) before sharing the database.
- [ ] **Open KeeSyncXC** from the application menu after installation, or run:

  ```bash
  "$HOME/.local/bin/keesyncxc"
  ```

Running without options starts setup automatically when the saved configuration, installed launcher, or menu shortcut is missing. Setup finishes without opening KeePassXC; run the launcher again or use the new shortcut to open your database. If setup runs in the application-menu terminal, it waits for Enter so you can review the installed paths and instructions.

The installed script is at `~/.local/bin/keesyncxc`. From that directory, run `bash keesyncxc`:

```bash
cd ~/.local/bin
bash keesyncxc
```

The installer checks the cloud file with read-only commands, saves your paths, and installs the launcher and menu shortcut. It does not create or upload a database or change existing database contents or synchronization records. On the first database launch, the existing cloud database is the source: the launcher downloads and verifies it before opening KeePassXC. An existing local database without synchronization state is accepted only when its MD5 matches the cloud copy, which also allows retrying a first download whose state write failed. If installation finds a differing local database, preserve it elsewhere or choose another local path before retrying.

### Connect Google Drive in rclone

When the installer opens `rclone config`, the next menu belongs to rclone. A **remote** is a saved cloud connection, such as `gdrive`. **No remotes found** is expected before you create your first connection.

1. Choose **`n` — New remote**. At `name>`, enter the exact remote name shown by the installer, such as `gdrive`, without a colon. When reconfiguring an existing connection, choose **`e` — Edit existing remote** and select that name instead. The **`s`** option sets a password for rclone's configuration file.
2. For storage, enter **`drive` — Google Drive**. Enter your own Google OAuth client ID and client secret, then choose **scope `1` — full access**.
3. Leave the service account file blank, skip advanced settings, and complete browser sign-in and consent. For a personal Drive, answer **No** to Shared Drive.
4. Choose **`y`** to save the connection, then **`q`** to quit rclone's menu and return to the installer.

## Everyday use

Start KeePassXC through KeeSyncXC and keep its terminal open while editing. Save and fully **Quit/Exit** KeePassXC to synchronize; closing its window to the system tray can leave the process running and delay the upload. Fully close any existing KeePassXC process before starting a launcher session.

Use one editing device at a time. The lock coordinates processes only on the current machine; another device can still change the cloud file between a check and an upload. See [upload protection and its limits](#how-publication-protects-the-files).

Inspect backups before restoring or publishing them. Restoration preserves the current local database in a verified safety backup, and a separate confirmation is required before restored contents can upload to Drive. That confirmation defaults to **No** and remains required across restarts and failed uploads.

The application-menu shortcut opens a terminal immediately so you can see the connection check, transfers, and any errors. Before synchronization, a connection check to Google's API host uses a three-second timeout, including DNS lookup. If it fails, an established local copy opens offline without waiting for rclone's retries. The launcher checks connectivity again after KeePassXC exits, so synchronization can resume if the connection returns while editing. A successful connection check does not guarantee that Drive authentication or requests will succeed; those can still take longer. Keep this terminal open while editing and until synchronization finishes after you fully quit KeePassXC. If KeePassXC changed the database, the terminal stays open for review until you press Enter, even after a successful upload. When the editor leaves the database unchanged, the final check confirms matching local and cloud copies, and no rename or duplicate-name warning needs review, the terminal shows `Auto-closing in 3 seconds`, then counts down before closing. Failures and identity warnings stay visible until you press Enter. These waits apply to interactive desktop launches; captured output does not wait or count down.

Double-clicking the executable script and choosing **Run** also opens this terminal before setup or connection checks, using `x-terminal-emulator` in a graphical session. Choosing **Run in Terminal** uses the file manager's terminal; choosing **Display** only opens the source text. The file manager must allow executing scripts. If `x-terminal-emulator` is unavailable, the launcher stops and shows a Zenity error when available; run it from a terminal instead. After updating the source, refresh the installed copy with `--repair` so the application menu uses the same changes.

### Reading terminal output

Setup groups its prompts and checks into five numbered stages: database locations, system tools, Google Drive connection, database verification, and installation. It shows the selected locations together and finishes with the installed launcher and shortcut details. When rclone's connection wizard takes over, full-width, labelled start and end borders separate its output from the launcher's instructions. The package manager's own prompts and messages also remain visible during installation.

Normal launches separate synchronization before editing, the KeePassXC session, and synchronization after editing. Status labels make the current action easier to follow: `[INFO]` for information, `[ OK ]` for completed checks, `[WAIT]` for work in progress, `[WARN]` for a condition that needs attention, and `[FAIL]` for a failed action. Offline and conflict messages explain what you can do next.

The main menu uses thick ASCII borders (`=` across the top and bottom, `||` at the sides); submenus and terminal conflict choices use single-line box borders around their titles and options. The main menu opens directly without a cloud or local path summary, including when returning from a submenu. Each menu remains visible in terminal history, with the input prompt below the box. Long lines wrap to fit narrower terminals, using `COLUMNS` when set or the terminal width when available. Use **0** to go back or cancel, and **H** for help; invalid input keeps you in the current submenu.

Interactive terminals use color when supported. Redirected output stays plain text, and setting `NO_COLOR` disables color in a terminal too:

```bash
NO_COLOR=1 bash keesyncxc.sh --status
```

## Commands

Use these options with the source script from the repository root or with the installed launcher:

```bash
bash keesyncxc.sh --help
"$HOME/.local/bin/keesyncxc" --menu
```

| Option | Action |
| --- | --- |
| No option | Open a terminal when run graphically without one; start setup if the configuration, installed launcher, or shortcut is missing, otherwise synchronize and open KeePassXC. |
| `--menu`, `--interactive` | Open the interactive management menu. |
| `--launch` | Synchronize and open KeePassXC directly, skipping automatic setup detection and terminal opening. |
| `--desktop` | Open a terminal if needed, with immediate startup feedback. Keep it open after setup, edits, failures, or identity warnings; close an unchanged, confirmed synchronized session after a three-second countdown. Used by the application-menu shortcut. |
| `--install` | Run the full setup/reconfiguration wizard, including dependencies and connection checks. |
| `--repair` | Refresh the installed script and shortcut from this running copy, retaining settings; start setup if no configuration exists. |
| `--dependencies` | Check/install required system tools. |
| `--status` | Show installation, configured database and cloud backup paths, and synchronization status without connecting to Drive. |
| `--check-connection` | Check the selected Drive connection and database with read-only requests, without transferring database contents. |
| `--sync` | Synchronize using the normal lock, conflict, and recovery handling, without opening KeePassXC. |
| `--backups` | List local backup-folder files and recovery archives with dates, sizes, and purpose, without connecting to Drive. |
| `--open-backups` | Open the local backup folder in the desktop file manager. |
| `--cloud-backups` | List the configured cloud backup folder without creating folders or changing Drive files. |
| `--inspect-backup` | Select and open a recognized local backup in KeePassXC, without automatic synchronization. |
| `--inspect-cloud-backup` | Check local MD5s, reuse matching contents or download, then open a verified private inspection copy without synchronization. |
| `--restore-backup` | Make and verify a safety backup, then restore a selected local backup into the working database. |
| `--settings` | Show current saved settings without connecting to Drive. |
| `--backup-folder` | Change the cloud backup folder name for future backups, without reinstalling or connecting to Drive. |
| `--move-local` | Move and verify the local working database and update its saved path, preserving synchronization records. |
| `--connection` | Open `rclone config` to manage cloud connections. |
| `--cleanup` | Choose age-based backup cleanup or keep the newest ordinary local backups. |
| `--cleanup-age`, `--cleanup-count` | Open age-based or newest-count pruning directly. |
| `--clear-local` | Preview and confirm deletion of the local working database, recognized backups, settings, and recovery records; keep the installed launcher and shortcut. |
| `--uninstall-keep-data` | Remove the installed launcher, shortcut, and settings after confirmation; retain databases, backups, and synchronization state. |
| `--uninstall` | Choose to remove the launcher, local data, or both; review the file list and confirm once. |
| `--switch-database`, `--reset` | Back up the local database and archive the current settings and state, then set up another cloud database. |
| `--version` | Print the launcher version without reading configuration or accessing Google Drive. |
| `--help`, `-h` | Show available commands. |

## Configuration and manual use

The installer writes these files:

| File | Location |
| --- | --- |
| Launcher | `$HOME/.local/bin/keesyncxc` |
| Saved settings | `${XDG_CONFIG_HOME:-$HOME/.config}/keesyncxc/config` |
| Application menu shortcut | `${XDG_DATA_HOME:-$HOME/.local/share}/applications/keesyncxc.desktop` |

With default XDG settings, the configuration is at `~/.config/keesyncxc/config` and the shortcut is at `~/.local/share/applications/keesyncxc.desktop`. The menu entry is named **KeeSyncXC**. Its desktop file uses `Terminal=true` and invokes the launcher with `--desktop`. The launcher stays at `$HOME/.local/bin/keesyncxc` when XDG paths change.

The built-in cloud database default is `gdrive:KeePass/kpDB.kdbx`, and the local working database defaults to `$HOME/Documents/KeeSyncXC/Passwords.kdbx`. Their filenames can differ. Saved settings take precedence over these defaults, so updating the script does not move a configured database. On a fresh setup, the first database launch downloads the cloud database to the local location. Synchronization records and backups use the [state directory](#state-and-backups).

The installer saves three plain data lines: the full `remote:path` database path, the absolute local database path, and the cloud backup folder name. The installer expands filenames entered without a remote prefix before saving them. For example, for a user named `alice`:

```text
gdrive:KeePass/kpDB.kdbx
/home/alice/Documents/KeeSyncXC/Passwords.kdbx
KeePass-Backups
```

Here, `gdrive` is the rclone remote name, `KeePass` is a folder in Drive, and `kpDB.kdbx` is the cloud database filename. The resulting cloud backup directory is `gdrive:KeePass/KeePass-Backups/`. Use **Settings and database selection** to show settings, change the backup folder, move the local database, manage the connection, or switch databases. The local working database must be outside the launcher's configuration, state, and backup directories.

Local paths can contain spaces and literal backslashes. Enter paths literally in setup prompts, and quote them when using shell commands. Local and XDG paths must be absolute and must not contain control characters.

For a database directly in the Drive root, the first line is `gdrive:kpDB.kdbx`. Use your actual rclone remote name and database filename, with no leading slash after the colon or empty, `.` or `..` path segments. The backup folder is one directory beside the database: a root database with the folder name `kpDB Backups` uses `gdrive:kpDB Backups/`. Spaces are allowed; slashes, colons, control characters, `.`, `..`, and the database's own filename are rejected. Omitting the third configuration line selects `KeePass-Backups`; an empty third line is invalid.

The installed launcher has permissions `700`, the saved configuration `600`, and the shortcut `644`. New synchronization records and backup files use owner-only permissions.

Google authentication is managed by rclone separately from these path settings. Find the actual rclone configuration location with:

```bash
rclone config file
```

Keep using the same rclone configuration when launching from a terminal or the application menu. `RCLONE_CONFIG` can select a different rclone configuration file. The launcher configuration stores paths and the backup folder name; Google credentials and the KeePassXC database password are handled by their respective applications.

For manual setup, install `keepassxc`, `rclone`, `zenity`, `util-linux`, `procps`, and `coreutils`, then configure and authenticate the Google Drive remote with `rclone config`. When no saved configuration exists, these values near the top of [keesyncxc.sh](keesyncxc.sh) provide the defaults:

```bash
REMOTE='gdrive:KeePass/kpDB.kdbx'
LOCAL="$HOME/Documents/KeeSyncXC/Passwords.kdbx"
REMOTE_BACKUP_FOLDER='KeePass-Backups'
```

Launch a manually configured or copied script with `bash keesyncxc.sh --launch`. This skips automatic setup detection. Both direct launches and the installed launcher load the saved configuration when present, overriding those script defaults.

Fully exit KeePassXC when finished. Closing its window to the system tray can keep the process running and delay synchronization. The launcher blocks overlapping local sessions and refuses to start while KeePassXC is already running.

### Update or reconfigure the launcher

Run `bash keesyncxc.sh --repair` from the updated source directory to refresh the installed launcher and shortcut while retaining settings. This copies the running script; it does not download a release. With no saved configuration, repair starts full setup. Use `--backup-folder` to change the cloud backup folder name and `--move-local` to move the working database safely. Changing the backup folder directs future backups there; existing backups stay where they are. See available options with `bash keesyncxc.sh --help`.

To select a different cloud database, use **Settings and database selection → Switch cloud database** in `--menu`, or run `--switch-database` (also `--reset`); this preserves the current local copy and its records before starting fresh setup. The `--install` option runs the full setup/reconfiguration wizard. It shows the built-in cloud default separately from the saved setting, keeps saved settings when you press Enter, and expands filenames without a remote prefix using the shown remote. Resolve a pending conflict or interrupted upload with its existing settings before changing paths or the cloud backup folder.

## Interactive menu and maintenance

Open the management menu in a terminal:

```bash
"$HOME/.local/bin/keesyncxc" --menu
```

`--interactive` opens the same menu. Actions are grouped as follows:

```text
Main menu
├── 1) Open database — synchronize, edit, synchronize on exit
├── 2) Status and troubleshooting
│   ├── 1) Show local status — offline
│   ├── 2) Check Google Drive connection — online, read-only
│   └── 3) Synchronize without opening KeePassXC
├── 3) Backups and recovery
│   ├── 1) Show local backups and recovery archives
│   ├── 2) Open local backup folder
│   ├── 3) List cloud backups — online, read-only
│   ├── 4) Inspect/open a local backup
│   ├── 5) Delete local backups older than N days
│   ├── 6) Keep the newest N local backups
│   ├── 7) Restore a local backup
│   └── 8) Inspect a cloud backup — reuse local contents or download
├── 4) Settings and database selection
│   ├── 1) Show current settings
│   ├── 2) Change cloud backup folder
│   ├── 3) Move local database
│   ├── 4) Manage Google Drive connection — opens rclone
│   └── 5) Switch cloud database — back up current selection
├── 5) Installation and advanced maintenance
│   ├── 1) Install/repair launcher and shortcut
│   ├── 2) Check/install required system tools
│   └── 3) Uninstall launcher and/or remove local data
├── H) Help
└── 0) Quit
```

Under **5 → 3**, choose **1** to uninstall the launcher and settings while keeping local data, **2** to delete local data and setup while keeping the launcher, or **3** for a complete uninstall. Each choice previews the affected files before confirmation.

Every submenu also offers **H: Help** and **0: Back**. Main-menu Help briefly explains options **1–5** with matching numbers, explains **H** and **0**, and points to each submenu's own help. Status, backups, settings, and maintenance Help each explain that submenu's actions and return to the same submenu. Conflict Help retains its dedicated resolution guide, while `--help` lists command-line usage.

Backup-selection Help explains inspection or restoration as appropriate and reminds you that backups can belong to previous database selections. Cleanup Help explains age/count pruning and which files it retains. See [Commands](#commands) for the equivalent command-line options.


Setup, the management menu, backup selection, settings changes, cleanup, uninstall, and switching databases accept interactive input. When invoked from a graphical session without terminal input, these actions use `x-terminal-emulator` if available. No-option and `--desktop` launches also open this terminal before setup or network checks when no input or output stream is a terminal. Graphical sessions are detected through `DISPLAY` or `WAYLAND_DISPLAY`; headless runs and explicitly piped answers keep their existing terminal behavior. Conflicts appear as text choices in the application-menu terminal; explicit `--launch` runs without terminal input use Zenity when a graphical session is available.

### Inspect and restore local backups

**Backups and recovery** groups inventory, folder access, backup inspection, restoration, and age/count pruning. Inspection opens the selected backup itself; edits change that backup only and never trigger synchronization. Fully quit KeePassXC afterward. Restoration accepts recognized regular `.kdbx` backups, requires a confirmed synchronization baseline, and asks for confirmation. It first makes and verifies a safety copy of the current working database, then verifies the restored contents. It preserves the baseline and pinned Drive ID and makes no cloud write.

**Restored contents require a separate confirmation before uploading to Drive.** The confirmation shows the local database, cloud destination, and cloud backup folder, and reminds you to inspect the backup and restored local contents first. It defaults to **No**. Declining, blank input, or end-of-input stops the action without changing either database; a later upload attempt asks again. Graphical direct launches use a dialog with cancellation selected by default. Synchronization still compares both copies with the last successful synchronization:

| Contents after restoration | Next synchronization |
| --- | --- |
| Local and cloud copies match | No transfer. |
| Only the local copy differs from the last synchronized copy | Ask for publication confirmation, then back up and verify the current cloud database before uploading restored contents. |
| Both copies differ from the last synchronized copy and from each other | Ask for a conflict decision; publishing the local copy also requires the restoration confirmation. |
| The restored copy matches the last synchronized copy, while Drive differs | Back up the local copy and download the cloud version. |

To review an older version, use **Inspect/open a local backup** first. Restore only when you intend to make it the working database. To inspect the restored working contents, open the local file directly in KeePassXC without using the launcher, then fully quit. Use **Synchronize without opening KeePassXC** when ready to publish, or launch normally and answer the publication confirmation before the editor opens.

The approval requirement survives restarts, offline edits, conflicts, and failed uploads. Approval applies to one upload attempt; successful, verified publication clears the requirement, and later ordinary edits synchronize normally. Matching copies or a successful download of the cloud version also clear it without an upload confirmation. Changing paths or backup folders and pruning backups are blocked while review is pending; inspection and full local cleanup remain available. Switching databases archives the pending review with the previous selection.

### Inspect cloud backups

Choose **Backups and recovery → 8: Inspect a cloud backup**, or run `--inspect-cloud-backup`, to inspect a backup in the configured cloud backup folder. The selection shows filenames, modification dates, and sizes, with **H** for help and **0** to cancel. This action requires rclone 1.54 or newer and KeePassXC to be fully closed. It remains available when conflicts, interrupted uploads, or restoration reviews are pending, and does not require the working database or a synchronization baseline to exist.

Before downloading, the launcher compares the selected cloud MD5 with the working database and recognized local backups, using file size to skip obvious mismatches. It prefers an identical private `cloud-*.kdbx` inspection copy and reuses that file without creating another copy. A match in the working database or another backup supplies the contents for a separate inspection copy, preserving the original file and its permissions. Symbolic links are skipped; a file with other hard links is never opened directly as an inspection copy. Edited inspection copies are reused only if their current MD5 still matches the selected cloud backup.

When no local match is available, the launcher downloads the selected Drive file by ID into private staging. In both cases it verifies the inspection copy's MD5 and local permissions, then checks that the selected file ID and checksum remain in the configured cloud folder. This also handles duplicate filenames without choosing a different object by name. New inspection copies are kept as `cloud-<UTC timestamp>-<random>.kdbx` in the local backup folder. The local copy is checked again before KeePassXC opens. Use the database credentials valid when the backup was made, and fully quit KeePassXC to return.

Edits saved during cloud inspection affect the local inspection copy. The working database, synchronization records, and cloud files remain unchanged; no synchronization follows inspection. The copy remains available even if KeePassXC reports an error. **Restore a local backup** can select it later, preserving the current working database and requiring separate confirmation before uploading restored contents. Ordinary age/count pruning keeps cloud inspection copies; full local cleanup removes recognized copies. `--cloud-backups` remains a listing-only action.

### Move the local database

**Move local database** verifies the destination copy before removing the original, updates the saved local path, and retains any baseline and Drive ID. It requires saved settings, an existing local database, and an unused, valid destination outside the launcher's own directories. A pending conflict, interrupted upload, or restored database review blocks moving, another restoration, and backup-folder changes until resolved. These actions require KeePassXC and other launcher sessions to be closed.

### Prune local backups

The `--cleanup` submenu offers these ordinary pruning actions; full deletion is separate under **Installation and advanced maintenance** or `--clear-local`:

| Choice | Result |
| --- | --- |
| Older than a chosen age | Delete eligible local backups older than the requested number of days; the default is 30. |
| Keep the newest copies | Retain the newest requested number of eligible backups and delete the rest; the default is 10. |
| Return | Leave cleanup without deleting files. |

Age and count accept zero. Ordinary backup cleanup combines generated `local-*.kdbx` and `upload-*.kdbx` files from the shared backup directory. Newest means most recently modified; equal modification times use filename order. These two choices retain the current database, recovery records, downloaded cloud inspection copies, and backups and archives created when switching databases. An unresolved conflict, interrupted upload, or restored database review for any remote blocks ordinary backup cleanup.

Every cleanup shows the exact paths and asks for confirmation that defaults to **No**. **Delete local database, backups, and setup** deliberately includes the configured working database, recognized backups and archives, settings, synchronization records including Drive IDs for previously configured remotes, and generated launcher temporary files; review the list before confirming. It keeps the installed launcher and shortcut for setup again. Unknown files and symlinks are retained. The shared lock file and its state directory can remain so concurrent launches still coordinate safely. All deletion actions require KeePassXC and other launcher sessions to be closed.

### Uninstall the launcher and local data

Choose **Installation and advanced maintenance → Uninstall launcher and/or remove local data**, or run the installed script:

```bash
bash ~/.local/bin/keesyncxc --uninstall
```

From `~/.local/bin`, the same command is `bash keesyncxc --uninstall`. The installed script can remove itself; the source checkout is not required. The source script also supports `bash keesyncxc.sh --uninstall`.

| Uninstall menu choice | Result |
| --- | --- |
| **1: Uninstall launcher and settings** | Remove the installed executable, shortcut, and settings; keep the working database, backups, and synchronization/recovery records. |
| **2: Delete local database, backups, and setup** | Remove the configured working database, recognized backups, settings, and synchronization/recovery records; keep the installed launcher and shortcut for fresh setup. |
| **3: Complete uninstall** | Remove both the launcher installation and its recognized local data, including the working database and backups. |
| **H: Help**, **0: Cancel** | Explain the choices or leave without deleting files. |

Choose an option, review the deletion preview, then answer **Yes** once to confirm. Cancelling the confirmation leaves the installation and data in place. `--uninstall-keep-data` opens choice 1 directly; `--clear-local` opens choice 2 directly.

**KeePassXC and rclone stay installed**, together with their credentials, cloud files, and the source checkout. Unknown files, symlinks, and the shared lock/state directory can remain. After uninstalling the launcher, run the source script without options to install again; after deleting only local data and setup, the installed launcher starts setup again.

### Switch cloud databases

**Switch cloud database** backs up and verifies any existing local database, archives the active settings and synchronization records, and then starts setup with a new cloud database selection. It offers the previous local destination for the replacement download. The preserved database and archives stay available if setup fails. Records for unrelated remote selections remain; selecting a previously configured target may require restoring its working local copy first. This is the way to change the active database while keeping a recovery copy of unsynchronized local work.

Cloud files, rclone credentials, installed system packages, and the source checkout remain untouched by local cleanup, switching, and uninstall. Status, settings, and local backup listing are offline, read-only actions. Connection checks and cloud backup listing contact Drive with read-only requests and do not create the backup folder. Managing the connection opens rclone's own configuration, where changes are under your control.

## Normal synchronization

The launcher stores the MD5 of the last successfully synchronized database as its baseline. Matching copies need no transfer. If only the cloud copy changed, it downloads that copy. If only the local copy changed, it publishes the local copy. If both changed, it opens the conflict menu.

Synchronization runs around each editing session: check Drive, synchronize, open KeePassXC, wait for a full exit, then synchronize again. Save your changes and fully quit KeePassXC to complete this sequence. Use one editing device at a time; the launcher lock coordinates processes on the current machine.

Progress messages show when the launcher is checking Drive, transferring a database, opening KeePassXC, waiting for its exit, and performing the final synchronization. Closing the terminal can interrupt a pending upload; launch again to let recovery inspect that attempt before continuing.

A previously synchronized local database can be opened offline. Saved changes stay local until synchronization succeeds on a later attempt. A deferred synchronization returns a nonzero exit status so it is not reported as complete.

Use **Status and troubleshooting → Synchronize without opening KeePassXC** or `--sync` to transfer changes without an editing session. This uses the same local lock, Drive identity checks, backups, conflict choices, and interrupted-upload recovery as a normal launch. It never opens KeePassXC, including from a conflict menu. Offline or unresolved synchronization returns a nonzero status and preserves local changes.

## Resolving a conflict

The conflict menu offers:

| Choice | Result |
| --- | --- |
| 1: Review/edit local changes | Open the current local database for reference or further editing. After KeePassXC fully exits, show the conflict menu again without uploading automatically. Unavailable during `--sync`; the menu explains this and lets you choose another action. |
| 2: Use the latest cloud database | Back up the local database, download and verify the latest cloud copy, and save its hash as the new baseline. During a normal launch, open that copy for editing and synchronize on exit; during `--sync`, finish without opening KeePassXC. |
| 3: Publish local changes over the cloud database | Show the local, cloud, and backup locations, then use a fresh snapshot of the current local database. Preserve the cloud version as a dated backup before publishing the snapshot. Finish after successful publication; selecting this choice authorizes the overwrite. |
| 0: Cancel and resolve later | Keep the copies and exit with a nonzero status. Show the conflict menu on the next launch or synchronization attempt. |
| H: Explain conflict choices and recovery | Explain each resolution choice and show the configured database and backup locations, then return to the conflict menu. |

Conflict choices are available in a terminal or through Zenity when running graphically without a terminal. If neither interface is available, the launcher preserves the files and exits.

Terminal choices **4** for cancellation and **5** for help are also accepted.

Choosing overwrite deliberately replaces the cloud contents, including changes that exist only there. A detected further cloud change stops publication and returns to the menu. Local backups remain available if you need to recover something.

There is no automatic database merge. KeePassXC 2.7.6 supports **Database → Merge From Database** if you want to combine versions manually. Review the merged contents before choosing to publish your local copy.

## How publication protects the files

The launcher snapshots the local database and checks the cloud MD5 and recorded Drive file ID. It creates the chosen cloud backup folder when an upload needs a backup, copies the existing cloud database there to a unique dated filename, verifies that backup, and rechecks the original contents and identity before writing. Installation, downloads, unchanged sessions, and offline status do not create this cloud folder. A detected change stops publication and requires conflict handling; failure to create or verify the backup stops the upload.

After successful checks, it uploads the frozen local snapshot directly into the existing cloud file. It does not rename that database or promote a new Drive object. Independent checks of the resulting MD5 and file ID must pass before it records the new synchronization baseline. The backup and local snapshot remain available if uploading or verification fails. A transaction record lets the next launch inspect an interrupted attempt before continuing.

Transaction records use six plain-data lines: `in-place-v2`, the previous cloud MD5, the upload snapshot's MD5, the original Drive file ID, the cloud backup path inside the configured backup folder, and the local snapshot path. Recovery validates the hashes, identity, and paths before proceeding. Invalid or unsupported records stop the launcher and remain available for investigation.

Use one editing device at a time. The launcher lock applies only to the current machine, and the cloud recheck and upload are separate operations. This workflow does not provide an atomic conditional write or prevent another device from changing the file between them. Renames or duplicates introduced after the final check can also race with a path-based transfer; the identity and checksum checks detect unexpected results afterward.

## State and backups

Local state and backups are stored under:

```text
${XDG_STATE_HOME:-$HOME/.local/state}/keesyncxc/
```

With default XDG settings this is `~/.local/state/keesyncxc/`. Files appear as needed:

```text
keesyncxc/
├── launcher.lock
├── <remote-id>.md5
├── <remote-id>.md5.id
├── <remote-id>.md5.conflict
├── <remote-id>.md5.transaction
├── <remote-id>.md5.restore
└── backups/
    ├── local-<UTC timestamp>-<random>.kdbx
    ├── upload-<UTC timestamp>-<random>.kdbx
    ├── cloud-<UTC timestamp>-<random>.kdbx
    ├── interrupted-upload-<random>.txt
    ├── switch-<UTC timestamp>-<random>.kdbx
    └── reset-<random>/
```

| File | Purpose |
| --- | --- |
| `launcher.lock` | Prevents overlapping local editing, installation, or maintenance sessions. |
| `<remote-id>.md5` | Stores the last confirmed shared database MD5. The filename's ID is a SHA-256 hash of the remote path. |
| `<remote-id>.md5.id` | Pins the actual Google Drive file ID on the first online launch; same-folder renames retain it, and a missing or replaced object stops synchronization. This ID is distinct from the remote-path hash used in state filenames. |
| `<remote-id>.md5.conflict` | Keeps a conflict pending until you resolve it. |
| `<remote-id>.md5.transaction` | Records an interrupted upload for recovery on the next launch. |
| `<remote-id>.md5.restore` | Keeps upload confirmation required after local restoration, including across restarts and offline edits. |
| `backups/local-*.kdbx` | Safety copies made before reviewing or replacing the local database. |
| `backups/upload-*.kdbx` | Frozen database snapshots captured for upload attempts. |
| `backups/cloud-*.kdbx` | Verified local copies downloaded for cloud backup inspection; edits stay local. Ordinary pruning preserves them. |
| `backups/interrupted-upload-*.txt` | Archived upload recovery records. |
| `backups/switch-*.kdbx` | Verified copy of the working database saved before choosing another cloud database. Ordinary backup cleanup preserves it. |
| `backups/reset-*/` | Archived active configuration and synchronization records from a database switch. Ordinary backup cleanup preserves it. |

Keep synchronization records while investigating an error: they preserve the information needed to avoid an unintended overwrite. The state and backup directories must belong to your user and must not grant group or other write access. The launcher restricts accepted state and backup directories to mode `700` and refuses symlinked or unsafe parent paths. It leaves unrelated parent permissions alone. The lock must be a regular, single-link file owned by your user with mode `600` or stricter, and is opened without truncating its contents.

Downloads use a private temporary staging directory beside the local working database. Rclone metadata preservation is disabled for downloads, and the verified database must have mode `600` before replacing the working file. An existing working database is copied to a local safety backup; both files must still match its pre-copy checksum before replacement proceeds. Temporary staging is removed on success or failure. Backup inventories display control characters in filenames as escaped text.

Cloud backups go into the folder chosen during setup, beside the configured database. With the default remote and backup folder name, the folder can contain:

```text
gdrive:KeePass/
├── kpDB.kdbx
└── KeePass-Backups/
    └── kpDB-<UTC timestamp>-<unique ID>.kdbx
```

For a root database such as `gdrive:kpDB.kdbx`, the structure is:

```text
gdrive:
├── kpDB.kdbx
└── KeePass-Backups/
    └── kpDB-<UTC timestamp>-<unique ID>.kdbx
```

The dated files preserve previous cloud versions. The folder is created automatically before the first cloud backup; the main database's location and file ID stay the same. Local backups keep using the state directory shown above. Choose another cloud backup folder name with `--backup-folder`; refresh the installed launcher after updating the source with `--repair`. Use `--status` or `--settings` to see the full configured cloud backup path.

Cloud backups are retained without automatic cleanup. Use `--backups` to inventory local copies and archives, `--inspect-backup` to open a local backup, and `--restore-backup` for guided local restoration. Use age or newest-count choices in **Backups and recovery** or `--cleanup` to prune generated local backups. `--clear-local` also removes the working database and local recovery records, as described in [maintenance](#interactive-menu-and-maintenance). Review and remove obsolete cloud backups manually when you are satisfied synchronization is healthy.

List the configured cloud backup folder with `--cloud-backups`, or use the command matching your database location, replacing `KeePass-Backups` if you chose another name:

```bash
# Database in the Drive root:
rclone lsf 'gdrive:KeePass-Backups' --files-only
# Database in gdrive:KeePass/:
rclone lsf 'gdrive:KeePass/KeePass-Backups' --files-only
```

## Renamed files and duplicate names

After the first online launch records the Drive file ID, renaming that file within the same folder produces a warning showing its configured and current cloud names. The launcher uses the current name for that session while keeping the saved path, synchronization history, and original backup filename prefix. It does not search other folders automatically. If the pinned ID is missing from a successful folder listing, or its metadata is unsafe, synchronization stops before opening the editor. A connection failure can still allow an established local copy to open offline.

When multiple cloud files share the pinned database's name, the launcher warns and can download the recorded object by ID, verifying its contents before replacing the local copy. This uses rclone's [Drive `copyid` command](https://rclone.org/drive/#copyid), introduced in [rclone 1.54](https://rclone.org/changelog/#v1540-2021-02-02). A matching local copy can also open. All uploads remain blocked while that name is ambiguous; saved local changes stay local. Rename the other copies to distinct names, then launch again to synchronize. Before a file ID has been recorded, duplicate names are rejected rather than choosing a file automatically.

A replacement file has a different ID even if its filename and contents match. To deliberately select a replacement, restored cloud backup, or file moved to another folder, use **Settings and database selection → Switch cloud database**, or `--switch-database` (also `--reset`), to preserve local work and start a new selection. Rename and duplicate-name warnings keep the desktop terminal open for review even after an unchanged session.

Restoring backup contents into the existing Drive object, keeping its ID, does not require a reset. Normal synchronization checks handle that change and ask for a conflict decision if the local copy also changed. Use the switch workflow when the restore creates a different Drive object.

## KeeAnywhere compatibility

KeeAnywhere [v2.1.0](https://github.com/Kyrodan/KeeAnywhere/releases/tag/v2.1.0) has a **Google Drive (restricted)** provider (`gdrive-r`) that uses per-file access and has no folder-creation option, so saving through that provider is practically limited to the Drive root. Its ordinary full-access Google Drive provider supports folders. See the [maintainer's explanation](https://github.com/Kyrodan/KeeAnywhere/issues/398#issuecomment-2173032754).

Root placement alone does not ensure that restricted mode can open a database. KeeAnywhere must be authorized for that particular Drive file. Its contributor describes creating an accessible copy with KeePass **File → Save As → Save to Cloud Drive** in [the upstream troubleshooting steps](https://github.com/Kyrodan/KeeAnywhere/issues/441#issuecomment-2549766406). Google documents this [per-file app authorization](https://developers.google.com/workspace/drive/api/guides/api-specific-auth).

The launcher copies the cloud version to a dated backup in your chosen folder and uploads local changes into the existing cloud file, checking that its file ID stays the same. A root database stays in the root; with the default folder name, only its backups go into `gdrive:KeePass-Backups/`. It records that ID on the first online launch and follows that same object within its configured folder, including after a rename. A missing or replaced object stops synchronization. This preserves the identity of an authorized file but cannot grant KeeAnywhere access. Live interoperability with KeeAnywhere restricted mode has not been validated; the automated tests use fake commands.

Before configuring the launcher, confirm that KeeAnywhere can close and reopen the database. If access fails, preserve the database and unsynchronized copies, then use KeeAnywhere's Save to Cloud Drive action to create an accessible file with a unique filename. Configure the launcher for that file before its first online launch. If a configured database reports a recorded file-ID mismatch, inspect the files and state rather than deleting the record to bypass the check.

## Security

### Report a vulnerability

For suspected vulnerabilities involving unintended overwrites, credential exposure, unsafe file handling, or command execution, use the repository's **Security → Report a vulnerability** option when private reporting is enabled. If it is unavailable, open an issue requesting a private contact method without including vulnerability details or sensitive files.

Provide the affected KeeSyncXC version (`bash keesyncxc.sh --version`) or commit, environment, reproduction steps using dummy data, expected behavior, and impact. Do not attach real password databases, key files, passwords, OAuth secrets, tokens, or rclone configuration. Ordinary usage problems belong in GitHub issues with redacted output.

Security fixes target the current default branch. There is no separate maintenance policy for older snapshots or releases.

### Credentials and database copies

KeePassXC handles database encryption and unlocking. rclone handles Google authentication in its own configuration. The launcher transfers the database as a file and stores local paths, synchronization hashes, Drive IDs, recovery records, and database backups. Treat those files as private, even when database contents are encrypted.

The repository's ignore rules cover common database, key-file, and credential filenames. They do not remove files already committed or detect secrets stored under other names. Review staged files and history before publishing; if a credential is exposed, revoke or rotate it through its provider.

### Local file handling

Launch, synchronization, and maintenance share a nontruncating lock routine. The lock must be a regular file owned by the running user, have a single hard link, and grant no group or other permissions. Symbolic links, FIFOs, directories, unsafe ownership, and shared lock permissions are refused before opening it. The opened descriptor is checked against the path before and after acquiring the lock.

The state and backup directories must be owned by the running user and must not be writable by other users. Accepted directories are restricted to mode `700`; newly created files use a private umask. Symbolic links and writable or untrusted ancestors are refused, with an exception for trusted system-owned sticky temporary directories such as `/tmp`. Unrelated parent-directory permissions are not changed. Correct an unsafe existing layout before retrying; the launcher does not take ownership of it automatically.

Cloud downloads use a private staging directory, disable rclone metadata preservation, and require mode `600` before replacing the working database. Remote permission metadata therefore cannot make the staged or published database accessible to other local users. Backup listings escape control characters in filenames and paths before displaying them, so filenames cannot inject terminal control sequences.

For synchronization limits, including concurrent edits on another device, see [upload protection and its limits](#how-publication-protects-the-files).

## Development

From the repository root:

```bash
bash -n keesyncxc.sh
python3 -B -m unittest discover -s tests -v
```

The tests use temporary homes and fake rclone/KeePassXC commands, without accessing a real database or Google Drive. The GitHub Actions [CI workflow](.github/workflows/ci.yml) runs these checks on pushes and pull requests. See the [testing guide](tests/TESTING.md) for coverage and disposable manual tests.

| File or directory | Purpose |
| --- | --- |
| [keesyncxc.sh](keesyncxc.sh) | Installer, launcher, synchronization, recovery, and maintenance. |
| [README.md](README.md) | Setup, usage, configuration, recovery, and security guidance. |
| [tests/](tests/) and [tests/TESTING.md](tests/TESTING.md) | Automated regressions, shared fixtures, and disposable manual test scenarios. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Bug reports, development, and pull requests. |
| [CHANGELOG.md](CHANGELOG.md) | Release history. |
| [LICENSE](LICENSE) | MIT license. |
| [.github/](.github/) | CI workflow and issue/pull request templates. |

The source script stays at the repository root so it can run directly. Usage and security guidance live in this README, and testing documentation lives with the regression suite in `tests/`. Local development material in `.dev-stuff/`, assistant/editor settings, Python caches, databases, and credentials are ignored and are not part of the published project.

## License

Copyright (c) 2026 Luis Carvalho. This project's source code and documentation are available under the [MIT License](LICENSE). KeePassXC, rclone, and other dependencies retain their own licenses.

## Version

**v1.0.0 — first public release.** See the [release notes](CHANGELOG.md).

Check your copy with `bash keesyncxc.sh --version` or the installed `keesyncxc --version`. This option reads no saved configuration and requires no cloud connection.
