#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Luis
# KeeSyncXC: on-demand KeePassXC synchronization with Google Drive for Linux.
# Requires: configured rclone, KeePassXC 2.7.6+, flock, procps, GNU coreutils.
# Optional: zenity for conflict choices from a graphical shortcut.
# The cloud copy is the initial authoritative copy. No scheduled sync.

# =============================================================================
# 1. Runtime safety
# Stop on unhandled errors and create new files with owner-only permissions.
# =============================================================================
set -Eeuo pipefail
umask 077

readonly APP_NAME='KeeSyncXC'
readonly APP_VERSION='1.0.0'

say() { printf '%s\n' "$*"; }
# Only human-readable data passes through this helper. Machine-readable paths,
# configuration, checksums, and rclone arguments retain their original bytes.
ui_escape() {
  local LC_ALL=C.UTF-8 value="$1" char code i
  for ((i = 0; i < ${#value}; i++)); do
    char=${value:i:1}
    printf -v code '%d' "'$char"
    if (( code < 32 || (code >= 127 && code <= 159) )); then
      printf '\\x%02X' "$code"
    elif (( code == 1564 || code == 8206 || code == 8207 ||
            (code >= 8234 && code <= 8238) || (code >= 8294 && code <= 8297) )); then
      # Directional formatting can otherwise disguise the displayed filename.
      printf '\\u%04X' "$code"
    else
      printf '%s' "$char"
    fi
  done
}
ui_paths() {
  local path
  for path in "$@"; do
    printf '  '
    ui_escape "$path"
    printf '\n'
  done
}
# rclone's CSV output quotes filenames containing commas, quotes, or newlines.
# Decode those fields before escaping their contents, preserving only genuine
# record separators as terminal line breaks. No field is executed as shell code.
ui_csv_listing() {
  local LC_ALL=C.UTF-8 csv="$1" field='' char next quoted=0 start=1 i length
  length=${#csv}
  for ((i = 0; i < length; i++)); do
    char=${csv:i:1}
    if [[ "$char" == '"' ]]; then
      if (( quoted )); then
        next=${csv:i+1:1}
        if [[ "$next" == '"' ]]; then field+='"'; i=$((i + 1)); else quoted=0; fi
      elif (( start )); then
        quoted=1
      else
        return 1
      fi
      start=0
    elif [[ "$char" == ',' && "$quoted" == 0 ]]; then
      ui_escape "$field"
      printf ' | '
      field=''; start=1
    elif [[ "$char" == $'\n' && "$quoted" == 0 ]]; then
      ui_escape "$field"
      printf '\n'
      field=''; start=1
    else
      field+="$char"; start=0
    fi
  done
  (( quoted == 0 )) || return 1
  if [[ -n "$field" || "$start" == 0 ]]; then
    ui_escape "$field"
    printf '\n'
  fi
}
# Color only the active terminal stream; logs and captured data stay plain.
ui_text() {
  if [[ -t 1 && "${TERM:-dumb}" != dumb && -z "${NO_COLOR+x}" ]]; then
    printf '\033[%sm' "$1"
    ui_escape "$2"
    printf '\033[0m'
  else
    ui_escape "$2"
  fi
}
ui_heading() {
  printf '\n'
  ui_text '1;36' "$1"
  printf '\n%s\n' '------------------------------------------------------------'
}
# Keep menu frames in scrollback; wrap text without clearing previous output.
ui_box_row() {
  local width="$1" text style="${3:-}" side="${4:-|}" part framed
  text=$(ui_escape "$2")
  while :; do
    part="$text"
    if (( ${#part} > width )); then
      part=${text:0:width}
      # Prefer a word boundary, but split long words to keep both sides aligned.
      if [[ "$part" == *' '* && -n "${part% *}" ]]; then part=${part% *}; fi
    fi
    printf -v framed '%s %-*s %s' "$side" "$width" "$part" "$side"
    if [[ -n "$style" ]]; then ui_text "$style" "$framed"; else printf '%s' "$framed"; fi
    printf '\n'
    text=${text:${#part}}
    text=${text# }
    [[ -n "$text" ]] || break
  done
}
ui_menu() {
  local thick=0 side='|' frame_width=4 width=72
  if [[ "$1" == --thick ]]; then
    # Wider side borders share the same total width as the other menus.
    thick=1 side='||' frame_width=6 width=70
    shift
  fi
  local title="$1" columns="${COLUMNS:-}" size border row
  shift
  if [[ -z "$columns" && -t 0 ]] && command -v stty >/dev/null; then
    size=$(stty size 2>/dev/null) || size=''
    columns=${size##* }
  fi
  columns=${columns:-80}
  if [[ "$columns" =~ ^[0-9]{1,4}$ ]] && (( 10#$columns >= 12 && 10#$columns < width + frame_width )); then
    width=$((10#$columns - frame_width))
  fi
  if [[ "$thick" == 1 ]]; then
    printf -v border '%*s' "$((width + frame_width))" ''
    border=${border// /=}
  else
    printf -v border '%*s' "$((width + 2))" ''
    border="+${border// /-}+"
  fi
  printf '\n'
  ui_text '1;36' "$border"; printf '\n'
  ui_box_row "$width" "$title" '1;36' "$side"
  ui_text '1;36' "$border"; printf '\n'
  for row in "$@"; do ui_box_row "$width" "$row" '' "$side"; done
  ui_text '1;36' "$border"; printf '\n'
}
ui_step() {
  printf '\n'
  ui_text '1;36' "[$1/$2] $3"
  printf '\n'
}
ui_message() {
  printf '  '
  ui_text "$2" "$1"
  printf ' '
  ui_escape "$3"
  printf '\n'
}
ui_info() { ui_message '[INFO]' 36 "$*"; }
ui_ok() { ui_message '[ OK ]' 32 "$*"; }
ui_wait() { ui_message '[WAIT]' 36 "$*"; }
ui_warn() { ui_message '[WARN]' 33 "$*"; }
ui_error() { ui_message '[FAIL]' '1;31' "$*" >&2; }
ui_detail() { printf '    '; ui_escape "$1"; printf ': '; ui_escape "$2"; printf '\n'; }
ui_note() { printf '    '; ui_escape "$*"; printf '\n'; }
ui_rclone_boundary() {
  local label
  printf -v label '| %-56s |' "$1"
  printf '\n'
  ui_text '1;35' '+==========================================================+'
  printf '\n'
  ui_text '1;35' "$label"
  printf '\n'
  ui_text '1;35' '+==========================================================+'
  printf '\n\n'
}
fail() { ui_error "$*"; exit 1; }
usage() {
  printf '%s v%s\n\n' "$APP_NAME" "$APP_VERSION"
  printf '%s\n' 'Usage: bash keesyncxc.sh [OPTION]

Without options, install on first run; otherwise synchronize and open KeePassXC.
--install  Install missing system packages, configure rclone and database paths,
           and install the launcher and application-menu shortcut for this user.
           Run in a terminal as your regular user; sudo is used only for packages.
--menu     Interactive launcher and maintenance menu (also --interactive).
--launch   Synchronize and open directly, bypassing first-run installation.
--desktop  Start setup or launch with visible progress in the menu shortcut
           terminal. Keep changes, setup results, and errors open for review.
           Unchanged, synchronized sessions auto-close after three seconds.
--status   Show installation and local synchronization status, without cloud access.
--check-connection Check the cloud database and identity without changing files.
--sync     Synchronize without opening KeePassXC, including upload recovery.
--backups  Show local backups and recovery archives, without cloud access.
--open-backups Open the local backup folder in your file manager.
--cloud-backups List cloud backups without transferring database contents.
--inspect-backup Open a selected local backup without synchronization.
--inspect-cloud-backup Inspect a cloud backup; reuse a matching local MD5
           or download and verify a private copy, without synchronization.
--restore-backup Restore a selected local backup; preserve the working copy first.
           Inspect first; publishing restored contents needs separate confirmation.
--cleanup  Choose local backup age/count pruning (with deletion previews).
--cleanup-age, --cleanup-count Open an individual backup pruning policy directly.
--settings Show saved database and backup locations, without cloud access.
--backup-folder Change the cloud backup folder without reinstalling.
--move-local Move and verify the local database, then save its new location.
--connection Manage the Google Drive connection using rclone configuration.
--switch-database Back up local data and settings, then select another cloud file.
           --reset remains an alias for this workflow.
--repair   Refresh the installed launcher and shortcut using this script;
           preserve existing settings, or run setup if settings are absent.
--dependencies Check/install required system tools only.
--clear-local Delete the local database, backups, state, and settings;
           keep the launcher and shortcut installed for fresh setup.
--uninstall-keep-data Remove the launcher, shortcut, and settings; keep local data.
--uninstall Choose to remove the launcher, local data, or both;
           review the selected files and confirm once before deletion.
           Cloud files, packages, rclone credentials, and the source are always kept.
--version  Show the application version without changing any files.
--help     Show this help without changing any files.

Google authentication requires your browser and your own Google OAuth client.
An existing Google Drive database is required; installation does not upload one.'
}

MODE=auto
[[ "$#" -le 1 ]] || fail 'Use one option, or no options. See --help.'
case "${1:-}" in
  '') ;;
  --install) MODE=install ;;
  --menu|--interactive) MODE=menu ;;
  --launch) MODE=launch ;;
  --desktop) MODE=desktop ;;
  --status) MODE=status ;;
  --check-connection) MODE=check-connection ;;
  --sync) MODE=sync ;;
  --backups) MODE=backups ;;
  --open-backups) MODE=open-backups ;;
  --cloud-backups) MODE=cloud-backups ;;
  --inspect-backup) MODE=inspect-backup ;;
  --inspect-cloud-backup) MODE=inspect-cloud-backup ;;
  --restore-backup) MODE=restore-backup ;;
  --cleanup) MODE=cleanup ;;
  --cleanup-age) MODE=cleanup-age ;;
  --cleanup-count) MODE=cleanup-count ;;
  --settings) MODE=settings ;;
  --backup-folder) MODE=backup-folder ;;
  --move-local) MODE=move-local ;;
  --connection) MODE=connection ;;
  --reset|--switch-database) MODE=reset ;;
  --repair) MODE=repair ;;
  --dependencies) MODE=dependencies ;;
  --clear-local) MODE=clear-local ;;
  --uninstall-keep-data) MODE=uninstall-keep-data ;;
  --uninstall) MODE=uninstall ;;
  --version) printf '%s v%s\n' "$APP_NAME" "$APP_VERSION"; exit 0 ;;
  --help|-h) usage; exit 0 ;;
  *) fail "Unknown option: $1. Use --help for usage." ;;
esac

