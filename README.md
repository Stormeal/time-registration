# QI Flow

QI Flow is a local-first Windows work-time tracker. Iteration 1 records work sessions,
lunches, daily context, and weekly/monthly totals in a local SQLite database. Optional Testhuset
and DSB browser integrations use reviewed weekly fills. Google Sheets V2 synchronization requires
per-machine authorization and a verified all-participant migration; SAP remains deferred.

Active product work is tracked in [USER_STORIES.md](USER_STORIES.md). Completed stories and their
acceptance criteria are preserved in [USER_STORIES_ARCHIVE.md](USER_STORIES_ARCHIVE.md).

## Requirements

- Windows 10 or later
- Python 3.12 or later
- A system tray provided by Windows Explorer
- Microsoft Edge for Testhuset scanning/filling and the isolated browser integration tests

## Set up for development

From PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --no-deps -r requirements/windows-build.txt
python -m pip install --no-deps --no-build-isolation -e .
```

Run the application:

```powershell
python -m qi_flow
```

Run all local checks:

```powershell
.\scripts\check.ps1
.\scripts\check-core.ps1
python -m pip check
```

Individual checks:

```powershell
python -m ruff format --check src tests scripts
python -m ruff check src tests scripts
python -m mypy
python -m pytest
```

The full gate always imports this checkout's `src`, including when an editable install points
elsewhere. `check-core.ps1` builds an independent environment under `.tmp/core-check`, installs
the project without runtime dependencies, verifies Qt/browser/Google/credential packages are
absent, and runs the explicit domain/application/SQLite selection with plugin autoload disabled.
PR and push Windows CI run both gates from the pinned dependency files, using installed Edge for
intercepted browser fixtures. Updating pins requires fresh clean-environment checks.

## Create a Windows installer

Install [Inno Setup 6](https://jrsoftware.org/isinfo.php) on the build machine, then run:

```powershell
.\scripts\build-installer.ps1 -InstallDependencies
```

If you use Windows Package Manager, install Inno Setup with:

```powershell
winget install --id JRSoftware.InnoSetup -e
```

The installer is written to `dist\installer`. It installs under the current user's local
application folder and does not require administrator permissions. Uninstalling removes the
application files only; QI Flow's local database, settings, backups, and logs stay in the
private Windows application-data folder. It also removes **Start with Windows** when that
registration still points to the installation being removed. In-app updates retain the installed
uninstaller; installations made before this cleanup change need one installer upgrade to receive
the new uninstall behavior.

## Create a prerelease from GitHub Actions

After the workflow is merged to the repository's default branch, open **Actions → Manual Windows
prerelease → Run workflow**. Select `main`. The optional version field accepts `MAJOR.MINOR.PATCH`,
such as `0.3.0`. Leave it blank to use the next patch version above the current project version and
existing numeric version tags, including older tags without a `v` prefix. Invalid, duplicate, or
non-increasing versions stop before packaging.

The workflow runs the project checks on Windows, builds and smoke-checks the application, creates
the per-user installer and in-app updater package, and publishes a GitHub prerelease named
`QI Flow v<version>` with tag `v<version>`. Download these assets from the release:

- `QI-Flow-Setup-<version>.exe`
- `QI-Flow-Update.zip`

The selected version is stamped only into the build; the workflow does not commit version changes
to the repository. QI Flow ignores prereleases when checking for updates. After reviewing and
testing a prerelease, edit it on GitHub, clear **Set as a pre-release**, and mark it as the latest
release. The existing in-app updater can then discover it as a stable release. Promotion is always
manual; the workflow only creates prereleases when you run it.

## Documentation map

- [REQUIREMENTS.md](REQUIREMENTS.md): stable product requirements and delivery scope.
- [DECISIONS.md](DECISIONS.md): confirmed product decisions from the design interview.
- [USER_STORIES.md](USER_STORIES.md): unfinished stories and current release acceptance.
- [USER_STORIES_ARCHIVE.md](USER_STORIES_ARCHIVE.md): completed stories and the historical register.
- [DESIGN.md](DESIGN.md): interface direction and interaction model.
- [ARCHITECTURE.md](ARCHITECTURE.md): module boundaries, dependency rules, data model, and handoff.
- [AGENTS.md](AGENTS.md): working conventions for implementation agents.

## Current state

- Architecture and project tooling: ready for feature work.
- SQLite migration infrastructure: ready, including active-state recovery and captured rounding policy.
- Desktop Today screen: Start work, Start/End lunch, Finish work, rounding selection, recovery, and Undo.
- Tray and lifecycle: compact popover, context menu mirroring Today's state, explicit Close app
  confirmation, an OS-owned process lock, single-instance focus, and optional Start with Windows.
  Without a tray, window close offers the same explicit exit flow. Exit/restore/update cancel and
  join owned workers; replacement processes start after the process lock is released.
- Timesheet review: month grouped by ISO week, daily totals, provisional active time, and weekly
  target progress with per-week overrides.
- Reminders: configurable work/lunch thresholds with tray notifications and 15/30/60-minute snooze.
- Automatic daily backups run off the UI thread, retain 30 daily copies, retry failures and
  observe Copenhagen date/folder changes. Restore validates its source and keeps a safety copy.
- Manual correction supports independent endpoint dates and explicit DST occurrences. Calendar
  summaries and CSV exports clip effective work and deductions to the requested Copenhagen range.
- Optional Google synchronization uses an immutable causal log, atomic local outbox capture,
  verified readback and explicit conflict resolution. Opening, eligible local edits and five-minute
  checks share one cancellable worker; closing retains pending changes for the next opening.
- DSB hours include only explicitly selected scanned Testhuset branches. Both destination reviews
  require Keep/Replace for every differing row and verify the external save.
- Automated remediation evidence and open release checks are recorded in
  [the progress report](docs/audits/2026-10-03-remediation-progress.md).
- Packaging: `scripts/build-installer.ps1` produces a per-user Windows installer. A clean-account
  installation verification remains before release.
- The same installer build creates `dist/QI-Flow-Update.zip`; the manual prerelease workflow
  attaches it under that exact asset name. QI Flow checks the latest stable release on user request
  and applies an update only after confirmation and SHA-256 verification. No user data or
  credentials are uploaded.
- Corrections and polish: Timesheet offers a completed-entry editor, Today explains configurable
  options with tooltips, and QI Flow uses a dedicated teal Windows icon.

## Testhuset weekly registration (Epic I)

1. In Settings → Testhuset, choose a date in the desired ISO week and **Scan Testhuset tasks**.
   Sign in directly in the temporary Edge window with Remember me unchecked. The browser closes
   when the scan finishes or is cancelled; there is no saved login or password field in QI Flow.
2. Choose your project/task and **Save default task**. No task is preselected. Timesheet →
   Edit sessions offers **Save task assignment** for a completed session; this does not alter its
   timestamps. Choose **Use configured default** to remove an override.
3. Select an ISO-week group or day in Timesheet and choose **Preview Testhuset week**. Sign in
   again. QI Flow navigates to that week, scans current tasks and shows daily net hours per task.
4. For every differing slot (including an empty slot), choose **Keep Testhuset value** or
   **Replace with QI Flow value**, then explicitly press **Fill Testhuset timesheet**. Matching
   values stay untouched. Only previewed slots are considered; unrelated entries are preserved.
5. Each changed slot requires Testhuset's save acknowledgement and matching returned hours.
   Close/approve the week yourself in Testhuset; QI Flow never does this.

The default is resolved when preparing the preview. Changing it affects sessions without an
override, including older sessions. Lunch and sleep-break deductions use effective rounded
boundaries, split at Copenhagen midnight; task/day totals are summed before two-decimal rounding.
The Timesheet Decimal hours column uses periods; CSV exports retain Danish decimal commas.

Task names and identifiers are stored in `testhuset-projects.json` beside the database. A complete
successful scan replaces that cache. Credentials, cookies and tokens are never written to the
database, task cache, logs, exports or backups. Browser traces and recordings are disabled.

If a task disappears, choose a current default/override. Finish active work before filling its
week. Filtered or combined-row Testhuset layouts must be disabled before scanning. Locked days,
missing slots, ambiguous decimals and tasks requiring comments stop the fill with an explanation.
Week navigation supports up to ten years from Testhuset's selected week.

After an error or interruption, some slots may already be saved. Prepare a fresh preview before
retrying; QI Flow re-reads the server and skips matches rather than replaying writes blindly.
Testhuset has no atomic compare-and-set exposed by this UI, so avoid simultaneous edits while
filling. A server-side change in the short interval after reconciliation cannot be locked out.

Verification: the live page's navigation, task identifiers and save-handler contract were
inspected read-only on 17/09/2026. Automated tests use an isolated Edge fixture for saves and
failure responses; no live workplace hours were changed. A first real fill and a packaged
installer smoke test remain release checks.
