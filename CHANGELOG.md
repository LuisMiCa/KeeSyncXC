# KeeSyncXC changelog

## v1.0.0

First public release of KeeSyncXC, a Linux launcher for synchronizing a KeePassXC database with Google Drive through rclone.

- Synchronize an encrypted KeePassXC database with Google Drive before editing and after KeePassXC fully exits.
- Set up dependencies, database paths, the rclone connection, and a KeeSyncXC desktop shortcut interactively.
- Verify database checksums and Google Drive file identity, preserve local and cloud backups, and recover interrupted uploads.
- Verify local safety backups and recheck the working database before replacing it with a downloaded cloud version.
- Support offline editing after initial synchronization and explicit choices when both copies change.
- Provide synchronization without opening KeePassXC, offline status, and read-only Drive connection checks.
- Inspect local and cloud backups without synchronization. Reuse matching local contents or download and verify a private cloud inspection copy while preserving the working database.
- Restore local backups after preserving the working database, with separate confirmation before uploading restored contents.
- Offer numbered menus with contextual help for status, backups and recovery, settings, and maintenance.
- Support custom cloud backup folders, verified local database moves, and switching cloud databases while preserving local work and recovery records.
- Provide launcher repair, backup pruning, and numbered uninstall choices with deletion previews and confirmation.
- Group launcher removal, local-data deletion, and complete uninstall under one maintenance menu entry.
- Use `keesyncxc.sh` as the source script and `keesyncxc` as the installed command, with XDG configuration and state directories.
- Default to `gdrive:KeePass/kpDB.kdbx`, `$HOME/Documents/KeeSyncXC/Passwords.kdbx`, and `gdrive:KeePass/KeePass-Backups`.
