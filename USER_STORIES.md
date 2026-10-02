# QI Flow — active user stories

Version: 1.4 · Updated: 2026-09-30 · Status: active backlog only

Completed stories and their original acceptance criteria are preserved in
`USER_STORIES_ARCHIVE.md`. This file contains only unfinished work. A story moves to the archive
after its acceptance criteria pass and any required release smoke check is recorded.

## Delivery status

| Status | Stories | Remaining work |
| --- | --- | --- |
| In progress | US21 | Clean-account installer and upgrade verification; package Google sync dependencies. |
| In progress | US32 | Updater and package staging are implemented; a published release asset and clean-install smoke test remain. |
| In progress | US28 | Complete authorization and synchronization behavior. |
| In progress | US29 | Completed sessions and deductions merge through the shared sheet; explicit conflict resolution and other record types remain. |
| In progress | US30 | Live DSB smoke check and release verification. |
| Not started | US31 | Limit DSB hours to user-approved Testhuset branches. |

## Epic G — Setup, settings, and distribution

### US21 — Install and upgrade on Windows · P2

Implementation status: **In progress**.

As a consultant, I want a per-user installer so that I can run QI Flow on permitted Windows machines without administrator rights.

Acceptance criteria:

- Installation, launch, optional startup, upgrade, and uninstall work for the current user without elevation.
- Upgrade preserves the database, settings, backups, and active-state compatibility through explicit schema migrations.
- Uninstall behavior clearly distinguishes application removal from user-data removal.
- No global keyboard shortcuts are registered in iteration 1.

### US32 — Update QI Flow in place · P2

Implementation status: **In progress** · manual release checks, verified download, staging, and helper-based replacement are implemented; a published update asset and Windows release smoke test remain.

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

Implementation status: **In progress** · Settings validates and saves a Sheet URL and desktop OAuth client ID; authorization and synchronization remain to be implemented.

As a consultant, I want to connect QI Flow to my private Google Sheet so that my work laptop and personal desktop can use the same timesheet safely.

Acceptance criteria:

- Settings accepts a valid `docs.google.com/spreadsheets` URL and desktop OAuth client ID; neither is embedded in the application or diagnostic logs.
- Each machine authorizes directly with Google in a visible browser flow. Refresh tokens are stored only in Windows Credential Manager and can be disconnected from Settings.
- QI Flow creates and owns dedicated structured sync tabs only; existing workbook tabs, formulas, formatting, and history remain unchanged.
- Sync can be initiated explicitly, reports its last successful time and actionable failure state, and never submits workplace time registrations.

### US29 — Synchronize records without silent loss · P1

Implementation status: **In progress** · completed work sessions and deductions are merged by
stable ID and revision. An empty local installation imports remote completed history before it
writes, so it cannot clear the shared tab.

As a consultant, I want completed time records to synchronize between my machines so that I can continue tracking without re-entering time.

Acceptance criteria:

- Work sessions, deductions, day details, assignments, deletions, and revisions use stable IDs and synchronize independently of presentation tabs.
- A completed local change is persisted before any network operation; offline changes remain pending and retry on the next explicit or scheduled sync.
- Concurrent edits to the same record are presented as a conflict with clear local and remote choices; QI Flow never silently overwrites either value.
- Active timers remain local until they become completed records; sync never creates a second active timer on another machine.
- Sync runs at app opening and closing on a best-effort basis, after a local change, and on a bounded periodic schedule while the app is open.

## Epic K — DSB internal time registration

### US30 — Review and insert DSB hours · P1

Implementation status: **In progress** · the Timesheet action, allocation default, reviewed per-day fill, and explicit DSB send flow are implemented; a live DSB smoke check remains.

As a DSB consultant, I want to review and insert a selected ISO week's completed hours into DSB so that I do not have to re-enter them manually.

Acceptance criteria:

- Settings opt-in, allocation scanning, and the default allocation remain per-user and disabled unless the user enables DSB time registration.
- Selecting a Timesheet date enables **Review & insert DSB hours** only when DSB is enabled;
  the selected ISO week number and year remain visible in the adjacent week summary.
- The review lists the chosen week, allocation, QI Flow decimal hours, existing DSB hours, and a per-row keep-or-replace decision before any external value is changed.
- The DSB browser uses the selected ISO week, fills only confirmed rows, then uses DSB's **Send** action. It never approves or locks the week.
- Any uncertain browser result stops the operation and requires a fresh review; it never retries or approves a week automatically.

### US31 — Exclude non-DSB branches from DSB hours · P1

Implementation status: **Not started**.

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