# =============================================================================
# 2. Database paths and sync storage
# Configure the database locations; keep sync metadata and backups separately.
# =============================================================================
# Defaults for manual setup. --install saves settings that override these values.
REMOTE='gdrive:KeePass/kpDB.kdbx'
DEFAULT_REMOTE="$REMOTE"
LOCAL="$HOME/Documents/KeeSyncXC/Passwords.kdbx"
REMOTE_BACKUP_FOLDER='KeePass-Backups'

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/keesyncxc"
CONFIG_FILE="$CONFIG_DIR/config"
INSTALLED_SCRIPT="$HOME/.local/bin/keesyncxc"
APPLICATIONS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_FILE="$APPLICATIONS/keesyncxc.desktop"
SCRIPT_PATH=${BASH_SOURCE[0]}
if [[ "$SCRIPT_PATH" != /* ]]; then SCRIPT_PATH="$PWD/$SCRIPT_PATH"; fi
# Settings are plain data lines: cloud file, local file, optional backup folder.
load_configuration() {
  local -a settings=()
  if [[ -e "$CONFIG_FILE" || -L "$CONFIG_FILE" ]]; then
    [[ -f "$CONFIG_FILE" ]] || { ui_error "Configuration is not a regular file: $CONFIG_FILE"; return 1; }
    mapfile -t settings <"$CONFIG_FILE" || { ui_error "Cannot read configuration: $CONFIG_FILE"; return 1; }
    [[ "${#settings[@]}" == 2 || "${#settings[@]}" == 3 ]] || {
      ui_error "Invalid configuration (expected cloud file, local file, and optional backup folder on separate lines): $CONFIG_FILE"; return 1;
    }
    REMOTE=${settings[0]}
    LOCAL=${settings[1]}
    if [[ "${#settings[@]}" == 3 ]]; then REMOTE_BACKUP_FOLDER=${settings[2]}; fi
  fi
}

DATA_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/keesyncxc"
BACKUPS="$DATA_DIR/backups"
LOCK="$DATA_DIR/launcher.lock"
APP_FAILED=0
EDITOR_CHANGED=0
REMOTE_REVIEW_REQUIRED=0
REMOTE_NOTICE=''

remote_file_path_valid() {
  local path="$1" relative="${1#*:}"
  [[ "$path" != -* && "$path" =~ ^[^/:]+:.+$ && ! "$path" =~ [[:cntrl:]] &&
     "$relative" != /* && "$relative" != */ && "$relative" != *//* &&
     "/$relative/" != */./* && "/$relative/" != */../* ]]
}
validate_remote_path() {
  remote_file_path_valid "$REMOTE" ||
    fail 'REMOTE must name a file, e.g. gdrive:kpDB.kdbx or gdrive:KeePass/kpDB.kdbx. Use no leading slash, empty/dot path segments, or control characters.'
}
remote_parent() {
  local path="$1" relative="${1#*:}"
  [[ "$path" == *:* ]] || return 1
  if [[ "$relative" == */* ]]; then
    printf '%s:%s' "${path%%:*}" "${relative%/*}"
  else
    printf '%s:' "${path%%:*}"
  fi
}
remote_filename() {
  local relative="${1#*:}"
  printf '%s' "${relative##*/}"
}
validate_backup_folder() {
  [[ -n "$REMOTE_BACKUP_FOLDER" && "$REMOTE_BACKUP_FOLDER" != . && "$REMOTE_BACKUP_FOLDER" != .. &&
     "$REMOTE_BACKUP_FOLDER" != */* && "$REMOTE_BACKUP_FOLDER" != *:* &&
     ! "$REMOTE_BACKUP_FOLDER" =~ [[:cntrl:]] &&
     "$REMOTE_BACKUP_FOLDER" != "$(remote_filename "$REMOTE")" ]] ||
    fail 'Cloud backup folder must be one folder name, different from the database filename. Slashes, colons, control characters, "." and ".." are not allowed.'
}
set_remote_paths() {
  REMOTE_DIR=$(remote_parent "$REMOTE")
  REMOTE_NAME=$(remote_filename "$REMOTE")
  # Join the backup folder directly to a root colon, or with a directory slash.
  REMOTE_BACKUP_DIR="$REMOTE_DIR"
  if [[ "$REMOTE_DIR" != "${REMOTE%%:*}:" ]]; then REMOTE_BACKUP_DIR+=/; fi
  REMOTE_BACKUP_DIR+="$REMOTE_BACKUP_FOLDER"
}
set_database_paths() {
  # State stores the last confirmed shared MD5; its filename identifies the remote.
  STATE="$DATA_DIR/$(printf '%s' "$REMOTE" | sha256sum | cut -d' ' -f1).md5"
  IDENTITY="$STATE.id"
  CONFLICT="$STATE.conflict"
  TRANSACTION="$STATE.transaction"
  RESTORE_REVIEW="$STATE.restore"
  set_remote_paths
}
validate_paths() {
  local path
  validate_remote_path
  validate_backup_folder
  for path in "$HOME" "$LOCAL" "$CONFIG_DIR" "$DATA_DIR"; do
    [[ "$path" == /* && ! "$path" =~ [[:cntrl:]] ]] || fail 'Local and XDG paths must be absolute, without control characters.'
  done
  [[ ! -e "$LOCAL" && ! -L "$LOCAL" || -f "$LOCAL" ]] || fail "Local database path is not a regular file: $LOCAL"
  local resolved_local resolved_data resolved_config artifact
  resolved_local=$(realpath -m -- "$LOCAL")
  resolved_data=$(realpath -m -- "$DATA_DIR")
  resolved_config=$(realpath -m -- "$CONFIG_DIR")
  [[ "$resolved_local" != "$resolved_data" && "$resolved_local" != "$resolved_data/"* &&
     "$resolved_local" != "$resolved_config" && "$resolved_local" != "$resolved_config/"* ]] ||
    fail 'Keep the working database outside launcher configuration, state, and backup directories.'
  for artifact in "$INSTALLED_SCRIPT" "$DESKTOP_FILE"; do
    [[ "$resolved_local" != "$(realpath -m -- "$artifact")" ]] || fail 'The database path overlaps an installed launcher file.'
  done
}

# =============================================================================
# 3. Startup checks and locking
# Prepare storage, check dependencies, and prevent overlapping editing sessions.
# =============================================================================
trusted_storage_path() {
  local current="$1" owner mode bits system_owner
  [[ "$current" == /* && ! "$current" =~ [[:cntrl:]] ]] || fail 'State paths must be absolute, without control characters.'
  # The root directory's owner also identifies system ownership in UID-mapped environments.
  system_owner=$(stat -c '%u' -- /) || fail 'Cannot verify system directory ownership.'
  while :; do
    while [[ "$current" != / && "$current" == */ ]]; do current=${current%/}; done
    [[ ! -L "$current" ]] || fail "Refusing a symlinked state path: $current"
    if [[ -e "$current" ]]; then
      [[ -d "$current" ]] || fail "A state path is not a directory: $current"
      read -r owner mode < <(stat -c '%u %a' -- "$current") || fail "Cannot inspect state directory: $current"
      [[ "$owner" =~ ^[0-9]+$ && "$mode" =~ ^[0-7]{1,4}$ ]] || fail "Invalid state directory metadata: $current"
      [[ "$owner" == "$EUID" || "$owner" == 0 || "$owner" == "$system_owner" ]] ||
        fail "State directory ancestry is owned by another user: $current"
      bits=$((8#$mode))
      if (( bits & 0022 )); then
        # A system-owned sticky directory, such as /tmp, protects user-owned children.
        if ! (( bits & 01000 )) || [[ "$owner" != 0 && "$owner" != "$system_owner" ]]; then
          fail "Refusing a group/world-writable state directory or ancestor: $current"
        fi
      fi
    fi
    [[ "$current" != / ]] || break
    current=${current%/*}
    current=${current:-/}
  done
}
private_storage_directory() {
  local path="$1" owner mode
  [[ -d "$path" && ! -L "$path" ]] || fail "State storage must be a regular directory: $path"
  read -r owner mode < <(stat -c '%u %a' -- "$path") || fail "Cannot inspect state storage: $path"
  [[ "$owner" == "$EUID" && "$mode" =~ ^[0-7]{1,4}$ ]] || fail "State storage must be owned by your user: $path"
  (( (8#$mode & 0022) == 0 )) || fail "Refusing group/world-writable state storage: $path"
  if [[ "$mode" != 700 ]]; then chmod 700 -- "$path" || fail "Cannot make state storage private: $path"; fi
  [[ "$(stat -c '%u:%a' -- "$path")" == "$EUID:700" ]] || fail "State storage permissions could not be secured: $path"
}
verify_launcher_lock() {
  local owner mode links
  [[ -f "$LOCK" && ! -L "$LOCK" ]] || fail "The launcher lock must be a regular file without symbolic links: $LOCK"
  read -r owner mode links < <(stat -c '%u %a %h' -- "$LOCK") || fail 'Cannot inspect the launcher lock.'
  [[ "$owner" == "$EUID" && "$mode" =~ ^[0-7]{1,4}$ && "$links" == 1 ]] ||
    fail 'The launcher lock must be owned by your user and have exactly one hard link.'
  (( (8#$mode & 0077) == 0 )) || fail 'The launcher lock must not grant access to other users.'
}
secure_launcher_lock() {
  local opened current
  trusted_storage_path "$DATA_DIR"
  trusted_storage_path "$BACKUPS"
  # Reject unsafe existing app directories before making any permissions changes.
  local path owner mode
  for path in "$DATA_DIR" "$BACKUPS"; do
    if [[ -e "$path" ]]; then
      read -r owner mode < <(stat -c '%u %a' -- "$path") || fail "Cannot inspect state storage: $path"
      [[ "$owner" == "$EUID" && "$mode" =~ ^[0-7]{1,4}$ ]] || fail "State storage must be owned by your user: $path"
      (( (8#$mode & 0022) == 0 )) || fail "Refusing group/world-writable state storage: $path"
    fi
  done
  mkdir -p -- "$DATA_DIR" || fail 'Cannot create launcher state storage.'
  trusted_storage_path "$DATA_DIR"
  private_storage_directory "$DATA_DIR"
  if [[ -e "$BACKUPS" ]]; then private_storage_directory "$BACKUPS"; fi
  if [[ -e "$LOCK" || -L "$LOCK" ]]; then verify_launcher_lock; fi
  # Appending opens without truncating a linked target; trusted parents prevent substitution.
  exec 9>>"$LOCK" || fail 'Cannot open the launcher lock.'
  verify_launcher_lock
  opened=$(stat -Lc '%d:%i' -- /proc/self/fd/9) || fail 'Cannot inspect the opened launcher lock.'
  current=$(stat -c '%d:%i' -- "$LOCK") || fail 'Cannot inspect the launcher lock path.'
  [[ "$opened" == "$current" ]] || fail 'The launcher lock changed while opening it.'
  flock -n 9 || fail 'Another copy of this launcher is already running.'
  verify_launcher_lock
  [[ "$(stat -c '%d:%i' -- "$LOCK")" == "$opened" ]] || fail 'The launcher lock was replaced while acquiring it.'
}
startup_checks() {
  command -v rclone >/dev/null || fail 'Run this script with --install to install rclone and configure synchronization.'
  command -v keepassxc >/dev/null || fail 'Run this script with --install to install KeePassXC.'
  command -v flock >/dev/null || fail 'flock is required (util-linux). Run this script with --install.'
  command -v pgrep >/dev/null || fail 'pgrep is required (procps). Run this script with --install.'
  command -v timeout >/dev/null || fail 'timeout is required (GNU coreutils). Run this script with --install.'
  validate_paths
  set_database_paths
  secure_launcher_lock
  mkdir -p -- "$BACKUPS" "$(dirname "$LOCAL")"
  private_storage_directory "$BACKUPS"
  if pgrep -u "$(id -u)" -x keepassxc >/dev/null 2>&1; then
    fail 'KeePassXC is already running. Fully exit KeePassXC (including tray) before starting through this shortcut.'
  fi
}

# Bound DNS resolution and the TCP connection together, before rclone can start
# its longer authentication/network retries. Use Google's API host rather than
# an unrelated website or ICMP, which may be blocked on an otherwise usable link.
check_internet_connection() {
  ui_wait 'Checking internet connection (3-second timeout)...'
  if timeout --kill-after=1s 3s bash -c 'exec 3<>/dev/tcp/www.googleapis.com/443' >/dev/null 2>&1; then
    return 0
  fi
  ui_warn 'No connection to Google Drive detected; skipping cloud requests.'
  return 1
}

# Installation runs separately from synchronization: it never transfers a database
# or writes synchronization state. Only missing packages require administrator access.
install_dependencies() {
  local pair command_name package
  local -a packages=()
  ui_wait 'Checking required system tools...'
  for pair in keepassxc:keepassxc rclone:rclone zenity:zenity flock:util-linux pgrep:procps; do
    command_name=${pair%%:*}
    package=${pair#*:}
    if ! command -v "$command_name" >/dev/null; then packages+=("$package"); fi
  done
  for command_name in cat chmod cp cut date dirname id install ln md5sum mkdir mktemp mv realpath rm rmdir sha256sum stat timeout; do
    if ! command -v "$command_name" >/dev/null; then packages+=(coreutils); break; fi
  done
  [[ "${#packages[@]}" != 0 ]] || { ui_ok 'All required system tools are already installed.'; return 0; }
  command -v apt-get >/dev/null || fail 'Automatic package installation requires Linux Mint, Ubuntu, or Debian. Install the missing tools manually and rerun --install.'
  command -v sudo >/dev/null || fail 'sudo is required to install missing system packages.'
  ui_info "Installing missing packages: ${packages[*]}"
  ui_note 'The next output comes from apt. sudo may ask for your Linux password.'
  ui_note '--- apt output begins ---'
  sudo apt-get update || fail 'Package index update failed; installation stopped.'
  sudo apt-get install -y -- "${packages[@]}" || fail 'Package installation failed; installation stopped.'
  ui_note '--- apt output ends ---'
  for command_name in keepassxc rclone zenity flock pgrep cat chmod cp cut date dirname id install ln md5sum mkdir mktemp mv realpath rm rmdir sha256sum stat timeout; do
    command -v "$command_name" >/dev/null || fail "Required command is still missing after package installation: $command_name"
  done
  ui_ok 'Required system tools are installed.'
}
prompt_setting() {
  local answer value_label="${3:-Default}"
  ui_info "$1" >&2
  if [[ -n "$2" ]]; then
    ui_detail "$value_label" "$2" >&2
    printf '    Enter value (Enter keeps %s): ' "${value_label,,}" >&2
  else
    printf '    Enter value (required): ' >&2
  fi
  IFS= read -r answer || { ui_info 'Action cancelled; saved settings were not changed.' >&2; return 130; }
  printf '\n' >&2
  printf '%s' "${answer:-$2}"
}
remote_is_configured() {
  local remotes name
  remotes=$(rclone listremotes) || fail 'Cannot read rclone remotes. Run rclone config, then retry --install.'
  while IFS= read -r name; do
    if [[ "$name" == "${REMOTE%%:*}:" ]]; then return 0; fi
  done <<<"$remotes"
  return 1
}
configure_rclone() {
  local answer remote_exists=0 remote_name="${REMOTE%%:*}" rclone_status=0
  ui_wait 'Checking saved rclone connections...'
  if remote_is_configured; then
    remote_exists=1
    ui_ok "Saved Google Drive connection found: $remote_name"
    printf '    Reconfigure existing rclone remote %s? [y/N]: ' "$(ui_escape "${REMOTE%%:*}")" >&2
    IFS= read -r answer || { ui_info 'Installation cancelled; saved settings were not changed.'; return 130; }
    printf '\n' >&2
    case "$answer" in
      ''|n|N|no|NO) ui_info "Keeping the existing rclone remote: $remote_name"; return 0 ;;
      y|Y|yes|YES) ;;
      *) fail 'Expected y or n; installation stopped.' ;;
    esac
  fi
  ui_heading 'rclone connection setup'
  ui_info 'Next: connect your Google Drive account using rclone.'
  ui_note 'The next menu belongs to rclone. A remote is a saved cloud connection.'
  ui_detail 'Remote name' "$remote_name (without a colon)"
  ui_detail 'Storage' 'drive (Google Drive)'
  ui_detail 'Client ID and client secret' 'your own Google OAuth credentials (Desktop app)'
  ui_detail 'Scope' '1 (full access)'
  ui_detail 'Service account file' 'leave blank'
  ui_detail 'Advanced config' 'n'
  ui_detail 'Browser authentication' 'y on this computer, then sign in to Google'
  ui_detail 'Shared Drive' 'n for a personal Drive'
  ui_note 'Create your Google OAuth credentials using this guide:'
  ui_note 'https://rclone.org/drive/#making-your-own-client-id'
  ui_info 'At the rclone menu, s sets a password for its configuration file.'
  if [[ "$remote_exists" == 1 ]]; then
    ui_note "Choose e (Edit existing remote), then select $remote_name."
  else
    ui_note 'If you see "No remotes found", this is expected before the first connection.'
    ui_note "Choose n (New remote), then enter $remote_name at name> without a colon."
  fi
  ui_note 'When finished, choose y to keep the remote, then q to leave rclone.'
  ui_note 'This installer will then verify your database and finish setup.'
  ui_wait 'Opening rclone configuration now...'
  ui_rclone_boundary 'rclone output begins | connection setup'
  rclone config || rclone_status=$?
  ui_rclone_boundary 'rclone output ends | back to launcher'
  [[ "$rclone_status" == 0 ]] || fail 'rclone configuration failed; installation stopped.'
  ui_wait 'Back in the KeeSyncXC installer; checking the saved Google Drive connection...'
  remote_is_configured || fail "rclone remote ${REMOTE%%:*} was not saved. Rerun --install after configuring it."
  ui_ok 'Google Drive connection is saved.'
}
desktop_exec_path() {
  local value="$1"
  # First quote the Exec argument, then escape backslashes for desktop string decoding.
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//\$/\\\$}
  value=${value//\`/\\\`}
  value=${value//%/%%}
  value=${value//\\/\\\\}
  printf '"%s"' "$value"
}
write_installation() {
  local executable="$HOME/.local/bin/keesyncxc"
  local applications="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
  local script_tmp config_tmp desktop_tmp
  [[ "$applications" == /* && ! "$applications" =~ [[:cntrl:]] ]] || fail 'XDG_DATA_HOME must be absolute, without control characters.'
  [[ ! -d "$executable" && ! -d "$CONFIG_FILE" && ! -d "$applications/keesyncxc.desktop" ]] ||
    fail 'An installation destination is a directory. Move it aside before installing.'
  mkdir -p "$HOME/.local/bin" "$CONFIG_DIR" "$applications"
  script_tmp=$(mktemp "$HOME/.local/bin/.keesyncxc-install.XXXXXXXX") || fail 'Cannot create a temporary launcher.'
  config_tmp=$(mktemp "$CONFIG_DIR/.config.XXXXXXXX") || {
    rm -f -- "$script_tmp"; fail 'Cannot create a temporary configuration.';
  }
  desktop_tmp=$(mktemp "$applications/.keesyncxc-install.XXXXXXXX") || {
    rm -f -- "$script_tmp" "$config_tmp"; fail 'Cannot create a temporary shortcut.';
  }
  if ! install -m 700 -- "${BASH_SOURCE[0]}" "$script_tmp" ||
     ! printf '%s\n%s\n%s\n' "$REMOTE" "$LOCAL" "$REMOTE_BACKUP_FOLDER" >"$config_tmp" ||
     ! {
       printf '[Desktop Entry]\nType=Application\nName=%s\n' "$APP_NAME"
       printf 'Comment=Synchronize the database with Google Drive before opening and after exit\n'
       printf 'Exec=/bin/bash %s --desktop\n' "$(desktop_exec_path "$executable")"
       printf 'Icon=keepassxc\nTerminal=true\nCategories=Utility;Security;\n'
     } >"$desktop_tmp" || ! chmod 600 "$config_tmp" || ! chmod 644 "$desktop_tmp" ||
     ! mv -fT -- "$script_tmp" "$executable" || ! mv -fT -- "$config_tmp" "$CONFIG_FILE" ||
     ! mv -fT -- "$desktop_tmp" "$applications/keesyncxc.desktop"; then
    rm -f -- "$script_tmp" "$config_tmp" "$desktop_tmp"
    fail 'Could not finish installing the launcher and shortcut. Fix the error above and rerun --install.'
  fi
  ui_ok 'Installed the launcher, application-menu shortcut, and settings.'
  ui_heading 'Setup complete'
  ui_detail 'Cloud database' "$REMOTE"
  ui_detail 'Cloud backups' "$REMOTE_BACKUP_DIR"
  ui_detail 'Local database' "$LOCAL"
  ui_detail 'Installed launcher' "$executable"
  ui_detail 'Saved settings' "$CONFIG_FILE"
  ui_info 'Installation complete. Open KeeSyncXC from your application menu.'
  ui_detail 'Or run' "\"$executable\""
  ui_detail 'Manage installation and backups' "\"$executable\" --menu"
  ui_note 'Save and fully quit KeePassXC when finished so changes upload to Drive.'
}
install_launcher() {
  local previous_remote="$REMOTE" previous_local="$LOCAL" previous_state cloudhash localhash baseline remote_name=gdrive
  local previous_backup_folder="$REMOTE_BACKUP_FOLDER" value_label=Default
  # coreutils may still need installing; Bash supplies EUID if id is unavailable.
  [[ "$(id -u 2>/dev/null || printf '%s' "$EUID")" != 0 ]] || fail 'Run --install as your regular user, without sudo. Only package installation uses sudo.'
  ui_heading "$APP_NAME v$APP_VERSION installation"
  ui_note 'Five steps: locations, system tools, Google Drive connection, verification, and installation.'
  ui_note 'The existing cloud database is the initial source. Upload your .kdbx to Drive before continuing.'
  ui_step 1 5 'Database and backup locations'
  if [[ -f "$CONFIG_FILE" ]]; then
    value_label='Saved setting'
    ui_info 'Your saved settings are selected below. Enter keeps each saved value.'
    ui_detail 'Default for new setup' "$DEFAULT_REMOTE"
    ui_note "To select that cloud path, enter $DEFAULT_REMOTE at the first prompt."
  fi
  if remote_file_path_valid "$REMOTE"; then remote_name=${REMOTE%%:*}; fi
  REMOTE=$(prompt_setting "Google Drive database (remote:path or filename in $remote_name:)" "$REMOTE" "$value_label")
  if [[ "$REMOTE" != *:* ]]; then
    REMOTE="$remote_name:$REMOTE"
    ui_info "Using Google Drive database: $REMOTE"
  fi
  validate_remote_path
  REMOTE_BACKUP_FOLDER=$(prompt_setting 'Google Drive backup folder name (beside the database)' "$REMOTE_BACKUP_FOLDER" "$value_label")
  validate_backup_folder
  LOCAL=$(prompt_setting 'Local database path' "$LOCAL" "$value_label")
  if [[ "$LOCAL" == '~/'* ]]; then LOCAL="$HOME/${LOCAL:2}"; fi
  set_remote_paths
  ui_ok 'Database and backup locations selected.'
  ui_detail 'Cloud database' "$REMOTE"
  ui_detail 'Cloud backups' "$REMOTE_BACKUP_DIR"
  ui_detail 'Local database' "$LOCAL"
  ui_step 2 5 'System tools'
  install_dependencies
  startup_checks
  # Do not let changing paths bypass recovery of the previous configuration.
  previous_state="$DATA_DIR/$(printf '%s' "$previous_remote" | sha256sum | cut -d' ' -f1).md5"
  if [[ "$REMOTE" != "$previous_remote" || "$LOCAL" != "$previous_local" || "$REMOTE_BACKUP_FOLDER" != "$previous_backup_folder" ]]; then
    [[ ! -e "$previous_state.transaction" && ! -e "$previous_state.conflict" &&
       ! -e "$previous_state.restore" && ! -L "$previous_state.restore" &&
       ! -e "$TRANSACTION" && ! -e "$CONFLICT" && ! -e "$RESTORE_REVIEW" && ! -L "$RESTORE_REVIEW" ]] ||
      fail 'Resolve the pending conflict, interrupted upload, or restored database review with the current settings before changing database paths or the backup folder.'
  fi
  ui_step 3 5 'Google Drive connection'
  configure_rclone
  ui_step 4 5 'Verify the cloud database'
  ui_wait "Verifying Google Drive database: $REMOTE"
  describe_remote_selection
  cloudhash=$(lookup_database) || fail 'Cannot verify the cloud database. Check the connection, credentials, and path, then rerun --install.'
  [[ "$cloudhash" != MISSING ]] || fail "Cloud database not found: $REMOTE. Upload it to Google Drive, then rerun --install."
  check_identity "$cloudhash" 0 >/dev/null || fail 'Cannot verify the existing Drive file ID. Use a regular Drive file and retry.'
  if [[ -f "$STATE" ]]; then
    baseline=$(cat "$STATE")
    [[ "$baseline" =~ ^[a-fA-F0-9]{32}$ ]] || fail 'Invalid synchronization state; investigate it before installing.'
    [[ -f "$LOCAL" ]] || fail 'Previously synchronized local database is missing. Restore it before installing.'
  fi
  if [[ -f "$LOCAL" && ( ! -f "$STATE" || "$LOCAL" != "$previous_local" || "$REMOTE" != "$previous_remote" ) ]]; then
    localhash=$(hash_local) || fail 'Cannot read the existing local database.'
    [[ "$localhash" == "$cloudhash" ]] || fail 'Existing local database differs from Drive. Preserve it elsewhere or select another local path, then rerun --install.'
  fi
  ui_ok 'Cloud database and file ID verified.'
  ui_step 5 5 'Install the launcher'
  ui_wait 'Saving your launcher, application-menu shortcut, and settings...'
  write_installation
}

# Maintenance uses the same lock as editing sessions, without requiring Drive.
installation_complete() {
  [[ -f "$CONFIG_FILE" && -f "$INSTALLED_SCRIPT" && -x "$INSTALLED_SCRIPT" && -f "$DESKTOP_FILE" ]]
}
confirm_action() {
  local answer
  printf '\n  %s [y/N]: ' "$1" >&2
  if IFS= read -r answer; then
    case "${answer,,}" in y|yes) return 0 ;; esac
  fi
  ui_info 'Cancelled; no files were removed.'
  return 130
}
maintenance_lock() {
  local path
  [[ "$(id -u 2>/dev/null || printf '%s' "$EUID")" != 0 ]] || fail 'Run maintenance as your regular user, without sudo.'
  for path in "$HOME" "$CONFIG_DIR" "$APPLICATIONS" "$DATA_DIR"; do
    [[ "$path" == /* && ! "$path" =~ [[:cntrl:]] ]] || fail 'Local and XDG paths must be absolute, without control characters.'
  done
  command -v flock >/dev/null || fail 'Maintenance requires flock (util-linux).'
  secure_launcher_lock
  if command -v pgrep >/dev/null && pgrep -u "$(id -u)" -x keepassxc >/dev/null 2>&1; then
    fail 'Fully quit KeePassXC, including its tray process, before maintenance.'
  fi
}
show_status() {
  local name missing=0 invalid=0 baseline
  ui_heading 'Launcher status'
  ui_detail 'Application' "$APP_NAME"
  ui_detail 'Version' "v$APP_VERSION"
  if installation_complete; then ui_ok 'Installation: complete'; else ui_warn 'Installation: incomplete (run --install)'; fi
  ui_detail 'Launcher' "$INSTALLED_SCRIPT"
  ui_detail 'Shortcut' "$DESKTOP_FILE"
  ui_detail 'Settings' "$CONFIG_FILE"
  if [[ ! -e "$CONFIG_FILE" && ! -L "$CONFIG_FILE" ]]; then
    ui_info 'Saved settings: absent (script defaults shown)'
  elif ! load_configuration; then
    ui_error 'Saved settings: invalid; use --uninstall to remove the installation if needed.'
    invalid=1
  fi
  if [[ "$invalid" == 0 ]] && ! (validate_remote_path && validate_backup_folder) 2>/dev/null; then
    ui_error 'Saved settings: invalid cloud database or backup folder; run --install to correct it.'
    invalid=1
  fi
  ui_heading 'Database and backups'
  ui_detail 'Cloud database' "$REMOTE (not contacted)"
  ui_detail 'Local database' "$LOCAL"
  if [[ -f "$LOCAL" ]]; then ui_ok 'Local database: present'; else ui_warn 'Local database: missing'; fi
  ui_detail 'Local backups' "$BACKUPS"
  for name in keepassxc rclone zenity flock pgrep cat cut md5sum sha256sum realpath stat; do
    if ! command -v "$name" >/dev/null; then ui_warn "Missing tool: $name"; missing=1; fi
  done
  if [[ "$missing" == 0 ]]; then ui_ok 'Required tools: available'; fi
  if [[ "$invalid" == 0 ]] && command -v sha256sum >/dev/null && command -v cut >/dev/null && command -v cat >/dev/null; then
    set_database_paths
    ui_detail 'Cloud backups' "$REMOTE_BACKUP_DIR"
    ui_heading 'Synchronization state'
    if [[ -f "$STATE" ]]; then
      baseline=$(cat "$STATE")
      if [[ "$baseline" =~ ^[a-fA-F0-9]{32}$ ]]; then
        ui_detail 'Last synchronized MD5' "$baseline"
      else
        ui_warn 'Synchronization state: invalid (preserve it for investigation)'
      fi
    else
      ui_info 'Synchronization state: no confirmed synchronization yet'
    fi
    if [[ -e "$CONFLICT" || -L "$CONFLICT" ]]; then ui_warn 'Pending conflict: yes'; fi
    if [[ -e "$TRANSACTION" || -L "$TRANSACTION" ]]; then ui_warn 'Interrupted upload: yes'; fi
    if [[ -e "$RESTORE_REVIEW" || -L "$RESTORE_REVIEW" ]]; then ui_warn 'Restored database: confirmation required before upload; inspect the backup first.'; fi
    if [[ -f "$IDENTITY" ]]; then ui_detail 'Pinned Drive file ID' "$(cat "$IDENTITY")"; fi
  fi
  return "$invalid"
}
# Match only names this launcher generates. Retention never prunes the safety
# copies and recovery archives made when switching to another cloud selection.
prunable_backup_name() {
  [[ "$1" =~ ^(local|upload)-[0-9]{8}T[0-9]{6}Z-[a-zA-Z0-9]{8}\.kdbx$ ]]
}
recognized_backup_name() {
  prunable_backup_name "$1" ||
    [[ "$1" =~ ^(switch|cloud)-[0-9]{8}T[0-9]{6}Z-[a-zA-Z0-9]{8}\.kdbx$ ||
       "$1" =~ ^interrupted-upload-[a-zA-Z0-9]{8}\.txt$ ]]
}
recognized_state_name() {
  [[ "$1" =~ ^[a-f0-9]{64}\.md5(\.(id|conflict|transaction|restore))?$ ]]
}
maintenance_plain_path() {
  # Reject both a final symlink and a symlink anywhere in its parents.
  [[ "$1" == /* && ! "$1" =~ [[:cntrl:]] && ! -L "$1" &&
     "$(realpath -ms -- "$1")" == "$(realpath -m -- "$1")" ]]
}
maintenance_add_file() {
  local file="$1" previous
  [[ -e "$file" || -L "$file" ]] || return 0
  maintenance_plain_path "$file" && [[ -f "$file" ]] ||
    fail "Refusing to remove a non-regular file or symlinked path: $file"
  file=$(realpath -ms -- "$file")
  for previous in "${MAINT_FILES[@]}"; do [[ "$previous" != "$file" ]] || return 0; done
  MAINT_FILES+=("$file")
  MAINT_IDENTITIES+=("$(stat -c '%d:%i:%Y:%s' -- "$file")")
}
maintenance_verify_files() {
  local index file identity
  for index in "${!MAINT_FILES[@]}"; do
    file=${MAINT_FILES[index]}
    maintenance_plain_path "$file" && [[ -f "$file" ]] || fail "A selected file changed; stopped: $file"
    identity=$(stat -c '%d:%i:%Y:%s' -- "$file") || fail "A selected file became unavailable: $file"
    [[ "$identity" == "${MAINT_IDENTITIES[index]}" ]] || fail "A selected file changed; review again: $file"
  done
}
maintenance_remove_files() {
  maintenance_verify_files
  if [[ "${#MAINT_FILES[@]}" != 0 ]]; then rm -- "${MAINT_FILES[@]}"; fi
}
maintenance_saved_local() {
  local -a settings=()
  FULL_LOCAL=''
  [[ -e "$CONFIG_FILE" || -L "$CONFIG_FILE" ]] || return 0
  maintenance_plain_path "$CONFIG_FILE" && [[ -f "$CONFIG_FILE" ]] ||
    fail "Cannot safely inspect the saved configuration: $CONFIG_FILE"
  mapfile -t settings <"$CONFIG_FILE" || fail 'Cannot inspect the saved settings.'
  # Never infer a database from defaults when the installed settings are broken.
  [[ ( "${#settings[@]}" == 2 || "${#settings[@]}" == 3 ) &&
     "${settings[1]}" == /* && ! "${settings[1]}" =~ [[:cntrl:]] ]] ||
    fail 'Saved settings are invalid. Local databases were preserved; use ordinary uninstall or correct the settings first.'
  FULL_LOCAL=${settings[1]}
  maintenance_plain_path "$FULL_LOCAL" || fail "Configured local database has a symlinked path; preserve it manually: $FULL_LOCAL"
  FULL_LOCAL=$(realpath -ms -- "$FULL_LOCAL")
  local artifact
  for artifact in "$CONFIG_FILE" "$INSTALLED_SCRIPT" "$DESKTOP_FILE" "$SCRIPT_PATH" "$LOCK"; do
    [[ "$(realpath -m -- "$FULL_LOCAL")" != "$(realpath -m -- "$artifact")" ]] ||
      fail "The database overlaps a launcher/source file; preserve it manually: $FULL_LOCAL"
  done
}
collect_cleanup_temps() {
  local directory="$1" pattern="$2" file
  # Abandoned temporary files are safe to identify only by exact generated
  # names in known directories. Never traverse links or select this source.
  [[ -d "$directory" && ! -L "$directory" ]] || return 0
  maintenance_plain_path "$directory" || return 0
  for file in "$directory"/.*; do
    [[ "${file##*/}" =~ $pattern && -f "$file" && ! -L "$file" &&
       ! "$file" -ef "$SCRIPT_PATH" ]] || continue
    maintenance_add_file "$file"
  done
}
# Recognize abandoned private download stages without claiming unknown files.
# Validate the whole directory before selecting its optional known payload.
collect_cleanup_download_stages() {
  local directory="$1" stage child payload valid
  [[ -d "$directory" && ! -L "$directory" ]] || return 0
  maintenance_plain_path "$directory" || return 0
  for stage in "$directory"/.keesyncxc-download.*; do
    [[ "${stage##*/}" =~ ^\.keesyncxc-download\.[a-zA-Z0-9]{8}$ &&
       -d "$stage" && ! -L "$stage" && -r "$stage" && -x "$stage" ]] || continue
    maintenance_plain_path "$stage" || continue
    [[ "$(stat -c '%u' -- "$stage")" == "$EUID" ]] || continue
    payload='' valid=1
    # Include hidden names, but never . or .., when checking for unknown entries.
    for child in "$stage"/* "$stage"/.[!.]* "$stage"/..?*; do
      [[ -e "$child" || -L "$child" ]] || continue
      if [[ "$child" != "$stage/database.kdbx" || ! -f "$child" || -L "$child" ||
            "$child" -ef "$SCRIPT_PATH" ]]; then
        valid=0
        break
      fi
      if ! maintenance_plain_path "$child" ||
         [[ "$(stat -c '%u:%h' -- "$child")" != "$EUID:1" ]]; then
        valid=0
        break
      fi
      payload="$child"
    done
    [[ "$valid" == 1 ]] || continue
    if [[ -n "$payload" ]]; then maintenance_add_file "$payload"; fi
    MAINT_DIRS+=("$stage")
  done
}

collect_full_cleanup() {
  local file name archive child
  command -v pgrep >/dev/null || fail 'Full cleanup requires pgrep (procps) to check that KeePassXC is closed. Ordinary uninstall can preserve local data without it.'
  MAINT_FILES=() MAINT_IDENTITIES=() MAINT_DIRS=()
  maintenance_saved_local
  if [[ -n "$FULL_LOCAL" ]]; then maintenance_add_file "$FULL_LOCAL"; fi
  for file in "$BACKUPS"/*; do
    name=${file##*/}
    if recognized_backup_name "$name"; then
      maintenance_add_file "$file"
    elif [[ "$name" =~ ^reset-[a-zA-Z0-9]{8}$ && -d "$file" && ! -L "$file" ]]; then
      archive="$file"
      maintenance_plain_path "$archive" || fail "Symlinked recovery archive: $archive"
      for child in "$archive"/*; do
        if [[ "${child##*/}" == config ]] || recognized_state_name "${child##*/}"; then
          maintenance_add_file "$child"
        fi
      done
      MAINT_DIRS+=("$archive")
    fi
  done
  for file in "$DATA_DIR"/*; do
    if recognized_state_name "${file##*/}"; then maintenance_add_file "$file"; fi
  done
  maintenance_add_file "$CONFIG_FILE"
  collect_cleanup_temps "$DATA_DIR" '^\.(sync-state|launcher-record)\.[a-zA-Z0-9]{8}$'
  collect_cleanup_temps "$CONFIG_DIR" '^\.config\.[a-zA-Z0-9]{8}$'
  collect_cleanup_temps "$HOME/.local/bin" '^\.keesyncxc-install\.[a-zA-Z0-9]{8}$'
  collect_cleanup_temps "$APPLICATIONS" '^\.keesyncxc-install\.[a-zA-Z0-9]{8}$'
  collect_cleanup_download_stages "$BACKUPS"
  if [[ -n "$FULL_LOCAL" ]]; then
    collect_cleanup_temps "${FULL_LOCAL%/*}" '^\.keesyncxc-download\.[a-zA-Z0-9]{8}$'
    collect_cleanup_download_stages "${FULL_LOCAL%/*}"
  fi
  MAINT_DIRS+=("$BACKUPS" "$CONFIG_DIR")
  if [[ -n "$FULL_LOCAL" ]]; then
    # Only attempt the configured database's immediate parent, never shared roots.
    file=${FULL_LOCAL%/*}
    if [[ "$file" != / && "$file" != "$HOME" && "$file" != "$HOME/Documents" &&
          "$file" != "$DATA_DIR" && "$file" != "$APPLICATIONS" && "$file" != "$HOME/.local/bin" ]]; then
      MAINT_DIRS+=("$file")
    fi
  fi
}
preview_full_cleanup() {
  local file
  ui_warn 'Full local cleanup permanently removes the following files:'
  if [[ -n "$FULL_LOCAL" ]]; then ui_detail 'Working database (will be deleted)' "$FULL_LOCAL"; fi
  if [[ "${#MAINT_FILES[@]}" != 0 ]]; then
    for file in "${MAINT_FILES[@]}"; do
      if [[ "$file" != "$FULL_LOCAL" ]]; then ui_paths "$file"; fi
    done
  else
    ui_info 'No recognized local data files found.'
  fi
  ui_warn 'This also discards local conflict and interrupted-upload records. It does not undo any cloud upload.'
  ui_note 'Cloud files, rclone credentials, packages, source files, unknown files, and symlinks are kept.'
  ui_note 'Only empty database/backup/settings folders are removed. The empty launcher.lock and state folder remain for concurrency safety.'
}
remove_empty_maintenance_dirs() {
  local directory
  for directory in "${MAINT_DIRS[@]}"; do
    if [[ -d "$directory" && ! -L "$directory" ]]; then rmdir -- "$directory" 2>/dev/null || true; fi
  done
}
full_local_cleanup() {
  local FULL_LOCAL=''
  local -a MAINT_FILES=() MAINT_IDENTITIES=() MAINT_DIRS=()
  collect_full_cleanup
  preview_full_cleanup
  confirm_action 'Permanently delete these local files and clear setup?' || return 130
  maintenance_remove_files
  remove_empty_maintenance_dirs
  ui_ok 'Local data and saved setup cleared. Installed launcher and menu shortcut are kept; the next launch starts setup.'
}
prune_backups() {
  command -v pgrep >/dev/null || fail 'Backup cleanup requires pgrep (procps) to check that KeePassXC is closed.'
  local policy="$1" limit cutoff file name timestamp resolved_local sorted entry count=0
  local -a entries=() ordered=() MAINT_FILES=() MAINT_IDENTITIES=()
  # Backups are shared by remotes; unresolved records always block pruning.
  for file in "$DATA_DIR"/*.conflict "$DATA_DIR"/*.transaction "$DATA_DIR"/*.restore; do
    if [[ -e "$file" || -L "$file" ]]; then
      fail "Resolve the pending conflict, interrupted upload, or restored database review before cleanup: $file"
    fi
  done
  if [[ "$policy" == age ]]; then
    limit=$(prompt_setting 'Delete backups older than how many days? (0 selects all past backups)' '30')
    [[ "$limit" =~ ^[0-9]{1,5}$ ]] || fail 'Enter a number of days from 0 to 36500.'
    limit=$((10#$limit))
    (( limit <= 36500 )) || fail 'Enter a number of days from 0 to 36500.'
    cutoff=$(( $(date +%s) - limit * 86400 ))
  else
    limit=$(prompt_setting 'Keep the newest how many local backups? (0 keeps none)' '10')
    [[ "$limit" =~ ^[0-9]{1,6}$ ]] || fail 'Enter a backup count from 0 to 100000.'
    limit=$((10#$limit))
    (( limit <= 100000 )) || fail 'Enter a backup count from 0 to 100000.'
  fi
  resolved_local=$(realpath -m -- "$LOCAL")
  for file in "$BACKUPS"/local-*.kdbx "$BACKUPS"/upload-*.kdbx; do
    [[ -f "$file" && ! -L "$file" ]] || continue
    name=${file##*/}
    prunable_backup_name "$name" || continue
    [[ "$(realpath -m -- "$file")" != "$resolved_local" && ! "$file" -ef "$LOCAL" ]] || continue
    timestamp=$(stat -c '%Y' -- "$file") || fail "Cannot inspect backup: $file"
    if [[ "$policy" == age ]]; then
      if (( timestamp < cutoff )); then maintenance_add_file "$file"; fi
    else
      entries+=("$timestamp"$'\t'"$file")
    fi
  done
  if [[ "$policy" == count && "${#entries[@]}" != 0 ]]; then
    command -v sort >/dev/null || fail 'Backup retention requires sort (GNU coreutils).'
    sorted=$(printf '%s\n' "${entries[@]}" | LC_ALL=C sort -t $'\t' -k1,1nr -k2,2) || fail 'Cannot order local backups.'
    mapfile -t ordered <<<"$sorted"
    for entry in "${ordered[@]}"; do
      if (( count >= limit )); then maintenance_add_file "${entry#*$'\t'}"; fi
      count=$((count + 1))
    done
  fi
  if [[ "${#MAINT_FILES[@]}" == 0 ]]; then ui_ok 'No eligible local backups selected for deletion.'; return 0; fi
  if [[ "$policy" == age ]]; then ui_warn "Local backups older than $limit days selected for deletion:"
  else ui_warn "Local backups selected for deletion; keeping the newest $limit (mtime, then filename):"; fi
  ui_paths "${MAINT_FILES[@]}"
  ui_note 'Cloud inspection copies, switch backups, and recovery archives are kept. No cloud files are contacted.'
  confirm_action "Delete these ${#MAINT_FILES[@]} local backups?" || return 130
  maintenance_remove_files
  ui_ok "Removed ${#MAINT_FILES[@]} local backups. Working database, sync records, and cloud files were kept."
}
cleanup_backups() {
  local choice
  while :; do
    ui_menu 'Local backup cleanup' \
      '1) Delete local backups older than a number of days' \
      '2) Keep only the newest number of local backups' \
      '' 'H) Help' '0) Back / cancel' >&2
    printf '  Choice [0-2, H]: ' >&2
    IFS= read -r choice || return 0
    case "$choice" in
      0|q|Q|'') ui_info 'Cancelled; no files were removed.'; return 0 ;;
      1|2) break ;;
      h|H|'?') show_menu_help cleanup ;;
      *) ui_warn 'Choose 0, 1, 2, or H for help.' ;;
    esac
  done
  maintenance_lock
  load_configuration || fail 'Fix the saved settings before cleaning backups.'
  if [[ "$choice" == 1 ]]; then prune_backups age; else prune_backups count; fi
}
uninstall_launcher() {
  local file configured_local='' resolved_file choice=1 FULL_LOCAL=''
  local -a settings=() MAINT_FILES=() MAINT_IDENTITIES=() MAINT_DIRS=()
  if [[ "${1:-}" != keep-data ]]; then
    while :; do
      ui_menu 'Uninstall launcher and/or remove local data' \
        '1) Uninstall launcher and settings (keep local database and backups)' \
        '2) Delete local database, backups, and setup (keep launcher)' \
        '3) Complete uninstall (launcher, settings, local database, and backups)' \
        '' 'H) Help' '0) Cancel' >&2
      printf '  Choice [0-3, H]: ' >&2
      IFS= read -r choice || choice=0
      case "$choice" in
        0|q|Q|'') ui_info 'Cancelled; no files were removed.'; return 130 ;;
        1|2|3) break ;;
        h|H|'?') show_menu_help uninstall ;;
        *) ui_warn 'Choose 0, 1, 2, 3, or H for help.' ;;
      esac
    done
  fi
  if [[ "$choice" == 2 ]]; then
    maintenance_lock
    full_local_cleanup
    return
  fi
  ui_heading 'Uninstall launcher'
  maintenance_lock
  # Ordinary uninstall still works with malformed settings; use any absolute
  # second line only to protect a database placed at an installation path.
  if [[ -f "$CONFIG_FILE" && ! -L "$CONFIG_FILE" ]]; then
    mapfile -t settings <"$CONFIG_FILE" || fail 'Cannot inspect the saved settings.'
    if [[ "${#settings[@]}" -ge 2 && "${settings[1]}" == /* ]]; then configured_local=$(realpath -m -- "${settings[1]}"); fi
  fi
  for file in "$INSTALLED_SCRIPT" "$DESKTOP_FILE" "$CONFIG_FILE"; do
    [[ -e "$file" || -L "$file" ]] || continue
    resolved_file=$(realpath -m -- "$file")
    [[ -z "$configured_local" || "$resolved_file" != "$configured_local" ]] || fail "Installation file overlaps the configured database; preserve it manually: $file"
    maintenance_add_file "$file"
  done
  if [[ "$choice" == 3 ]]; then
    collect_full_cleanup
    maintenance_add_file "$INSTALLED_SCRIPT"
    maintenance_add_file "$DESKTOP_FILE"
    preview_full_cleanup
    confirm_action 'Permanently delete these local files and uninstall?' || return 130
  else
    ui_warn 'Installation files selected for removal:'
    if [[ "${#MAINT_FILES[@]}" != 0 ]]; then ui_paths "${MAINT_FILES[@]}"; else ui_info 'No installed launcher files were found.'; fi
    ui_note 'Databases, backups, sync records, rclone credentials, packages, and source files are kept.'
    confirm_action 'Uninstall the launcher?' || return 130
  fi
  maintenance_remove_files
  MAINT_DIRS+=("$CONFIG_DIR")
  remove_empty_maintenance_dirs
  ui_ok 'Launcher uninstalled. Run the source script again to set it up.'
}
reset_selection() {
  local backup='' archive file destination sourcehash copiedhash FULL_LOCAL='' previous_local
  local -a MAINT_FILES=() MAINT_IDENTITIES=() MAINT_DIRS=()
  ui_heading 'Back up local database and select another cloud file'
  command -v pgrep >/dev/null || fail 'Changing the cloud selection requires pgrep (procps) to check that KeePassXC is closed.'
  load_configuration || fail 'Correct the saved settings before switching cloud files.'
  [[ -f "$CONFIG_FILE" ]] || fail 'No saved selection exists. Run --install to configure a cloud database.'
  maintenance_lock
  maintenance_saved_local
  previous_local="$FULL_LOCAL"
  validate_remote_path
  set_database_paths
  maintenance_add_file "$CONFIG_FILE"
  for file in "$STATE" "$IDENTITY" "$CONFLICT" "$TRANSACTION" "$RESTORE_REVIEW"; do maintenance_add_file "$file"; done
  if [[ -e "$FULL_LOCAL" || -L "$FULL_LOCAL" ]]; then
    maintenance_plain_path "$FULL_LOCAL" && [[ -f "$FULL_LOCAL" ]] || fail "Cannot safely back up the working database: $FULL_LOCAL"
  fi
  ui_detail 'Current cloud file' "$REMOTE"
  ui_detail 'Working database' "$FULL_LOCAL"
  ui_detail 'Safety backups and recovery archives' "$BACKUPS"
  ui_warn 'The working database will be backed up and removed from its current path. Saved selection, file ID, and active recovery records will be archived before fresh setup.'
  ui_warn 'Selecting a restored cloud backup accepts that file as the new initial source. Existing cloud files will not be changed.'
  confirm_action 'Back up this selection and start fresh cloud setup?' || return 130
  mkdir -p "$BACKUPS"
  if [[ -f "$FULL_LOCAL" ]]; then
    sourcehash=$(hash_file "$FULL_LOCAL") || fail 'Cannot read the working database; selection was kept.'
    backup=$(mktemp "$BACKUPS/switch-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXXXX.kdbx") || fail 'Cannot create the safety backup; selection was kept.'
    cp -- "$FULL_LOCAL" "$backup" || fail "Cannot complete the safety backup; original selection was kept: $backup"
    copiedhash=$(hash_file "$backup") || fail 'Cannot verify the safety backup; original selection was kept.'
    [[ "$copiedhash" == "$sourcehash" && "$(hash_file "$FULL_LOCAL")" == "$sourcehash" ]] ||
      fail "Safety backup verification failed; original selection was kept: $backup"
    ui_ok 'Local database safety backup verified.'
    ui_detail 'Keep this safety backup' "$backup"
  else
    ui_info 'Working database is absent; saved settings and recovery records will still be archived.'
  fi
  archive=$(mktemp -d "$BACKUPS/reset-XXXXXXXX") || fail 'Cannot create the recovery archive; original selection was kept.'
  for file in "${MAINT_FILES[@]}"; do
    destination="$archive/${file##*/}"
    cp -- "$file" "$destination" || fail "Cannot archive selection; originals were kept. Archive: $archive"
    sourcehash=$(hash_file "$file") || fail 'Cannot verify the recovery archive; originals were kept.'
    copiedhash=$(hash_file "$destination") || fail 'Cannot verify the recovery archive; originals were kept.'
    [[ "$sourcehash" == "$copiedhash" ]] || fail "Recovery archive verification failed; originals were kept: $archive"
  done
  maintenance_verify_files
  if [[ -n "$backup" ]]; then
    [[ "$(hash_file "$FULL_LOCAL")" == "$(hash_file "$backup")" ]] || fail 'Working database changed; original selection was kept.'
    rm -- "$FULL_LOCAL"
  fi
  maintenance_remove_files
  ui_ok 'Previous selection archived; starting fresh setup.'
  ui_detail 'Recovery archive (includes previous settings)' "$archive"
  if [[ -n "$backup" ]]; then ui_detail 'Previous database backup' "$backup"; fi
  ui_note 'If setup is cancelled or fails, these backups remain. Rerun --install, or restore the database and archived settings manually.'
  ui_note 'Existing sync records for another selected target are preserved. If setup reports a missing previously synchronized database, restore that target selection before continuing.'
  # Empty remote forces an explicit choice; Enter cannot quietly reselect the
  # replaced Drive object. The previous local destination is safe to reuse now.
  REMOTE=''
  LOCAL="$previous_local"
  install_launcher
}
# Focused settings, recovery, and read-only diagnostics for the task menus.
management_prompt() {
  local answer
  ui_info "$1" >&2
  [[ -z "${2:-}" ]] || ui_detail 'Current value' "$2" >&2
  printf '    Enter value (0 cancels%s): ' "${2:+; Enter keeps current value}" >&2
  if ! IFS= read -r answer; then ui_info 'Cancelled.' >&2; return 130; fi
  if [[ "$answer" == 0 ]]; then ui_info 'Cancelled.' >&2; return 130; fi
  printf '%s' "${answer:-${2:-}}"
}
management_confirm() {
  local answer
  printf '\n  %s [y/N]: ' "$1" >&2
  if IFS= read -r answer && [[ "${answer,,}" == y || "${answer,,}" == yes ]]; then return 0; fi
  ui_info 'Cancelled; files were kept.'
  return 130
}
management_configuration() {
  [[ -f "$CONFIG_FILE" ]] || fail 'No saved settings exist. Run --install to configure this launcher first.'
  maintenance_plain_path "$CONFIG_FILE" || fail 'Refusing to use settings through a symlinked path.'
  load_configuration || fail 'Correct the saved settings before this action.'
  validate_paths
  [[ "$(realpath -m -- "$LOCAL")" != "$(realpath -m -- "$SCRIPT_PATH")" && ! "$LOCAL" -ef "$SCRIPT_PATH" ]] ||
    fail 'The configured database overlaps this source script; preserve it manually before maintenance.'
  set_database_paths
}
management_database_lock() {
  local path
  command -v pgrep >/dev/null || fail 'This action requires pgrep (procps) to check that KeePassXC is closed.'
  for path in "$DATA_DIR" "$BACKUPS" "$LOCK"; do
    maintenance_plain_path "$path" || fail 'Refusing maintenance through a symlinked state, backup, or lock path.'
  done
  maintenance_lock
}
management_require_settled() {
  local file
  for file in "$CONFLICT" "$TRANSACTION" "$RESTORE_REVIEW"; do
    [[ ! -e "$file" && ! -L "$file" ]] ||
      fail "Resolve the pending conflict, interrupted upload, or restored database review before changing this selection: $file"
  done
}
save_configuration() {
  local tmp
  maintenance_plain_path "$CONFIG_FILE" || fail 'Refusing to write settings through a symlinked path.'
  [[ ! -e "$CONFIG_FILE" || -f "$CONFIG_FILE" ]] || fail 'Saved settings are not a regular file.'
  mkdir -p "$CONFIG_DIR" || fail 'Cannot create the settings directory.'
  tmp=$(mktemp "$CONFIG_DIR/.config.XXXXXXXX") || fail 'Cannot prepare updated settings.'
  if ! printf '%s\n%s\n%s\n' "$REMOTE" "$LOCAL" "$REMOTE_BACKUP_FOLDER" >"$tmp" ||
     ! chmod 600 "$tmp" || ! mv -fT -- "$tmp" "$CONFIG_FILE"; then
    rm -f -- "$tmp"
    fail 'Cannot save updated settings; previous settings were kept.'
  fi
}
show_settings() {
  load_configuration || fail 'Correct the saved settings before viewing them.'
  validate_paths
  set_database_paths
  ui_heading 'Current settings (offline)'
  [[ -f "$CONFIG_FILE" ]] || ui_info 'No saved settings; script defaults are shown.'
  ui_detail 'Cloud database' "$REMOTE"
  ui_detail 'Local database' "$LOCAL"
  ui_detail 'Cloud backup folder name' "$REMOTE_BACKUP_FOLDER"
  ui_detail 'Cloud backups' "$REMOTE_BACKUP_DIR"
  ui_detail 'Local backups and recovery archives' "$BACKUPS"
  ui_detail 'Saved settings' "$CONFIG_FILE"
  ui_detail 'Installed launcher' "$INSTALLED_SCRIPT"
  ui_note 'Google Drive was not contacted.'
}
change_backup_folder() {
  local answer status
  management_configuration
  management_database_lock
  management_require_settled
  answer=$(management_prompt 'Cloud backup folder name (beside the database)' "$REMOTE_BACKUP_FOLDER") || { status=$?; return "$status"; }
  REMOTE_BACKUP_FOLDER="$answer"
  validate_backup_folder
  set_remote_paths
  save_configuration
  ui_ok 'Cloud backup folder setting saved.'
  ui_detail 'Future cloud backups' "$REMOTE_BACKUP_DIR"
  ui_note 'Existing cloud backups were kept in their previous folder. No cloud connection was made.'
}
move_local_database() {
  local source target tmp sourcehash copiedhash status
  management_configuration
  management_database_lock
  management_require_settled
  source="$LOCAL"
  maintenance_plain_path "$source" && [[ -f "$source" ]] || fail 'The current local database must be a regular file without symlinked parents.'
  target=$(management_prompt 'New absolute local database path' "$source") || { status=$?; return "$status"; }
  [[ "$target" != '~/'* ]] || target="$HOME/${target:2}"
  [[ "$target" == /* && "$target" != */ && ! "$target" =~ [[:cntrl:]] ]] || fail 'Enter an absolute destination filename without control characters.'
  maintenance_plain_path "$target" || fail 'Refusing to move the database through a symlinked path.'
  target=$(realpath -ms -- "$target")
  if [[ "$target" == "$(realpath -ms -- "$source")" ]]; then ui_info 'Local database path is unchanged.'; return 0; fi
  [[ ! -e "$target" && ! -L "$target" ]] || fail "Destination already exists; choose a new filename: $target"
  LOCAL="$target"
  validate_paths
  [[ "$target" != "$(realpath -m -- "$SCRIPT_PATH")" ]] || fail 'The destination overlaps this source script.'
  ui_detail 'Current database' "$source"
  ui_detail 'New database' "$target"
  ui_note 'The original is removed only after a verified copy and saved settings update.'
  management_confirm 'Move the local database to this path?' || return 130
  sourcehash=$(hash_file "$source") || fail 'Cannot read the current database; settings were kept.'
  mkdir -p -- "$(dirname "$target")" || fail 'Cannot create the destination folder; original database and settings were kept.'
  maintenance_plain_path "$target" || fail 'The destination path changed; original database and settings were kept.'
  tmp=$(mktemp "$(dirname "$target")/.keesyncxc-move.XXXXXXXX") || fail 'Cannot prepare the moved database.'
  if ! cp -- "$source" "$tmp" || ! chmod 600 "$tmp"; then
    rm -f -- "$tmp"; fail 'Cannot copy the database; original database and settings were kept.'
  fi
  copiedhash=$(hash_file "$tmp") || { rm -f -- "$tmp"; fail 'Cannot verify the moved database; original database and settings were kept.'; }
  if [[ "$copiedhash" != "$sourcehash" || "$(hash_file "$source")" != "$sourcehash" ]]; then
    rm -f -- "$tmp"; fail 'The database changed or its copy failed verification; original database and settings were kept.'
  fi
  # A hard-link claim makes publication atomic and refuses an existing target.
  if ! ln -T -- "$tmp" "$target"; then
    rm -f -- "$tmp"; fail 'Cannot claim the destination; original database and settings were kept.'
  fi
  rm -f -- "$tmp"
  if ! maintenance_plain_path "$target" || [[ ! -f "$target" || "$(hash_file "$target")" != "$sourcehash" ]]; then
    fail 'The destination changed after verification; original database and settings were kept.'
  fi
  save_configuration
  # Settings now select the verified copy. A changed source is kept for review.
  if ! maintenance_plain_path "$target" || [[ ! -f "$target" || "$(hash_file "$target")" != "$sourcehash" ]]; then
    ui_warn "Settings select the new path, but its contents changed. The original was kept: $source"
    return 1
  fi
  if ! maintenance_plain_path "$source" || [[ ! -f "$source" || "$(hash_file "$source")" != "$sourcehash" ]]; then
    ui_warn "Settings select the new database, but the changed original was kept: $source"
    return 1
  fi
  rm -- "$source" || fail "Settings select the verified new database, but the original could not be removed: $source"
  ui_ok 'Local database moved and settings saved.'
  ui_detail 'Local database' "$LOCAL"
  ui_note 'Synchronization history and Drive identity were kept. Google Drive was not contacted.'
}
manage_drive_connection() {
  management_configuration
  management_database_lock
  command -v rclone >/dev/null || fail 'rclone is required. Check/install required system tools first.'
  configure_rclone
  ui_ok 'Drive connection settings reviewed. No database transfers were requested.'
}
check_cloud_connection() {
  local metadata cloudhash
  # This diagnostic deliberately does not acquire a creating lock or pin an ID.
  load_configuration || fail 'Correct the saved settings before checking the connection.'
  validate_paths
  set_database_paths
  command -v rclone >/dev/null || fail 'rclone is required to check the cloud connection.'
  ui_heading 'Google Drive connection check (read only)'
  ui_detail 'Cloud database' "$REMOTE"
  cloudhash=$(lookup_database) || fail 'Cannot verify the selected Drive database; check the connection, path, and saved file ID.'
  [[ "$cloudhash" != MISSING ]] || fail 'The selected Google Drive database is missing.'
  metadata=$(check_identity "$cloudhash" 0) || fail 'Cannot verify the selected Google Drive file ID and checksum.'
  ui_ok 'Google Drive connection and selected database verified.'
  ui_detail 'Drive file ID' "$metadata"
  ui_detail 'Cloud database MD5' "$cloudhash"
  ui_note 'No database was transferred. Local settings, synchronization records, and cloud files were kept.'
}
list_cloud_backups() {
  local listing
  load_configuration || fail 'Correct the saved settings before listing cloud backups.'
  validate_paths
  set_database_paths
  command -v rclone >/dev/null || fail 'rclone is required to list cloud backups.'
  ui_heading 'Cloud backups (read only)'
  ui_detail 'Cloud backup folder' "$REMOTE_BACKUP_DIR"
  ui_note 'Listing size, modified time, and filename. A missing folder has no available listing.'
  listing=$(rclone lsf "$REMOTE_BACKUP_DIR" --files-only --max-depth 1 --format stp --csv --separator ',' --retries 2) ||
    fail 'Cannot list cloud backups. The folder may not exist yet, or the connection may be unavailable.'
  ui_csv_listing "$listing" || fail 'Cannot safely display the cloud backup listing.'
  ui_note 'No database was transferred or modified. Local synchronization records were kept.'
}
show_backup_inventory() {
  local file child name count=0 archives=0 purpose metadata
  ui_heading 'Local backups and recovery archives'
  ui_detail 'Local backups' "$BACKUPS"
  maintenance_plain_path "$BACKUPS" || fail 'Refusing to list a symlinked backup directory.'
  for file in "$BACKUPS"/*; do
    if [[ -f "$file" && ! -L "$file" ]]; then
      name=${file##*/}
      case "$name" in
        local-*.kdbx) purpose='local safety copy' ;;
        upload-*.kdbx) purpose='upload snapshot' ;;
        cloud-*.kdbx) purpose='downloaded cloud inspection copy' ;;
        switch-*.kdbx) purpose='database selection safety copy' ;;
        interrupted-upload-*.txt) purpose='interrupted upload recovery record' ;;
        *) purpose='other file' ;;
      esac
      metadata=$(stat -c '%y | %s bytes' -- "$file") || fail "Cannot inspect backup: $file"
      printf '  %s\n    %s | %s\n' "$(ui_escape "$file")" "$metadata" "$purpose"
      count=$((count + 1))
    elif [[ "${file##*/}" =~ ^reset-[a-zA-Z0-9]{8}$ && -d "$file" && ! -L "$file" ]]; then
      maintenance_plain_path "$file" || continue
      metadata=$(stat -c '%y' -- "$file") || fail "Cannot inspect recovery archive: $file"
      printf '  %s\n    %s | previous settings and synchronization records\n' "$(ui_escape "$file")" "$metadata"
      for child in "$file"/*; do
        [[ -f "$child" && ! -L "$child" ]] || continue
        if [[ "${child##*/}" == config ]] || recognized_state_name "${child##*/}"; then
          printf '    %s (%s bytes)\n' "$(ui_escape "${child##*/}")" "$(stat -c '%s' -- "$child")"
        fi
      done
      archives=$((archives + 1))
    fi
  done
  ui_info "$count local backup or recovery files; $archives recovery archives. Cloud files were not contacted."
}
open_backup_folder() {
  maintenance_plain_path "$BACKUPS" && [[ -d "$BACKUPS" ]] || fail "A regular local backup folder does not exist yet: $BACKUPS"
  command -v xdg-open >/dev/null || fail 'Opening the backup folder requires xdg-open.'
  xdg-open "$BACKUPS" || fail 'Cannot open the local backup folder.'
}
select_local_backup() {
  local purpose="${1:-inspect}" file choice metadata index=0
  local -a candidates=()
  maintenance_plain_path "$BACKUPS" || fail 'Refusing to select backups through a symlinked path.'
  ui_heading 'Select a local database backup' >&2
  ui_note 'Backups can belong to previous cloud selections. Verify which database you intend to use.' >&2
  for file in "$BACKUPS"/*.kdbx; do
    [[ -f "$file" && ! -L "$file" ]] || continue
    recognized_backup_name "${file##*/}" || continue
    maintenance_plain_path "$file" || continue
    # Never offer the configured working file as a backup, including hard links.
    [[ ! -f "$LOCAL" || ! "$file" -ef "$LOCAL" ]] || continue
    candidates+=("$file")
    index=$((index + 1))
    metadata=$(stat -c '%y | %s bytes' -- "$file") || fail "Cannot inspect backup: $file"
    printf '  %s) %s\n     %s\n' "$index" "$(ui_escape "${file##*/}")" "$metadata" >&2
  done
  if [[ "${#candidates[@]}" == 0 ]]; then ui_info 'No recognized local database backups are available.' >&2; return 130; fi
  while :; do
    printf '\n  H) Help\n  0) Cancel\n  Backup number: ' >&2
    if ! IFS= read -r choice || [[ -z "$choice" || "$choice" == 0 ]]; then ui_info 'Cancelled.' >&2; return 130; fi
    if [[ "$choice" == h || "$choice" == H || "$choice" == '?' ]]; then show_menu_help "$purpose" >&2; continue; fi
    if [[ "$choice" =~ ^[0-9]{1,6}$ ]]; then
      index=$((10#$choice))
      if (( index >= 1 && index <= ${#candidates[@]} )); then break; fi
    fi
    ui_warn 'Enter a displayed backup number, H for help, or 0 to cancel.' >&2
  done
  file=${candidates[index - 1]}
  maintenance_plain_path "$file" && [[ -f "$file" ]] || fail 'Selected backup is no longer a regular file.'
  printf '%s' "$file"
}
inspect_local_backup() {
  local selected status before_hash after_hash
  load_configuration || fail 'Correct the saved settings before inspecting a backup.'
  management_database_lock
  command -v keepassxc >/dev/null || fail 'KeePassXC is required to inspect a database backup.'
  selected=$(select_local_backup inspect) || { status=$?; return "$status"; }
  before_hash=$(hash_file "$selected") || fail 'Cannot read the selected backup.'
  ui_info "Opening backup in KeePassXC: $selected"
  ui_note 'This is a backup copy. Changes saved here affect this backup only; no synchronization follows.'
  ui_note 'Use the database credentials that were valid when this backup was created. Fully quit KeePassXC to return.'
  keepassxc "$selected" || { ui_warn 'KeePassXC exited with an error; the backup was not synchronized.'; return 1; }
  after_hash=$(hash_file "$selected") || fail 'The inspected backup became unavailable.'
  if [[ "$before_hash" != "$after_hash" ]]; then ui_warn 'Changes were saved to this backup copy.'; fi
  ui_ok 'Backup inspection finished. No synchronization was performed.'
}
# CSV keeps commas, quotes, and embedded newlines inside a filename together.
# IDs select Drive objects; remote names are displayed but never used as paths.
cloud_backup_csv_row() {
  [[ "$#" == 5 ]] || return 1
  local id="$1" size="$2" modified="$3" name="$4" hash="$5" previous
  [[ "$name" == *.kdbx ]] || return 0
  [[ "$id" =~ ^[a-zA-Z0-9_-]+$ && "$size" =~ ^[0-9]+$ &&
     "$hash" =~ ^[a-fA-F0-9]{32}$ ]] || return 1
  for previous in "${CLOUD_IDS[@]}"; do [[ "$previous" != "$id" ]] || return 1; done
  CLOUD_IDS+=("$id") CLOUD_SIZES+=("$size") CLOUD_TIMES+=("$modified")
  CLOUD_NAMES+=("$name") CLOUD_HASHES+=("${hash,,}")
}
parse_cloud_backup_csv() {
  local csv="$1" field='' char next quoted=0 start=1 closed=0 i
  local -a fields=()
  for ((i = 0; i < ${#csv}; i++)); do
    char=${csv:i:1}
    if [[ "$char" == '"' ]]; then
      if (( quoted )); then
        next=${csv:i+1:1}
        if [[ "$next" == '"' ]]; then field+='"'; i=$((i + 1)); else quoted=0; closed=1; fi
      elif (( start && ! closed )); then
        quoted=1
      else
        return 1
      fi
      start=0
    elif [[ "$quoted" == 0 && ( "$char" == ',' || "$char" == $'\n' ) ]]; then
      fields+=("$field")
      field='' start=1 closed=0
      if [[ "$char" == $'\n' ]]; then
        cloud_backup_csv_row "${fields[@]}" || return 1
        fields=()
      fi
    else
      (( closed == 0 )) || return 1
      field+="$char"; start=0
    fi
  done
  (( quoted == 0 )) || return 1
  if [[ "${#fields[@]}" != 0 || "$start" == 0 ]]; then
    fields+=("$field")
    cloud_backup_csv_row "${fields[@]}" || return 1
  fi
}
load_cloud_backup_choices() {
  local listing
  CLOUD_IDS=() CLOUD_SIZES=() CLOUD_TIMES=() CLOUD_NAMES=() CLOUD_HASHES=()
  listing=$(rclone lsf "$REMOTE_BACKUP_DIR" --files-only --max-depth 1 --format istph \
    --hash MD5 --csv --separator ',' --drive-skip-shortcuts --retries 2) || {
    ui_error 'Cannot list cloud backups. Check the connection and configured backup folder.'; return 1;
  }
  parse_cloud_backup_csv "$listing" || {
    ui_error 'Cloud backup listing has invalid records, file IDs, sizes, or MD5 checksums; inspection stopped.'; return 1;
  }
}
select_cloud_backup() {
  local index choice
  ui_heading 'Select a cloud database backup' >&2
  ui_detail 'Cloud backup folder' "$REMOTE_BACKUP_DIR" >&2
  ui_note 'Backups may belong to other databases. Check the filename and date before selecting.' >&2
  for index in "${!CLOUD_IDS[@]}"; do
    printf '  %s) %s\n     %s | %s bytes\n' "$((index + 1))" "$(ui_escape "${CLOUD_NAMES[index]}")" \
      "$(ui_escape "${CLOUD_TIMES[index]}")" "${CLOUD_SIZES[index]}" >&2
  done
  if [[ "${#CLOUD_IDS[@]}" == 0 ]]; then ui_info 'No cloud database backups are available.' >&2; return 130; fi
  while :; do
    printf '\n  H) Help\n  0) Cancel\n  Backup number: ' >&2
    if ! IFS= read -r choice || [[ -z "$choice" || "$choice" == 0 ]]; then ui_info 'Cancelled.' >&2; return 130; fi
    if [[ "$choice" == h || "$choice" == H || "$choice" == '?' ]]; then show_menu_help inspect-cloud >&2; continue; fi
    if [[ "$choice" =~ ^[0-9]{1,6}$ ]]; then
      index=$((10#$choice))
      if (( index >= 1 && index <= ${#CLOUD_IDS[@]} )); then printf '%s' "$((index - 1))"; return 0; fi
    fi
    ui_warn 'Enter a displayed backup number, H for help, or 0 to cancel.' >&2
  done
}
local_backup_matches() {
  local file="$1" expected="$2" size="$3" hash
  [[ -f "$file" && ! -L "$file" && -r "$file" ]] && maintenance_plain_path "$file" || return 1
  [[ "$(stat -c '%s' -- "$file" 2>/dev/null)" == "$size" ]] || return 1
  hash=$(hash_file "$file" 2>/dev/null) || return 1
  [[ "$hash" == "$expected" ]]
}
reusable_cloud_inspection_copy() {
  local file="$1"
  [[ "${file##*/}" =~ ^cloud-[0-9]{8}T[0-9]{6}Z-[a-zA-Z0-9]{8}\.kdbx$ &&
     -f "$file" && ! -L "$file" ]] && maintenance_plain_path "$file" || return 1
  [[ "$(stat -c '%u:%a:%h' -- "$file")" == "$EUID:600:1" &&
     ! "$file" -ef "$LOCAL" && ! "$file" -ef "$SCRIPT_PATH" ]]
}
find_matching_local_backup() {
  local expected="$1" size="$2" file
  # Prefer an existing private inspection copy, avoiding repeated local copies.
  for file in "$BACKUPS"/cloud-*.kdbx; do
    if reusable_cloud_inspection_copy "$file" && local_backup_matches "$file" "$expected" "$size"; then
      printf '%s' "$file"; return 0
    fi
  done
  # Other matching files are copied before editing, including the working file.
  for file in "$LOCAL" "$BACKUPS"/*.kdbx; do
    [[ "$file" == "$LOCAL" ]] || recognized_backup_name "${file##*/}" || continue
    if local_backup_matches "$file" "$expected" "$size"; then printf '%s' "$file"; return 0; fi
  done
  return 1
}
inspect_cloud_backup() {
  local selected status id expected size stage tmp got index count=0 copy='' after_hash source=''
  local -a CLOUD_IDS=() CLOUD_SIZES=() CLOUD_TIMES=() CLOUD_NAMES=() CLOUD_HASHES=()
  management_configuration
  management_database_lock
  command -v rclone >/dev/null || fail 'Cloud backup inspection requires rclone 1.54 or newer.'
  command -v keepassxc >/dev/null || fail 'KeePassXC is required to inspect a cloud backup.'
  load_cloud_backup_choices || return 1
  selected=$(select_cloud_backup) || { status=$?; return "$status"; }
  id=${CLOUD_IDS[selected]} expected=${CLOUD_HASHES[selected]} size=${CLOUD_SIZES[selected]}
  ui_heading 'Inspect a cloud backup'
  ui_detail 'Selected cloud backup' "${CLOUD_NAMES[selected]}"
  ui_note 'A verified copy will be kept in the local backup folder. Saved edits affect that copy; no synchronization follows.'
  mkdir -p -- "$BACKUPS" || fail 'Cannot create the local backup folder.'
  private_storage_directory "$BACKUPS"
  ui_wait 'Checking local database and backup MD5s before downloading...'
  if source=$(find_matching_local_backup "$expected" "$size"); then
    ui_ok 'Matching local MD5 found; no cloud download is needed.'
    ui_detail 'Matching local file' "$source"
    if reusable_cloud_inspection_copy "$source"; then copy="$source"; fi
  else
    source=''
  fi
  stage=$(mktemp -d "$BACKUPS/.keesyncxc-download.XXXXXXXX") || fail 'Cannot prepare private inspection staging.'
  tmp="$stage/database.kdbx"
  if ! chmod 700 -- "$stage" || [[ -L "$stage" || "$(stat -c '%u:%a' -- "$stage")" != "$EUID:700" ]]; then
    cleanup_download_stage "$stage" || true; fail 'Cannot secure the inspection staging directory.'
  fi
  if [[ -n "$copy" ]]; then
    tmp="$copy"
    ui_info 'Reusing the existing local inspection copy.'
  elif [[ -n "$source" ]]; then
    if ! cp -- "$source" "$tmp"; then
      cleanup_download_stage "$stage" || true; fail 'Cannot prepare a separate inspection copy from the matching local file.'
    fi
  else
    ui_wait 'Downloading the selected cloud backup by file ID...'
    if ! RCLONE_METADATA=false rclone backend copyid "${REMOTE%%:*}:" "$id" "$tmp" --retries 2 --drive-skip-shortcuts; then
      cleanup_download_stage "$stage" || true; fail 'Cloud backup download failed. File-ID downloads require rclone 1.54 or newer.'
    fi
  fi
  if [[ ! -f "$tmp" || -L "$tmp" ]] || { [[ -z "$copy" ]] && ! chmod 600 -- "$tmp"; } ||
     [[ "$(stat -c '%u:%a:%h' -- "$tmp")" != "$EUID:600:1" ]]; then
    cleanup_download_stage "$stage" || true; fail 'Cannot secure the downloaded inspection copy.'
  fi
  got=$(hash_file "$tmp") || { cleanup_download_stage "$stage" || true; fail 'Cannot verify the downloaded backup.'; }
  if [[ "$got" != "$expected" ]] || ! load_cloud_backup_choices; then
    cleanup_download_stage "$stage" || true; fail 'Cloud backup inspection copy failed verification; no database was opened.'
  fi
  for index in "${!CLOUD_IDS[@]}"; do
    if [[ "${CLOUD_IDS[index]}" == "$id" && "${CLOUD_HASHES[index]}" == "$expected" ]]; then count=$((count + 1)); fi
  done
  if [[ "$count" != 1 ]]; then
    cleanup_download_stage "$stage" || true; fail 'The selected cloud backup changed or left its folder; no database was opened.'
  fi
  if [[ -z "$copy" ]]; then
    copy=$(mktemp "$BACKUPS/cloud-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXXXX.kdbx") || {
      cleanup_download_stage "$stage" || true; fail 'Cannot retain the verified inspection copy.';
    }
    if ! mv -fT -- "$tmp" "$copy"; then
      rm -f -- "$copy"; cleanup_download_stage "$stage" || true; fail 'Cannot retain the verified inspection copy.'
    fi
  fi
  cleanup_download_stage "$stage" || fail 'The inspection copy was retained, but staging cleanup failed.'
  if ! reusable_cloud_inspection_copy "$copy" || [[ "$(hash_file "$copy")" != "$expected" ]]; then
    fail 'The local inspection copy changed before it could be opened; inspect its contents and retry.'
  fi
  ui_detail 'Local inspection copy' "$copy"
  ui_note 'Use the credentials valid when this backup was created. Fully quit KeePassXC to return.'
  ui_note 'To use this copy as the working database later, choose Restore a local backup; publication requires separate confirmation.'
  keepassxc "$copy" || { ui_warn "KeePassXC exited with an error. The inspection copy is kept: $copy"; return 1; }
  after_hash=$(hash_file "$copy") || fail "The inspection copy became unavailable: $copy"
  if [[ "$after_hash" != "$expected" ]]; then ui_warn 'Edits were saved to the local inspection copy. Cloud files were not changed.'; fi
  ui_ok 'Cloud backup inspection finished. The local copy was kept; no synchronization was performed.'
}
restore_local_backup() {
  local selected status baseline backuphash currenthash='' safety='' tmp copiedhash
  management_configuration
  management_database_lock
  management_require_settled
  maintenance_plain_path "$LOCAL" || fail 'Refusing to restore through a symlinked database path.'
  maintenance_plain_path "$STATE" && [[ -f "$STATE" ]] || fail 'Restore requires a confirmed synchronization baseline. Complete first synchronization before restoring a backup.'
  baseline=$(cat "$STATE") || fail 'Cannot read the synchronization baseline.'
  [[ "$baseline" =~ ^[a-fA-F0-9]{32}$ ]] || fail 'The synchronization baseline is invalid; investigate it before restoring.'
  if [[ -e "$IDENTITY" || -L "$IDENTITY" ]]; then
    maintenance_plain_path "$IDENTITY" && [[ -f "$IDENTITY" ]] || fail 'The saved Drive identity is unsafe; investigate it before restoring.'
    [[ "$(cat "$IDENTITY")" =~ ^[a-zA-Z0-9_-]+$ ]] || fail 'The saved Drive identity is invalid; investigate it before restoring.'
  fi
  selected=$(select_local_backup restore) || { status=$?; return "$status"; }
  backuphash=$(hash_file "$selected") || fail 'Cannot read the selected backup.'
  if [[ -f "$LOCAL" ]]; then currenthash=$(hash_local) || fail 'Cannot read the working database; restore stopped.'; fi
  if [[ "$currenthash" == "$backuphash" ]]; then ui_info 'The working database already matches this backup.'; return 0; fi
  ui_heading 'Restore local backup'
  ui_detail 'Selected backup' "$selected"
  ui_detail 'Working database to replace' "$LOCAL"
  ui_warn 'The selected backup will become the working local database. A safety copy preserves the current database when present.'
  ui_warn 'Inspect the backup before deciding to publish it. The next launch or sync asks for confirmation before uploading restored contents.'
  ui_note 'Choose No at that confirmation to stop without uploading. Approval applies to that upload attempt only.'
  ui_note 'If both copies changed, you must resolve a conflict. If only Drive changed, its version is downloaded instead.'
  ui_note 'To review a backup without restoring or synchronizing it, cancel and use Inspect/open a local backup.'
  ui_note 'Synchronization history and Drive identity are kept.'
  ui_note 'This action does not connect to Drive or upload anything.'
  management_confirm 'Restore this backup as the working local database?' || return 130
  if [[ -n "$currenthash" ]]; then
    mkdir -p "$BACKUPS" || fail 'Cannot prepare a safety backup; working database was kept.'
    safety=$(mktemp "$BACKUPS/local-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXXXX.kdbx") || fail 'Cannot create a safety backup; working database was kept.'
    cp -- "$LOCAL" "$safety" || fail "Cannot complete the safety backup; working database was kept: $safety"
    copiedhash=$(hash_file "$safety") || fail 'Cannot verify the safety backup; working database was kept.'
    [[ "$copiedhash" == "$currenthash" && "$(hash_local)" == "$currenthash" ]] || fail 'Safety backup verification failed; working database was kept.'
    ui_detail 'Verified safety backup' "$safety"
  fi
  mkdir -p -- "$(dirname "$LOCAL")" || fail 'Cannot prepare the working database folder.'
  tmp=$(mktemp "$(dirname "$LOCAL")/.keesyncxc-download.XXXXXXXX") || fail 'Cannot prepare the restored database.'
  if ! cp -- "$selected" "$tmp" || ! chmod 600 "$tmp"; then rm -f -- "$tmp"; fail 'Cannot copy the selected backup; working database was kept.'; fi
  copiedhash=$(hash_file "$tmp") || { rm -f -- "$tmp"; fail 'Cannot verify the restored copy; working database was kept.'; }
  if [[ "$copiedhash" != "$backuphash" || "$(hash_file "$selected")" != "$backuphash" ]]; then
    rm -f -- "$tmp"; fail 'The selected backup changed or failed verification; working database was kept.'
  fi
  maintenance_plain_path "$LOCAL" || { rm -f -- "$tmp"; fail 'The working database path changed; restore stopped.'; }
  if [[ -n "$currenthash" ]]; then
    [[ "$(hash_local)" == "$currenthash" ]] || { rm -f -- "$tmp"; fail 'The working database changed; restore stopped.'; }
  else
    [[ ! -e "$LOCAL" && ! -L "$LOCAL" ]] || { rm -f -- "$tmp"; fail 'A working database appeared; restore stopped.'; }
  fi
  # Record the required approval before replacement, so an interruption cannot
  # leave restored contents eligible for an automatic upload on the next run.
  if ! write_record "$RESTORE_REVIEW" restore-review-v1 "$LOCAL" "$backuphash"; then
    rm -f -- "$tmp"; fail 'Cannot record the required upload confirmation; working database was kept.'
  fi
  mv -fT -- "$tmp" "$LOCAL" || { rm -f -- "$tmp"; fail 'Cannot install the restored database; previous database was kept.'; }
  ui_ok 'Local backup restored. Synchronization history and Drive identity were preserved.'
  ui_note 'Inspect the backup or restored database before approving publication. An upload requires a separate confirmation.'
  ui_note 'Use Open database or Synchronize without opening KeePassXC when ready to compare with Google Drive.'
}
repair_installation() {
  [[ "$(id -u 2>/dev/null || printf '%s' "$EUID")" != 0 ]] || fail 'Run launcher repair as your regular user, without sudo.'
  if [[ ! -e "$CONFIG_FILE" && ! -L "$CONFIG_FILE" ]]; then
    ui_info 'No saved settings exist; starting first-run setup.'
    install_launcher
    return
  fi
  management_configuration
  management_database_lock
  ui_heading 'Install/repair launcher and shortcut'
  ui_note 'Keeping the saved database selection. This installs the script currently running; it does not download a new release.'
  write_installation
}

show_launcher_help() {
  ui_heading 'Using the launcher'
  ui_info '1) Open database: synchronize, open KeePassXC for editing, then synchronize after it fully exits.'
  ui_note 'Save and use Quit/Exit; closing to the system tray can delay synchronization.'
  ui_note 'Keep the progress terminal open until the final synchronization finishes.'
  ui_info '2) Status and troubleshooting: view offline status, check the Drive connection, or synchronize without opening KeePassXC.'
  ui_info '3) Backups and recovery: list or inspect backups, restore a local copy, prune local backups, or browse recovery archives.'
  ui_note 'Inspect before restoring or publishing. Restoration first backs up the working database; uploading restored contents needs confirmation.'
  ui_info '4) Settings and database selection: view settings, change the cloud backup folder, move the local database, manage the connection, or switch cloud databases.'
  ui_note 'Switching databases preserves the current local database and archives its settings and records.'
  ui_info '5) Installation and advanced maintenance: repair the launcher, install missing tools, uninstall, or delete local data after confirmation.'
  ui_info 'H) Help: explain the main menu options. Each submenu has its own H) Help explaining its actions.'
  ui_info '0) Quit: leave the main menu. In submenus, use 0 to go back or cancel.'
  ui_note 'An established local copy can open offline; saved edits synchronize on a later online run.'
  ui_note 'Use one editing device at a time. Conflicts ask which version to use.'
}
show_menu_help() {
  case "$1" in
    status)
      ui_heading 'Status and troubleshooting help'
      ui_info '1) Show local status: inspect installation, configured paths, and saved synchronization records offline.'
      ui_note 'A saved synchronization record does not confirm the current cloud contents.'
      ui_info '2) Check Google Drive connection: verify the selected cloud database and file ID with read-only requests.'
      ui_note 'This check does not transfer a database, save a file ID, or repair a connection.'
      ui_info '3) Synchronize without opening KeePassXC: download or upload changes using the normal backups and recovery checks.'
      ui_note 'Fully quit KeePassXC first. Conflicts require a decision; offline or unresolved synchronization remains incomplete.'
      ui_note 'Use Settings and database selection to manage the Drive connection.'
      ;;
    backups)
      ui_heading 'Backups and recovery help'
      ui_info '1) Show local backups and recovery archives: view dates, sizes, and the purpose of saved files offline.'
      ui_note 'Local safety copies, upload snapshots, cloud inspection copies, switch backups, and previous settings archives share this folder.'
      ui_info '2) Open local backup folder: browse existing copies in your file manager.'
      ui_info '3) List cloud backups: show the configured Drive backup folder with read-only requests.'
      ui_note 'The cloud folder is created when an upload first needs a backup; listing does not create it.'
      ui_info '4) Inspect/open a local backup: open the selected backup itself in KeePassXC without synchronization.'
      ui_note 'Edits saved during inspection change that backup only.'
      ui_info '5) Delete local backups older than N days; 6) Keep the newest N local backups.'
      ui_note 'Both preview eligible safety copies and upload snapshots, then ask for confirmation. Cloud inspection copies, switch backups, and recovery archives are kept.'
      ui_info '7) Restore a local backup: verify a safety copy of the current database before replacing it.'
      ui_note 'Restoration preserves synchronization history and Drive identity. Inspect first; the next upload requires a separate confirmation.'
      ui_note 'Backups may belong to previous cloud selections. Verify the database before restoring.'
      ui_info '8) Inspect a cloud backup: reuse matching local contents or download and verify a private copy, then open it without synchronization.'
      ui_note 'The local inspection copy is kept; edits affect that copy. The working database, cloud files, and synchronization records stay unchanged.'
      ;;
    settings)
      ui_heading 'Settings and database selection help'
      ui_info '1) Show current settings: view the selected database and backup locations offline.'
      ui_info '2) Change cloud backup folder: choose the folder beside the cloud database for future backups.'
      ui_note 'Existing backups stay in their previous folder; this action does not connect to Drive.'
      ui_info '3) Move local database: copy and verify the database, save the new path, then remove the original.'
      ui_note 'Choose an unused absolute filename. Synchronization history and Drive identity are kept.'
      ui_info '4) Manage Google Drive connection: open rclone configuration to review or edit cloud connections.'
      ui_note 'Google authentication and connection changes happen in rclone; no database transfer is requested.'
      ui_info '5) Switch cloud database: preserve the current local database and archive its settings and records before fresh setup.'
      ui_note 'The chosen existing cloud database becomes the initial source; cloud files are kept.'
      ui_note 'Fully quit KeePassXC first. Resolve pending conflicts or uploads before moving the database or changing its backup folder.'
      ;;
    maintenance)
      ui_heading 'Installation and advanced maintenance help'
      ui_info '1) Install/repair launcher and shortcut: copy this running script into the installed location while retaining saved settings.'
      ui_note 'It does not download a newer release. With no saved settings, it starts first-run setup.'
      ui_info '2) Check/install required system tools: install missing dependencies; sudo may ask for your Linux password.'
      ui_info '3) Uninstall launcher and/or remove local data: choose launcher only, local data only, or complete uninstall.'
      ui_note 'Each choice previews the selected files and asks for one confirmation.'
      ui_info '4) Delete local database, backups, and setup: preview and confirm removal of the working database and recognized local data.'
      ui_note 'This also deletes saved settings and recovery records. The launcher and shortcut remain for fresh setup.'
      ui_info 'Complete uninstall: choose option 3 above, then option 3 in the uninstall menu.'
      ui_note 'Or run bash ~/.local/bin/keesyncxc --uninstall from a terminal and choose 3.'
      ui_note 'Review the deletion preview, then answer Yes once to confirm.'
      ui_note 'This removes the launcher, shortcut, settings, working database, recognized backups, and synchronization/recovery records.'
      ui_note 'KeePassXC and rclone stay installed. Cloud files, rclone credentials, and this source checkout are kept.'
      ui_note 'Fully quit KeePassXC before repair, uninstall, or local-data deletion.'
      ;;
    uninstall)
      ui_heading 'Uninstall and local-data removal help'
      ui_info '1) Uninstall launcher and settings: remove the installed script, shortcut, and saved settings.'
      ui_note 'The working database, backups, and synchronization/recovery records remain.'
      ui_info '2) Delete local database, backups, and setup: keep the installed launcher and shortcut for fresh setup.'
      ui_info '3) Complete uninstall: remove the launcher, shortcut, settings, working database, recognized backups, and synchronization/recovery records.'
      ui_note 'Choose an option, review the deletion preview, then answer Yes once to confirm. Cancelling preserves the installation and data.'
      ui_note 'KeePassXC, rclone, cloud files, rclone credentials, and source files are kept.'
      ui_note 'Fully quit KeePassXC before uninstalling or deleting local data.'
      ;;
    cleanup)
      ui_heading 'Local backup cleanup help'
      ui_info '1) Delete backups older than a chosen number of days (default: 30). Zero selects all past backups.'
      ui_info '2) Keep the newest chosen number of backups (default: 10). Zero keeps none of the eligible copies.'
      ui_note 'Age uses modification time. Newest-count retention combines local safety copies and upload snapshots.'
      ui_note 'A preview lists exact paths before confirmation. The working database, switch backups, recovery archives, and cloud files are kept.'
      ui_note 'Fully quit KeePassXC first. Pending conflicts or interrupted uploads block pruning until resolved.'
      ui_note 'Full local-data deletion is a separate action under Installation and advanced maintenance.'
      ;;
    inspect)
      ui_heading 'Inspect a local backup help'
      ui_info 'Choose a displayed backup number to open that copy directly in KeePassXC.'
      ui_note 'Dates and sizes help identify a copy. Backups may belong to previous cloud selections.'
      ui_note 'Use the database password and key file that were valid when the backup was saved.'
      ui_note 'Edits saved here change this backup only. The working database and cloud copy are not synchronized.'
      ui_note 'Fully quit KeePassXC after inspection to return to the menu. Choose Restore a local backup to make a copy your working database.'
      ;;
    inspect-cloud)
      ui_heading 'Inspect a cloud backup help'
      ui_info 'Choose a displayed cloud backup number using its filename, date, and size.'
      ui_note 'Local MD5s are checked first. An existing inspection copy is reused; other matching local files are copied so their originals stay unchanged.'
      ui_note 'Without a local match, the selected Drive file is downloaded by ID. The local MD5 and cloud file ID/checksum are verified before KeePassXC opens.'
      ui_note 'A private cloud-*.kdbx inspection copy is kept in the local backup folder. Edits affect that copy; no synchronization follows.'
      ui_note 'The working database, synchronization records, and cloud files stay unchanged.'
      ui_note 'Use the credentials valid when the backup was created. Fully quit KeePassXC to return.'
      ui_note 'Inspect before deciding to restore or publish. Restore a local backup can select the retained copy later; uploading it requires confirmation.'
      ui_note 'Inspection requires rclone 1.54 or newer. Ordinary age/count pruning keeps downloaded inspection copies; full local cleanup removes them.'
      ;;
    restore)
      ui_heading 'Restore a local backup help'
      ui_info 'Choose a displayed backup number, then review and confirm replacing the working local database.'
      ui_note 'Backups may belong to previous cloud selections. Verify this is the database you intend to restore.'
      ui_note 'The current working database, when present, is copied to a verified safety backup before replacement.'
      ui_note 'Restoration requires a confirmed synchronization history and no pending conflict or interrupted upload.'
      ui_note 'Synchronization history and the pinned Drive file ID are preserved. Restoration does not contact Drive.'
      ui_warn 'Inspect the backup before deciding to publish it. Restored contents require confirmation before any upload.'
      ui_note 'The confirmation defaults to No and remains required across restarts or failed uploads. A verified cloud backup precedes publication.'
      ui_note 'Changes on both sides still require a conflict decision; choosing local publication also requires the restoration confirmation.'
      ui_note 'If only Drive changed since the last sync, its version is downloaded instead. Matching copies need no transfer.'
      ui_note 'Use Inspect/open a local backup first to review it without restoring or synchronizing.'
      ;;
    *) show_launcher_help; return ;;
  esac
  ui_note 'Help returns to the same menu or selection. Choose a numbered action, or 0 to go back or cancel.'
}
# Each action owns its process and lock; returning to a menu never retains a lock
# or stale settings from a preceding move, switch, restore, or reconfiguration.
run_menu_action() {
  local status
  if bash "$SCRIPT_PATH" "$@"; then
    if [[ "${1:-}" == --uninstall-keep-data ||
          ( "${1:-}" == --uninstall && ! -e "$INSTALLED_SCRIPT" ) ]]; then MENU_EXIT=1; fi
  else
    status=$?
    if [[ "$status" != 130 ]]; then ui_warn 'Action did not complete; returning to the menu.'; fi
  fi
}
management_submenu() {
  local section="$1" choice title
  local -a entries=() actions=()
  case "$section" in
    status)
      title='Status and troubleshooting'
      entries=('1) Show local status (offline)' '2) Check Google Drive connection (online)' '3) Synchronize without opening KeePassXC')
      actions=(--status --check-connection --sync)
      ;;
    backups)
      title='Backups and recovery'
      entries=('1) Show local backups and recovery archives' '2) Open local backup folder' '3) List cloud backups (online)' '4) Inspect/open a local backup (no synchronization)' '5) Delete local backups older than N days' '6) Keep the newest N local backups' '7) Restore a local backup' '8) Inspect a cloud backup (no synchronization)')
      actions=(--backups --open-backups --cloud-backups --inspect-backup --cleanup-age --cleanup-count --restore-backup --inspect-cloud-backup)
      ;;
    settings)
      title='Settings and database selection'
      entries=('1) Show current settings' '2) Change cloud backup folder' '3) Move local database' '4) Manage Google Drive connection (opens rclone)' '5) Switch cloud database (back up current selection)')
      actions=(--settings --backup-folder --move-local --connection --switch-database)
      ;;
    maintenance)
      title='Installation and advanced maintenance'
      entries=('1) Install/repair launcher and shortcut from this script' '2) Check/install required system tools' '3) Uninstall launcher and/or remove local data' '4) Delete local database, backups, and setup (keep launcher)')
      actions=(--repair --dependencies --uninstall --clear-local)
      ;;
  esac
  while [[ "$MENU_EXIT" == 0 ]]; do
    ui_menu "$title" "${entries[@]}" '' 'H) Help' '0) Back' >&2
    printf '  Choice [0-%s, H]: ' "${#actions[@]}" >&2
    IFS= read -r choice || return 0
    case "$choice" in
      0|q|Q|'') return 0 ;;
      h|H|'?') show_menu_help "$section" ;;
      *)
        if [[ "$choice" =~ ^[1-9]$ ]] && (( choice <= ${#actions[@]} )); then
          run_menu_action "${actions[choice-1]}"
        else
          ui_warn "Choose a number from 0 to ${#actions[@]}, or H for help."
        fi
        ;;
    esac
  done
}
interactive_menu() {
  local choice MENU_EXIT=0
  while [[ "$MENU_EXIT" == 0 ]]; do
    ui_menu --thick "$APP_NAME v$APP_VERSION" \
      '1) Open database (synchronize before opening and after exit)' \
      '2) Status and troubleshooting' \
      '3) Backups and recovery' \
      '4) Settings and database selection' \
      '5) Installation and advanced maintenance' \
      '' 'H) Help' '0) Quit' >&2
    printf '  Choice [0-5, H]: ' >&2
    IFS= read -r choice || return 0
    case "$choice" in
      0|q|Q|'') return 0 ;;
      1) run_menu_action ;;
      2) management_submenu status ;;
      3) management_submenu backups ;;
      4) management_submenu settings ;;
      5) management_submenu maintenance ;;
      h|H|'?') show_launcher_help ;;
      *) ui_warn 'Choose a number from 0 to 5, or H for help.' ;;
    esac
  done
}

