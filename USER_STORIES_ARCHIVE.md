# QI Flow — archived user-story register

Archived: 2026-09-27 · Updated with completed Epic B stories

This file preserves the original story definitions, acceptance criteria, and delivery notes. The
current actionable backlog is maintained in `USER_STORIES.md`. Stories marked complete here are
archived; unfinished stories are retained only as historical context and are duplicated in the
active backlog.

These stories implement the confirmed local-only scope in `REQUIREMENTS.md` and `DECISIONS.md`. Priority definitions: **P0** is required for a usable tracker, **P1** is required before iteration 1 ships, and **P2** completes resilience and distribution.

## Delivery status

| Status | Stories | Meaning |
| --- | --- | --- |
| Complete | US01–US04, US09–US20 | Implemented and covered by automated checks. |
| Complete | US22–US24 | Implemented and covered by automated checks. |
| Complete | US05–US08 | Correction, history, daily context, and sleep recovery implemented and covered by automated checks. |
| In progress | US21 | Installer and uninstall cleanup are implemented; clean-account verification remains. |
| Complete | US25–US27 | Epic I implemented and covered by automated checks; live first-fill and packaged smoke checks remain. |

## Epic A — Core tracking

### US01 — Start and finish work · P0

Implementation status: **Complete** · verified by `tests/integration/test_time_tracking.py`.

As a consultant, I want to start and finish work with one action so that QI Flow records my work without manual calculation.

Acceptance criteria:

- Start work immediately persists an active session with actual and effective start information.
- The active timer shows actual elapsed and net time.
- Finish work applies the configured rounding, persists the result, and updates daily and weekly totals.
- Starting again after Finish creates another session on the same date.
- Start and Finish actions offer Undo for 30 seconds.

### US02 — Record lunch inside a continuous session · P0

Implementation status: **Complete** · verified by `tests/integration/test_time_tracking.py`.

As a consultant, I want to time lunch without ending my work session so that gross presence and net work remain accurate.

Acceptance criteria:

- Start lunch is available only during active work and begins a persisted lunch interval.
- The work session retains its original start while lunch is active.
- End lunch closes and deducts the rounded lunch interval.
- Finish work is unavailable while lunch is active.
- Multiple lunch intervals are allowed in one work session, and invalid or zero-length rounded lunches require correction.

### US03 — Apply configurable rounding · P0

Implementation status: **Complete** · verified by `tests/integration/test_time_tracking.py`.

As a consultant, I want consistent rounding for timer actions so that recorded hours follow my chosen precision.

Acceptance criteria:

- Settings offers 1, 5, 10, and 15-minute intervals, defaulting to 5.
- Timer-created work starts round down and finishes round up to the configured boundary.
- Timer-created lunch deductions use nearest-interval rounding when completed.
- Live elapsed time uses actual timestamps; ordinary timesheets use effective rounded timestamps.
- Changing the setting affects future actions only.
- Entry details can show original action timestamps.

### US04 — Recover active tracking state · P0

Implementation status: **Complete** · verified by `tests/integration/test_time_tracking.py`.

As a consultant, I want active work and lunch to survive crashes, shutdowns, and process exit so that time is not silently lost.

Acceptance criteria:

- Every state transition is committed transactionally before the interface reports success.
- Reopening reconstructs active work, lunch, and deducted-break state from timestamps.
- An unfinished previous-day session blocks new timer actions until resolved.
- Recovery allows setting a finish, deleting, continuing, or opening the timesheet without inventing a suggested finish time.
- Continuing a cross-midnight session allocates totals between dates correctly.

## Epic B — Corrections and daily records

### US05 — Add and edit time manually · P0

Implementation status: **Complete** · verified by integration and UI tests.

Audit amendment (2026-10-03): edits and history restores validate the affected aggregate before audit/save. Invalid restored boundaries, open deductions under completed parents, and overlapping work are refused transactionally; unrelated legacy defects can be repaired independently (A06).

As a consultant, I want to add or correct work and lunch intervals so that forgotten or inaccurate entries can be repaired.

Acceptance criteria:

- Completed work and lunch intervals can be added and edited to minute precision without automatic rounding.
- Manual entries choose one work-session date and use time-only start and end controls.
- Future dates, end-before-start, overlaps, orphan lunch/break intervals, and multiple active intervals are blocked with actionable messages.
- The active session start may be corrected if it still contains all child intervals.
- Only timer actions can create open-ended intervals.
- Unsaved form changes require confirmation before discard.

### US06 — Delete, undo, and recover changes · P1

Implementation status: **Complete** · verified by integration and UI tests.

Audit amendment (2026-10-03): restoring a before-image is subject to the same current aggregate rules as editing. A refusal preserves both persisted entries and audit history for a subsequent correction (A06).

As a consultant, I want safe correction controls so that an accidental edit or deletion does not permanently destroy my record.

Acceptance criteria:

- Deleting an entry requires confirmation and immediately removes it from totals.
- Deleted entries and previous edited values remain recoverable for 30 days.
- Timer actions expose Undo for 30 seconds.
- Recovery restores the former values and recalculates affected totals.
- History is available from entry details without cluttering the ordinary list.

### US07 — Record daily context · P1

Implementation status: **Complete** · verified by integration and UI tests.

As a consultant, I want to mark office attendance and add a daily note so that the timesheet retains necessary context.

Acceptance criteria:

- Each date has one office/remote value and one multiline Unicode note.
- New days default to remote/unchecked; days without work display no workplace label.
- Context can be entered before work exists and remains attached to the date.
- A cross-midnight session copies office status to the second date, which can then be edited independently.
- Notes are not written to diagnostic logs.

### US08 — Resolve Windows sleep · P1

Implementation status: **Complete** · verified by integration and UI tests.

As a consultant, I want to classify long computer sleep so that unattended time is not silently included or removed.

Acceptance criteria:

- Sleep detection is configurable and defaults to 30 minutes.
- On qualifying resume, QI Flow offers Include as work, Exclude as break, or Decide later.
- Excluded time becomes a labelled deducted break inside the continuous session.
- Decide later permits viewing but disables timer actions until resolved.
- Sleep detection can be disabled.

## Epic C — Tray and lifecycle

### US09 — Operate from the system tray · P0

Implementation status: **Complete** · verified by `tests/ui/test_tray.py`.

Audit amendment (2026-10-03): without a system tray, window close and a visible Close app action use the shared exit coordinator. Cancel or failed Finish keeps the window accessible (A17).

As a consultant, I want QI Flow available from the tray so that tracking does not occupy my taskbar or interrupt other work.

Acceptance criteria:

- Closing the main window hides it to the tray without changing timer state.
- Left-click opens a compact panel containing current state, net time, session start, lunch duration, the valid timer action, Add entry, and Open timesheet.
- Right-click shows the confirmed context-menu actions and an explicit Close app command.
- Tray and main-window state remain consistent after every action.

### US10 — Exit safely · P1

Implementation status: **Complete** · verified by `tests/ui/test_exit_dialog.py`.

Audit amendment (2026-10-03): Finish and close emits process exit only after successful persistence. Pending sleep, invalid finish, and persistence failures leave the timer recoverable; explicit Keep running still allows exit (A11).

As a consultant, I want an explicit warning when closing QI Flow during active tracking so that I choose what happens to the session.

Acceptance criteria:

- Close app exits immediately when no session is active.
- During work, it offers Keep running and close, Finish work and close, or Cancel, with Cancel selected by default.
- During lunch, it offers the equivalent explicit resolution without silently ending lunch.
- Keeping the session running explains that reminders pause while the process is closed.
- Finish and close saves the rounded finish before process exit.

### US11 — Start once and focus the existing app · P1

Implementation status: **Complete** · startup verified by `tests/unit/test_startup.py`; ownership
regressions are in `tests/ui/test_single_instance.py`,
`tests/integration/test_single_instance_processes.py`, and `tests/ui/test_bootstrap_lifecycle.py`.

Audit amendment (2026-10-03): exclusive OS ownership is acquired before opening SQLite, including while the primary is not yet listening for focus. Failed focus delivery does not permit a second database owner. Crash/normal exit releases native Windows ownership (A08).

As a consultant, I want predictable Windows startup and single-instance behavior so that two processes cannot alter the same database.

Acceptance criteria:

- Start with Windows is optional and disabled by default.
- Automatic startup stays in the tray unless recovery needs attention.
- Manual launch opens the full window.
- A second launch activates the existing process and never opens the live database concurrently.

