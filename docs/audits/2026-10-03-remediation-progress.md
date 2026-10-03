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

## Sync capture and transport verified

The capture slice at `1964347` passed the 505-test quality gate. It records completed aggregates,
metadata, assignment, deletion and restoration with their outbox and causal heads atomically.
Finish uses durable publication grace; Undo records a withdrawal rather than deleting ancestry.

The subsequent append/readback implementation passed `scripts/check.ps1`: **525 tests in
83.55 seconds**, 110 formatted Python files, Ruff clean, strict mypy clean for 61 source modules.
Synthetic shared-Sheet fixtures cover both client orders, lost responses, stable retries, concurrent
local edits, stale configuration, raw quarantine, incomplete groups, cancellation and quota refusal.
This verifies protocol storage and transport; causal materialization and reviewed migration remain
open, so ordinary V2 sync is still disabled.

## Causal reconciliation verified

The causal application core passed `scripts/check.ps1`: **547 tests in 71.58 seconds**,
112 formatted Python files, Ruff clean, strict mypy clean for 62 source modules. Its 22 focused
cases preserve independent heads despite unequal revisions or copied device metadata; refuse
invalid/overlapping imports without partial writes; permit valid unrelated imports; stage missing
ancestry; preserve tombstones after audit expiry; and reconcile restored pending edits before any
append. Imports update provenance without creating a local publication echo. Domain writes occur
only after the whole candidate aggregate validates.

The composed V2 service is still not connected to the production UI. Guided migration, conflict
review, authorization/scheduling and runtime ownership gates remain. The legacy public sync method
and snapshot adapter refuse; the actionable upgrade message in Settings is carried into Task 11.

## Guided migration verified

The migration application, separate Sheets safety ledger, verified SQLite safety-copy adapter and
review dialog passed `scripts/check.ps1`: **572 tests in 71.68 seconds**, 118 formatted Python
files, Ruff clean, strict mypy clean for 65 source modules. The 25 added checks cover divergent and
identical participant histories, deletions, empty machines, unsupported formats, missing
acknowledgements, backup failure, active aggregates, stable retry after lost seed/completion
responses, competing initializers and renewed V1 writes. No real workbook was modified.

This completes the migration components; production Settings and owned-worker wiring remain in
Tasks 11–12. The existing production UI still refuses legacy snapshot synchronization.

## Conflict commands and review verified

The conflict application command and Qt review dialog passed the **586-test quality gate in
77.47 seconds**, with formatting/lint and strict mypy clean. Reviews show local, competing and
causal base values. Each entry requires an explicit choice; corrections use the same exact-minute
Copenhagen date/DST controls as ordinary edits. Invalid incoming dates remain reviewable.
Resolution validates the whole candidate timesheet and atomically saves payloads, every reviewed
causal parent, outbox, provenance and conflict closure. Stale local changes or newly observed
remote heads require a new review; a third writer arriving afterward remains a new conflict.

Task 11's Settings/status/runtime wiring subsequently passed the gate below.

## Production sync and bounded authorization verified

The production application facade, Settings migration/status/conflict actions and owned Google
controller passed `scripts/check.ps1`: **609 tests in 73.73 seconds**, 125 formatted Python files,
Ruff clean and strict mypy clean for 68 source modules. Manual V2 sync becomes available only after
verified migration. Failures retain durable pending counts and the prior confirmed success time.
Authorization runs outside the UI thread, validates callback state, closes its loopback listener,
supports cancellation and has a two-minute deadline. Token exchange, refresh and Sheets HTTP calls
have bounded timeouts; cancellation checks separate every adapter request. Normal exit cancels and
joins the Google worker before releasing database ownership. Tests use synthetic credentials and
temporary databases, including the real Qt shutdown sequence.

Restore/restart and other integration/update worker ownership
still require the remaining lifecycle work; this checkpoint does not claim all exit paths complete.

## Automatic sync verified

Task 13 passed `scripts/check.ps1`: **626 tests in 100.31 seconds**, 127 formatted Python files,
Ruff clean and strict mypy clean for 69 source modules. Reviewed, authorized bindings sync on
opening, after whole groups become eligible following Undo grace, and every five minutes. Edits
during a job coalesce into the next job; acknowledgements do not trigger a publication echo.
Failures and cancellation use exponential backoff capped at five minutes, honoring a longer Google
Retry-After. Disable, disconnect and configuration changes stop obsolete jobs and discard stale UI
deliveries. Resume observes overdue checks. OAuth client setup cannot change during authorization,
and credentials for a different client require renewed authorization.

Closing cancels current work and retains the durable outbox. It starts no new two-minute sync job,
because its bounded HTTP cleanup cannot fit the five-second best-effort closing budget. Local
tracking never opens an authorization prompt automatically. Other worker/restart paths remain
part of Task 19 before final lifecycle acceptance.

## Task status

