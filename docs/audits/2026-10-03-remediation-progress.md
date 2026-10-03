# QI Flow audit remediation progress

Implementation authorized on 3 October 2026. Follow the
[implementation plan](../superpowers/plans/2026-10-03-audit-remediation.md) and
[original audit](2026-10-02-application-audit.md).

Branch: `codex/audit-remediation-2026-10-03`. Baseline application commit:
`61b507f6a306263effc701bc0eedb03ee9a01ecd`. Work uses temporary databases, synthetic OAuth values,
and local browser fixtures; release checks remain separate.

## Verification baseline

The isolated-worktree baseline passed `scripts/check.ps1`: 258 tests in 57.17 seconds,
Ruff formatting/lint clean, and strict mypy clean for 56 source modules.

## Integrated verification

The first integrated remediation state passed `scripts/check.ps1`: **433 tests in 83.24 seconds**,
100 Python files formatted, Ruff lint clean, and strict mypy clean for 57 source modules.
The source tree at commit `74eb45c` is the verified state; the following commits separate its slices:

| Task | Commit |
| --- | --- |
| 1 — Privacy | `944b23a` |
| 2 — Validation and restore | `03f6d7a` |
| 3 — Core process/exit/recovery lifecycle | `f0ef7d1` |
| 4 — Updater ownership | `370b4c7` |
| 5 — Exact DSB row and reordered verification | `e89f661` |
| 6 — Explicit row choices | `7b6200b` |
| 15 — Calendar and export | `74eb45c` |

The checks used the worktree's `src` explicitly because its local environment reuses the original
checkout's dependency installation. Commit messages include owning stories, acceptance behavior,
focused regression evidence, review, and remaining release checks.

## Second verified batch

The source tree at `0f4c89f` passed `scripts/check.ps1`: **494 tests in 77.93 seconds**,
106 Python files formatted, Ruff clean, and strict mypy clean for 59 source modules.

| Slice | Commit |
| --- | --- |
| A16 parent-date checkpoint | `78c2c14` |
| Task 17 reminder identity, including stale dialogs | `d2b1315` |
| Task 16 explicit overnight editing / US44 | `42eeb8e` |
| Task 7a durable sync foundation and V1 containment | `0f4c89f` |

Task 7a does not complete Task 7 or enable ordinary V2 sync. Local mutation capture,
append transport, causal reconciliation, and reviewed migration remain required.
After the agents reached the account limit, execution continued inline in the existing worktree.

## Task status

| Plan task | Audit / stories | Status |
| --- | --- | --- |
| 1 — Diagnostic privacy | A05; US20, US28 | Complete; independently reviewed and integrated gate passed. |
| 2 — Shared validation / restore | A06, prerequisite for A04; US05, US06 | Complete; 83 focused tests, independent review, integrated gate passed. |
| 3 — Process and exit lifecycle | A08, A11, A17; US09–US11 | Core fix complete; 47 focused tests, review and integrated gate pass. Owned-worker shutdown is carried into Tasks 12–13. |
| 4 — Updater ownership | A07; US32 | Complete; 23 focused tests, review and integrated gate pass; packaged release check remains open. |
| 5 — Exact DSB row | A09; US30 | Complete; 29 focused tests, review and integrated gate pass; live DSB check remains open. |
| 6 — Explicit row choices | A18; US45 | Complete code; 93 focused tests, review and integrated gate pass; destination release checks remain open. |
| 7–13 — Durable sync, migration, conflicts, authorization, schedule | A01–A04, A10; US28, US29, US46 | Task 7a foundation verified; Task 7b capture underway. Ordinary V2 publication remains disabled until protocol and migration pass; public V1 sync visibly refuses unsafe snapshot writes. |
| 14 — DSB allowlist | US31 | Pending. |
| 15 — Calendar and export | A12, A13; US12, US17, US19, US22 | Complete; 32 focused tests, review and integrated gate pass. |
| 16 — Parent dates / overnight editing | A16; US44 | Complete implementation; endpoint-date/DST/dirty-edit and overnight restart tests pass; integrated gate passed. |
| 17 — Reminder identity | A15; US02, US14 | Complete; independent timers and stale-dialog guards pass 57 focused tests and the integrated gate. |
| 18 — Unattended backups | A14; US15 | Pending. |
| 19 — Architecture / CI | Cross-cutting | Pending. |
| 20 — Windows and integration release gates | US21, US28–US32 | Pending. |

## Implementation rulings

- Validation follows actual instants for overlap and containment. Effective rounded boundaries
  remain paired and positive, while valid outward work/nearest lunch rounding retains established
  allocation/clipping behavior. An effective finish may extend past its actual press time.
- Diagnostic privacy uses approved event templates with bounded safe fields. Dependency request
  logs and raw exception/traceback contents cannot enter the rotating file. D066 records the
  stricter boundary without rewriting archived US20's historical criteria.
- Windows process exclusivity will use an OS-released file lock before migrations; the local
  socket remains a focus channel. This avoids age/PID/hostname-based ownership assumptions.
- Confirmed validated external fills consume the latest process-scoped prepared preview before
  reconciliation/write; uncertainty requires preparing a new review even when remote values
  happen to remain unchanged. Invalid choices/unconfirmed calls do not consume a review.
- Failed SQLite connection configuration closes its handle before rethrowing, allowing corrupt
  recovery while the exception traceback remains alive. Restoration retains ownership until the
  file replacement is complete, releases before child launch, and reports launch refusal.
- Task 7 is split into foundation and local mutation capture. Duplicate-ID remote corruption
  preserves both the original and incoming variant as a durable target-bound problem; recording
  the problem must not raise inside its transaction and accidentally roll back the evidence.
- Only disjoint file ownership runs in parallel. Root serializes commits and the integrated
  quality gate. Tasks sharing tracking, Settings, ports, or bootstrap are coordinated explicitly.

## Release checks still required

Clean-account installation/uninstallation, packaged updater success and rollback, two-client
scratch-workbook concurrency and V1 cutover, and authorized live reviewed Testhuset/DSB fills
remain open. Passing fixtures does not close these acceptance checks.