# =============================================================================
# 4. Hash and sync-state helpers
# Compare database contents and record successful synchronization atomically.
# =============================================================================
hash_file() {
  local out hash
  out=$(md5sum -- "$1") || return 1
  # GNU md5sum prefixes escaped filenames with a backslash before the digest.
  out=${out#\\}
  hash=${out%% *}
  [[ "$hash" =~ ^[a-fA-F0-9]{32}$ ]] || return 1
  printf '%s' "${hash,,}"
}
# MISSING is distinct from a hashing failure, which returns a nonzero status.
hash_local() { if [[ -f "$LOCAL" ]]; then hash_file "$LOCAL"; else printf 'MISSING'; fi; }
# These helpers are called in conditional lists, so critical failures must be
# checked explicitly: Bash does not apply errexit inside those function calls.
# Replace state only after writing the complete hash beside the existing file.
write_state() {
  local tmp
  tmp=$(mktemp "$DATA_DIR/.sync-state.XXXXXXXX") || return 1
  if ! printf '%s\n' "$1" >"$tmp" || ! mv -f -- "$tmp" "$STATE"; then
    rm -f -- "$tmp"
    return 1
  fi
}
# Resolve the pinned object within its original parent. Keep REMOTE and its
# state key stable when the same Drive object is renamed. These output variables
# are local to each caller (Bash's dynamic scope), including subshell callers.
# Status 2 means a known identity is unsafe; it must not become an offline retry.
select_remote() {
  selected_remote="$REMOTE"
  selected_id=''
  selected_hash=''
  selected_count=1
  [[ -e "$IDENTITY" || -L "$IDENTITY" ]] || return 0
  local recorded listing record id rest hash name path matches=0
  local -a names=()
  [[ -f "$IDENTITY" && ! -L "$IDENTITY" ]] || {
    ui_error 'Invalid Drive identity record; preserve it for investigation.'; return 2;
  }
  recorded=$(cat "$IDENTITY") || return 1
  [[ "$recorded" =~ ^[a-zA-Z0-9_-]+$ ]] || {
    ui_error 'Invalid Drive identity record; preserve it for investigation.'; return 2;
  }
  listing=$(rclone lsf "$REMOTE_DIR" --files-only --max-depth 1 --format ihp --hash MD5 --separator ';' --retries 2) || return 1
  while IFS= read -r record; do
    [[ -n "$record" ]] || continue
    [[ "$record" == *';'* && "${record#*;}" == *';'* ]] || {
      ui_error 'Cannot safely parse the Google Drive identity listing.'; return 2;
    }
    id=${record%%;*}
    rest=${record#*;}
    hash=${rest%%;*}
    name=${rest#*;}
    names+=("$name")
    [[ "$id" == "$recorded" ]] || continue
    matches=$((matches + 1))
    path="$REMOTE_DIR"
    if [[ "$path" != "${REMOTE%%:*}:" ]]; then path+=/; fi
    path+="$name"
    [[ "$hash" =~ ^[a-fA-F0-9]{32}$ && "$name" != */* ]] && remote_file_path_valid "$path" || {
      ui_error 'The pinned Drive object has unsupported metadata or filename; select a regular database file.'; return 2;
    }
    selected_remote="$path"
    selected_id="$id"
    selected_hash=${hash,,}
  done <<<"$listing"
  [[ "$matches" == 1 ]] || {
    ui_error 'The pinned Drive file ID is missing, replaced, or ambiguous in its configured folder. Restore the original object, or use --reset to back up local data and deliberately select another remote. Local changes and recovery records are preserved.'
    return 2
  }
  selected_count=0
  name=$(remote_filename "$selected_remote")
  local selected_name="$name"
  for name in "${names[@]}"; do
    if [[ "$name" == "$selected_name" ]]; then selected_count=$((selected_count + 1)); fi
  done
}
describe_remote_selection() {
  local selected_remote selected_id selected_hash selected_count status notice
  select_remote || {
    status=$?
    if [[ "$status" == 2 ]]; then fail 'Drive identity requires attention; no editing session was opened.'; fi
    return 0 # The normal sync path handles a network failure and offline use.
  }
  notice="$selected_remote;$selected_count"
  [[ "$notice" != "$REMOTE_NOTICE" ]] || return 0
  REMOTE_NOTICE="$notice"
  if [[ "$selected_remote" != "$REMOTE" ]]; then
    ui_warn 'The Google Drive database was renamed; its pinned file ID is unchanged.'
    ui_detail 'Configured cloud name' "$REMOTE"
    ui_detail 'Current cloud name' "$selected_remote"
    ui_note 'The saved selection and synchronization history are retained.'
    REMOTE_REVIEW_REQUIRED=1
  fi
  if (( selected_count > 1 )); then
    ui_warn 'Duplicate Google Drive filenames detected. Loading the pinned file by ID; uploads are blocked until its name is unambiguous.'
    ui_detail 'Pinned Drive file ID' "$selected_id"
    ui_detail 'Cloud name' "$selected_remote"
    REMOTE_REVIEW_REQUIRED=1
  fi
}
# A successful directory listing is required before calling a file missing.
# Unpinned files and backup destinations must always have an unambiguous name.
lookup_remote() {
  local path="$1" directory listing name filename count=0 out hash
  remote_file_path_valid "$path" || return 1
  directory=$(remote_parent "$path")
  [[ "$directory" == "$REMOTE_DIR" || "$directory" == "$REMOTE_BACKUP_DIR" ]] || return 1
  filename=$(remote_filename "$path")
  listing=$(rclone lsf "$directory" --files-only --max-depth 1 --format p --retries 2) || return 1
  while IFS= read -r name; do
    if [[ "$name" == "$filename" ]]; then count=$((count + 1)); fi
  done <<<"$listing"
  if [[ "$count" == 0 ]]; then printf 'MISSING'; return 0; fi
  if [[ "$count" != 1 ]]; then
    ui_error "Ambiguous duplicate Google Drive filename: $path"
    return 2
  fi
  out=$(rclone md5sum "$path" --retries 2) || return 1
  [[ "$out" != *$'\n'* ]] || return 1
  hash=${out%% *}
  [[ "$hash" =~ ^[a-fA-F0-9]{32}$ ]] || return 1
  printf '%s' "${hash,,}"
}
lookup_database() {
  local selected_remote selected_id selected_hash selected_count status
  select_remote || { status=$?; return "$status"; }
  if (( selected_count > 1 )); then printf '%s' "$selected_hash"; return 0; fi
  lookup_remote "$selected_remote"
}
remote_hash() {
  local hash status
  hash=$(lookup_database) || { status=$?; return "$status"; }
  [[ "$hash" != MISSING ]] || return 1
  printf '%s' "$hash"
}
# Read identity and checksum together. Drive shortcuts expose a composite ID;
# reject them because updating a shortcut can replace the underlying object.
remote_metadata() {
  local out id hash selected_remote selected_id selected_hash selected_count status
  select_remote || { status=$?; return "$status"; }
  if (( selected_count > 1 )); then printf '%s;%s' "$selected_id" "$selected_hash"; return 0; fi
  out=$(rclone lsf "$selected_remote" --files-only --format ih --hash MD5 --separator ';' --retries 2) || return 1
  [[ "$out" != *$'\n'* && "$out" == *';'* ]] || return 2
  id=${out%%;*}
  hash=${out#*;}
  [[ "$id" =~ ^[a-zA-Z0-9_-]+$ && "$hash" =~ ^[a-fA-F0-9]{32}$ ]] || return 2
  printf '%s;%s' "$id" "${hash,,}"
}
check_identity() {
  local expected_hash="$1" pin="${2:-1}" metadata id hash recorded status
  metadata=$(remote_metadata) || {
    status=$?
    [[ "$status" != 2 ]] || fail 'Unsafe or unsupported Drive metadata. Preserve the pinned object and use a regular database file.'
    ui_error 'Cannot read the Drive file ID and checksum.'
    return 1
  }
  id=${metadata%%;*}
  hash=${metadata#*;}
  [[ "$hash" == "$expected_hash" ]] || { ui_error 'The cloud database changed during verification; retry synchronization.'; return 1; }
  if [[ -e "$IDENTITY" || -L "$IDENTITY" ]]; then
    [[ -f "$IDENTITY" ]] || fail 'Invalid Drive identity record; preserve it for investigation.'
    recorded=$(cat "$IDENTITY") || return 1
    [[ "$recorded" =~ ^[a-zA-Z0-9_-]+$ ]] || fail 'Invalid Drive identity record; preserve it for investigation.'
    [[ "$recorded" == "$id" ]] || fail 'The cloud database has a different Drive file ID. Restore the original Drive object, or use --reset to back up local data and select the replacement deliberately. Local changes and recovery records are preserved.'
  elif [[ "$pin" == 1 ]]; then
    write_record "$IDENTITY" "$id" || return 1
  fi
  printf '%s' "$id"
}
# Return a path only when it identifies one verified object. rclone copyto can
# read by ID through copyid, but cannot address an upload destination by file ID.
writable_remote_path() {
  local expected_id="$1" selected_remote selected_id selected_hash selected_count status
  select_remote || { status=$?; return "$status"; }
  [[ "$selected_count" == 1 && "$selected_id" == "$expected_id" ]] || {
    ui_error 'Upload blocked: duplicate or changed Google Drive identity. Rename the other duplicate files so the pinned database has one unambiguous name; local changes are preserved.'
    return 1
  }
  printf '%s' "$selected_remote"
}
write_record() {
  local target="$1" tmp
  shift
  tmp=$(mktemp "$DATA_DIR/.launcher-record.XXXXXXXX") || return 1
  if ! printf '%s\n' "$@" >"$tmp" || ! mv -f -- "$tmp" "$target"; then
    rm -f -- "$tmp"
    return 1
  fi
}
# A restoration approval is deliberately not persisted: every upload attempt
# requires consent until publication, a verified download, or matching copies.
validate_restore_review() {
  local -a fields=()
  [[ -e "$RESTORE_REVIEW" || -L "$RESTORE_REVIEW" ]] || return 0
  if ! maintenance_plain_path "$RESTORE_REVIEW" || [[ ! -f "$RESTORE_REVIEW" ]] ||
     ! mapfile -t fields <"$RESTORE_REVIEW"; then
    ui_error 'Cannot safely read the restored database review record; synchronization stopped.'
    return 1
  fi
  if [[ "${#fields[@]}" != 3 || "${fields[0]}" != restore-review-v1 ||
        "${fields[1]}" != "$LOCAL" || ! "${fields[2]}" =~ ^[a-fA-F0-9]{32}$ ]]; then
    ui_error 'Invalid restored database review record; preserve it and investigate before synchronizing.'
    return 1
  fi
}
confirm_restored_upload() {
  local message
  validate_restore_review || return 1
  [[ -f "$RESTORE_REVIEW" ]] || return 0
  ui_heading 'Confirm publication of restored database'
  ui_detail 'Local database to publish' "$LOCAL"
  ui_detail 'Cloud database to replace' "$REMOTE"
  ui_detail 'Previous cloud contents will be backed up in' "$REMOTE_BACKUP_DIR"
  ui_warn 'Inspect the backup and restored local contents before making this decision.'
  ui_note 'Use Backups and recovery -> Inspect/open a local backup, or open the restored local file directly in KeePassXC without the launcher.'
  ui_note 'Choosing No stops this action and keeps both databases. The next upload attempt will ask again.'
  if [[ ! -t 0 && -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
    command -v zenity >/dev/null || { ui_error 'Open the launcher in a terminal to confirm publication.'; return 130; }
    printf -v message 'Inspect the backup and restored local contents before deciding.\n\nPublish local database:\n%s\n\nReplace cloud database:\n%s\n\nA verified cloud backup will be kept in:\n%s\n\nCancel to inspect first; both databases will be kept and the next upload attempt will ask again.' \
      "$(ui_escape "$LOCAL")" "$(ui_escape "$REMOTE")" "$(ui_escape "$REMOTE_BACKUP_DIR")"
    if zenity --question --title="$APP_NAME: publish restored database" --text="$message" \
       --default-cancel --ok-label='Publish restored copy' --cancel-label='Cancel and inspect first'; then
      return 0
    fi
    ui_info 'Publication cancelled; both databases were kept. Inspect before retrying.'
    return 130
  fi
  management_confirm 'Publish the restored local database to Google Drive?' || return 130
}
conflict_detected() {
  write_record "$CONFLICT" "$*" || { ui_error 'Cannot record the conflict safely.'; return 1; }
  ui_warn "Conflict: $*" >&2
  return 2
}

# =============================================================================
# 5. Backups and database transfers
# Preserve the previous copy and verify transfers before updating sync state.
# =============================================================================
# Use a unique local backup name so repeated saves cannot overwrite a backup.
backup_local() {
  [[ -f "$LOCAL" ]] || return 0
  local file
  file=$(mktemp "$BACKUPS/local-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXXXX.kdbx") || return 1
  if ! cp -- "$LOCAL" "$file"; then
    rm -f -- "$file"
    ui_error 'Failed to back up local database.'
    return 1
  fi
  ui_ok 'Local safety copy saved.'
  ui_detail 'Local backup' "$file"
}
# Remove only the known payload; never traverse or recursively remove a stage.
cleanup_download_stage() {
  local stage="$1"
  rm -f -- "$stage/database.kdbx" || return 1
  rmdir -- "$stage"
}
# The private staging directory keeps a permissive remote metadata mode from
# exposing the database before owner-only permissions are verified. Keeping it
# beside LOCAL permits an atomic rename on the same filesystem.
download_remote() {
  local expected="$1" stage tmp got metadata selected_remote selected_id selected_hash selected_count
  check_identity "$expected" >/dev/null || return 1
  select_remote || return 1
  ui_wait 'Downloading and verifying the Google Drive database...'
  stage=$(mktemp -d "$(dirname "$LOCAL")/.keesyncxc-download.XXXXXXXX") || return 1
  if ! chmod 700 -- "$stage" || [[ -L "$stage" || ! -d "$stage" ||
        "$(stat -c '%u:%a' -- "$stage")" != "$EUID:700" ]]; then
    cleanup_download_stage "$stage" || true
    ui_error 'Cannot prepare private download staging; the local database was kept.'
    return 1
  fi
  tmp="$stage/database.kdbx"
  if (( selected_count > 1 )); then
    ui_warn 'Downloading the pinned Drive file by ID because its filename is duplicated.'
    REMOTE_REVIEW_REQUIRED=1
    if ! RCLONE_METADATA=false rclone backend copyid "${REMOTE%%:*}:" "$selected_id" "$tmp" --retries 2 --drive-skip-shortcuts; then
      cleanup_download_stage "$stage" || true
      ui_error 'Exact-ID download failed. This requires rclone 1.54 or newer; the existing local database is preserved.'
      return 1
    fi
  elif ! RCLONE_METADATA=false rclone copyto "$selected_remote" "$tmp" --retries 2; then
    cleanup_download_stage "$stage" || true
    return 1
  fi
  # The environment override also works with versions predating --metadata.
  # Treat permissions as a required publication check even if rclone ignores the
  # metadata override or global rclone settings change in a later release.
  if [[ ! -f "$tmp" || -L "$tmp" ]] || ! chmod 600 -- "$tmp" ||
     [[ "$(stat -c '%u:%a:%h' -- "$tmp")" != "$EUID:600:1" ]]; then
    cleanup_download_stage "$stage" || true
    ui_error 'Cannot secure the downloaded database permissions; the local database was kept.'
    return 1
  fi
  got=$(hash_file "$tmp") || { cleanup_download_stage "$stage" || true; return 1; }
  # Reject a corrupt transfer or a cloud file changed since its initial hash read.
  if [[ "$got" != "$expected" ]]; then cleanup_download_stage "$stage" || true; return 1; fi
  metadata=$(remote_metadata) || { cleanup_download_stage "$stage" || true; return 1; }
  if [[ "${metadata%%;*}" != "$selected_id" || "${metadata#*;}" != "$expected" ]]; then
    cleanup_download_stage "$stage" || true
    ui_error 'Drive identity or contents changed during download; the local database was kept.'
    return 1
  fi
  if ! backup_local || ! mv -fT -- "$tmp" "$LOCAL"; then
    cleanup_download_stage "$stage" || true
    return 1
  fi
  cleanup_download_stage "$stage" || return 1
  write_state "$got" || return 1
  rm -f -- "$RESTORE_REVIEW" || return 1
  ui_ok 'Downloaded latest Google Drive database.'
}

# Journal fields are data, never shell code: format, previous MD5, uploaded MD5,
# original Drive ID, cloud backup path, and retained local upload snapshot.
read_transaction() {
  local -a fields=()
  mapfile -t fields <"$TRANSACTION" || return 1
  [[ "${#fields[@]}" == 6 ]] || return 1
  [[ "${fields[0]}" == in-place-v2 ]] || return 1
  TX_EXPECTED=${fields[1]}
  TX_HASH=${fields[2]}
  TX_ID=${fields[3]}
  TX_BACKUP=${fields[4]}
  TX_SNAPSHOT=${fields[5]}
  [[ "$TX_ID" =~ ^[a-zA-Z0-9_-]+$ ]] || return 1
  [[ "$TX_EXPECTED" =~ ^[a-f0-9]{32}$ && "$TX_HASH" =~ ^[a-f0-9]{32}$ ]] || return 1
  remote_file_path_valid "$TX_BACKUP" || return 1
  [[ "$(remote_parent "$TX_BACKUP")" == "$REMOTE_BACKUP_DIR" ]] || return 1
  [[ "$(remote_filename "$TX_BACKUP")" == "${REMOTE_NAME%.kdbx}-"*'.kdbx' ]] || return 1
  [[ "${TX_SNAPSHOT%/*}" == "$BACKUPS" && "${TX_SNAPSHOT##*/}" == upload-*.kdbx ]] || return 1
}
archive_transaction() {
  local archive
  archive=$(mktemp "$BACKUPS/interrupted-upload-XXXXXXXX.txt") || return 1
  mv -f -- "$TRANSACTION" "$archive" || return 1
  say "Upload recovery record: $(ui_escape "$archive")"
}
retire_as_conflict() {
  local status=0
  # Persist the decision before retiring the recovery record. A crash or disk
  # error between these steps must never turn a conflict into an auto-upload.
  conflict_detected "$1" || status=$?
  [[ "$status" == 2 ]] || return 1
  archive_transaction || return 1
  return 2
}
recover_transaction() {
  validate_restore_review || return 1
  [[ -f "$TRANSACTION" ]] || return 0
  read_transaction || { ui_error 'Invalid upload recovery record; investigate it before continuing.'; return 1; }
  ui_heading 'Upload recovery'
  check_internet_connection || return 1
  ui_wait 'Checking an interrupted Google Drive upload...'
  describe_remote_selection
  local current backup metadata id
  current=$(lookup_database) || return 1
  [[ "$current" != MISSING ]] || { ui_error 'The original Drive file is missing. Restore the original object; recovery will not create a replacement with a new ID.'; return 1; }
  metadata=$(remote_metadata) || { ui_error 'Cannot verify the Drive identity for upload recovery.'; return 1; }
  id=${metadata%%;*}
  current=${metadata#*;}
  [[ "$id" == "$TX_ID" ]] || { ui_error 'The Drive file ID changed during publication. Preserve all copies and restore the original object before retrying.'; return 1; }
  check_identity "$current" >/dev/null || return 1
  backup=$(lookup_remote "$TX_BACKUP") || return 1
  [[ "$backup" == "$TX_EXPECTED" ]] || { ui_error 'The upload backup is missing or changed. Preserve the recovery record for manual investigation.'; return 1; }
  if [[ "$current" == "$TX_HASH" ]]; then
    write_state "$TX_HASH" || return 1
    rm -f -- "$TRANSACTION" "$CONFLICT" "$RESTORE_REVIEW" || return 1
    ui_ok 'Previously uploaded database and original Drive ID verified; synchronization state recovered.'
    return 0
  fi
  retire_as_conflict 'An interrupted in-place upload needs a decision. The original Drive object, cloud backup, and local upload snapshot are preserved. Review the versions before retrying.'
}

# Preserve the authorized Drive object: copy its old contents to a unique
# backup, then upload a local snapshot into that same object and verify its ID.
upload_local() {
  local expected_remote="$1" current localhash check snapshot uuid backup original_id metadata status source_path target_path
  [[ ! -f "$TRANSACTION" ]] || { ui_error 'An upload recovery record must be resolved first.'; return 1; }
  validate_restore_review || return 1
  [[ -f "$LOCAL" ]] || return 1
  snapshot=$(mktemp "$BACKUPS/upload-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXXXX.kdbx") || return 1
  cp -- "$LOCAL" "$snapshot" || { rm -f -- "$snapshot"; return 1; }
  localhash=$(hash_file "$snapshot") || return 1
  ui_detail 'Upload snapshot' "$snapshot"
  status=0
  current=$(remote_hash) || status=$?
  if [[ "$status" != 0 ]]; then
    [[ "$status" != 2 ]] || return 1
    ui_warn 'Google Drive inaccessible: upload deferred.'
    return 3
  fi
  if [[ "$current" != "$expected_remote" ]]; then
    conflict_detected 'The cloud database changed before upload. No database upload was attempted.'
    return $?
  fi
  original_id=$(check_identity "$expected_remote") || return 1
  source_path=$(writable_remote_path "$original_id") || return 1
  # Approval happens before any cloud write, including creating its backup.
  status=0
  confirm_restored_upload || status=$?
  [[ "$status" == 0 ]] || return "$status"
  if [[ -f "$RESTORE_REVIEW" ]]; then
    current=$(hash_local) || return 1
    [[ "$current" == "$localhash" ]] || {
      ui_error 'The local database changed while awaiting approval. Inspect its current contents and retry; no cloud write was attempted.'
      return 1
    }
  fi
  IFS= read -r uuid </proc/sys/kernel/random/uuid || return 1
  [[ "$uuid" =~ ^[a-f0-9-]{36}$ ]] || return 1
  backup="$REMOTE_BACKUP_DIR/${REMOTE_NAME%.kdbx}-$(date -u +%Y%m%dT%H%M%SZ)-$uuid.kdbx"
  rclone mkdir "$REMOTE_BACKUP_DIR" --retries 2 || {
    ui_error 'Cannot create the cloud backup folder; no database upload was attempted.'
    return 1
  }
  check=$(lookup_remote "$backup") || return 1
  [[ "$check" == MISSING ]] || return 1
  ui_wait 'Copying Google Drive backup...'
  ui_detail 'Cloud backup' "$backup"
  if ! rclone copyto "$source_path" "$backup" --ignore-existing --checksum --backup-dir '' --suffix '' --retries 1; then
    ui_error 'Cloud backup failed; the working cloud database was not deliberately changed.'
    return 1
  fi
  check=$(lookup_remote "$backup") || return 1
  [[ "$check" == "$expected_remote" ]] || { ui_error 'Cloud backup verification failed; no database upload was attempted.'; return 1; }
  ui_ok 'Cloud backup verified.'
  metadata=$(remote_metadata) || {
    status=$?
    ui_error 'Cannot recheck Drive identity; no database upload was attempted.'
    if [[ "$status" == 2 ]]; then return 1; else return 3; fi
  }
  [[ "${metadata%%;*}" == "$original_id" ]] || { ui_error 'The Drive file ID changed before upload; restore the original object before retrying.'; return 1; }
  current=${metadata#*;}
  if [[ "$current" != "$expected_remote" ]]; then
    conflict_detected 'The cloud database changed while copying its backup. No database upload was attempted.'
    return $?
  fi
  target_path=$(writable_remote_path "$original_id") || return 1
  write_record "$TRANSACTION" in-place-v2 "$expected_remote" "$localhash" "$original_id" "$backup" "$snapshot" || return 1
  # Upload from LOCAL DISK: a Drive-to-Drive copy may replace the destination ID.
  # Disable rclone's post-transfer deletion on size/hash mismatch; our mandatory
  # ID+MD5 checks retain the object and recovery journal on any mismatch.
  # Empty backup flags also override environment defaults that would move it.
  ui_wait 'Uploading saved changes to the Google Drive database...'
  if ! rclone copyto "$snapshot" "$target_path" --ignore-times --ignore-checksum --ignore-size \
      --backup-dir '' --suffix '' --retries 1 --low-level-retries 1; then
    ui_error 'Database upload did not complete. Keep all copies and restart the launcher to check recovery.'
    return 1
  fi
  ui_wait 'Verifying the uploaded database and Drive file ID...'
  metadata=$(remote_metadata) || { ui_error 'Cannot verify the uploaded database and Drive ID; recovery record retained.'; return 1; }
  [[ "${metadata%%;*}" == "$original_id" && "${metadata#*;}" == "$localhash" ]] || {
    ui_error 'Upload checksum or Drive ID verification failed. All backup and recovery records are retained.'
    return 1
  }
  write_state "$localhash" || return 1
  rm -f -- "$TRANSACTION" "$CONFLICT" "$RESTORE_REVIEW" || return 1
  ui_ok 'Uploaded saved KeePassXC database to the original Google Drive file; its ID is unchanged.'
}

# =============================================================================
# 6. Synchronization decisions
# Compare local and cloud hashes against the baseline to choose a safe action.
# =============================================================================
# Status: 0 synchronized, 1 failed, 2 conflict, 3 offline/deferred, 130 cancelled.
sync_once() {
  local baseline localhash cloudhash status recorded
  validate_restore_review || return 1
  localhash=$(hash_local) || { ui_error 'Cannot read local database.'; return 1; }
  # Validate existing state before allowing either online or offline use.
  if [[ -f "$STATE" ]]; then
    baseline=$(cat "$STATE") || { ui_error 'Cannot read sync state.'; return 1; }
    [[ "$baseline" =~ ^[a-fA-F0-9]{32}$ ]] || fail 'Invalid sync state. Do not delete state casually: investigate first.'
    baseline=${baseline,,}
    [[ "$localhash" != 'MISSING' ]] || fail 'Local database missing. Restore it from backup before continuing.'
  fi
  [[ ! -f "$CONFLICT" ]] || return 2
  # Offline editing requires an existing local database with valid sync state.
  status=0
  if check_internet_connection; then
    ui_wait 'Checking Google Drive...'
    describe_remote_selection
    cloudhash=$(remote_hash) || status=$?
  else
    # A failed probe must not bypass validation of an existing local ID record.
    if [[ -e "$IDENTITY" || -L "$IDENTITY" ]]; then
      [[ -f "$IDENTITY" && ! -L "$IDENTITY" ]] && recorded=$(cat "$IDENTITY") &&
        [[ "$recorded" =~ ^[a-zA-Z0-9_-]+$ ]] ||
        fail 'Invalid Drive identity record; preserve it for investigation.'
    fi
    status=1
  fi
  if [[ "$status" != 0 ]]; then
    [[ "$status" != 2 ]] || fail 'Drive identity requires attention; synchronization stopped with all local data preserved.'
    if [[ -f "$LOCAL" && -f "$STATE" ]]; then
      ui_warn 'Google Drive unavailable; synchronization deferred. Local database is available offline.'
      return 3
    fi
    fail 'Cannot reach Google Drive, or remote database path is incorrect. No previously synchronized offline copy is available.'
  fi
  if ! check_identity "$cloudhash" >/dev/null; then
    if [[ -f "$LOCAL" && -f "$STATE" ]]; then
      ui_warn 'Cannot verify Drive identity; synchronization deferred. The previously synchronized local database is available offline.'
      return 3
    fi
    fail 'Cannot verify the original Drive file identity before first synchronization.'
  fi
  # First use downloads from Drive and refuses to replace an existing local file.
  if [[ ! -f "$STATE" ]]; then
    # A failed first-run state write may leave a correctly installed download.
    if [[ "$localhash" == "$cloudhash" ]]; then
      write_state "$cloudhash" || return 1
      ui_ok 'Existing local database verified against Google Drive.'
      return 0
    fi
    [[ "$localhash" == 'MISSING' ]] || fail 'Initial setup found an existing local file. Back it up/move it elsewhere first; the cloud database must be the initial source.'
    download_remote "$cloudhash" || fail 'Initial database download/verification failed.'
    return 0
  fi
  if [[ "$localhash" == "$cloudhash" ]]; then
    # Both copies already agree; refresh the shared baseline.
    write_state "$localhash" || return 1
    rm -f -- "$RESTORE_REVIEW" || return 1
    ui_ok 'Local and Drive copies match.'
  elif [[ "$localhash" == "$baseline" ]]; then
    # Only Drive changed: download its version.
    download_remote "$cloudhash" || fail 'Remote update could not be downloaded safely.'
  elif [[ "$cloudhash" == "$baseline" ]]; then
    # Only the local copy changed: upload it, preserving any failure status.
    upload_local "$cloudhash" || {
      status=$?
      ui_warn 'Synchronization incomplete; local database preserved.' >&2
      return "$status"
    }
  else
    # Keep the decision pending across cancellation and subsequent launches.
    status=0
    conflict_detected 'Both local and Google Drive versions changed. Neither version was overwritten.' || status=$?
    [[ "$status" == 2 ]] || return 1
    backup_local || return 1
    return 2
  fi
}

# =============================================================================
# 7. Conflict choices and application lifecycle
# Reference editing returns to the menu. Only cloud adoption or explicit
# publication resolves a pending conflict.
# =============================================================================
open_local() {
  local before_hash after_hash
  [[ -f "$LOCAL" ]] || fail 'Local database is missing.'
  if pgrep -u "$(id -u)" -x keepassxc >/dev/null 2>&1; then
    fail 'KeePassXC is already running. Fully exit it before continuing.'
  fi
  before_hash=$(hash_local) || EDITOR_CHANGED=1
  ui_heading 'KeePassXC session'
  ui_info "Opening KeePassXC: $LOCAL"
  ui_wait 'Waiting for KeePassXC to fully exit.'
  ui_note 'Save and use Quit/Exit when finished; saved changes synchronize afterward.'
  if keepassxc "$LOCAL"; then
    ui_ok 'KeePassXC exited.'
  else
    APP_FAILED=1
    ui_warn 'KeePassXC exited with an error; saved changes will still be checked.' >&2
  fi
  after_hash=$(hash_local) || EDITOR_CHANGED=1
  if [[ ! "$before_hash" =~ ^[a-fA-F0-9]{32}$ || "$before_hash" != "$after_hash" ]]; then
    EDITOR_CHANGED=1
  fi
}
show_conflict_information() {
  local information
  information=$(cat <<EOF
Resolving a $APP_NAME synchronization conflict

The local and cloud copies may contain different changes, or a previous upload
still needs a decision. Review the copies before choosing which one to publish.

1) Open my local changes
   Open the current local database for reference or further editing. Fully exit
   KeePassXC to return to the conflict menu. Closing this session does not upload
   your changes. Closing to the system tray may keep KeePassXC running.
   During sync-only mode, cancel and use Open database for a reference session.

2) Use the latest cloud database
   Back up your current local database, download and verify the cloud version,
   and adopt it as the working copy. Normal launches open it for editing and
   synchronize on full exit. Sync-only mode finishes without opening KeePassXC.

3) Overwrite cloud with my current local copy
   Publish the current local contents, including any edits from a reference
   session. The previous cloud version becomes a dated backup. A detected cloud
   change during this attempt stops publication and returns to the menu.

0) Cancel and resolve later
   Keep all copies and leave the conflict pending for the next launch.

Your local working database:
  $(ui_escape "$LOCAL")

Your cloud database:
  $(ui_escape "$REMOTE")

Local backups and upload snapshots:
  $(ui_escape "$BACKUPS")

  local-<UTC timestamp>-<random>.kdbx
    Safety copies made before reviewing or replacing the local database.
  upload-<UTC timestamp>-<random>.kdbx
    The exact local contents captured for an upload attempt.

Local backups and snapshots remain after a successful cloud overwrite. There is
no automatic cleanup. Open a backup directly in KeePassXC using the credentials
that were valid when that copy was saved. If the backup folder is hidden, Ctrl+H
shows hidden folders in the file manager.

Cloud backups are stored in a dedicated folder:
  $(ui_escape "$REMOTE_BACKUP_DIR")/
  $(ui_escape "${REMOTE_NAME%.kdbx}")-<UTC timestamp>-<unique ID>.kdbx

Keep all copies while a conflict or upload is unresolved. Cloud backups can be
removed manually when no longer needed.

Closing this information returns to the conflict menu.
EOF
  ) || return 1
  if [[ ! -t 0 && -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
    # Closing the information window, including Cancel, returns to the choices.
    zenity --text-info --title="$APP_NAME conflict information" \
      --width=820 --height=620 --ok-label='Back to conflict menu' <<<"$information" || true
  else
    ui_heading 'Conflict help' >&2
    say "$information" >&2
  fi
}
choose_conflict_action() {
  local choice review='Open my local changes for reference or editing'
  if [[ "$MODE" == sync ]]; then review='Local review requires Open database; this sync-only action never opens KeePassXC'; fi
  if [[ ! -t 0 && -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
    command -v zenity >/dev/null || { ui_error 'Open this launcher in a terminal, or install zenity for conflict dialogs.'; return 1; }
    choice=$(zenity --list --title="$APP_NAME synchronization conflict" \
      --text='The database changed in the meantime. All copies are preserved. Choose how to continue.' \
      --column='Choice' --column='Action' --hide-column=1 --print-column=1 --width=760 --height=380 \
      1 "$review (return here on exit)" \
      2 'Use the latest cloud database (back up local changes first)' \
      3 'Overwrite the cloud database with my current local copy (keep a cloud backup)' \
      h 'More information about conflict resolution and backups' \
      0 'Cancel and resolve later') || return 1
  else
    ui_menu 'Synchronization conflict' \
      'The local and cloud copies need a decision. All copies are preserved.' '' \
      "1) $review" \
      '   Return to this menu on exit; this does not upload changes.' \
      '2) Use the latest cloud database (back up local changes first)' \
      '3) Overwrite cloud with my current local copy (keep a cloud backup)' \
      'H) More information about conflict resolution and backups' \
      '0) Cancel and resolve later' >&2
    printf '  Choice [0-3, H]: ' >&2
    IFS= read -r choice || return 1
  fi
  printf '%s' "$choice"
}
resolve_conflict() {
  local choice expected status
  while :; do
    choice=$(choose_conflict_action) || { ui_warn 'Conflict left unresolved; all copies preserved.' >&2; return 1; }
    case "$choice" in
      1)
        if [[ "$MODE" == sync ]]; then
          ui_info 'Local review is unavailable during sync-only mode. Cancel and choose Open database to review or edit.'
          continue
        fi
        backup_local || return 1
        ui_info 'Reference session: local edits are allowed. No upload will run when KeePassXC closes.'
        open_local
        ;;
      2)
        expected=$(remote_hash) || { ui_error 'Cannot read the latest cloud database; choose another action or retry.'; continue; }
        if download_remote "$expected"; then
          rm -f -- "$CONFLICT" || return 1
          return 4 # The caller opens this adopted cloud version normally.
        fi
        ui_warn 'Cloud download failed; local changes and the conflict remain preserved.' >&2
        ;;
      3)
        ui_heading 'Publish local changes'
        ui_detail 'Local copy to publish' "$LOCAL"
        ui_detail 'Cloud database to overwrite' "$REMOTE"
        ui_detail 'Previous cloud contents will be backed up in' "$REMOTE_BACKUP_DIR"
        expected=$(remote_hash) || { ui_error 'Cannot read the cloud version to replace; choose another action or retry.'; continue; }
        status=0
        upload_local "$expected" || status=$?
        if [[ "$status" == 0 ]]; then return 0; fi
        if [[ "$status" == 130 ]]; then return 130; fi
        [[ ! -f "$TRANSACTION" ]] || { ui_error 'Publication was interrupted; restart the launcher to recover safely.'; return 1; }
        ui_warn 'The database was not published. All copies are preserved; choose how to continue.' >&2
        ;;
      0|4|q|Q|'') ui_warn 'Conflict left unresolved; all copies preserved.' >&2; return 130 ;;
      h|H|'?'|5) show_conflict_information ;;
      *) ui_warn 'Choose 0, 1, 2, 3, or H for help.' >&2 ;;
    esac
  done
}
finish_success() {
  local localhash baseline close_result=review final_match=0
  [[ "$APP_FAILED" == 0 ]] || fail 'Synchronization completed, but KeePassXC exited with an error.'
  # The final sync just verified Drive. Check the local copy again so an edit
  # after that check cannot make a desktop session close without review.
  if localhash=$(hash_local) && baseline=$(cat "$STATE") &&
     [[ "$baseline" =~ ^[a-fA-F0-9]{32}$ && "$localhash" == "${baseline,,}" ]]; then
    final_match=1
    if [[ "$EDITOR_CHANGED" == 1 ]]; then
      close_result=synced-changed
    elif [[ "$REMOTE_REVIEW_REQUIRED" == 0 ]]; then
      close_result=synced-unchanged
    fi
  fi
  ui_heading 'Session complete'
  ui_ok 'Finished. Synchronization completed.'
  if [[ "$EDITOR_CHANGED" == 1 ]]; then ui_info 'KeePassXC changed the database during this session.'; fi
  if [[ "$close_result" == review ]]; then
    if [[ "$final_match" == 1 ]]; then
      ui_warn 'Review the cloud filename and file-ID warnings above before closing this session.'
    else
      ui_warn 'Review the local database; its final contents could not be confirmed against the synchronized copy.'
    fi
  fi
  # FD 8 is a private result channel opened only by the desktop wrapper. It
  # carries UI state, never database contents, credentials, or executable code.
  if [[ "${KEESYNCXC_DESKTOP_REPORT:-}" == 1 ]]; then
    if ! printf '%s\n' "$close_result" >&8; then
      ui_warn 'Cannot confirm automatic closing; review this session before closing the window.'
    fi
  fi
}

