# QI Flow agent instructions

## Start here

Read, in order:

1. `REQUIREMENTS.md`
2. `DECISIONS.md`
3. `USER_STORIES.md`
4. `ARCHITECTURE.md`

Implement one user story or a tightly related group at a time. Keep its acceptance criteria in
the change description and update documentation when a behavior decision changes.

## Architecture rules

- Preserve the inward dependency rule in `ARCHITECTURE.md`.
- Keep domain and application tests runnable without PySide6.
- Keep SQL in versioned migrations or SQLite adapters; never in widgets/controllers.
- Use timezone-aware UTC timestamps internally and Europe/Copenhagen for calendar allocation.
- Use injected clocks and identifier generators in business behavior; do not call current time
  or UUID generation from domain rules.
- Persist timer transitions before presenting them as successful.
- Do not add Google, Testhuset, SAP, telemetry, automatic updates, or global shortcuts during
  iteration 1.
- Do not store credentials, user notes, or time-entry contents in diagnostic logs.

## Quality gate

Run from PowerShell:

```powershell
.\scripts\check.ps1
```

Every behavior change needs a meaningful test at the owning layer. UI tests should validate
interactions and state transitions; avoid brittle pixel or incidental-label assertions.

