# QI Flow — active user stories

Version: 1.5 · Updated: 2026-10-03 · Status: active backlog only

Completed stories and their original acceptance criteria are preserved in
`USER_STORIES_ARCHIVE.md`. This file contains only unfinished work. A story moves to the archive
after its acceptance criteria pass and any required release smoke check is recorded.

## Delivery status

| Status | Stories | Remaining work |
| --- | --- | --- |
| In progress | US21 | Per-user installer and uninstall cleanup are implemented; clean-account install, upgrade, and uninstall verification remain. |
| In progress | US32 | Rollback ownership repair is verified; packaged Windows update, uninstall preservation, and failure recovery checks remain. |
| In progress | US28 | Make authorization responsive and cancellable; complete synchronization status and privacy safeguards. |
| In progress | US29 | Replace unsafe snapshot publication, validate imports, and complete conflicts, record coverage, and scheduling. |
| In progress | US30 | Exact row targeting and explicit choices are verified; live DSB and packaged release checks remain. |
| Not started | US31 | Limit DSB hours to user-approved Testhuset branches. |
| In progress | US44 | Endpoint-date/DST editing and protected unsaved changes pass the 494-test gate; final branch review remains. |
| In progress | US45 | Explicit choices pass automated acceptance and review; destination release verification remains open. |
| In progress | US46 | Upgrade existing shared Sheets data while preserving the sheet and every participating machine's history. |

## Epic G — Setup, settings, and distribution

### US21 — Install and upgrade on Windows · P2

Implementation status: **In progress** · uninstall now removes its own optional startup entry;
the installer retains user data and cleans app-owned runtime files. A clean-account Windows
install, upgrade, and uninstall smoke test remains.

As a consultant, I want a per-user installer so that I can run QI Flow on permitted Windows machines without administrator rights.

Acceptance criteria:

- Installation, launch, optional startup, upgrade, and uninstall work for the current user without elevation.
- Upgrade preserves the database, settings, backups, and active-state compatibility through explicit schema migrations.
- Uninstall behavior clearly distinguishes application removal from user-data removal.
- No global keyboard shortcuts are registered in iteration 1.

### US32 — Update QI Flow in place · P2

Implementation status: **In progress** · verified download and preservation of installed uninstall
files are implemented. A07 rollback ownership repair passes regression tests and the integrated
quality gate; packaged Windows update/rollback verification remains.

As a QI Flow user, I want to receive and apply verified application updates from inside the app so
that I do not have to find, download, and run a new installer for every release.

Acceptance criteria:

- QI Flow checks the fixed public GitHub release feed when the user requests a check and tells the
  user when a newer compatible version is available; checks and failures disclose no usage, notes,
  time-entry, or credential data.
- The user explicitly confirms applying an update. QI Flow downloads the application package to a
  staging location and verifies its authenticity and integrity before changing installed files.
- A small updater applies the verified package after QI Flow closes, then relaunches the updated
  application. It requires no administrator rights and leaves the installer available for first
  installation and recovery.
- The updater does not overwrite the database, settings, backups, or other user data. Existing
  schema migrations remain responsible for compatible database changes.
- If download, verification, or file replacement fails, the current installation remains usable or
  is restored, and the user receives an actionable message; retry is safe and does not duplicate or
  damage user data.
- Update checks respect offline conditions and do not block launch or ordinary tracking.
- Tests cover available/no-update responses, invalid or tampered packages, interrupted downloads,
  successful in-place replacement, unsafe archive paths, failure recovery, and preservation of user
  data. A Windows release smoke test verifies helper exit wait, replacement, restart, and rollback.

## Epic J — Google Sheets cross-machine synchronization

### US28 — Connect a private shared timesheet · P1

Implementation status: **In progress** · configuration, cancellable bounded browser authorization,
manual V2 sync, durable operational status and diagnostic privacy pass automated acceptance in the
609-test gate. The owned Google worker closes before process ownership is released. Live workbook
and packaged release acceptance remain.

