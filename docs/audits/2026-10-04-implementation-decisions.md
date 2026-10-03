# Audit implementation decisions — 2026-10-04

The following rulings were recorded while executing the approved remediation plan. They include
behavior decisions and implementation/test organization. External release acceptance remains open;
see the release evidence. No deferred minor code findings remain after the final review fix pass.

- Ruling: Parallel work is limited to disjoint file ownership; root serializes commits and full gates — the developer requires useful parallel delegation — the cost of a mistaken ownership boundary is rework, not shared-index corruption.

- Ruling: Existing planning documents are copied into the isolated worktree; the original checkout stays available — implementation isolation is authorized by the task — final artifacts live in the attached worktree.

- Ruling: No live accounts, workplace writes, publishing, or destructive data tests are part of automated execution; retain manual release gates.

- Ruling: Canonical protocol serialization must encode tuple-keyed aggregate bases as sorted records, not JSON object keys — this makes the planned mapping round-trip deterministic.

- Ruling: Task 2 aggregate overlap/containment follows actual instants; effective rounded intervals retain established clipping rules — preserves D021/D022 and existing behavior — stricter effective-space checks would reject valid rounding.

- Ruling: Root implements Task 3 while independent agents occupy all slots; fresh task review remains mandatory and commits/gates stay serialized.

- Ruling: Native Windows file locking replaces planned stale heuristic and named-mutex fallback; ownership releases with OS process lifetime, socket is focus only.

- Ruling: Task 5 new integration file is test_dsb_browser_rows.py because pytest non-package test modules cannot share the existing unit module basename.

- Task 3: fix round 1/5 in progress for restart ownership ordering and composed no-tray tests. Ruling: include connect configuration-error cleanup and retained-traceback corrupt-file recovery regression — a leaked SQLite handle prevents the recovery path under review — expands Task 3 by two persistence files without changing schema.

- Ruling: Task 6 confirmed validated fill consumes the latest process-scoped prepared preview before external reconciliation/write — US45 requires fresh review after uncertainty/cancellation even if remote value did not change — callers must prepare again after attempted fill; invalid choices/unconfirmed calls do not consume it.

- Ruling: Task16 integration coverage uses separate test_overnight_edits.py — prevents shared-file collision with independent Task17 tests — same owning-layer coverage, no pytest configuration change.

- Ruling: Observation corruption records target-bound incoming variant without raising inside UoW — exception would lose quarantine on rollback — downstream reconciliation/publication must check persistent problems before materializing/acknowledging.

- Ruling: include ReminderView subject ID and stale-dialog recheck in Task17 — otherwise an old dialog snoozes a later timer — supplemental DTO/tray changes preserve reminder identity end-to-end.

- Ruling: update historical time-only UI tests for approved US44 explicit dates, retaining stronger persisted exact-minute checks — old controls were intentionally replaced — no acceptance coverage removed.

- Ruling: Task7a includes SQLite UoW cleanup after rejected commit — a deferred constraint failure leaked the connection/write lock — try/finally close preserves rollback and future commands.

- Ruling: Task7b conservatively authors withdrawal for every Undo of a captured completion, including unattempted groups — preserves immutable causal ancestors and handles uncertain publication uniformly — more log rows, no lost history.

- Ruling: local capture requires target-bound migration_complete plus explicit enable; Task10 must establish baseline heads before activation — prevents unreviewed existing data from becoming unrelated roots — sync stays unavailable until reviewed migration.

- Ruling: Task8 uses publication phase helper before Task9 composes full SyncService; it stages protocol data and never imports records — keeps transport independently testable while ordinary sync remains disabled — public production enable still depends on Tasks9–10.

- Ruling: V2 completed interval payloads require explicit known fields and nonnull created/updated metadata — SQLite requires these and silently inventing missing V2 fields would hide invalid imports — legacy migration must transform explicit known V1 formats separately.

- Ruling: DayDetailsRepository gains delete in Task9 — the approved resolve contract allows null metadata payloads — deletion stays in SQLite adapter, no UI SQL.

- Ruling: quarantine blocks descendants of ambiguous IDs, not only that ID — otherwise a later head falsely claims proven ancestry — unrelated valid components may still materialize.

- Ruling: migration seeds normalize legacy bookkeeping revision/created/updated metadata (revision1, reviewed migration instant), while complete source snapshots and verified SQLite safety copies preserve originals — identical business values must deduplicate without revision winners — source/rounding/effective bounds/assignments remain logical differences.

- Ruling: changed acknowledged local histories block cutover with an explicit restart/review instruction rather than silently choose a newer snapshot — there is no trusted causal relation between simultaneous participant snapshots — preserves changes at cost of repeating migration review.

- Ruling: Task10 UI emits migration actions and shows verified results; production factory/worker wiring is serialized into Task11/12 — avoids constructing adapters in widgets or introducing blocking network UI — ordinary production sync stays unavailable until that wiring.

- Ruling: Task10 UI tests use test_sync_migration_dialog.py because non-package pytest files cannot share the integration basename — preserves scope without changing test discovery.

- Ruling: implement Task12 owned bounded worker before wiring Task11 production Settings — the original authorization blocks indefinitely on UI — avoids exposing a new workflow with known lifecycle hazards while preserving task acceptance.

- Ruling: resolution fingerprints the whole local timesheet in addition to affected heads — detects restored/untracked physical changes and new overlap risks — an unrelated edit may require a fresh review, but no newer entry can be silently overwritten.

- Ruling: normal exit does not force-terminate QThread; bounded OAuth/HTTP cancellation is joined before guard release — prevents callback/SQLite lifetime races — cleanup may wait for the remaining bounded request.

- Ruling: real Qt lifecycle test restores shared fixture quit policy and removes its posted Quit event — application has one event-loop exit while test fixture is reused — prevents false downstream test cancellation without changing production behavior.

- Ruling: best-effort closing starts no fresh sync — two-minute job/15-second request cleanup cannot meet five-second close budget — outbox persists for opening retry; existing worker is cancelled and safely joined.

- Ruling: disable/restore/update and non-Google owned-worker criteria remain Task19 — Google lifecycle alone cannot prove process ownership for all operations — final lifecycle completion stays open.

- Ruling: fully excluded week may open a read-only coverage review with Fill disabled — otherwise unresolved/excluded sessions cannot be inspected as US31 requires — empty allowlist still blocks preview before remote reads.

- Ruling: preserve previously selected missing scanned IDs visibly; never infer replacements — historical assignments become unresolved/excluded until reviewed — requires rescan or explicit selection correction.

- Ruling: extract shared OwnedOperationController and application BackupOperations/views rather than clone the Google worker or import concrete backup adapters into widgets — preserves composition and reuse — adapter exports old view names explicitly for compatibility.

- Ruling: backup controller tests use the same QApplication ownership as production — unparented fixture cycles were collected while Qt queued deferred deletion, aborting the test process — mirrors runtime lifetime; shutdown state and join remain asserted.

- Final: Ruling: reviewer declined real clean-account installation/rollback and disposable two-client Google — retain explicit release gates because these environments are unavailable in this run — real environmental incompatibilities may remain despite fixtures and dry build.

- Final: Ruling: reviewer declined live reviewed Testhuset/DSB writes — user has not authorized concrete week/task/allocation/hour values — destination save behavior remains a release acceptance gate.

- Final: Ruling: browser destination interfaces provide no compare-and-set — preserve documented simultaneous third-party-edit limitation with exact identity/reconciliation/readback safeguards — a remote edit after reconciliation can race the reviewed fill.
