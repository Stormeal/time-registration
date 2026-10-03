# QI Flow Audit Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair all 18 audit findings, complete the related safety and synchronization gaps, and verify the resulting application through owning-layer regressions and Windows release checks.

**Architecture:** Keep business validation and reconciliation in domain/application code, with SQLite, Google, Windows, and browser behavior behind injected ports. Add durable sync metadata and append immutable changes instead of replacing a shared snapshot. Extract responsibilities only where the fixes need a shared rule or a clear application boundary.

**Tech Stack:** Python 3.12+, PySide6, SQLite migrations, pytest/pytest-qt, Playwright/Edge, Google Sheets/OAuth, PowerShell, Ruff, strict mypy.

**Spec:** [Application audit](../../audits/2026-10-02-application-audit.md), [requirements](../../../REQUIREMENTS.md), [decisions](../../../DECISIONS.md), [active stories](../../../USER_STORIES.md), [archived acceptance criteria](../../../USER_STORIES_ARCHIVE.md), and [architecture](../../../ARCHITECTURE.md).

**Status:** Implementation authorized and started on 2026-10-03 in the attached `codex/audit-remediation-2026-10-03` worktree. The audited baseline is commit `61b507f6a306263effc701bc0eedb03ee9a01ecd`; its quality gate passed with 258 tests. [Remediation progress](../../audits/2026-10-03-remediation-progress.md) records completed slices and fresh verification; the baseline result does not establish that the fixes pass.

## Global Constraints

- Implement one user story or a tightly related group at a time. Include its acceptance criteria, audit IDs, verification results, and remaining release checks in the change description.
- Preserve the inward dependency rule. Domain uses the standard library; domain/application tests run without PySide6. UI contains no SQL and does not construct concrete integration adapters.
- Use timezone-aware UTC timestamps internally and Europe/Copenhagen for calendar allocation. Inject business clocks and identifiers.
- Persist timer transitions before presenting them as successful. Failed imports, restores, and edits must not leave partially changed aggregates.
- Add the next numbered migration; never edit an applied migration. `0007` is currently next, but recheck before execution.
- Do not store credentials, user notes, or time-entry contents in diagnostic logs. OAuth codes, state, callback URLs, and tokens are included in this exclusion.
- Integrations and updates stay within the later authorizations in DECISIONS.md. Add no SAP integration, telemetry, global shortcuts, or automatic update installation.
- Every behavior change needs a meaningful test at the owning layer. UI tests validate interactions and state transitions, not pixels or incidental wording.
- Use temporary databases, synthetic credentials, fake gateways, and local browser fixtures for automated tests. Live reviewed fills and release checks are separate acceptance work.
- Run `.\scripts\check.ps1` for each completed related change. Keep existing data and unrelated local changes intact.

## Review Focus

- A network write succeeds but its response is lost: restarting and retrying preserves one logical change and never claims unverified success. Task 8 owns this test.
- A session is finished, queued for sync, then reopened by Undo: active timers remain local and remote completion is withdrawn if already published. Task 7 owns this test.
- A backup contains old sync bases or an expired local deletion history: restoring preserves remote tombstones and exposes divergence. Task 9 owns this test.
- Local times occur twice or do not exist on a DST transition: editing chooses the intended UTC instant or rejects the invalid time. Tasks 15–16 own these tests.
- A second process, cancellation, or window close arrives during initialization/background work: one database owner remains and no worker or failed finish produces a false success. Tasks 3, 12, and 13 own these tests.

## Delivery order and coverage

| Phase | Tasks | Purpose and dependencies |
| --- | --- | --- |
| 1 | 1–6 | Protect privacy, aggregate integrity, desktop lifecycle, installed files, and external values. Disable unsafe V1 publication in Task 7 at the start of any sync work. |
| 2 | 7–13 | Replace the sync protocol, migrate deliberately, then expose conflicts and responsive authorization; enable scheduling last. Task 9 requires Task 2. |
| 3 | 14 | Complete existing DSB branch filtering after row identity and explicit decisions are safe. |
| 4 | 15–18 | Correct calendar allocation, exports, manual editing, reminders, and unattended backups. Task 16 follows Tasks 2 and 15. |
| 5 | 19 | Strengthen architecture checks, composition, CI, dependency reproducibility, and affected code structure. |
| 6 | 20 | Reconcile documentation and execute packaged Windows and integration acceptance checks. |

Independent tasks may run in parallel only when they do not edit the same files. Tasks that touch `time_tracking.py`, `settings_page.py`, `ports.py`, or bootstrap must be serialized or integrated in small reviewed changes. Do not enable automatic sync before Tasks 7–11 pass and migration is verified.

| Audit finding | Owning stories | Task |
| --- | --- | --- |
| A01 snapshot loss | US29, proposed US46 | 7–10 |
| A02 missed conflicts | US29 | 7, 9, 11 |
| A03 deduction without parent | US29 | 7, 9 |
| A04 invalid imports | US29 | 2, 9 |
| A05 OAuth logging | US20, US28 | 1 |
| A06 invalid history restoration | US05, US06 | 2 |
| A07 unrelated updater rollback | US32 | 4 |
| A08 simultaneous primary processes | US11 | 3 |
| A09 wrong DSB allocation row | US30 | 5 |
| A10 unbounded UI authorization | US28 | 12 |
| A11 exit after failed finish | US04, US10 | 3 |
| A12 DST detail query | US12, US19, US22 | 15 |
| A13 export outside range | US17 | 15 |
| A14 missing unattended backups | US15 | 18 |
| A15 second-lunch reminder | US02, US14 | 17 |
| A16 hidden deduction date | US05, US22, US35; proposed US44 | 16 |
| A17 no-tray exit | US09, US10 | 3 |
| A18 implicit Replace | US27, US30, US45 | 6 |