## Epic D — Review and totals

### US12 — Review a month by ISO week · P0

Implementation status: **Complete** · verified by integration and UI tests.

As a consultant, I want every day grouped by week number so that I can identify missing entries and review monthly time quickly.

Acceptance criteria:

- The month lists every day, including weekends, in ISO Monday–Sunday week groups.
- Each daily row shows first start, final finish, session count, total lunch, net time, office status, and notes indicator.
- A date with multiple sessions shows its boundary times and session count without treating the gap as work.
- Selecting a day opens all sessions and deductions for editing.
- Active time is clearly provisional.

### US13 — Review weekly progress · P1

Implementation status: **Complete** · verified by integration and UI tests.

As a consultant, I want weekly totals compared with my target so that I know whether time is remaining or over target.

Acceptance criteria:

- The default target is 37 hours.
- A per-week override changes only the selected week.
- The summary reports logged and remaining/excess time neutrally.
- Weekly and monthly summaries show hours/minutes and decimal hours.
- Cross-midnight work is allocated to the correct ISO week.

## Epic E — Reminders

### US14 — Receive configurable reminders · P1

Implementation status: **Complete** · verified by `tests/integration/test_time_tracking.py`.

As a consultant, I want reminders for unusually long work and lunch so that I catch forgotten timer actions.

Acceptance criteria:

- Work reminder defaults to 9 elapsed hours including lunch; lunch reminder defaults to 45 minutes.
- Each reminder can be enabled and configured independently.
- Notifications show relevant elapsed/net time and offer Open QI Flow or 15/30/60-minute snooze.
- State-changing actions occur inside QI Flow.
- Reminders resume correctly after normal process restart and remain unavailable while the process is closed.

## Epic F — Local resilience and export

### US15 — Back up local data automatically · P1

Implementation status: **Complete** · active-session start correction covered by
`tests/integration/test_time_tracking.py`.

As a consultant, I want automatic backups so that a machine or database problem does not erase my only timesheet.

Acceptance criteria:

- QI Flow creates at most one complete, consistent backup per day and retains the newest 30.
- Backup includes active state and settings.
- Settings shows and allows changing the backup folder, including OneDrive locations.
- A failure never blocks tracking and produces a persistent warning until a backup succeeds.

### US16 — Restore data safely · P2

Implementation status: **Complete**.

As a consultant, I want guided restoration so that I can recover without accidentally overwriting the only usable database.

Acceptance criteria:

- Restore lists valid backups with timestamps and requires active ambiguity to be resolved first.
- QI Flow makes a safety backup of current data before replacement.
- Restoration requires confirmation and restarts the application afterward.
- If the live database is unreadable, QI Flow does not create an empty replacement and offers the newest valid backup.

### US17 — Export readable timesheets · P1

Implementation status: **Complete**.

Audit amendment (2026-10-03): detailed export clips to the selected Copenhagen calendar period and splits work/deduction fragments at local midnight. Completed legacy boundaries use the same fallback as summaries; active/deleted records and raw action metadata are excluded (A13).

As a consultant, I want CSV exports so that I can inspect or reuse my local records outside QI Flow.

Acceptance criteria:

- Export supports selected week, selected month, or all history.
- Summary export emits one row per date; detailed export separates sessions, lunches, and deducted breaks.
- Files are UTF-8 and semicolon-separated with Danish dates, 24-hour times, and decimal commas.
- Deleted data and actual unrounded action metadata are excluded.

## Epic G — Setup, settings, and distribution

### US18 — Configure QI Flow on first launch · P1

Implementation status: **Complete**.

As a consultant, I want a short initial setup so that useful defaults are transparent and adjustable.

Acceptance criteria:

- One compact screen presents rounding, target, startup, reminders, sleep threshold, theme, and backup settings.
- Confirmed defaults from D072 are preselected.
- Completion creates the local database and opens Today.
- The setup can be completed without administrator permissions or network access.

### US19 — Use Danish formats in an English interface · P1

Implementation status: **Complete**.

As a consultant, I want familiar regional formatting so that timesheets match how I work in Denmark.

Acceptance criteria:

- Labels are English; dates use `dd/MM/yyyy`; times use 24-hour format; week numbers follow ISO 8601.
- Elapsed calculations use Europe/Copenhagen and remain correct through daylight-saving changes.
- Theme follows Windows by default with Light and Dark overrides.
- QI Flow uses the approved teal direction and consistent QI app/tray identity.

### US20 — Diagnose locally without telemetry · P2

Implementation status: **Complete**.

Audit amendment (2026-10-03): D066 and the current privacy requirement strengthen the historical
criteria below. Persistent diagnostics contain only approved application events and never store
credentials, OAuth callback URLs/codes/state/tokens, notes, or time-entry contents, including
dependency requests and exception payloads. Audit A05 tracks the regression and verification.

As a user, I want privacy-safe local diagnostics so that problems can be investigated without sending my work records elsewhere.

Acceptance criteria:

- Logs retain approximately seven days and avoid notes and time-entry contents where possible.
- Settings exposes the application-data path and Open log folder.
- The live database path is standard per-user application data and cannot be relocated from the UI.
- QI Flow performs no telemetry. This archived update-check restriction is superseded by revised
  requirement R30 and Epic G story US32, which authorize verified in-app updates.

### US21 — Install and upgrade on Windows · P2

Implementation status: **In progress** · optional startup cleanup and preservation of user data
are implemented; clean-account install, upgrade, and uninstall verification remains.

As a consultant, I want a per-user installer so that I can run QI Flow on permitted Windows machines without administrator rights.

Acceptance criteria:

- Installation, launch, optional startup, upgrade, and uninstall work for the current user without elevation.
- Upgrade preserves the database, settings, backups, and active-state compatibility through explicit schema migrations.
- Uninstall behavior clearly distinguishes application removal from user-data removal.
- No global keyboard shortcuts are registered in iteration 1.

## Epic H — Corrections, guidance, and identity

### US22 — Correct completed timesheet entries · P1

Implementation status: **Complete**.

As a consultant, I want to edit completed work sessions and deductions from Timesheet so that
my recorded time remains accurate when I notice a mistake.

Acceptance criteria:

- Selecting or double-clicking a day provides an **Edit sessions** action that opens a focused editor
  for that day.
- The editor lists completed work sessions and their lunch/break deductions, and supports adding,
  editing, and deleting them.
- The editor title identifies the selected date; its list and correction controls show times only.
- The editor can mark the selected day as office work without changing its daily note.
- A running work session is shown for its start date; its start time can be corrected without
  stopping the timer. Its finish time and any active lunch remain managed through Today.
- Edited entries use the exact start and finish times entered by the user; they are treated as
  manual corrections and are not rounded again.
- Saves reject overlapping work sessions and deductions that fall outside their parent session,
  with a clear explanation of the conflict.
- Existing 30-day audit and restore behavior remains available for changed and deleted entries.

### US23 — Explain configurable Today options · P2

Implementation status: **Complete**.

As a consultant, I want short help text for Today options so that I can enable them with
confidence.

Acceptance criteria:

- Hovering a configurable Today label or control shows a concise English tooltip.
- Tooltips cover rounding, office location, sleep detection, sleep threshold, work reminder, and
  lunch reminder.
- Each tooltip states the trigger and the resulting behavior.
- Sleep-detection help states that QI Flow asks for a decision and never removes time
  automatically.

### US24 — Use a recognizable QI Flow icon · P2

Implementation status: **Complete**.

As a consultant, I want a clear QI Flow icon so that I can find the application reliably in
Windows.

Acceptance criteria:

- The icon is a modern teal QI monogram with a subtle clock hand and remains recognizable at tray
  size.
- A multi-resolution Windows `.ico` file and PNG source assets are versioned with the project.
- The same icon appears in the application window, tray, packaged executable, Start menu, and
  installer.
- The icon remains legible in light and dark Windows themes.

## Epic I — Testhuset weekly registration

### US25 — Scan Testhuset project tasks · P1

Implementation status: **Complete** · live page inspected read-only; browser contract covered by
`tests/integration/test_testhuset_browser.py`.

As a consultant, I want to scan my available Testhuset weekly-sheet tasks so that I can choose
where QI Flow should register time.

Acceptance criteria:

- **Scan Testhuset tasks** opens a visible, temporary browser session. By default the user enters
  credentials directly on Testhuset's login page. They may opt in to save a sign-in in Windows
  Credential Manager, which QI Flow fills only into that temporary browser.
- The temporary browser context is closed after use. QI Flow never writes credentials to its own
  files, backups, exports, or logs, and never stores cookies or session tokens.
- The browser opens Testhuset's `weeksheet2.aspx` directly, then scans the selected ISO week without
  changing a Testhuset value. Testhuset presents its usual login page when authentication is needed.
- Scanning stores project/task display names and stable page identifiers in a local
  `testhuset-projects.json` cache, adds new tasks, and removes tasks absent from the latest scan.
- Settings lets each user choose their own default project/task after a successful scan; no
  preconfigured task is assumed.

### US26 — Assign Testhuset tasks to work · P1

Implementation status: **Complete** · covered by assignment, migration, audit, allocation and UI tests.

As a consultant, I want work sessions to use a default Testhuset task with per-session overrides
so that exceptional work is registered in the right slot.

Acceptance criteria:

- Each completed work session uses the configured default Testhuset project/task unless it has an
  explicit override.
- The completed-session editor offers an override dropdown populated from the latest task scan.
- If the configured default is absent from a fresh scan, QI Flow stops and requires a current task
  selection before publishing.
- Timesheet shows a **Decimal hours** column for each date, calculated from rounded net time and
  formatted with two period-separated decimal places.

### US27 — Fill a Testhuset timesheet safely · P1

Implementation status: **Complete** · confirmation, stale previews, conflict choices, matching values,
failed saves and accepted-value checks verified with isolated browser fixtures. No live hours
were changed; a first real fill remains a release smoke check.

As a consultant, I want QI Flow to prepare and fill my Testhuset week safely so that I can avoid
re-entering daily decimal hours.

Acceptance criteria:

- Publishing uses the ISO week selected in Timesheet and navigates Testhuset from its current week
  to that matching week before scanning rows or filling values.
- QI Flow shows a date, project/task, and decimal-hour preview and requires **Fill Testhuset
  timesheet** confirmation before changing any Testhuset slot.
- QI Flow writes exactly two decimal places with a period, such as `7.75` for 7 hours 45 minutes.
  It reads Testhuset comma values such as `7,75` as decimal hours and rejects ambiguous values
  such as `7.750`.
- Matching existing slot values are left unchanged. Each differing value defaults to
  **Replace with QI Flow value**; the user can instead choose **Keep Testhuset value** per slot.
- QI Flow waits for and verifies Testhuset’s own save/update response for every changed slot.
- A currently active work session is excluded from the preview, so completed days in the same week
  can still be filled before the current day is finished.
- After a successful fill, QI Flow states that closing the week remains a manual Testhuset action;
  it does not automate the irreversible close-week control.

## Epic K — DSB internal time registration

### US30 — Review and insert DSB hours · P1

Implementation status: **In progress** · the Timesheet action, allocation default, reviewed
per-day fill, and explicit DSB send flow are implemented; a live DSB smoke check remains.

As a DSB consultant, I want to review and insert a selected ISO week's completed hours into
DSB so that I do not have to re-enter them manually.

Acceptance criteria:

- Settings opt-in, allocation scanning, and the default allocation remain per-user and disabled
  unless the user enables DSB time registration.
- Selecting a Timesheet date exposes **Review & insert DSB hours — week X, YYYY** only when DSB
  is enabled.
- The review lists the chosen week, allocation, QI Flow decimal hours, existing DSB hours, and a
  per-row keep-or-replace decision before any external value is changed.
- The DSB browser uses the selected ISO week, fills only confirmed rows, then uses DSB's **Send**
  action. It never approves or locks the week.
- Any uncertain browser result stops the operation and requires a fresh review; it never retries
  or approves a week automatically.

## Iteration 1 release acceptance

- All iteration-1 P0, P1, and P2 stories (US01–US24) meet their acceptance criteria.
- Automated tests cover calculations, rounding, validation, SQLite transactions/migrations, recovery, backup, restore, and export.
- A clean Windows-account test passes installation and the Start → Lunch → End lunch → Finish → edit → restart → export flow.
- Recovery tests cover crash, Windows shutdown, previous-day active state, sleep classification, corrupted database, and invalid rounded intervals.
- Google and SAP capabilities remain deferred. Epic I (US25–US27) is a separately authorized
  integration; it fills Testhuset hours only after preview confirmation and never closes a week.

