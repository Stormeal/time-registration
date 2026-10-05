# QI Flow architecture

Version: 0.2 · Updated: 2026-10-04

## Architectural goals

QI Flow is a small desktop application, so the architecture optimizes for understandable
boundaries rather than maximum abstraction. Business time rules must be testable without Qt,
SQLite, the Windows shell, or a running event loop. External systems planned for later
iterations must attach through application ports instead of entering the domain layer.

## Dependency rule

Dependencies point inward:

```text
ui ───────────────┐
                  ├──> application ──> domain
infrastructure ───┘

bootstrap/composition imports every layer and wires concrete adapters to ports.
```

- `qi_flow.domain` imports only the Python standard library.
- `qi_flow.application` may import `domain`; it defines use cases and ports.
- `qi_flow.infrastructure` implements application ports for SQLite, Windows, files, and clocks.
- `qi_flow.ui` translates Qt events and view state into application commands and queries.
- `qi_flow.bootstrap` is the composition root. No other module creates global services.

The UI never executes SQL. Infrastructure never imports UI code. Domain objects never emit Qt
signals. Qt signals stay in UI controllers/view models, which call ordinary application methods.

## Package layout

```text
src/qi_flow/
  __main__.py                 GUI entry point
  bootstrap.py                composition root and lifecycle
  domain/
    models.py                 entities and value objects
    errors.py                 domain-facing failures
    time_rules.py             rounding, net-time, and midnight-split calculations
  application/
    dto.py                    immutable input/output records
    ports.py                  repositories, unit of work, clock, identifiers
    services.py               use-case boundary (TimeTrackingService protocol)
    time_tracking.py          TimeTrackingApplicationService implementation
  infrastructure/
    paths.py                  per-user Windows paths
    logging.py                privacy-safe rotating diagnostics
    system.py                 system clock and UUID identifier generators
    startup.py                optional "Start with Windows" registry adapter
    updates.py                HTTPS release checks and SHA-256-verified package staging
    single_instance.py        native process ownership lock plus Qt focus socket
    sqlite/
      database.py             connections, transactions, migration runner
      repositories.py         SQLite adapters for every application port
      migrations/             ordered, immutable SQL migrations
  ui/
    main_window.py            application shell and navigation
    today_page.py             Today screen (P0 timer flow)
    manual_entry_dialog.py    explicit-save manual entry form
    tray.py                   tray icon, context menu, lifecycle adapter
    tray_panel.py             compact left-click tray popover
    exit_dialog.py            Close app confirmation (US10)
    formatting.py             shared display-formatting helpers
tests/
  unit/                       pure domain/application tests
  integration/                SQLite adapter and migration tests
  ui/                         pytest-qt interaction tests
```

## Runtime composition

1. `qi_flow.__main__.main` calls the composition root.
2. The composition root sets Qt organization/application metadata before resolving paths.
3. `QStandardPaths.AppLocalDataLocation` supplies the per-user root.
4. Create the data directory and acquire its exclusive native Windows file lock before opening
   SQLite or running migrations. The OS releases ownership on process exit/crash. A second
   process uses the Qt local socket only to request focus and exits without opening SQLite;
   failure to deliver focus never authorizes taking ownership. Lock I/O failures are visible.
5. Initialize privacy-filtered logging and SQLite migrations before constructing the window.
6. `QApplication.setQuitOnLastWindowClosed(False)` keeps the process alive in the tray. If no
   tray is available, window close and the visible Close app action use the same exit coordinator.
7. Exit confirmation follows successful persisted Finish or explicit Keep running. Cancellation,
   pending sleep, invalid finish, or save failure keep the window accessible. Stop owned workers
   before releasing the process lock.

No database connection is shared across threads. Open a short-lived connection or transaction
per application operation. UI updates always return to the Qt main thread through signals.

## Persistence model

Migration `0001_initial.sql` reserves these concepts:

- `work_sessions`: continuous gross work spans with actual and rounded effective boundaries.
- `deductions`: lunch and sleep-break intervals that belong to a work session.
- `day_details`: daily office/remote context and notes.
- `weekly_targets`: per-ISO-week overrides of the configured default target.
- `settings`: local application preferences.
- `audit_entries`: recoverable before-images for edits and soft deletions.