As a consultant, I want to connect QI Flow to my private Google Sheet so that my work laptop and personal desktop can use the same timesheet safely.

Acceptance criteria:

- Settings accepts a valid `docs.google.com/spreadsheets` URL and desktop OAuth client ID; neither is embedded in the application or diagnostic logs.
- Each machine authorizes directly with Google in a visible browser flow. Refresh tokens are stored only in Windows Credential Manager and can be disconnected from Settings.
- QI Flow creates and owns dedicated structured sync tabs only; existing workbook tabs, formulas, formatting, and history remain unchanged.
- Sync can be initiated explicitly, reports its last successful time and actionable failure state, and never submits workplace time registrations.
- Authorization and sync leave tracking responsive, support cancellation and bounded timeouts,
  and close their temporary callback server and workers after success, cancellation, or failure.
- Callback URLs, codes, state, tokens, credentials, notes, and time-entry contents never enter
  diagnostic logs, including logs emitted by dependencies.

### US29 — Synchronize records without silent loss · P1

Implementation status: **In progress** · unsafe V1 snapshot writes are contained. Durable atomic
capture, append/readback transport, causal aggregate reconciliation, explicit conflict resolution,
and production runtime wiring for A01–A04 pass automated acceptance in the 609-test quality gate.
V2 sync is enabled only after reviewed migration. Opening, eligible committed changes and five-minute
checks share one cancellable worker; failed jobs back off and retain pending changes. The 626-test
gate covers scheduling and connection invalidation. Live multi-client and release acceptance remain.

As a consultant, I want completed time records to synchronize between my machines so that I can continue tracking without re-entering time.

Acceptance criteria:

- Work sessions, deductions, day details, assignments, deletions, and revisions use stable IDs and synchronize independently of presentation tabs.
- A completed local change is persisted before any network operation; offline changes remain pending and retry on the next explicit or scheduled sync.
- Concurrent edits to the same record are presented as a conflict with clear local and remote choices; QI Flow never silently overwrites either value.
- Active timers remain local until they become completed records; sync never creates a second active timer on another machine.
- Sync runs at app opening and closing on a best-effort basis, after a local change, and on a bounded periodic schedule while the app is open.
- Local changes and their pending sync records commit atomically. An uncertain publication can be
  retried after restart without losing changes or creating duplicate logical records.
- Independent changes remain conflicts even when numeric revisions differ. Deletion markers
  survive recovery-history expiry, and restoring a backup cannot silently resurrect shared deletions.
- Imported records obey the same overlap, parent-containment, active-state, and timestamp rules as
  local edits. Invalid groups remain available for reconciliation without partially changing totals.
- Deductions are published only with an eligible completed parent. Finish Undo keeps active state
  local and does not leave a reopened session represented remotely as completed work.

## Epic K — DSB internal time registration

### US30 — Review and insert DSB hours · P1

Implementation status: **In progress** · allocation/date targeting, row-reorder verification, and
the US45 explicit-choice policy pass automated regressions and the integrated quality gate.
A live reviewed DSB smoke check and packaged release verification remain.

As a DSB consultant, I want to review and insert a selected ISO week's completed hours into DSB so that I do not have to re-enter them manually.

Acceptance criteria:

- Settings opt-in, allocation scanning, and the default allocation remain per-user and disabled unless the user enables DSB time registration.
- Selecting a Timesheet date enables **Review & insert DSB hours** only when DSB is enabled;
  the selected ISO week number and year remain visible in the adjacent week summary.
- The review lists the chosen week, allocation, QI Flow decimal hours, existing DSB hours, and a per-row keep-or-replace decision before any external value is changed.
- The DSB browser uses the selected ISO week, fills only confirmed rows, then uses DSB's **Send** action. It never approves or locks the week.
- Any uncertain browser result stops the operation and requires a fresh review; it never retries or approves a week automatically.

