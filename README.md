# QI Flow

QI Flow is a local-first Windows work-time tracker. Iteration 1 records work sessions,
lunches, daily context, and weekly/monthly totals in a local SQLite database. Google Sheets,
Testhuset, and SAP integrations are intentionally deferred.

Product behavior is tracked in [USER_STORIES.md](USER_STORIES.md). The first P0 tracking slice is
implemented; remaining iteration-1 stories are still planned.

## Requirements

- Windows 10 or later
- Python 3.12 or later
- A system tray provided by Windows Explorer

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

## Documentation map

- [REQUIREMENTS.md](REQUIREMENTS.md): stable product requirements and delivery scope.
- [DECISIONS.md](DECISIONS.md): confirmed product decisions from the design interview.
- [USER_STORIES.md](USER_STORIES.md): prioritized iteration 1 stories and acceptance criteria.
- [DESIGN.md](DESIGN.md): interface direction and interaction model.
- [ARCHITECTURE.md](ARCHITECTURE.md): module boundaries, dependency rules, data model, and handoff.
- [AGENTS.md](AGENTS.md): working conventions for implementation agents.

## Current state

- Architecture and project tooling: ready for feature work.
- SQLite migration infrastructure: ready, including active-state recovery and captured rounding policy.
- Desktop Today screen: Start work, Start/End lunch, Finish work, rounding selection, recovery, and Undo.
- Functional user stories: US01–US04 implemented and verified.
- Installer: not configured yet.
