# Contributing to KeeSyncXC

Bug reports, documentation improvements, and focused pull requests are welcome. Read the [README](README.md) for supported behavior and synchronization limits, and use a disposable database with dummy entries for development.

## Report a bug or request a feature

Use the repository's issue templates. For a bug, include the KeeSyncXC version or commit, Linux distribution, Bash/KeePassXC/rclone versions, launch method, steps to reproduce, and relevant redacted output. Explain the expected behavior and what happened.

For suspected vulnerabilities, follow the README's [private reporting guidance](README.md#report-a-vulnerability). Keep vulnerability details out of public issues.

Do not submit password databases, key files, passwords, OAuth secrets, tokens, or rclone configuration. Redact personal paths, account information, and Drive file IDs from logs. Ignore rules do not protect files already tracked by Git.

## Make and validate a change

Keep each pull request focused on one problem. Preserve checksum verification, Drive file identity, recovery records, backup behavior, and confirmation before deletion or publication of restored contents. Describe any deliberate behavior change in the pull request and update the relevant documentation and [changelog](CHANGELOG.md).

From the repository root, run:

```bash
bash -n keesyncxc.sh
bash keesyncxc.sh --version
python3 -B -m unittest discover -s tests -v
```

The regression suite uses temporary homes and fake external commands; it does not open KeePassXC or contact Google Drive. Add a regression test for a bug fix when it verifies meaningful behavior. See the [testing guide](tests/TESTING.md) for shared fixtures, focused test commands, and disposable manual checks.

Changes to Drive transfers, authentication, or desktop integration also need the relevant manual checks with a disposable database and account. State which checks you ran and which remain unverified; mocked tests alone do not establish live interoperability.

## Open a pull request

Explain the problem, the resulting behavior, and how you validated it. Include any data-loss or compatibility implications and relevant documentation updates. Review your diff for accidental credentials, databases, and personal information before submitting.
