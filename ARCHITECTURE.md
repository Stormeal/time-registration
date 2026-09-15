# QI Flow architecture

Version: 0.1 · Updated: 2026-09-15

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
  application/
    dto.py                    immutable input/output records
    ports.py                  repositories, unit of work, clock, identifiers
    services.py               use-case boundary for future implementation
  infrastructure/
    paths.py                  per-user Windows paths
    logging.py                privacy-safe rotating diagnostics
    sqlite/
      database.py             connections, transactions, migration runner
      migrations/             ordered, immutable SQL migrations
  ui/
    main_window.py            application shell and navigation
    tray.py                   tray lifecycle adapter
tests/
  unit/                       pure domain/application tests
  integration/                SQLite adapter and migration tests
  ui/                         pytest-qt interaction tests
```

## Runtime composition

1. `qi_flow.__main__.main` calls the composition root.
2. The composition root sets Qt organization/application metadata before resolving paths.
3. `QStandardPaths.AppLocalDataLocation` supplies the per-user root.
4. Logging and SQLite migrations initialize before the main window appears.
5. `QApplication.setQuitOnLastWindowClosed(False)` keeps the process alive in the tray.
6. The tray controller owns explicit process exit. Feature controllers will later own active
   session warnings and recovery decisions.

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

`TimeTrackingService` defines the intended use-case surface without implementing behavior yet.
UI code should depend on that boundary, not concrete repositories. Commands and queries use
immutable DTOs so adapters do not leak SQLite rows or Qt models into application logic.

The first implementation sequence should be:

1. US01–US04: clock, rounding policy, transaction service, and active-state recovery.
2. US05–US08: editing, audit history, daily context, and sleep classification.
3. US09–US14: Qt screens, tray state, summaries, and reminders.
4. US15–US21: backup/restore, export, setup, diagnostics, and packaging.

## Testing strategy

- Unit tests use fixed clocks and in-memory fake ports; they must not import PySide6.
- SQLite integration tests use a temporary on-disk database and real migrations.
- UI tests use pytest-qt's `qtbot` to own widgets, simulate actions, and wait for signals.
- One smoke test verifies application composition against a temporary data root.
- Release verification uses the packaged executable on a clean Windows account.

Prefer tests around rules and failure boundaries. Avoid tests that only restate widget text or
dataclass fields.

## Future adapters

Google Sheets sync, Playwright/Testhuset, and SAP GUI scripting belong under `infrastructure`
and implement new ports defined in `application`. They must not change domain entities into API
payloads directly; mapping happens in their adapters. No future synchronization or submission
code should be introduced in iteration 1 modules behind inactive flags.

## Handoff checklist

Before implementing a story:

1. Read `REQUIREMENTS.md`, `DECISIONS.md`, and the story acceptance criteria.
2. Add or extend a use case in `application` before connecting a widget.
3. Put calculation and validation rules in `domain`, persistence in `infrastructure`, and event
   handling/rendering in `ui`.
4. Add the smallest meaningful tests at the layer that owns the behavior.
5. Run `scripts/check.ps1` and update story status only after acceptance criteria pass.

