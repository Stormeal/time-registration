# Epic G Windows release smoke gate

Run this on a clean standard-user Windows account before promoting the migration prerelease.
Record the Windows version, prior QI Flow version, candidate tag, asset digests, tester, date,
and observed result for each row. The automated suite does not replace these checks.

| Check | Action | Required result |
| --- | --- | --- |
| Fresh install | Run `QI-Flow-Setup-<version>.exe` without elevation. Launch from Start and desktop shortcuts. | `QI Flow Launcher.exe`, `current\QI Flow.exe`, and Inno uninstall files are in `%LOCALAPPDATA%\Programs\QI Flow`; both shortcuts work. |
| Data and timer | Start a work session, restart through the launcher, then finish it. | Active state and completed entry survive; data stays under the user's AppData location. |
| Optional startup | Enable Start with Windows, inspect `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, sign out and back in. | The entry points to the outer launcher with `--start-minimized`; the app opens in the tray. |
| Local v2 apply before promotion | On a disposable candidate installation, start QI Flow and launch `QI Flow Launcher.exe --apply-update --pid <running-app-pid> --archive <candidate-v2-zip> --sha256 <candidate-zip-sha256>`. This may reapply the same version for the first v2 release. | The app restarts; the launcher and Inno uninstaller remain; `current` is replaced; AppData and active state survive. |
| Stable-feed check after promotion | On a retained prior v2 installation, use the in-app update check and confirmation after the candidate is promoted to latest stable. | The expected fixed GitHub asset is found, verified, installed, and relaunched. Record this post-promotion result before closing Epic G. |
| Interrupted swap | In a disposable install, stop the launcher after `current` is moved to `update-work\previous-*` but before the staged bundle is moved to `current`; then use the Start shortcut. | The launcher restores the old bundle and opens it; no manual folder repair or elevation is needed. |
| Failed readiness | In a disposable install, trial a bundle that does not report readiness. | The trial process closes, the previous bundle is restored, and the failure message gives the recovery-installer path. |
| Cleanup retry | Make removal of a committed `previous-*` folder fail once, then install another valid update and restore deletion access. | The second update succeeds and a later launch removes only committed previous folders. |
| Legacy migration | Install the last legacy version, enable startup, create time data, then run the candidate installer over it. | Shortcuts and the enabled Run entry use the outer launcher; the new bundle is under `current`; data and Inno uninstall support survive. |
| Uninstall | Uninstall the migrated or freshly installed app. | Launcher, `current`, update work, shortcuts, and this installation's Run entry are removed. AppData is retained and an unrelated Run command is untouched. |
| Previous-version database compatibility | On a copy of real representative data, start the candidate once to apply any migrations, then launch the immediately previous app version against that copied database. | The previous version opens and displays the data correctly. If it cannot, block promotion and design a reviewed data-recovery migration. |

The migration release must contain the installer and `QI-Flow-Update-v2.zip`, with no legacy
`QI-Flow-Update.zip`. An older installation that reports a missing valid update package must
be migrated with the installer once. Keep the GitHub workflow manual: create a prerelease,
review the smoke evidence, then manually promote it to latest stable. Do not claim Epic G is
complete until every row has an observed passing result.

## Evidence recorded on 2026-10-02

- `scripts/check.ps1` passed: formatting, lint, mypy, and 267 tests.
- An isolated build produced the application bundle, stable launcher, v2 ZIP, and Inno 6 installer.
  ZIP inspection found `QI Flow/QI Flow.exe`, no launcher, and no legacy-named ZIP.
- A temporary copy of the installer with a separate AppId installed under the worktree without
  elevation. The installed layout contained the outer launcher, `current/QI Flow.exe`, and Inno
  uninstall files. Its uninstaller removed the app files and registration while an external data
  sentinel remained intact. This is an isolated installer check, not the clean-account migration
  or user-data smoke test above.
- The user's earlier check verified the legacy version's latest-release lookup and ordinary
  in-app update. The v2 release must still pass the remaining candidate and post-promotion rows.
