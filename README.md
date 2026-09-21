# QI Flow

QI Flow is a local-first Windows work-time tracker. Iteration 1 records work sessions,
lunches, daily context, and weekly/monthly totals in a local SQLite database. Epic I adds
user-confirmed Testhuset weekly fills. Google Sheets and SAP integrations remain deferred.

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
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run the architecture shell:

```powershell
python -m qi_flow
```

Run all local checks:

```powershell
.\scripts\check.ps1
```

Individual checks:

```powershell
python -m ruff format --check src tests
python -m ruff check src tests
python -m mypy
python -m pytest
```

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
private Windows application-data folder.

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
  confirmation, single-instance focus, and optional Start with Windows.
- Timesheet review: month grouped by ISO week, daily totals, provisional active time, and weekly
  target progress with per-week overrides.
- Reminders: configurable work/lunch thresholds with tray notifications and 15/30/60-minute snooze.
- Functional user stories: US01–US04 and US09–US20 implemented and verified.
- Packaging: `scripts/build-installer.ps1` produces a per-user Windows installer. A clean-account
  installation verification remains before release.
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