# The desktop opens a terminal before starting this wrapper. Keep early errors
# visible, including failures before configuration or synchronization can begin.
desktop_launch() {
  local status=0 answer remaining
  local -a results=()
  ui_heading "$APP_NAME v$APP_VERSION"
  ui_info "Starting $APP_NAME..."
  ui_note 'Keep this window open while editing and syncing.'
  ui_note 'Changed databases and setup results stay open for review.'
  if DESKTOP_REPORT_FILE=$(mktemp "${TMPDIR:-/tmp}/keesyncxc-desktop.XXXXXXXX"); then
    trap 'rm -f -- "$DESKTOP_REPORT_FILE"' EXIT
    if KEESYNCXC_DESKTOP_REPORT=1 bash "$SCRIPT_PATH" 8>"$DESKTOP_REPORT_FILE"; then
      status=0
    else
      status=$?
    fi
    mapfile -t results <"$DESKTOP_REPORT_FILE" || results=()
  else
    ui_error 'Cannot prepare the desktop session status; launch from a terminal and retry.'
    status=1
  fi
  # Pipes and unattended invocations never wait for input or a countdown.
  if [[ ! -t 0 ]]; then return "$status"; fi
  if [[ "$status" == 0 && "${#results[@]}" == 1 && "${results[0]}" == synced-unchanged ]]; then
    for remaining in 3 2 1; do
      ui_info "Auto-closing in $remaining seconds..."
      sleep 1
    done
  else
    if [[ "$status" != 0 ]]; then
      ui_heading 'Action required'
      ui_warn "Launcher stopped with exit status $status. Press Enter to close this window."
    else
      ui_heading 'Review session'
      if [[ "${#results[@]}" == 1 && "${results[0]}" == synced-changed ]]; then
        ui_info 'KeePassXC changed the database. Review the synchronization results above.'
      else
        ui_info 'Review the results above before closing this window.'
      fi
      ui_note 'Press Enter to close this window.'
    fi
    IFS= read -r answer || true
  fi
  return "$status"
}