Timestamps are ISO-8601 UTC instants. Dates, ISO-week allocation, and rounding are domain rules
using Europe/Copenhagen. SQLite stores integer durations only when they are derived/cacheable;
source timestamps remain authoritative. Identifiers are UUID strings generated outside SQLite.

Schema migrations are append-only. Never edit a migration that may have run; add the next
numbered migration. Run migrations transactionally before constructing repositories.

## Application boundary

`TimeTrackingApplicationService` owns tracking commands and calendar queries. UI code uses
application services and ports, never concrete adapters or repositories. Commands and queries use
immutable DTOs so adapters do not leak SQLite rows or Qt models into application logic.

`application.desktop` and `application.backups` define export, release, startup, path and backup
boundaries. The composition root injects their implementations and owns replacement-process launch.
Shared aggregate validation lives in `domain.interval_validation`; sync capture, reconciliation,
migration, conflict commands and scheduling have focused application modules.

## Testing strategy

- Unit tests use fixed clocks and in-memory fake ports; they must not import PySide6.
- SQLite integration tests use a temporary on-disk database and real migrations.
- UI tests use pytest-qt's `qtbot` to own widgets, simulate actions, and wait for signals.
- One smoke test verifies application composition against a temporary data root.
- Release verification uses the packaged executable on a clean Windows account.
- `scripts/check.ps1` checks source, tests and maintained Python scripts, strict types and the
  full suite. Recursive architecture checks resolve nested/relative imports and detect SQL/private
  persistence access in widgets.
- `scripts/check-core.ps1` installs the project without runtime adapters in an independent
  environment, asserts Qt/Playwright/Google/keyring are absent and disables plugin autoload.
- Windows PR/push and manual prerelease CI use the same pinned runtime/build dependency set.

Prefer tests around rules and failure boundaries. Avoid tests that only restate widget text or
dataclass fields.

## Future adapters

Google Sheets sync, Playwright/Testhuset, and SAP GUI scripting belong under `infrastructure`
and implement new ports defined in `application`. They must not change domain entities into API
payloads directly; mapping happens in their adapters. No future synchronization or submission
code should be introduced in iteration 1 modules behind inactive flags. Later decisions authorize
the optional Google/Testhuset/DSB adapters now implemented; SAP remains deferred.

## Background operations and replacement

Qt's owned operation controller parents its worker, uses cooperative cancellation and joins native
thread cleanup before signalling shutdown readiness. Google and backup controllers, updater check
and download threads, and temporary Playwright workers participate in one runtime shutdown group.
Exit, restore and explicit update restart freeze commands and drain that group. Restore validates
and replaces SQLite while the process lock remains held. Only after the event loop ends and every
worker joins does bootstrap release ownership and launch a replacement. Restore failure resumes
controllers and tracking; a launch refusal is visible. Repeated close requests cannot replace the
already approved restart command.

Daily backup policy captures the destination and Copenhagen date and schedules opening, day/folder
changes and failure retry. Each copy uses its worker's own bounded SQLite connections, staging and
atomic replacement. The adapter caches derived catalog validity by folder/file metadata to avoid
repeated integrity scans; restore and recovery independently revalidate their source and staged
copy. See the measured [runtime profile](docs/audits/2026-10-04-runtime-profile.md).

## Shared timesheet protocol

Migration `0007_sync_changes.sql` stores target-bound immutable changes, complete atomic groups,
publication attempts/readback, materialized bases, conflicts and quarantined variants. Completed
eligible local mutations capture their outbox changes in the same transaction; active work and
deductions remain local. Network calls run outside database transactions. Causal ancestry and
explicit whole-aggregate validation determine materialization; revision/device order never chooses
a winner. Tombstones and sync evidence outlive local recovery-history expiry.

