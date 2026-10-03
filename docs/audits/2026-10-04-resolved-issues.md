# Resolved audit issues and backlog review — 4 October 2026

Reviewed source checkpoint `509cddc` on `codex/audit-remediation-2026-10-03`; application
changes end at `c8c5b35`. All **18 audit defects A01–A18 are resolved and archived** below.
The [original audit](2026-10-02-application-audit.md) preserves their reproduction details.
There are **no open GitHub issues**: `gh issue list --state open --limit 100 --json
number,title,body,labels,url,updatedAt` returned an empty list for `Stormeal/time-registration`.

Fresh verification for this review: PowerShell `scripts/check.ps1` passed **672 tests in
83.88 seconds**, with **146 files formatted**, clean Ruff checks and strict mypy for **76
source modules**. Tests use temporary databases, synthetic authorization and intercepted Edge
fixtures. No live workplace or Google data was changed. Earlier clean-environment, independent
core and package-build evidence is preserved in the [release record](../release-checks/2026-10-04-audit-remediation.md).

## Archived defects

Each row closes the reproduced code defect. Associated end-to-end release checks remain under
the active stories; closing a defect does not infer that those checks passed.

| ID | Resolution | Owning-layer regression evidence |
| --- | --- | --- |
| A01 — Shared snapshot loss | V1 publication refuses writes; V2 appends immutable target-bound groups, verifies readback and retries the same IDs after an uncertain response. | [Transport tests](../../tests/integration/test_sync_transport.py): two publishers converge to their union; accepted append/lost response/restart preserves records. [V1 refusal](../../tests/unit/test_google_sync_service.py). |
| A02 — Missed independent edits | Causal ancestry replaces numeric revision winners; divergent work and day-detail versions remain explicit conflicts. Valid propagated resolutions close superseded conflicts while later forks remain visible. | [Reconciliation tests](../../tests/integration/test_sync_reconciliation.py): unequal revisions, day-detail forks and propagated resolutions. [Resolution tests](../../tests/integration/test_sync_resolution.py). |
| A03 — Completed lunch without its parent | Capture publishes only eligible completed aggregates after Undo grace. Active parents and their deductions remain local; imports cannot attach a completed child to a running local parent. | [Capture tests](../../tests/integration/test_sync_capture.py): aggregate eligibility, grace and Undo. [Reconciliation tests](../../tests/integration/test_sync_reconciliation.py): active-parent and unresolved-lunch preservation. |
| A04 — Invalid imported aggregates | Shared interval validation checks whole candidate aggregates before transactional materialization; invalid groups stay reviewable without partial totals. | [Reconciliation tests](../../tests/integration/test_sync_reconciliation.py): duplicate spans, overlapping children, malformed records and rollback. [Validation tests](../../tests/unit/test_interval_validation.py). |
| A05 — OAuth values in diagnostics | Persisted diagnostics allow approved events and safe bounded fields only; dependency request logging, exception payloads and tracebacks are excluded. | [Privacy tests](../../tests/unit/test_logging_privacy.py): the installed OAuth callback handler cannot persist synthetic code/state/URLs; repeated setup retains one handler. |
| A06 — Invalid open history restores | Restoration validates open parents, containment and completed overlaps inside the same transaction as the history change. Refusal preserves the existing aggregate. | [Tracking tests](../../tests/integration/test_time_tracking.py): open lunch requires an active parent; restored open work rejects completed overlap. |
| A07 — Unrelated updater rollback | Preflight refusal never rolls back another recovery folder. Swap state identifies only this invocation's recovery tree; failed replacement/relaunch preserves usable files. | [Updater helper tests](../../tests/integration/test_update_helper.py): preexisting recovery, relaunch failure, interrupted placement and cleanup failure. |
| A08 — Two primary Windows processes | An OS file lock owns the data directory before SQLite initialization; the focus socket cannot confer ownership. Crash/exit releases the native lock. | [Real process tests](../../tests/integration/test_single_instance_processes.py): simultaneous launch, pre-listen refusal, focus, crash recovery and lock failure. [Bootstrap tests](../../tests/ui/test_bootstrap_lifecycle.py). |
| A09 — Wrong DSB allocation row | Reads, writes and verification identify the exact full calendar date and allocation; missing or ambiguous rows refuse without repurposing another row. | [Edge row tests](../../tests/integration/test_dsb_browser_rows.py): allocation A/B isolation, missing/duplicate identity, ISO-year boundary and row reordering. |
| A10 — Blocking unbounded authorization | An owned cancellable worker runs OAuth with callback deadlines and bounded HTTP requests. Cleanup releases the server, preserves valid prior credentials and joins before exit. | [OAuth tests](../../tests/unit/test_google_oauth.py): abandonment, cancellation, bounded exchange and credential failure. [UI tests](../../tests/ui/test_google_sync_ui.py): responsive tracking and shutdown. |
| A11 — Exit after failed Finish | Exit is confirmed only after persisted Finish or the explicit Keep running choice. Failed Finish, pending sleep and cancellation preserve access and recoverable state. | [Exit interaction tests](../../tests/ui/test_exit_dialog.py): failed/invalid finish and pending sleep. [No-tray interaction tests](../../tests/ui/test_main_window.py). |
| A12 — Fixed-length DST detail days | Detail queries convert each Copenhagen midnight independently to UTC and include every intersecting completed session. | [Calendar tests](../../tests/integration/test_today_summary.py): first/final hours on spring/autumn days. [Overnight UI tests](../../tests/ui/test_overnight_editing.py): either day exposes the same session. |
| A13 — Export outside the selected period | Detailed CSV clips work and deductions to the selected interval, splits at Copenhagen midnight and agrees with daily summaries. | [Export tests](../../tests/integration/test_backups_and_exports.py): cross-period clipping, DST splitting, out-of-range deductions and legacy intervals. |
| A14 — Missing unattended daily backups | An injected-clock policy schedules startup, Copenhagen day changes, destination changes and bounded failure retry. One owned worker creates atomic validated copies off the UI thread. | [Policy tests](../../tests/unit/test_backup_schedule.py), [backup integration tests](../../tests/integration/test_backups_and_exports.py) and [worker interaction tests](../../tests/ui/test_backup_controller.py). |
| A15 — Later lunch loses reminders | Lunch reminder/snooze state belongs to its deduction ID; work reminders retain their session ID. Old dialogs cannot snooze a replacement timer. | [Tracking tests](../../tests/integration/test_time_tracking.py): second lunch, snooze, end/Undo and deletion. [Tray tests](../../tests/ui/test_tray.py): stale-dialog guard. |
| A16 — Hidden unrelated deduction date | Independent visible start/end dates support selected-parent suggestions and explicit overnight edits without silently moving entered endpoints. | [Parent-date regression](../../tests/ui/test_compact_follow_up.py), [overnight interactions](../../tests/ui/test_overnight_editing.py) and [persisted correction](../../tests/integration/test_overnight_edits.py). |
| A17 — Inaccessible no-tray exit | No-tray window close and the visible Close app action use the shared exit coordinator; cancellation or failed Finish keeps the window accessible. | [Main-window tests](../../tests/ui/test_main_window.py): inactive/active close choices and persistence failure. [Bootstrap lifecycle tests](../../tests/ui/test_bootstrap_lifecycle.py). |
| A18 — Implicit external replacement | Every differing Testhuset/DSB row starts undecided. UI and application require explicit Keep/Replace; matching rows are untouched and uncertain attempts require a new review. | [Dialog tests](../../tests/ui/test_testhuset_ui.py), [Testhuset service tests](../../tests/integration/test_testhuset.py) and [DSB service tests](../../tests/unit/test_dsb_service.py): incomplete choices, matching rows, cancellation and uncertainty. |