## Epic J — Google Sheets cross-machine synchronization

### US28 — Connect a private shared timesheet · P1

Implementation status: **In progress** · Settings validates and saves a Sheet URL and desktop OAuth
client ID; authorization and synchronization remain to be implemented.

As a consultant, I want to connect QI Flow to my private Google Sheet so that my work laptop and
personal desktop can use the same timesheet safely.

Acceptance criteria:

- Settings accepts a valid `docs.google.com/spreadsheets` URL and desktop OAuth client ID; neither
  is embedded in the application or diagnostic logs.
- Each machine authorizes directly with Google in a visible browser flow. Refresh tokens are stored
  only in Windows Credential Manager and can be disconnected from Settings.
- QI Flow creates and owns dedicated structured sync tabs only; existing workbook tabs, formulas,
  formatting, and history remain unchanged.
- Sync can be initiated explicitly, reports its last successful time and actionable failure state,
  and never submits workplace time registrations.

### US29 — Synchronize records without silent loss · P1

Implementation status: **Not started**.

As a consultant, I want completed time records to synchronize between my machines so that I can
continue tracking without re-entering time.

Acceptance criteria:

- Work sessions, deductions, day details, assignments, deletions, and revisions use stable IDs and
  synchronize independently of presentation tabs.
- A completed local change is persisted before any network operation; offline changes remain pending
  and retry on the next explicit or scheduled sync.
- Concurrent edits to the same record are presented as a conflict with clear local and remote
  choices; QI Flow never silently overwrites either value.
- Active timers remain local until they become completed records; sync never creates a second active
  timer on another machine.
- Sync runs at app opening and closing on a best-effort basis, after a local change, and on a
  bounded periodic schedule while the app is open.

## Review status

- Requirements and decision interview: approved.
- User stories: awaiting review.
- Development: US01–US08, US09–US20, US22–US27 are implemented and verified; US21 and
  US28–US30 remain in progress, and US31 has not started.
## Epic L — Compact application design

### US33 — Modernize the interface while preserving workflows · P1

Implementation status: **Complete** · 28/09/2026. Quality gate: 184 tests, formatting, lint and strict mypy pass; both independent review findings have failing-then-passing regression tests. Wheel logos verified unchanged. Offscreen Qt scaling verified; manual monitor/keyboard, clean-account installer and live integration checks remain release work.

As a daily QI Flow user, I want a compact, branded interface so that tracking and review remain
easy without losing existing functionality.

Acceptance criteria:

- Use the supplied transparent TestHuset primary/white dark logos, orange and warm grey, preserving
  QI Flow's name. System/Light/Dark changes apply without rebuilding pages or discarding edits.
- Today/Timesheet/Settings remain accessible through horizontal navigation and existing tray routes;
  closing the window continues tracking in the tray.
- Today retains Start/Lunch/End lunch/Finish, Undo, recovery, manual entry, full session editing and
  task assignment, office/note Save. Show actual session/lunch time and effective/provisional day
  and week totals using application queries and the injected clock.
- Unsaved daily context survives timer refresh, theme changes and navigation. Date rollover offers
  Save/Discard/Cancel and never saves yesterday's draft to today's date implicitly.
- Settings retains configurable rounding, default target, sleep and both reminders, including
  independent thresholds/help, plus existing startup, backup, export, diagnostics, integration and
  update controls.
- Timesheet retains all nine fields, every calendar day grouped by ISO week, weekly target overrides,
  full editors/history and selected-week registration actions. Returning refreshes persisted totals
  and preserves selected date/week.
- Controls remain reachable in a 640×520 logical window with reflow/scrolling; light/dark Qt previews
  cover scale factors 1, 1.25, 1.5 and 2. Both supplied logos are included unchanged in a built wheel.
- The complete quality gate and automated tracking → lunch → finish → second session → correction
  → reopen → summary/detailed CSV flow pass. Existing recovery, history, tray and integration tests
  continue passing. No unfinished integration or release smoke check is declared complete.

### US34 — Refine compact usability and recover a forgotten start · P1