Existing V1 snapshot publication refuses writes. A separate append-only migration ledger retains
raw V1 data and all declared participants' reviewed snapshots, with verified local safety copies.
Verified all-participant cutover seeds V2 before enabling ordinary sync. A changed acknowledged
history requires a fresh reviewed migration; it cannot silently supersede the acknowledgements.
Opening and committed Finish-work notifications request sync through one worker. The tracking
service invokes its injected Finish callback only after the local/outbox transaction commits.
One-shot timers preserve 30-second Undo grace and coalesce Finish requests while a Google job is
running. No recurring eligibility or network timer remains. Failed attempts await another opening,
Finish or manual Sync now; later automatic requests honor server cooldown. Authorization checks
run outside eligibility transactions because the credential adapter reads saved client settings.
Disable/configuration changes invalidate obsolete work, and closing preserves the outbox for the
next opening. Protocol details and limitations are in [google-sync-v2.md](docs/google-sync-v2.md).

Calendar queries split at Copenhagen midnight and clip work/deductions before aggregation and
rounding. Editors expose independent endpoint dates and reject missing DST times while allowing
explicit occurrence selection for repeated times. Unsaved changes use Save/Discard/Cancel.

## Handoff checklist

### Epic I adapter (2026-09-17)

Epic I is a separately authorized post-iteration-1 addition. `application/testhuset.py` defines
the task-cache and weekly-sheet ports, assignment use cases, allocation, preview and reconciliation.
`domain/testhuset.py` owns task identities and strict decimal-hour parsing/formatting. These modules
remain runnable without Qt or Playwright. Migration 0004 adds a nullable session task override;
the current default uses the existing settings repository and overrides use session audit history.

`infrastructure/testhuset_cache.py` atomically replaces an allowlisted JSON task cache.
`infrastructure/testhuset_browser.py` implements the inspected `weeksheet2.aspx` UI contract with
a non-persistent visible Edge context. The opt-in `WindowsCredentialStore` uses the current Windows
user's Credential Manager entry and is injected at the composition root; it is never part of QI
Flow's data directory, database, backups, exports or logs. The adapter opens `weeksheet2.aspx`
directly before scanning, expands project rows, validates
the date/task field identity and verifies `/ajaxupdatetime` responses (`d` success code `1` plus
the returned accepted value). There is deliberately no close-week port or adapter action.

Invoiced or locked days can render `.ws-form-control-number` values instead of dated inputs.
The adapter reads these only from a unique task row with exactly seven ordered day cells in
the verified ISO week. Matching or explicitly kept values remain untouched; replacing one
stops with a dated locked/invoiced explanation. Unavailable markers and ambiguous layouts
are never inferred as zero hours.

The composition root injects the service and temporary-browser factory into the UI. A dedicated
Qt worker owns every Playwright object throughout one scan/fill. The main thread handles the
preview and choices; an event releases the worker only after explicit confirmation. Cancellation
keeps the dialog alive until browser cleanup completes. Exceptions from Playwright are sanitized;
credentials, cookies, page snapshots and request contents are never logged or persisted.

The adapter reloads the server sheet before confirmation reconciliation. Because the destination
does not offer UI-level atomic compare-and-set, simultaneous external edits remain a limitation.
Browser integration tests intercept all page requests and exercise the DOM/save contract in Edge.

### Epic G updater (2026-09-27)

The infrastructure release client reads only the public GitHub latest-release endpoint and
downloads a fixed-name update ZIP after explicit user confirmation. It validates the version,
asset URL, size, and release asset SHA-256 digest before staging under the existing per-user
application-data directory. It sends no local identity or time-tracking data.

The standard-library updater helper is copied out of the install folder before launch so Windows
does not lock the helper while replacing the application directory. It waits for QI Flow to exit,
re-verifies the staged package, rejects unsafe archive paths, stages on the install volume, and
renames the old app folder aside before moving the new folder into place. Failed replacement
restores the previous folder; user data remains outside the install directory. Release assets must
use the expected filename and expose a SHA-256 digest. The installer remains available for first
installs and recovery.

### Before implementation

Before implementing a story:

1. Read `REQUIREMENTS.md`, `DECISIONS.md`, and the story acceptance criteria.
2. Add or extend a use case in `application` before connecting a widget.
3. Put calculation and validation rules in `domain`, persistence in `infrastructure`, and event
   handling/rendering in `ui`.
4. Add the smallest meaningful tests at the layer that owns the behavior.
5. Run `scripts/check.ps1` and update story status only after acceptance criteria pass.