US44 adds endpoint-date editing and protected unsaved changes. US45 records the user's confirmed explicit-choice policy, superseding archived US27's default Replace. US46 adds guided migration of existing shared data. Other repairs remain under their existing stories; US31 is already accepted unfinished functionality.

## Working and verification loop

For each task below:

1. Add the named regression at its owning layer. Run the task's focused command and confirm the intended assertion fails on the baseline, rather than an unrelated setup/import failure.
2. Implement the stated behavior and contracts. Re-run the focused command and require all cases to pass.
3. Run `.\scripts\check.ps1`. Review the diff against the owning story's acceptance criteria and privacy/dependency constraints.
4. Update relevant behavior documentation and record evidence in the change description. Make one reviewable commit for the completed slice when executing the plan.

PowerShell commands assume the repository root and `.\.venv\Scripts\python.exe`. New filenames below are planned files, not files already present. Checks for external effects assert calls and persisted results; they never submit real hours.

## Phase 1: Protect existing workflows

### Task 1: Exclude authentication data from diagnostics — A05

**Files:** Modify `src/qi_flow/infrastructure/logging.py`, `src/qi_flow/infrastructure/google_oauth.py`; create `tests/unit/test_logging_privacy.py`.

- [x] Add `test_oauth_callback_never_reaches_diagnostic_file`: invoke the installed OAuth request handler with synthetic code/state and assert neither those values nor the callback URL reaches the actual rotating log file. Include synthetic token/credential exceptions and ordinary approved application events.
- [x] Run `.\.venv\Scripts\python.exe -m pytest tests/unit/test_logging_privacy.py -q` and confirm the callback regression fails.
- [x] Restrict persisted diagnostics to approved application events; suppress dependency HTTP/auth request logging and sanitize failures before logging. Preserve useful error categories without payloads. Ensure installing logging twice does not duplicate handlers.
- [x] Re-run the focused tests and the quality gate. Record the privacy guarantee under US20/US28; do not delete existing user logs automatically.

### Task 2: Share aggregate validation and make restoration transactional — A06, prerequisite for A04

**Files:** Create `src/qi_flow/domain/interval_validation.py` and `tests/unit/test_interval_validation.py`; modify `src/qi_flow/application/time_tracking.py`; extend `tests/integration/test_time_tracking.py`.

**Contract:** `validate_intervals(sessions: Sequence[WorkSession], deductions: Sequence[Deduction], *, as_of: datetime) -> None` raises `DomainError` for invalid live aggregates. Exclude soft-deleted entries internally; interpret open spans through `as_of`. Actual timestamps cannot be in the future, while valid effective rounding may extend a completed finish past the press time as existing rules permit.

- [x] Add `test_restore_open_lunch_requires_active_parent` using delete running work/lunch → restore work → finish → restore lunch. Assert rejection and unchanged SQLite/audit state; later work can still start lunch.
- [x] Add `test_restore_open_work_rejects_completed_overlap`: restore 08:00 open work at noon over saved 09:00–10:00 work. Assert rejection and no five-hour total over the four-hour span.
- [x] Add pure cases for multiple active parents/deductions, missing parents, overlaps, out-of-parent deductions, invalid actual/effective boundaries, valid outward rounding, and touching intervals.
- [x] Run `.\.venv\Scripts\python.exe -m pytest tests/unit/test_interval_validation.py tests/integration/test_time_tracking.py -q`.
- [x] Extract existing rules without changing accepted rounding. Use the validator for edit and history-restoration candidate aggregates before writes; keep validation and audit writes in one unit of work. Expose the same validator to sync reconciliation.
- [x] Re-run and gate; document refusal/recovery behavior under US05/US06.

### Task 3: Own the database exclusively and exit only after the selected action succeeds — A08, A11, A17

**Files:** Modify `src/qi_flow/infrastructure/single_instance.py`, `src/qi_flow/bootstrap.py`, `src/qi_flow/ui/exit_dialog.py`, `src/qi_flow/ui/main_window.py`, `src/qi_flow/infrastructure/sqlite/database.py`; extend `tests/ui/test_single_instance.py`, `tests/ui/test_exit_dialog.py`, `tests/ui/test_main_window.py`; create `tests/integration/test_single_instance_processes.py`, `tests/ui/test_bootstrap_lifecycle.py`; extend `tests/integration/test_database.py`.