Implementation status: **Complete** · 28/09/2026. User requested seven concrete improvements.

As a daily user, I want a readable compact window and direct correction shortcuts so that reviewing
sessions, writing notes and recovering a forgotten timer start are quick and predictable.

Acceptance criteria:

- Default main client size approximately640x860 logical pixels, matching the supplied screenshot.
- Timesheet hover highlights the full visible row; the selected month sits between Previous/Next.
- Session editor keeps the table readable alongside scrollable correction/context/task controls,
  selects an entry when available, preserves selection on refresh and retains save/delete/history.
- Daily note uses a separate Save/Cancel dialog from Today and the session editor. Cancel preserves
  the saved note and confirms changed-draft discard; a dialog open across midnight saves to its
  original date. Office context and exact-note content remain supported.
- Settings wheel input scrolls the page without changing spin/date/dropdown values, even when
  focused; explicit keyboard edits and selections continue working.
- Today provides Start at when stopped and Change start when running. User-entered starts use
  Copenhagen time, exact manual minutes and existing persistence/containment rules. Future/overlap
  and unresolved recovery states remain blocked, including states arising while the dialog is open.
- Timer begins from the specified actual instant; correcting a running start retains session ID
  and deductions. A new specified start retains30-second Undo from the action instant.
- Untouched sessions with nonzero timestamp seconds close without false discard prompts; changed
  visible values still trigger the existing unsaved-correction safeguard.

## Epic M — User-reported usability and defects

These reports were provided by a QI Flow user on 2026-09-30 and translated from Danish.

### US35 — Add and review lunch during an active session · P1

Implementation status: **Complete** · 2026-09-30.

As a user who forgot to record lunch when it started, I want to add or complete a lunch interval
while my work session is still active so that I can correct the record without ending and
restarting the work session.

Acceptance criteria:

- **Add lunch** is available from the active session editor and accepts a lunch interval that
  already occurred, subject to the existing session-boundary and overlap validation.
- Saving a lunch correction persists it without ending or restarting the active work session.
- A lunch interval started and ended with the timer appears in the active session editor as soon
  as it is complete; the user does not have to finish the work session first to see it.
- The corrected lunch interval is included in the session's net-work calculation.

### US36 — Keep task assignment scrolling independent · P1

Implementation status: **Complete** · 2026-09-30.

As a user editing a session, I want scrolling the dialog to leave the task assignment selection
unchanged so that I do not accidentally assign my work to a different task.

Acceptance criteria:

- Scrolling the session editor outside the task-assignment control does not move the task tree's
  selection or scroll position.
- The task-assignment tree scrolls when the user scrolls over or focuses that control.
- Scrolling the dialog alone never changes the assigned task; a task changes only through an
  explicit selection and save.

### US37 — Explain why disabled actions are unavailable · P2

Implementation status: **Complete** · 2026-09-30.

As a user, I want a short explanation when an action is disabled so that I know what condition I
need to resolve before I can use it.

Acceptance criteria:

- Disabled buttons with a user-resolvable availability condition provide a concise English tooltip
  explaining why the action is unavailable.
- The tooltip reflects the current state and updates when the action becomes available or its
  blocking condition changes.
- Actions that are merely decorative or have no actionable explanation are not given misleading
  tooltips.
- Main-window Today and Timesheet actions, session correction, recoverable-history restoration,
  Testhuset fill review, and tray timer controls explain their relevant disabled states.

### US38 — Make the weekly-target increment control work · P2

Implementation status: **Complete** · verified on `origin/main` 2026-09-30; the reported defect
could not be reproduced.

As a user adjusting a weekly target in Timesheet, I want the increase control to raise the target
so that I can adjust it without opening another editor.

Acceptance criteria:

- Pressing the weekly-target up control increases the displayed target by one configured step and
  saves the new target for the selected ISO week.
- Pressing the down control decreases it by the same step, within the allowed range.
- The displayed value remains consistent with the saved value after changing the selected week or
  refreshing Timesheet.

## Epic N — User-reported interface improvements

### US39 — Give the main window more room on startup · P2

Implementation status: **Complete** · 2026-09-30.

As a user, I want QI Flow to open in a wider window so that more of the Timesheet content is
visible without expanding sections manually.

Acceptance criteria:

- The initial main-window width gives the Timesheet content more room than the current startup
  layout while keeping the window usable on supported display sizes.
- Timesheet information that is currently hidden behind expandable sections is easier to discover
  or review without requiring the user to expand every section individually.
- Resizing and window-state behavior remain usable on smaller displays.

### US40 — Arrange Today actions horizontally · P2

Implementation status: **Complete** · 2026-09-30.

As a user, I want the primary Today actions arranged horizontally when space allows so that the
timer controls are easier to scan and use.

Acceptance criteria:

- Today’s primary timer actions are presented in a horizontal row at supported window sizes where
  the controls fit comfortably.
- The actions remain readable and operable at narrower window sizes, using a responsive layout
  when a horizontal row does not fit.

Verification: 215 automated tests pass; Ruff formatting and lint, strict mypy, and the 760-pixel
startup-width and responsive Today layout interactions pass. The weekly-target interaction test
clicks the upper and lower spin-box controls and verifies persistence to the selected ISO week.
Owning-layer and Qt interaction regressions cover these criteria; both independent review findings
were reproduced RED, fixed GREEN and included in the full suite. Real offscreen Qt light/dark
previews at 640x860, 640x520 and 100/125/150/200% were inspected with Windows fonts. The project
check script could not run because global Python has no Ruff module; the same Ruff, mypy, and pytest
checks passed using the existing project virtual environment. Manual monitor/keyboard, production
data, installer and authenticated live integration checks remain release work. Existing
cross-midnight time-only correction and switching rows with unsaved interval edits remain
pre-existing editor limitations outside this follow-up.

## Epic O — Settings clarity

### US41 — Organize Settings for first-time users · P1

Implementation status: **Complete** · 2026-09-30.

As a new QI Flow user, I want Settings to show clear categories and explanations so that I can
find and change an option without searching through a long page of controls.

Acceptance criteria:

- Settings opens on a concise overview of available categories. Each category has a plain-English
  description and one action to open its detail view.
- Each detail group has at most one button. Tracking, appearance, Google Sheets, Testhuset, DSB,
  backups, export, updates, and diagnostics remain reachable when configured.
- Choosing a backup folder saves it immediately. CSV format is selected before one Export action;
  Google authorization uses one state-aware connect/disconnect action.
- Existing preference persistence, reminder behavior, update progress, and integration safeguards
  continue working. Narrow windows scroll vertically without requiring horizontal scrolling.

Verification: `scripts/check.ps1` passes 237 tests, Ruff formatting and lint, and strict mypy.
Qt interaction tests cover category navigation, one button per group, preference saving, backup
location, export selection, Google authorization, update progress, and narrow-window scrolling.

## Epic P — Assign EazyProject task when starting work

### US42 — Choose a task for each new work session · P1

Implementation status: **Complete** · 2026-10-02.

As a consultant, I want to choose the EazyProject project/task when I start work so that each
session is attributed to the branch where that work began.

Acceptance criteria:

- When cached EazyProject tasks are available, **Start work** opens a compact task dropdown.
- The dropdown preselects the Settings default task. A different choice applies only to the new
  work session and does not change the saved default.
- Canceling the prompt leaves work stopped.
- The selected task is persisted with the active session before the UI reports that work started.
- If no tasks have been scanned, **Start work** keeps its existing behavior.

## Epic Q — Daily tracking polish

### US43 — Make timer and correction screens fit their content · P1

Implementation status: **Complete** · 2026-10-02.
This later request supersedes the wide-screen action-row layout recorded in US40.

As a daily QI Flow user, I want the timer, correction editor and actions to use available space
well, and my recorded start to reflect the configured rounding fairly.

Acceptance criteria:

- The session editor opens with a compact session list and correction controls that remain
  reachable without horizontal scrolling, including when a scanned task has a long name.
- Today's timer actions use the empty space beside the timer at wider sizes and reflow below it
  at narrow sizes without hiding timer actions.
- For timer-created work at 15-minute precision, an 08:06 start records 08:00, while an 08:40
  start records 08:40. At the exact midpoint the actual start is retained; finishes still round
  up and lunch deductions still use nearest rounding.
- Enabled action buttons use the orange filled primary style or orange outlined alternative
  style across light and dark themes.