# File managers run scripts without a terminal. Open one before reading settings
# or touching Drive; the desktop wrapper then owns review and closing behavior.
# Explicit --launch and piped input remain available for direct/scripted use.
if [[ ( "$MODE" == auto || "$MODE" == desktop ) &&
      -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" &&
      ! -t 0 && ! -t 1 && ! -t 2 && ! -p /dev/stdin &&
      "${KEESYNCXC_DESKTOP_REPORT:-}" != 1 ]]; then
  if command -v x-terminal-emulator >/dev/null; then
    exec x-terminal-emulator -e /bin/bash "$SCRIPT_PATH" --desktop
  fi
  terminal_error='Cannot open the progress window: x-terminal-emulator is unavailable. Install a terminal that provides it, or run this script from an existing terminal.'
  if command -v zenity >/dev/null; then
    zenity --error --title="$APP_NAME" --text="$terminal_error" || true
  fi
  fail "$terminal_error"
fi

if [[ "$MODE" == desktop ]]; then
  desktop_status=0
  desktop_launch || desktop_status=$?
  exit "$desktop_status"
fi

if [[ "$MODE" == auto ]]; then
  if installation_complete; then
    MODE=launch
  else
    ui_info 'Launcher setup is missing or incomplete; starting installation.'
    MODE=install
  fi