- [x] Add barrier-coordinated real Windows process launches. Assert exactly one acquires ownership before any database initialization; the other requests focus and does not open SQLite. Cover primary not yet listening, normal release, crash recovery, and lock-path permission failure.
- [x] Add finish-and-close tests with pending sleep, invalid finish, and persistence failure. Assert no `exit_confirmed`, the timer remains recoverable, and the user can cancel or resolve the failure.
- [x] Add no-tray close tests for inactive exit and active Cancel/Keep running/Finish. Assert the window remains accessible on cancellation or failed finish.
- [x] Run `.\.venv\Scripts\python.exe -m pytest tests/integration/test_single_instance_processes.py tests/ui/test_single_instance.py tests/ui/test_exit_dialog.py tests/ui/test_main_window.py -q`.
- [x] Acquire a per-data-directory native OS file lock on Windows before migrations and retain its descriptor for the process lifetime; the existing socket is only for focus. OS ownership releases on process exit/crash and uses no PID, hostname, or age-based stale heuristic. Refusal to deliver focus never permits removing ownership. Other platforms retain a non-expiring Qt lock fallback. Cover Unicode paths, simultaneous startup, pre-listen startup, crash release, and permission/I/O errors with real Windows process tests. This execution ruling supersedes the initial Qt-lock/named-mutex proposal.
- [ ] Emit exit only after successful finish persistence or explicit Keep running. Give no-tray users the same exit coordinator through window close and a visible Close app action. Shut down workers before releasing database ownership.
- [x] Close SQLite connections on configuration failure so retained exceptions cannot block corrupt-file recovery. Restore while owned, release ownership before replacement launch, and report failed launch.
- [x] Re-run and gate; update architecture runtime ordering and US09–US11 behavior documentation.

### Task 4: Roll back only files swapped by this updater attempt — A07

**Files:** Modify `scripts/update_helper.py`; extend `tests/integration/test_update_helper.py` and `tests/unit/test_packaging.py`.

- [x] Add `test_main_preflight_refusal_preserves_existing_recovery`: current executable contains `current-good`, unrelated `.previous` contains `older-release`. Assert failure leaves both trees byte-for-byte intact.
- [x] Test failure before swap, after current→recovery, after staged→current, during relaunch, and during cleanup. Assert user data and uninstall files survive, and failed cleanup does not destroy the only usable installation.
- [x] Run `.\.venv\Scripts\python.exe -m pytest tests/integration/test_update_helper.py tests/unit/test_packaging.py -q`.
- [x] Track swap state and the recovery path owned by this invocation explicitly. Preflight errors never enter rollback; post-swap rollback uses only the owned recovery tree. Verify resolved file targets stay inside the named staging/install/recovery roots before moving or deleting them.
- [x] Re-run and gate. Keep packaged update/rollback testing open under US32 until Task 20.

### Task 5: Target the exact DSB date and allocation — A09

**Files:** Modify `src/qi_flow/infrastructure/dsb_browser.py`; extend `tests/unit/test_dsb_browser.py`; create `tests/integration/test_dsb_browser_rows.py` with local Edge HTML fixtures.

- [x] Add a day with Allocation A=`4.00` and B=`2.00`. Reading/writing B must use B and leave A unchanged. Assert the actual row identity and saved field values.
- [x] Add missing and duplicate date/allocation matches, ISO-year boundary, changed allocation after preview, and uncertain save response. Assert no write/send on ambiguity.
- [x] Run `.\.venv\Scripts\python.exe -m pytest tests/unit/test_dsb_browser.py tests/integration/test_dsb_browser_rows.py -q`.
- [x] Match stable allocation identity within the selected date/week and require one row. Missing rows produce an actionable refusal; do not repurpose another allocation. Implement insertion of a new empty row only as a separately tested change after the live DOM contract is verified.
- [x] Re-run and gate. Retain DSB Send after confirmed writes and forbid approval/locking.

### Task 6: Require complete external row decisions — A18, US45

**Files:** Modify `src/qi_flow/ui/testhuset_dialog.py`, `src/qi_flow/application/testhuset.py`, `src/qi_flow/application/dsb.py`; extend `tests/ui/test_testhuset_ui.py`, `tests/integration/test_testhuset.py`; create `tests/unit/test_dsb_service.py`.

**Contract:** `FillDecision` has KEEP/REPLACE; `FillDecisions = Mapping[int, FillDecision]`. Both service `fill` methods receive the complete decisions mapping and existing explicit `confirmed` flag. A mapping is complete exactly when its keys are all and only differing preview-slot indices.

- [x] Parameterize dialog tests for Testhuset and DSB: blank/zero/different slots start unselected, Fill is disabled until all decisions are made, returning one row to unselected disables it again, and matching slots need no selection.
- [x] Test direct service calls with missing/invalid choices, stale local assignments, changed remote values, cancellation, and partial-save failure. Assert only explicitly replaced slots are written and no blind retry occurs.
- [x] Run `.\.venv\Scripts\python.exe -m pytest tests/ui/test_testhuset_ui.py tests/integration/test_testhuset.py tests/unit/test_dsb_service.py -q`.
- [x] Add an unselected prompt to each differing row; validate complete choices in the application boundary before any writes. Preserve browser cleanup, fresh-preview requirements, and destination-specific confirmation/send behavior.
- [x] Re-run and gate. Preserve archived US27 and reference the 2026-10-03 decision confirmation and US45.

## Phase 2: Replace unsafe synchronization before scheduling it

### Sync design and shared contracts

Create `src/qi_flow/application/sync_models.py` for immutable protocol DTOs and define repository/gateway ports in `application/ports.py`. Domain entities retain their business meaning; payload serialization belongs at the boundary.

