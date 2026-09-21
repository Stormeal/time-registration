# QI Flow — active user stories

Version: 1.0 · Updated: 2026-09-19 · Status: active backlog only

Completed stories and their original acceptance criteria are preserved in
`USER_STORIES_ARCHIVE.md`. This file contains only unfinished work. A story moves to the archive
after its acceptance criteria pass and any required release smoke check is recorded.

## Delivery status

| Status | Stories | Remaining work |
| --- | --- | --- |
| In progress | US05–US08 | Correction/history interaction and recovery edge cases. |
| In progress | US21 | Clean-account installer and upgrade verification; package Google sync dependencies. |
| In progress | US28 | Complete authorization and synchronization behavior. |
| In progress | US29 | Completed sessions and deductions merge through the shared sheet; explicit conflict resolution and other record types remain. |
| In progress | US30 | Live DSB smoke check and release verification. |
| Not started | US31 | Limit DSB hours to user-approved Testhuset branches. |

## Epic B — Corrections and daily records

### US05 — Add and edit time manually · P0

Implementation status: **In progress** · manual intervals and validation exist; entry-editing UI remains.

As a consultant, I want to add or correct work and lunch intervals so that forgotten or inaccurate entries can be repaired.

Acceptance criteria:

- Completed work and lunch intervals can be added and edited to minute precision without automatic rounding.
- Manual entries choose one work-session date and use time-only start and end controls.
- Future dates, end-before-start, overlaps, orphan lunch/break intervals, and multiple active intervals are blocked with actionable messages.
- The active session start may be corrected if it still contains all child intervals.
- Only timer actions can create open-ended intervals.
- Unsaved form changes require confirmation before discard.

### US06 — Delete, undo, and recover changes · P1

Implementation status: **In progress** · soft deletion and 30-day audit recovery exist; history UI remains.

As a consultant, I want safe correction controls so that an accidental edit or deletion does not permanently destroy my record.

Acceptance criteria:

- Deleting an entry requires confirmation and immediately removes it from totals.
- Deleted entries and previous edited values remain recoverable for 30 days.
- Timer actions expose Undo for 30 seconds.
- Recovery restores the former values and recalculates affected totals.
- History is available from entry details without cluttering the ordinary list.

### US07 — Record daily context · P1

Implementation status: **In progress** · office and note fields exist; cross-midnight context handling remains.

As a consultant, I want to mark office attendance and add a daily note so that the timesheet retains necessary context.

Acceptance criteria:

- Each date has one office/remote value and one multiline Unicode note.
- New days default to remote/unchecked; days without work display no workplace label.
- Context can be entered before work exists and remains attached to the date.
- A cross-midnight session copies office status to the second date, which can then be edited independently.
- Notes are not written to diagnostic logs.

### US08 — Resolve Windows sleep · P1

Implementation status: **In progress** · local sleep-gap resolution exists; remaining platform-hardening work remains.

As a consultant, I want to classify long computer sleep so that unattended time is not silently included or removed.

Acceptance criteria:

- Sleep detection is configurable and defaults to 30 minutes.
- On qualifying resume, QI Flow offers Include as work, Exclude as break, or Decide later.
- Excluded time becomes a labelled deducted break inside the continuous session.
- Decide later permits viewing but disables timer actions until resolved.
- Sleep detection can be disabled.

## Epic G — Setup, settings, and distribution

### US21 — Install and upgrade on Windows · P2

Implementation status: **In progress**.

As a consultant, I want a per-user installer so that I can run QI Flow on permitted Windows machines without administrator rights.

Acceptance criteria:

- Installation, launch, optional startup, upgrade, and uninstall work for the current user without elevation.
- Upgrade preserves the database, settings, backups, and active-state compatibility through explicit schema migrations.
- Uninstall behavior clearly distinguishes application removal from user-data removal.
- No global keyboard shortcuts are registered in iteration 1.

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
- Selecting a Timesheet date exposes **Review & insert DSB hours — week X, YYYY** only when DSB is enabled.
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

- A clean Windows-account test passes installation and the Start → Lunch → End lunch → Finish → edit → restart → export flow.
- Recovery tests cover crash, Windows shutdown, previous-day active state, sleep classification, corrupted database, and invalid rounded intervals.
- Testhuset receives a first real-fill and packaged-installer smoke check without automating week closure.
- DSB receives a live reviewed-fill smoke check without approving or locking the week.

## Review status

- Requirements and decision interview: approved.
- Completed stories: archived in `USER_STORIES_ARCHIVE.md`.
- New epics and stories: intentionally deferred until the current product-direction ideas are reviewed.