### US31 — Exclude non-DSB branches from DSB hours · P1

Implementation status: **In progress** · scanned stable-ID branch selection, resolved assignment
filtering, included/excluded inspection and stale-review invalidation pass automated acceptance in
the 634-test quality gate. Live reviewed DSB and packaged release acceptance remain.

As a consultant who sometimes works on internal Testhuset activities, I want to choose which
Testhuset project/task branches count as DSB work so that QI Flow inserts only DSB-related hours
into DSB.

Acceptance criteria:

- Settings lets the user select one or more branches from the latest scanned Testhuset task list
  as **Included in DSB hours**. No branch is enabled implicitly from its name.
- The selection supports the three currently identified DSB branches without hard-coding them:
  **Teknisk Tester**, **overarb - 50% (de første 3 timer på hverdage)**, and
  **Overarb - 100% (efter 3.time på hd + lør/søn/hd)** under
  **Team Web, DSB (AST) - 12522**.
- DSB allocation uses each completed work session's resolved Testhuset assignment, including its
  per-session override. Only sessions assigned to a branch currently included for DSB contribute
  hours to the DSB preview and fill.
- A session assigned to another Testhuset or internal branch remains available for Testhuset
  registration and ordinary QI Flow totals, but contributes zero hours to DSB.
- The DSB review clearly shows the week's included DSB total and excluded total before any external
  value is changed. Excluded sessions can be inspected by date and branch.
- Sessions with no resolvable Testhuset assignment are excluded and called out for review rather
  than silently treated as DSB work.
- Changing the included-branch selection invalidates an already prepared DSB review and requires a
  fresh review before **Send**.
- If no branch is included, QI Flow blocks DSB preview/fill with an actionable explanation.

## Epic R — Audit follow-ups

The [October audit](docs/audits/2026-10-02-application-audit.md) and
[implementation and test plan](docs/superpowers/plans/2026-10-03-audit-remediation.md) track A01–A18
against their existing owning stories. A repair does not erase the archived acceptance criteria
or become complete until its regression and quality gate pass. The following stories cover
additional interactions or migration capability; proposed stories are not implemented behavior.

### US44 — Correct overnight entries with explicit dates · P2

Implementation status: **In progress** · explicit endpoint dates, DST occurrence choices, overnight
identity/history, and Save/Discard/Cancel interactions pass automated acceptance and the 494-test
quality gate. Final whole-branch review remains. Authorized on 2026-10-03 alongside A12, A13, and A16.
This extends the
historical time-only correction controls in US05 and US22.

As a consultant, I want to correct the dates and times of overnight work and deductions so that
the saved interval reflects what happened without splitting or silently shifting it.

Acceptance criteria:

- Selecting any Copenhagen date intersected by completed work exposes the same session ID,
  including work that began on the previous date.
- Manual and completed-entry editors show independently editable start and finish dates and
  exact-minute times for work, lunch, and deducted breaks.
- Saving preserves entry identity, validates ordering, future actual timestamps, work overlap,
  deduction overlap and containment, and retains the existing 30-day recovery history.
- Selecting another parent or changing a date never silently moves an existing endpoint. Any
  initial suggested dates are visible before saving.
- Changing rows or closing a dirty editor offers Save, Discard, and Cancel. Cancel retains the
  current selection and unsaved input; failed Save leaves the editor open.
- Invalid spring DST times are rejected; ambiguous autumn times require an explicit occurrence
  choice. Daily, ISO-week, monthly, and export totals agree with the saved UTC interval.

### US45 — Choose each differing external value explicitly · P1

Implementation status: **In progress** · explicit choices and application validation pass automated
acceptance, independent review, and the 433-test integrated quality gate. Destination live-fill
and packaged release checks remain open under US30 and the remediation plan. Confirmed by the user on
2026-10-03 for Testhuset and DSB.
This supersedes archived US27's default-to-Replace interaction; D097 and D102 govern the new flow.