- `SyncTarget`: `spreadsheet_id: str` and `log_id: str`. The immutable V2 initialization manifest supplies the log ID; a changed/recreated log cannot inherit another log's acknowledgements. Namespace all changes, heads, acknowledgements, conflicts, and migration state by this target.
- `SyncChange`: `change_id: str`, `schema_version: int` (V2), `entity_kind: str`, `entity_id: str`, `parent_ids: tuple[str, ...]`, `group_id: str`, `group_members: tuple[str, ...]`, `group_digest: str`, `aggregate_base_heads: Mapping[tuple[str, str], tuple[str, ...]]`, `operation: Literal["upsert", "delete", "withdraw"]`, `payload: Mapping[str, object] | None`, `created_at: datetime`, `device_id: str`. Delete/withdraw operations have null payloads. Canonical serialization gives the same content digest independent of key order.
- A complete group is an immutable commit envelope: every member repeats the same group ID, sorted member-ID list, aggregate base heads, and SHA-256 digest of all canonical member contents excluding `group_digest`. Import/acknowledge only after all listed members and their digest are verified. Causal parents belong to the same `(entity_kind, entity_id)`; aggregate base heads identify the parent/deduction state reviewed by that command. Incomplete groups remain staged, not partially materialized.
- `SyncGateway.read_changes() -> tuple[SyncChange, ...]` and `append_changes(changes: Sequence[SyncChange]) -> None`. Timeout/failure does not imply append failed; the application must verify matching IDs/content before acknowledging.
- `UnitOfWork.sync_for(target: SyncTarget) -> SyncRepository` supplies a target-bound repository. It defines `pending() -> tuple[SyncChange, ...]`, `observed() -> tuple[SyncChange, ...]`, `enqueue(changes: Sequence[SyncChange]) -> None`, `observe(changes: Sequence[SyncChange]) -> None`, `acknowledge(change_ids: Sequence[str]) -> None`, `save_conflict(conflict: SyncConflict) -> None`, `close_conflict(conflict_id: str, reviewed_head_ids: frozenset[str]) -> None`, and `conflicts() -> tuple[SyncConflict, ...]`. `SyncConflict` carries a stable conflict ID, affected entity keys, immutable competing changes, and a validation/conflict reason.
- Extend the injected identifier port with `change_id() -> str`, `group_id() -> str`, and `conflict_id() -> str`. Device provenance is local, contains no username, and is not sufficient to establish ancestry.
- `SyncService.run_once(*, cancelled: Callable[[], bool], deadline: datetime) -> SyncResult` and `resolve(conflict_id: str, reviewed_head_ids: frozenset[str], chosen_payloads: Mapping[tuple[str, str], Mapping[str, object] | None]) -> None`. Each service/gateway instance is bound to one immutable target and configuration generation. Check that generation before publication and before consuming job results; discard obsolete callbacks without redirecting work. `SyncResult` exposes pending/conflict counts and confirmed last-success time without credentials or time-entry contents in logs.

The design uses causal parent IDs: one change descends from another only through that graph. Independent heads remain conflicts regardless of numeric revision. Resolution references every head the user reviewed. Unknown schema, malformed data, missing parents/groups, duplicate ID with different content, and invalid aggregates are durable reconciliation problems, never permission to discard data.