fi

# A menu shortcut has no terminal stdin. Use the desktop's terminal for prompts;
# piped answers remain supported for scripted setup and maintenance.
interactive_mode=0
case "$MODE" in
  install|menu|cleanup|cleanup-age|cleanup-count|uninstall|uninstall-keep-data|reset|repair|dependencies|clear-local|backup-folder|move-local|connection|inspect-backup|inspect-cloud-backup|restore-backup) interactive_mode=1 ;;
esac
if [[ "$interactive_mode" == 1 ]] &&
   [[ ! -t 0 && ! -t 1 && ! -t 2 && ! -p /dev/stdin &&
      -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" &&
      "${KEESYNCXC_DESKTOP_REPORT:-}" != 1 ]]; then
  if command -v x-terminal-emulator >/dev/null; then
    exec x-terminal-emulator -e /bin/bash "$SCRIPT_PATH" "--$MODE"
  fi
  fail "Open this script in a terminal with --$MODE to answer its prompts."
fi

case "$MODE" in
  menu) interactive_menu; exit 0 ;;
  status) show_status; exit $? ;;
  check-connection) check_cloud_connection; exit 0 ;;
  backups) show_backup_inventory; exit 0 ;;
  open-backups) open_backup_folder; exit 0 ;;
  cloud-backups) list_cloud_backups; exit 0 ;;
  inspect-backup) inspect_local_backup; exit 0 ;;
  inspect-cloud-backup) inspect_cloud_backup; exit 0 ;;
  restore-backup) restore_local_backup; exit 0 ;;
  settings) show_settings; exit 0 ;;
  backup-folder) change_backup_folder; exit 0 ;;
  move-local) move_local_database; exit 0 ;;
  connection) manage_drive_connection; exit 0 ;;
  repair) repair_installation; exit 0 ;;
  dependencies)
    [[ "$(id -u 2>/dev/null || printf '%s' "$EUID")" != 0 ]] || fail 'Run dependency installation as your regular user, without sudo.'
    install_dependencies; exit 0 ;;
  cleanup) cleanup_backups; exit 0 ;;
  cleanup-age|cleanup-count)
    maintenance_lock
    load_configuration || fail 'Fix the saved settings before cleaning backups.'
    if [[ "$MODE" == cleanup-age ]]; then prune_backups age; else prune_backups count; fi
    exit 0 ;;
  clear-local) maintenance_lock; full_local_cleanup; exit 0 ;;
  reset) reset_selection; exit 0 ;;
  uninstall) uninstall_launcher; exit 0 ;;
  uninstall-keep-data) uninstall_launcher keep-data; exit 0 ;;