As a consultant, I want to choose Keep or Replace for every differing external row so that filling
a timesheet cannot replace a value merely because a default was selected for me.

Acceptance criteria:

- Every differing Testhuset or DSB slot starts with no decision, including blank and zero values.
- Fill remains disabled until every differing slot has an explicit Keep or Replace decision.
  Matching slots require no decision and are not written.
- The application boundary rejects an incomplete decision set even if called outside the dialog.
- Only explicitly replaced, previewed slots are written after confirmation. Kept and unrelated
  values remain unchanged; changed local mappings or external values require a fresh review.
- Cancellation performs no writes and closes the temporary browser. An uncertain or partial save
  stops and requires a fresh review; no automatic retry, week closure, approval, or locking occurs.

### US46 — Upgrade a shared timesheet without losing history · P1

Implementation status: **In progress** · guided migration application, safety-copy adapter and
review dialog and production Settings/worker wiring pass automated acceptance in the 609-test gate.
The two-client live workbook release check remains. Authorized with the remediation plan on 2026-10-03;
required before enabling the replacement sync protocol for
an existing V1 workbook. US29 remains the owner of ordinary synchronization and backup reconciliation.

As a consultant using QI Flow on several machines, I want a guided sync-format upgrade so that
the shared sheet and each machine's local changes survive the upgrade.

Acceptance criteria:

- The upgrade explains that all V1 writers must be paused and upgraded before V2 publication;
  it does not claim safe simultaneous use with older versions that replace the shared snapshot.
- The original remote snapshot is preserved in an immutable safety copy, alongside the original
  sync tab, unrelated workbook tabs, and a verified local safety backup. A declared participant
  roster and verified snapshot acknowledgements include every participating machine's local
  history before cutover is marked complete.
- Identical imported records become one logical seed; different values without proven ancestry
  are preserved as conflicts rather than selected by numeric revision.
- Interrupted upgrade and retry retain stable change IDs and do not clear data or duplicate
  logical records. Completion is recorded durably and verified before automatic sync starts.
- Unsupported schemas or renewed V1 writes pause publication when detected, with an actionable
  explanation. Detection cannot fence a running old writer; pausing and upgrading all V1 writers
  remains a prerequisite.
- Tests cover empty/new machines, multiple nonempty local snapshots, conflicting deletions,
  interrupted upgrades, and preservation of existing workbook content.

## Release acceptance still open

- A clean Windows-account test passes installation and the Start → Lunch → End lunch → Finish → edit → restart → export flow, plus in-place updater success and failure recovery.
- Recovery tests cover crash, Windows shutdown, previous-day active state, sleep classification, corrupted database, and invalid rounded intervals.
- Testhuset receives a first real-fill and packaged-installer smoke check without automating week closure.
- DSB receives a live reviewed-fill smoke check without approving or locking the week.

## Review status

- Requirements and decision interview: approved.
- Completed stories: archived in `USER_STORIES_ARCHIVE.md`.
- US05–US08 (Epic B), US01–US04, US09–US20, US22–US27 and US33–US34 are implemented and verified.
- US21 and US28–US32 remain in progress or not started as shown above.
- US35–US40 (Epics M and N) are complete and archived in `USER_STORIES_ARCHIVE.md`.
- US41 (Epic O, Settings clarity) is complete and archived in `USER_STORIES_ARCHIVE.md`.
- US42 (Epic P, EazyProject assignment on start) is complete and archived in
  `USER_STORIES_ARCHIVE.md`.
- US43 (Epic Q, daily action layout and fair start rounding) is complete and archived in
  `USER_STORIES_ARCHIVE.md`.
- US44 passes automated implementation acceptance; US46 migration code passes automated
  acceptance, with live release acceptance remaining.
  US45 records the explicit row-choice requirement
  confirmed on 2026-10-03. The user authorized implementation of the remediation plan on
  2026-10-03; task and verification progress is recorded alongside the plan.
