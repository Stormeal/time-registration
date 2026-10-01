# Epic G update recovery design

Date: 2026-10-01 · Status: proposed for review

## Intent and scope

QI Flow must keep its per-user installation removable and its time data usable after an in-app update, including an interrupted update. The user has verified the normal latest-stable-release check and update on their machine. This design addresses the four remaining Epic G risks: lost uninstall support, an interrupted folder swap, a leftover recovery folder, and stale Windows startup registration.

The fixed GitHub release feed, explicit update confirmation, SHA-256 verification, and local AppData storage remain as defined in US21 and US32. There is no telemetry or administrator requirement.

## Chosen layout

The per-user Inno installer owns a stable outer directory at `%LOCALAPPDATA%\Programs\QI Flow`. It installs its uninstall files and a small QI Flow launcher there. The application bundle lives in `{app}\current`; shortcuts and optional Windows startup invoke the stable launcher, which starts `current\QI Flow.exe`. The update helper replaces only `current`, so it never moves or deletes the launcher or Inno's uninstall files. The launcher's command and readiness protocol are versioned; changing that stable launcher requires an installer upgrade, while ordinary application releases remain in-app updates.

The installer records removal of the entire `current` and updater-working directories on uninstall. These directories contain application files only. The database, settings, backups, logs, and task cache remain in the separate per-user application-data directory and are retained on uninstall. The uninstall action also removes the HKCU Run value only when its command refers to this QI Flow installation.

## Update and recovery flow

1. QI Flow checks the fixed latest-stable GitHub release only when requested. After explicit confirmation, it downloads the fixed-name asset outside the install folder and verifies the release digest and size, as today.
2. A helper outside `current` waits for QI Flow to exit, verifies the staged archive again, rejects unsafe paths, and extracts it to a unique staging directory on the install volume. It records an update transaction in the stable outer directory using atomic state writes before changing the live bundle.
3. The helper moves `current` to a unique previous directory, then moves the staged bundle to `current`. Directory presence, the transaction state, and the expected package digest identify what happened if the process stops between steps. An interrupted operation is safe to retry.
4. The helper starts the stable launcher in provisional-update mode with the transaction ID. In that mode the launcher starts the new bundle for validation. Ordinary launches wait while the helper holds the update lock. If the helper has died, the launcher examines the transaction and directory state, restores the previous bundle when `current` is absent or unvalidated, and launches it. Recovery therefore runs even when the original update helper was terminated, because shortcuts and Windows startup point to the stable launcher.
5. The new app signals readiness only after database migrations, service composition, and its initial UI startup succeed. Tracking actions stay unavailable until readiness is signalled. The helper accepts success only when the signal arrives and the app remains running through a short bounded check; otherwise it stops the new process, restores the previous bundle, and reports a useful error.
6. Once the new app is ready, the previous bundle becomes cleanup work. A failed deletion is recorded and retried later; it does not block another update. Each update uses a unique previous directory, so an older leftover cannot be mistaken for the current transaction. Cleanup never removes the only usable bundle.

The launcher and helper coordinate through one per-user update lock, preventing a second launch or update from racing a swap. Messages shown after failure identify whether QI Flow restored the previous version or needs the recovery installer. Diagnostic state contains only version, paths, digest, stage, and error category; it contains no work entries, notes, or credentials.

## Data and schema compatibility

The updater changes application files only. It never overwrites or deletes user data. A release that adds a database migration must be tested so the immediately previous application version can reopen the migrated database if startup validation forces a rollback. If that compatibility cannot be provided, the release needs a separately reviewed data-recovery migration before publication. Active timer state remains persisted in the database throughout the update.

## Transition for existing installations

Existing installations place the executable and Inno uninstall files together in `{app}`. Their current updater cannot preserve the uninstall record during a full-folder swap. A one-time per-user installer upgrade moves them to the stable layout, preserves the existing AppData and Inno uninstall registration, updates shortcuts, and rewrites an enabled HKCU Run value to the launcher. The installer removes only known legacy application files after the new layout is usable.

The migration release is distributed with its installer and clear instructions, but without an update ZIP usable by the legacy updater. The old client's missing-package message is explained in the release instructions, with the one-time installer step and data-preservation behavior. This prevents old clients from performing the unsafe full-folder swap. After the one-time migration, future releases again use the in-app update asset. The installer remains available for first installation and recovery.

## Verification and release gate

- Unit and integration tests inject failure before and after each directory move, during launch/readiness, and during cleanup. They verify automatic launcher recovery, idempotent retry, preservation of user data, and a later update despite stale cleanup work.
- Packaging tests verify the launcher and Inno files stay outside `current`, the update ZIP contains only the app bundle, and uninstall removes new bundle files added by an in-app update.
- Windows smoke testing on a clean standard-user account covers install, optional startup, update, restart, uninstall, and retained AppData. A second smoke test interrupts the helper between renames, then launches through the normal shortcut and verifies rollback. A third simulates cleanup failure and verifies the following update.
- Release verification checks database and active-state compatibility with the immediately previous version. `scripts/check.ps1` must pass before publication.

Epic G remains open until the migration release and these Windows smoke tests pass. The already verified normal update should be recorded as completed evidence rather than listed as pending.