Publish via `AppendCellsRequest` to dedicated `QI_FLOW_SYNC_V2`, with canonical JSON written as `stringValue` cells. Google documents append-after-last-data-row behavior and atomic batch requests, but collaborator changes can affect final state. This append-log protocol is an engineering inference from those primitives and requires the concurrency smoke check in Task 20; readback verifies each published change. [Google append request](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#appendcellsrequest), [batchUpdate guarantees](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate).

Do not clear/replace either shared tab, allocate a destination row from a client-side read, or implement a sheet-cell lock without an atomic acquisition/fencing primitive. Keep compaction out of this repair; capacity/quota failures leave durable pending changes and an actionable state.

### Task 7: Persist local changes and pending publication atomically — A01–A03

**Files:** Create `application/sync_models.py` under `src/qi_flow`, the next `infrastructure/sqlite/migrations/*_sync_changes.sql`, and `tests/integration/test_sync_persistence.py`; modify `application/ports.py`, `application/time_tracking.py`, `application/testhuset.py`, `application/google_sync_service.py`, `infrastructure/sqlite/repositories.py`, and `infrastructure/system.py` under `src/qi_flow`; create `docs/google-sync-v2.md`.

- [ ] Add failure-injection tests: record update or outbox insertion failure rolls back both. Persist two successive offline edits with correct causal parents and retain them through restart.
- [ ] Test completed work, deductions, day details, assignments, deletions, history restores, and configuration opt-out. An ended lunch with active parent creates no publishable orphan.
- [ ] Test Finish → sync request within 30-second Undo window → Undo, including restart before the deadline. Persist the publication grace deadline and attempted-publication state. Test accepted append → lost response → reopen: a possibly published completion cannot be treated as never published. Persist a compensating withdrawal with local Undo; sync verifies/retries the completion before publishing its withdrawal. Active state remains local and Undo needs no network call. Completing again descends from the withdrawal.
- [ ] Test switching Sheet A→B, disabling, and reconnecting while a job is running. A's pending changes and causal state cannot publish to B or be acknowledged by B's job. Establish a new destination's baseline through reviewed initialization/migration.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/integration/test_sync_persistence.py tests/unit/test_google_sync_service.py -q`.
- [ ] Stop unsafe V1 publication with a visible upgrade-required state; local tracking remains available. Add durable changes, publication acknowledgements, causal heads, conflicts, and migration state in a versioned migration. Capture mutations with their outbox changes in the same transaction; never hold that transaction during network work.
- [ ] Define the contracts above and document data kinds, schema, active eligibility, deletion retention, and Undo semantics in `docs/google-sync-v2.md`. Keep existing numeric revisions for history/display only. Do not sync machine credentials or local operational settings.
- [ ] Re-run and gate; verify migration of a copy of an existing database preserves all tracking/settings/audit rows.

### Task 8: Append and verify immutable changes — A01

**Files:** Modify `src/qi_flow/infrastructure/google_sheets_sync.py`, `src/qi_flow/application/google_sync_service.py`; extend `tests/unit/test_google_sync.py`; create `tests/integration/test_sync_transport.py`.

- [ ] Make two clients read the same log and publish independent changes in either interleaving. Assert the remote and both clients converge to the union.
- [ ] Simulate append accepted → response timeout → restart → same-ID retry. Assert one logical change after deduplication, pending acknowledgement until content is read back, and no destruction of existing rows.
- [ ] Cover request rejection, identical duplicate rows, conflicting duplicate IDs, concurrent tab creation/log-identity disagreement, unsupported schema, quota/capacity failure, and strings beginning with `=`. Assert preserved tabs/formulas and no formula interpretation of payloads.
- [ ] Change configuration after append starts but before its result. Verify against the original target and leave the new target's state untouched; never redirect an uncertain retry to another sheet.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/unit/test_google_sync.py tests/integration/test_sync_transport.py -q`.
- [ ] Implement server-side append of complete groups; read back IDs plus canonical content before acknowledging their exact pending IDs. Retry with the original IDs. A local edit made during publication stays pending and cannot be acknowledged by the older job.
- [ ] Re-run and gate. Remove snapshot replacement from V2 paths, and retain V1 reads only for Task 10 migration.

### Task 9: Reconcile causally and validate imports — A02–A04

**Files:** Modify `src/qi_flow/application/google_sync_service.py`; create `src/qi_flow/application/sync_reconciliation.py`, `tests/integration/test_sync_reconciliation.py`; extend `tests/unit/test_google_sync_service.py`.

- [ ] Add unequal-revision independent edits and equal-revision differing day details; assert both variants survive as conflicts. Identify old payloads by explicit version/kind, never absence of `source`.
- [ ] Import different IDs for the same one-hour span. Assert the second invalid group is staged for reconciliation and the timesheet remains 3,600 net seconds, not 7,200. Cover missing parent, active parent, open imports, overlapping deductions, invalid boundaries, future timestamps, and incomplete groups.
- [ ] Test delete-versus-edit, long-offline reconnect after 30-day audit expiry, restored backup with obsolete heads/pending rows, and copied device metadata. Assert remote tombstones are preserved; restored divergence cannot silently resurrect deleted data.
- [ ] Test a valid unrelated group alongside an invalid group, truncated groups, missing/out-of-order ancestors, and concurrent parent-session versus deduction edits: durable observations/conflicts survive restart and no invalid aggregate is partially materialized.
- [ ] Pull the same changes repeatedly and assert zero new outbox records. Remote materialization preserves origin; only local commands and explicit resolutions author new changes. Include restored state bound to a different sheet/log.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/unit/test_google_sync_service.py tests/integration/test_sync_reconciliation.py -q`.
- [ ] Build candidate heads by ancestry, stage missing/invalid input, and materialize complete valid aggregates using Task 2 validation and short SQLite transactions. Keep all competing changes and tombstones beyond audit expiry. Backup restore enters reconciliation before any new shared publication.
- [ ] Re-run and gate under US29, including preservation of stable IDs and assignment/day-detail coverage.

### Task 10: Migrate all participating legacy histories deliberately — proposed US46

**Files:** Create `src/qi_flow/application/sync_migration.py`, `src/qi_flow/ui/sync_migration_dialog.py`, `tests/integration/test_sync_migration.py`, `tests/ui/test_sync_migration.py`; modify `src/qi_flow/infrastructure/google_sheets_sync.py`, `src/qi_flow/bootstrap.py`, `docs/google-sync-v2.md`.

- [ ] Test remote V1 plus two nonempty divergent local snapshots, identical seeds, empty machines, unknown legacy variants, conflicting deletions, interruption/retry, and a crash before completion marker. Assert original snapshots remain recoverable and no revision-based winner is chosen.
- [ ] Test V1 mutation after migration and concurrent V2 tab initialization. Assert publication pauses for renewed legacy writes and unrelated workbook content remains intact.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/integration/test_sync_migration.py tests/ui/test_sync_migration.py -q`.
- [ ] Require an explicit participant roster and all-machines pause/upgrade workflow. Create verified local safety copies and an immutable copy of the original remote snapshot; preserve the V1 tab too. Import every participant's snapshot, deduplicate identical canonical seeds, and retain divergent variants as conflicts. Stable deterministic seed IDs make retries idempotent.
- [ ] Permit only idempotent migration-seed appends before cutover. Record the frozen V1 fingerprint and a completion manifest with verified participant snapshot acknowledgements; verify all seed groups before enabling ordinary V2 publication. Fingerprint checks detect renewed legacy writes when observed and cannot atomically fence old clients. Older snapshot writers cannot participate safely, so do not promise transparent mixed-version compatibility.
- [ ] Re-run and gate. Record the cutover decision when implemented; leave automatic sync disabled until completion is verified.

### Task 11: Expose durable conflicts and truthful status — US28–US29

**Files:** Create `src/qi_flow/ui/sync_conflict_dialog.py`, `tests/ui/test_google_sync_ui.py`; modify `src/qi_flow/application/google_sync_service.py`, `src/qi_flow/application/dto.py`, `src/qi_flow/ui/settings_page.py`, `src/qi_flow/bootstrap.py`; extend `tests/integration/test_sync_reconciliation.py`.

- [ ] Test local/remote/base presentation, explicit valid resolution, cancel, restart with unresolved conflicts, edit/delete conflict, and aggregate overlap requiring correction or deletion. No conflict choice is preselected.
- [ ] Race a third writer with an already-open conflict review. Assert resolution references reviewed heads and the third version remains visible as a new conflict. Also edit locally after opening review: recheck local heads/aggregate within the resolution transaction and require a fresh review rather than overwrite the newer local edit.
- [ ] Test pending, offline, authorization-required, cancelled, invalid-data, conflict, and confirmed-sync states. Last-success time changes only after verified publication/import; display it in Copenhagen time.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/ui/test_google_sync_ui.py tests/integration/test_sync_reconciliation.py -q`.
- [ ] Implement `resolve` through Task 2 validation and the durable change/outbox boundary. Wire services in bootstrap and inject application ports into Settings; remove access to the tracking service's private `_uow_factory`.
- [ ] Re-run and gate against existing US28–US29 conflict/status criteria.

### Task 12: Make Google authorization bounded and cancellable — A10

**Files:** Modify `src/qi_flow/application/ports.py`, `src/qi_flow/infrastructure/google_oauth.py`, `src/qi_flow/ui/settings_page.py`, `src/qi_flow/bootstrap.py`; create `src/qi_flow/ui/google_sync_controller.py`, `tests/unit/test_google_oauth.py`; extend `tests/ui/test_google_sync_ui.py`.

**Contract:** Define an application `GoogleAuthorization` port with `authorize(*, cancelled: Callable[[], bool], timeout_seconds: float) -> None`. The infrastructure adapter owns callback-server polling/cleanup; a UI controller owns the managed worker. Default authorization deadline: 120 seconds.

- [ ] Test no callback, browser abandonment, cancellation, successful callback, credential-store failure, and window close during authorization. Assert callback port/server cleanup and no persisted partial credentials.
- [ ] Use a blocked fake authorization port in Qt tests; timer commands and event processing must still succeed, duplicate authorization is disabled, and completion/error reaches the main thread once.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/unit/test_google_oauth.py tests/ui/test_google_sync_ui.py tests/unit/test_logging_privacy.py -q`.
- [ ] Move authorization off the UI thread. Use short callback waits that check cancellation/deadline; bound HTTP/token exchange and release the callback server in `finally`. Store credentials only after successful authorization, preserve existing valid credentials on failed reauthorization, and never terminate QThread forcibly.
- [ ] Re-run and gate. Cancelled/expired authorization is visible without blocking ordinary tracking or exit.

### Task 13: Add coalesced automatic sync after the protocol passes — US29

**Files:** Create `src/qi_flow/application/sync_schedule.py`, `tests/unit/test_sync_schedule.py`; modify `src/qi_flow/ui/google_sync_controller.py`, `src/qi_flow/bootstrap.py`; extend `tests/ui/test_google_sync_ui.py`, `tests/integration/test_sync_transport.py`.

- [ ] With an injected clock test opening, eligible local commit, five-minute periodic checks, coalesced requests, offline retries, cancellation, resume, and disable/disconnect. Assert one job runs at a time and no jobs start before verified migration.
- [ ] Test edits during an in-flight job, HTTP timeout/429 with Retry-After, target/configuration change, restart, and best-effort closing. Assert pending changes survive failed/cancelled shutdown, old workers cannot update the new connection's status, and no database connection crosses threads.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/unit/test_sync_schedule.py tests/ui/test_google_sync_ui.py tests/integration/test_sync_transport.py -q`.
- [ ] Schedule only completed eligible changes after Task 7's Undo grace period. Use bounded HTTP operations, exponential backoff capped at five minutes (honor a longer server Retry-After), and cooperative cancellation. Closing allows at most five seconds of best-effort sync; do not begin an operation whose bounded cleanup cannot finish within the remaining close budget. Preserve durable pending work on exit.
- [ ] Re-run and gate. Verify no network operation holds a SQLite transaction and no authorization prompt opens automatically during tracking.

## Phase 3: Complete the accepted DSB filtering story

### Task 14: Exclude unapproved Testhuset branches — US31

**Files:** Modify `src/qi_flow/application/dsb.py`, `src/qi_flow/application/dto.py`, `src/qi_flow/ui/settings_page.py`, `src/qi_flow/ui/testhuset_dialog.py`; extend `tests/unit/test_dsb_service.py`, `tests/ui/test_settings_preferences.py`, `tests/ui/test_testhuset_ui.py`.

- [ ] Add sessions assigned to included, excluded, overridden, and unresolved branches. Assert only included completed sessions contribute DSB net hours; ordinary and Testhuset totals are unchanged.
- [ ] Test empty allowlist, historical sessions resolving the current default, deductions, rounding after aggregation, and allowlist change between preview and fill. Assert changed configuration invalidates the prepared review even if rounded totals happen to match.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/unit/test_dsb_service.py tests/ui/test_settings_preferences.py tests/ui/test_testhuset_ui.py -q`.
- [ ] Store user-chosen stable task IDs from the scanned cache; infer none from names. Include an allowlist/configuration version in the review token. Show included/excluded totals with inspection by date/branch; prevent fill when no branch is included.
- [ ] Re-run and gate against every US31 acceptance criterion. No new story is needed.

## Phase 4: Correct calendar and unattended behavior

### Task 15: Use Copenhagen calendar boundaries for details and exports — A12, A13

**Files:** Modify `src/qi_flow/domain/time_rules.py`, `src/qi_flow/application/time_tracking.py`, `src/qi_flow/infrastructure/csv_export.py`; extend `tests/unit/test_time_rules.py`, `tests/integration/test_today_summary.py`, `tests/integration/test_backups_and_exports.py`.

**Contract:** `local_day_bounds(work_date: date) -> tuple[datetime, datetime]` returns UTC bounds computed from that date's local midnight and the next local date's midnight. Export uses existing midnight splitting plus intersection clipping for work and deductions.

- [x] Test 29 March 2026 (23-hour day) and 25 October 2026 (25-hour day), first/final hours and adjacent-date exclusion. The 25 October 23:15–23:45 session appears in details and contributes 1,800 seconds.
- [x] Test September 30 23:00→October 1 01:00: October detail export contains only 3,600 work seconds on October 1. Include deductions entirely outside and partially inside the selected period, midnight/week/month/ISO-year crossings, and both DST transitions.
- [x] Assert detailed work minus deducted fragments equals summary net seconds for each local date and selected period. Preserve UTF-8, semicolons, Danish formats, and exclusion of deleted/actual-press metadata.
- [x] Run `.\.venv\Scripts\python.exe -m pytest tests/unit/test_time_rules.py tests/integration/test_today_summary.py tests/integration/test_backups_and_exports.py -q`.
- [x] Reuse the calendar helper for detail queries and export bounds; clip before emitting daily fragments. Do not assume 86,400 elapsed seconds for a local date.
- [x] Re-run and gate; document split detailed rows under US17 and use the helper in Task 16.

### Task 16: Fix parent dates and add explicit overnight correction — A16, proposed US44

**Files:** Modify `src/qi_flow/ui/manual_entry_dialog.py`, `src/qi_flow/ui/session_editor_dialog.py`, `src/qi_flow/ui/timesheet_page.py`; create `src/qi_flow/ui/date_time_input.py`, `tests/ui/test_overnight_editing.py`; extend `tests/ui/test_compact_follow_up.py`; create `tests/integration/test_overnight_edits.py`.

- [x] First add the A16 regression: open Add entry for October 2, select October 1 parent work, enter 12:00–12:30; visible dates and saved UTC interval belong to October 1 and remain inside that parent.
- [x] Test selecting either side of an overnight session, editing independent endpoint dates, cross-midnight lunch, preservation of session ID/history, and recalculated daily/weekly totals.
- [x] Test dirty row changes/close with Save/Discard/Cancel and failed Save. Cancel retains both input and selection; choosing a different parent does not silently move existing endpoints.
- [x] Test nonexistent 29 March 02:30 and both occurrences of 25 October 02:30. Reject the former; explicitly choose the earlier/later UTC occurrence for the latter. Test future actual timestamps and invalid order/containment.
- [x] Run `.\.venv\Scripts\python.exe -m pytest tests/ui/test_overnight_editing.py tests/ui/test_compact_follow_up.py tests/integration/test_overnight_edits.py -q`.
- [x] Repair the existing parent-date bug as its own small commit, then add the proposed US44 interaction. Use visible date/time controls and an explicit repeated-hour occurrence choice; preserve exact-minute manual semantics and Task 2 validation. Never use automatic midnight rollover inference for edited endpoints.
- [x] Re-run and gate; record the new endpoint-date/DST interaction decision when US44 is implemented.

### Task 17: Scope lunch reminders to each deduction — A15

**Files:** Modify `src/qi_flow/application/time_tracking.py`; extend `tests/integration/test_time_tracking.py` and `tests/ui/test_tray.py`.

- [x] Add `test_second_lunch_in_same_work_gets_own_reminder`: each of two lunches crossing the 45-minute threshold produces its own reminder. Snoozing the first never suppresses the second; a snoozed current lunch respects 15/30/60 minutes.
- [x] Cover lunch end, deletion, undo, restart, and parent work reminder independence with an injected clock.
- [x] Run `.\.venv\Scripts\python.exe -m pytest tests/integration/test_time_tracking.py tests/ui/test_tray.py -q`.
- [x] Key lunch notification/snooze state by deduction ID and work state by session ID. Ignore obsolete legacy parent-keyed lunch state and retire only the finished deduction's reminder state.
- [x] Re-run and gate under US02/US14.

### Task 18: Create daily backups while the app stays open — A14

**Files:** Create `src/qi_flow/application/backup_schedule.py`, `tests/unit/test_backup_schedule.py`; modify `src/qi_flow/infrastructure/backups.py`, `src/qi_flow/bootstrap.py`, `src/qi_flow/ui/settings_page.py`; extend `tests/integration/test_backups_and_exports.py`.

**Contract:** A pure `BackupSchedule(clock: Clock, destination: Callable[[], str])` consumes a normalized destination identity. `due() -> bool` and `record_attempt(destination: str, work_date: date, *, succeeded: bool) -> None` schedule startup/day-change checks and failed-attempt retries. Capture the destination/date when starting each job so that completion cannot mark a different folder or day successful. The Qt composition timer polls once per minute; retry is at most once per 15 minutes.

- [ ] Advance the clock across midnight without restart and assert one successful daily backup per destination/day. A failed attempt retries after 15 minutes, preserves the visible warning, and a later success clears it.
- [ ] Change the folder after today's success and assert the new folder receives a backup. Cover sleep/resume, inaccessible destination, bounded/coalesced workers, clean shutdown, valid SQLite contents, active state, and retention of the latest 30 daily backups.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/unit/test_backup_schedule.py tests/integration/test_backups_and_exports.py -q`.
- [ ] Execute backup copies off the UI thread with a worker-owned SQLite connection; schedule without changing timer transactions. Re-evaluate on folder changes and resume, and do not run overlapping backup jobs.
- [ ] Re-run and gate under US15. Keep backup restore safety copies and existing explicit confirmation behavior.

## Phase 5: Improve maintainability at the repaired boundaries

### Task 19: Enforce composition, reproducible checks, and targeted structure

**Files:** Modify `tests/unit/test_architecture.py`, `src/qi_flow/bootstrap.py`, `src/qi_flow/application/time_tracking.py`, `src/qi_flow/ui/settings_page.py`, `scripts/check.ps1`, `pyproject.toml`, `.github/workflows/prerelease.yml`; create `.github/workflows/check.yml`, `scripts/check-core.ps1`, `pytest-core.ini`, `requirements/windows-build.txt`, and focused modules only for responsibilities actually extracted.

- [ ] Extend architecture checks recursively: domain stdlib only; application imports no infrastructure/UI/Qt; infrastructure imports no UI; widgets contain no SQL/private repository access. Handle relative imports and nested modules.
- [ ] Add `scripts/check-core.ps1` to run a minimal environment containing pytest/tzdata and QI Flow installed without runtime dependencies. Use `pytest-core.ini` without Qt configuration and an explicit selection of pure domain/application test modules, including the new validation/scheduling/service tests. Disable plugin autoload so the full environment's Qt plugin cannot mask this check. No PySide6/Playwright/Google libraries are installed; separate external-adapter tests from this selection.
- [ ] Inject backup/export/update/integration boundaries from bootstrap. Extract aggregate validation, sync reconciliation/scheduling, and focused Settings integration controllers already justified by these tasks. Keep the public tracking service stable and avoid a broad rewrite of unrelated screens.
- [ ] Add PR/push Windows CI running the documented quality gate with pinned tested dependencies and Edge fixtures; retain manual prerelease workflow. Extend lint/format checks to maintained release/updater scripts and verify CI does not rely on uncommitted `.venv` contents or credentials.
- [ ] Profile Today refresh and backup-list integrity scans against representative long history. Record query counts and timings before deciding to optimize; cache only derived results with tested invalidation on edits/restore/day changes if measured work is excessive.
- [ ] Run `.\scripts\check.ps1`, `.\scripts\check-core.ps1`, `.\.venv\Scripts\python.exe -m pip check`, and a clean CI-equivalent environment. Require successful boundary checks and no behavior regressions; document any measured optimization separately.

## Phase 6: Verify release behavior and reconcile documentation

### Task 20: Complete the real delivery gates

**Files:** Update `REQUIREMENTS.md`, `DECISIONS.md`, `ARCHITECTURE.md`, `README.md`, `USER_STORIES.md`, `USER_STORIES_ARCHIVE.md`, and add dated evidence under `docs/release-checks/`.

- [ ] Run `.\scripts\check.ps1` from PowerShell after all integrated changes. Record formatter/lint/type/test results, the final commit, migration versions, and dependency consistency.
- [ ] On a clean Windows account, test per-user install, launch, optional startup, simultaneous launch, Start → Lunch → End lunch → Finish → edit → restart → export, uninstall, and data retention/removal choices. Verify crash, previous-day recovery, sleep decisions, backup/restore, corrupted database handling, and new migration rollback-on-failure.
- [ ] Test the packaged updater with verified package, preflight refusal and preexisting recovery, successful swap/relaunch, interrupted replacement, rollback, uninstall preservation, and retained database/settings/backups.
- [ ] Against a disposable Google workbook and two isolated clients, verify real append interleaving, accepted-write/response-loss recovery, conflicts, V1 cutover, deletion/restore, and preservation of unrelated tabs/formulas. Do not use a user's working sheet for fault injection.
- [ ] Record authorized live Testhuset and DSB reviewed-fill evidence: selected ISO week/year, exact task/allocation, explicit differing-row decisions, only confirmed hours, verified save/DSB Send, and no week closure/approval/locking. Live writes require the user's explicit instruction for the concrete reviewed values; automated fixtures can run without it.
- [ ] Refresh stale implementation claims and document the new lock, sync protocol, worker ownership, calendar export, and overnight editor behavior. Move a story to the archive only after its criteria and required smoke checks pass; keep blocked release checks visibly open.

## Completion criteria

Every A01–A18 row has an implemented owning-layer regression and passing related checks. US28–US29's records/conflicts/schedule work, US31, and the new interaction/migration stories meet their criteria before being marked complete. Existing rounding, recovery, settings, and integration safeguards remain covered. A passing automated suite alone does not close the clean-account installer, packaged updater, scratch-workbook concurrency, or live reviewed-fill gates.

The first implementation slice should be Task 1 (OAuth log privacy), followed by Task 2 (restore/import validation). Keep unsafe shared publication unavailable while the V2 work is underway. Review each slice before proceeding to broader refactoring.