The audit's cross-cutting improvements are also implemented: injected runtime boundaries, owned
workers, recursive dependency checks, an independent core environment, pinned Windows CI/build
dependencies, updated operational documentation and measured backup-catalog caching. Evidence is
in [remediation progress](2026-10-03-remediation-progress.md), the [runtime profile](2026-10-04-runtime-profile.md)
and the release record. Splitting every large module was a suggested direction; the approved plan
extracts responsibilities required by these fixes and does not require a blanket rewrite.

## Active story decisions

Every active story was reviewed against its complete acceptance criteria, the implementation,
the tests and recorded release requirements. **No additional active story can be archived yet**:
all eight have implemented behavior but outstanding real-environment acceptance. US44 is already
archived with its original criteria; this review reconfirms its overnight/DST/editor tests.

| Story | Implemented and covered | Acceptance still required |
| --- | --- | --- |
| US21 — Install and upgrade on Windows | Per-user installer, startup ownership, migrations, app-owned uninstall cleanup and separate retained data; packaging/startup/migration tests and successful dry build. | Clean-account installation, launch, startup, upgrade and uninstall/data choices without elevation. |
| US32 — Update QI Flow in place | Release check, explicit confirmation, verified download, path validation, owned replacement/rollback and restart coordination; update UI/client/helper/launcher tests. | Packaged Windows helper wait, swap, relaunch, interruption/rollback and uninstall/data preservation. Explicitly required by the story. |
| US28 — Connect a private shared timesheet | Configuration, Credential Manager boundary, cancellable OAuth, owned worker, explicit sync/status and diagnostic privacy; OAuth, sync, privacy and Settings interaction tests. | Real authorized scratch workbook and packaged connection/disconnect; verify unrelated workbook content survives. |
| US29 — Synchronize records without silent loss | Atomic capture, complete record kinds, causal conflicts, tombstones, active-state protection, offline retry and coalesced scheduling; capture/transport/reconciliation/resolution/schedule/runtime tests. | Two authorized isolated clients against a disposable workbook: real interleaving, uncertain-write recovery, conflicts and deletion/backup recovery. |
| US30 — Review and insert DSB hours | Opt-in/default allocation, selected ISO week, exact row targeting, explicit choices, verified Send and uncertainty safeguards; service, intercepted Edge and dialog tests. | Authorized live reviewed DSB fill/Send and packaged flow, with no approval or locking. |
| US31 — Exclude non-DSB branches | Scanned stable-ID allowlist, resolved overrides, unchanged ordinary/Testhuset totals, excluded/unresolved inspection, empty-selection refusal and stale-review invalidation; DSB service, Settings and review tests. | Live reviewed DSB flow confirms only explicitly included branches contribute. |
| US45 — Choose each differing external value explicitly | Blank/zero differences start undecided; Fill and application validation require every decision; kept/matching/unrelated rows stay unchanged; cancellation and uncertainty close the browser and require fresh review. | Authorized live Testhuset/DSB and packaged destination verification retained by the remediation plan. |
| US46 — Upgrade a shared timesheet without losing history | Explicit pause/roster review, immutable V1/local safety copies, participant acknowledgements, conflict-preserving stable seeds and verified cutover; migration application/adapter/dialog/runtime tests. | Real all-participant migration with two authorized isolated clients; unrelated tabs/formulas and each history remain intact. |

The current closing behavior is already approved in `DECISIONS.md`: closing starts no new sync
request, cancels/joins current bounded work and preserves durable pending changes for opening.
US29's criterion now reflects that decision; no runtime behavior changes in this review.

The [active backlog](../../USER_STORIES.md) retains these stories and their criteria. The
[release record](../release-checks/2026-10-04-audit-remediation.md#required-real-acceptance) defines
the remaining environments and evidence needed to archive them. Historical archived stories
may still mention release limitations; those limitations remain in this shared release checklist.