| Plan task | Audit / stories | Status |
| --- | --- | --- |
| 1 — Diagnostic privacy | A05; US20, US28 | Complete; independently reviewed and integrated gate passed. |
| 2 — Shared validation / restore | A06, prerequisite for A04; US05, US06 | Complete; 83 focused tests, independent review, integrated gate passed. |
| 3 — Process and exit lifecycle | A08, A11, A17; US09–US11 | Core fix complete; 47 focused tests, review and integrated gate pass. Owned-worker shutdown is carried into Tasks 12–13. |
| 4 — Updater ownership | A07; US32 | Complete; 23 focused tests, review and integrated gate pass; packaged release check remains open. |
| 5 — Exact DSB row | A09; US30 | Complete; 29 focused tests, review and integrated gate pass; live DSB check remains open. |
| 6 — Explicit row choices | A18; US45 | Complete code; 93 focused tests, review and integrated gate pass; destination release checks remain open. |
| 7–13 — Durable sync, migration, conflicts, authorization, schedule | A01–A04, A10; US28, US29, US46 | Automated implementation acceptance and integrated gates passed through Task 13. V2 sync requires verified migration; V1 sync refuses snapshot writes. Live multi-client and release checks remain. |
| 14 — DSB allowlist | US31 | Automated acceptance and 634-test gate passed; live reviewed fill/release acceptance remains. |
| 15 — Calendar and export | A12, A13; US12, US17, US19, US22 | Complete; 32 focused tests, review and integrated gate pass. |
| 16 — Parent dates / overnight editing | A16; US44 | Complete implementation; endpoint-date/DST/dirty-edit and overnight restart tests pass; integrated gate passed. |
| 17 — Reminder identity | A15; US02, US14 | Complete; independent timers and stale-dialog guards pass 57 focused tests and the integrated gate. |
| 18 — Unattended backups | A14; US15 | Automated acceptance and 645-test gate passed. |
| 19 — Architecture / CI | Cross-cutting | Automated acceptance passed: 663 full tests, 311 independent core tests, clean locked environment and dependency checks. Final branch review remains. |
| 20 — Windows and integration release gates | US21, US28–US32 | Pending. |

## Implementation rulings

Task 18 passed **645 tests in 81.54 seconds**, 133 formatted Python files, Ruff clean and strict
mypy clean for 73 source modules. A pure Copenhagen date/destination policy schedules opening,
midnight, resume and folder changes with 15-minute failure retries. SQLite copies run on an owned
worker with its own connections and cooperative cancellation; staging and atomic replacement keep
prior valid copies intact on failure. Daily success is scoped to the captured folder/date. Copy and
integrity operations are bounded, the newest 30 daily files are retained, and safety copies remain
separate. Settings receives status and manual backup actions through the application backup port.
The shared owned-worker controller is extracted from the Google controller. Composed exit cancels
and joins both Google and backup work before lock release. Restore/update/temporary-browser workers
are still the remaining Task 19 lifecycle scope.

Task 14 passed **634 tests in 74.29 seconds**, Ruff formatting/lint and strict mypy clean.
Settings selects stable IDs from scanned Testhuset branches without name defaults. DSB uses completed
net seconds for included resolved assignments, including per-session overrides and historical
default resolution, before aggregation/rounding. Other local and Testhuset totals remain unchanged.
The review lists included/excluded totals and every date/branch, calls out unresolved assignments,
and disables Fill for fully excluded weeks. Empty selections block preview before external reads.
Changing the selection invalidates the prepared review even if proposed rounded totals are equal.
Previously selected IDs missing from a later scan remain visible but their assignments are excluded
as unresolved; rescanning never silently substitutes a different branch.

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

Task 19's lifecycle checkpoint passed **662 tests in 101.95 seconds**, 138 formatted Python
files, Ruff clean and strict mypy clean for 76 source modules. Application ports now supply
desktop backup/export/update boundaries. Recursive architecture checks cover nested and relative
imports. Exit, restore and update restart share cancellation and native worker joins across
Google, backup, updater and temporary browser operations. Restoration completes while process
ownership is held; replacement processes launch only after release. Failed restore resumes
tracking, and repeated close requests preserve the approved restart command.

Clean-account installation/uninstallation, packaged updater success and rollback, two-client
scratch-workbook concurrency and V1 cutover, and authorized live reviewed Testhuset/DSB fills
remain open. Passing fixtures does not close these acceptance checks.

The completed Task 19 gate passed **663 tests in 99.12 seconds**, 145 formatted Python files,
Ruff clean and strict mypy clean for 76 source modules. A separately installed locked environment
passed the same gate (**663 tests in 99.71 seconds**) and `pip check`. The independent core
environment passed **311 tests in 21.61 seconds** with Qt, Playwright, Google, keyring and pytest-qt
absent. CI runs both gates on Windows and pins the runtime, development and PyInstaller/build
backend dependencies. The quality gate now checks maintained Python scripts and imports this
checkout's source even when an editable installation points elsewhere.

Profiling 10,000 sessions and 30 backup files found Today refresh at 27.70 ms with 24 SELECTs.
Backup status/list refresh performed 60 integrity scans and took 2,042.86 ms. Derived file-validity
caching with file/folder/day/replacement invalidation tests reduced repeated refresh to 10.03 ms.
Restore and recovery retain independent integrity validation. See
[the profile evidence](2026-10-04-runtime-profile.md).