esac

load_configuration || fail 'Fix the saved configuration or use --uninstall to remove it.'
if [[ "$MODE" == install ]]; then install_launcher; exit 0; fi

ui_heading 'Launcher checks'
ui_wait 'Starting launcher checks...'
startup_checks
ui_ok 'Required tools and local session checks passed.'
ui_detail 'Cloud database' "$REMOTE"
ui_detail 'Local database' "$LOCAL"
recovery_status=0
recover_transaction || recovery_status=$?
[[ "$recovery_status" == 0 || "$recovery_status" == 2 ]] || fail 'Upload recovery requires attention. No editing session was opened.'

if [[ "$MODE" == sync ]]; then
  ui_heading 'Synchronize without opening KeePassXC'
  sync_status=0
  sync_once || sync_status=$?
  case "$sync_status" in
    0) finish_success ;;
    130) exit 130 ;;
    2)
      resolution=0
      resolve_conflict || resolution=$?
      case "$resolution" in
        0|4) finish_success ;;
        130) exit 130 ;;
        *) fail 'Conflict remains unresolved; local changes are intact.' ;;
      esac
      ;;
    3) fail 'Synchronization deferred; Google Drive is unavailable. Local changes are intact.' ;;
    *) fail 'Synchronization requires attention; local database is preserved.' ;;
  esac
  exit 0
fi

phase=before
while :; do
  if [[ "$phase" == after ]]; then
    ui_heading 'After editing'
    ui_wait 'KeePassXC is closed; checking for saved changes...'
  else
    ui_heading 'Before editing'
  fi
  sync_status=0
  sync_once || sync_status=$?
  case "$sync_status" in
    130) exit 130 ;;
    0)
      if [[ "$phase" == after ]]; then finish_success; break; fi
      open_local
      phase=after
      ;;
    2)
      resolution=0
      resolve_conflict || resolution=$?
      case "$resolution" in
        0) finish_success; break ;;
        4) open_local; phase=after ;;
        130) exit 130 ;;
        *) fail 'Conflict remains unresolved; local changes are intact.' ;;
      esac
      ;;
    3)
      [[ "$phase" == before ]] || fail 'Post-close sync deferred; local database is intact. Retry when Google Drive is available.'
      ui_info 'Opening the previously synchronized offline copy; synchronization will be retried on exit.'
      ui_note 'Reconnect when possible; keep using the launcher to synchronize saved local changes.'
      open_local
      phase=after
      ;;
    *) fail 'Synchronization requires attention; local database is preserved.' ;;
  esac
done
