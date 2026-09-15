# QI Flow — iteration 1 user stories

Version: 0.1 · Updated: 2026-09-15 · Status: US01–US04 implemented and verified

These stories implement the confirmed local-only scope in `REQUIREMENTS.md` and `DECISIONS.md`. Priority definitions: **P0** is required for a usable tracker, **P1** is required before iteration 1 ships, and **P2** completes resilience and distribution.

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
- Button actions use nearest-interval rounding when their interval completes.
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

As a consultant, I want to add or correct work and lunch intervals so that forgotten or inaccurate entries can be repaired.

Acceptance criteria:

- Completed work and lunch intervals can be added and edited to minute precision without automatic rounding.
- Future dates, end-before-start, overlaps, orphan lunch/break intervals, and multiple active intervals are blocked with actionable messages.
- The active session start may be corrected if it still contains all child intervals.
- Only timer actions can create open-ended intervals.
- Unsaved form changes require confirmation before discard.

### US06 — Delete, undo, and recover changes · P1

As a consultant, I want safe correction controls so that an accidental edit or deletion does not permanently destroy my record.

Acceptance criteria:

- Deleting an entry requires confirmation and immediately removes it from totals.
- Deleted entries and previous edited values remain recoverable for 30 days.
- Timer actions expose Undo for 30 seconds.
- Recovery restores the former values and recalculates affected totals.
- History is available from entry details without cluttering the ordinary list.

### US07 — Record daily context · P1

As a consultant, I want to mark office attendance and add a daily note so that the timesheet retains necessary context.

Acceptance criteria:

- Each date has one office/remote value and one multiline Unicode note.
- New days default to remote/unchecked; days without work display no workplace label.
- Context can be entered before work exists and remains attached to the date.
- A cross-midnight session copies office status to the second date, which can then be edited independently.
- Notes are not written to diagnostic logs.

### US08 — Resolve Windows sleep · P1

As a consultant, I want to classify long computer sleep so that unattended time is not silently included or removed.

Acceptance criteria:

- Sleep detection is configurable and defaults to 30 minutes.
- On qualifying resume, QI Flow offers Include as work, Exclude as break, or Decide later.
- Excluded time becomes a labelled deducted break inside the continuous session.
- Decide later permits viewing but disables timer actions until resolved.
- Sleep detection can be disabled.

## Epic C — Tray and lifecycle

### US09 — Operate from the system tray · P0

As a consultant, I want QI Flow available from the tray so that tracking does not occupy my taskbar or interrupt other work.

Acceptance criteria:

- Closing the main window hides it to the tray without changing timer state.
- Left-click opens a compact panel containing current state, net time, session start, lunch duration, the valid timer action, Add entry, and Open timesheet.
- Right-click shows the confirmed context-menu actions and an explicit Close app command.
- Tray and main-window state remain consistent after every action.

### US10 — Exit safely · P1

As a consultant, I want an explicit warning when closing QI Flow during active tracking so that I choose what happens to the session.

Acceptance criteria:

- Close app exits immediately when no session is active.
- During work, it offers Keep running and close, Finish work and close, or Cancel, with Cancel selected by default.
- During lunch, it offers the equivalent explicit resolution without silently ending lunch.
- Keeping the session running explains that reminders pause while the process is closed.
- Finish and close saves the rounded finish before process exit.

### US11 — Start once and focus the existing app · P1

As a consultant, I want predictable Windows startup and single-instance behavior so that two processes cannot alter the same database.

Acceptance criteria:

- Start with Windows is optional and disabled by default.
- Automatic startup stays in the tray unless recovery needs attention.
- Manual launch opens the full window.
- A second launch activates the existing process and never opens the live database concurrently.

## Epic D — Review and totals

### US12 — Review a month by ISO week · P0

As a consultant, I want every day grouped by week number so that I can identify missing entries and review monthly time quickly.

Acceptance criteria:

- The month lists every day, including weekends, in ISO Monday–Sunday week groups.
- Each daily row shows first start, final finish, session count, total lunch, net time, office status, and notes indicator.
- A date with multiple sessions shows its boundary times and session count without treating the gap as work.
- Selecting a day opens all sessions and deductions for editing.
- Active time is clearly provisional.

### US13 — Review weekly progress · P1

As a consultant, I want weekly totals compared with my target so that I know whether time is remaining or over target.

Acceptance criteria:

- The default target is 37 hours.
- A per-week override changes only the selected week.
- The summary reports logged and remaining/excess time neutrally.
- Weekly and monthly summaries show hours/minutes and decimal hours.
- Cross-midnight work is allocated to the correct ISO week.

## Epic E — Reminders

### US14 — Receive configurable reminders · P1

As a consultant, I want reminders for unusually long work and lunch so that I catch forgotten timer actions.

Acceptance criteria:

- Work reminder defaults to 9 elapsed hours including lunch; lunch reminder defaults to 45 minutes.
- Each reminder can be enabled and configured independently.
- Notifications show relevant elapsed/net time and offer Open QI Flow or 15/30/60-minute snooze.
- State-changing actions occur inside QI Flow.
- Reminders resume correctly after normal process restart and remain unavailable while the process is closed.

## Epic F — Local resilience and export

### US15 — Back up local data automatically · P1

As a consultant, I want automatic backups so that a machine or database problem does not erase my only timesheet.

Acceptance criteria:

- QI Flow creates at most one complete, consistent backup per day and retains the newest 30.
- Backup includes active state and settings.
- Settings shows and allows changing the backup folder, including OneDrive locations.
- A failure never blocks tracking and produces a persistent warning until a backup succeeds.

### US16 — Restore data safely · P2

As a consultant, I want guided restoration so that I can recover without accidentally overwriting the only usable database.

Acceptance criteria:

- Restore lists valid backups with timestamps and requires active ambiguity to be resolved first.
- QI Flow makes a safety backup of current data before replacement.
- Restoration requires confirmation and restarts the application afterward.
- If the live database is unreadable, QI Flow does not create an empty replacement and offers the newest valid backup.

### US17 — Export readable timesheets · P1

As a consultant, I want CSV exports so that I can inspect or reuse my local records outside QI Flow.

Acceptance criteria:

- Export supports selected week, selected month, or all history.
- Summary export emits one row per date; detailed export separates sessions, lunches, and deducted breaks.
- Files are UTF-8 and semicolon-separated with Danish dates, 24-hour times, and decimal commas.
- Deleted data and actual unrounded action metadata are excluded.

## Epic G — Setup, settings, and distribution

### US18 — Configure QI Flow on first launch · P1

As a consultant, I want a short initial setup so that useful defaults are transparent and adjustable.

Acceptance criteria:

- One compact screen presents rounding, target, startup, reminders, sleep threshold, theme, and backup settings.
- Confirmed defaults from D072 are preselected.
- Completion creates the local database and opens Today.
- The setup can be completed without administrator permissions or network access.

### US19 — Use Danish formats in an English interface · P1

As a consultant, I want familiar regional formatting so that timesheets match how I work in Denmark.

Acceptance criteria:

- Labels are English; dates use `dd/MM/yyyy`; times use 24-hour format; week numbers follow ISO 8601.
- Elapsed calculations use Europe/Copenhagen and remain correct through daylight-saving changes.
- Theme follows Windows by default with Light and Dark overrides.
- QI Flow uses the approved teal direction and consistent QI app/tray identity.

### US20 — Diagnose locally without telemetry · P2

As a user, I want privacy-safe local diagnostics so that problems can be investigated without sending my work records elsewhere.

Acceptance criteria:

- Logs retain approximately seven days and avoid notes and time-entry contents where possible.
- Settings exposes the application-data path and Open log folder.
- The live database path is standard per-user application data and cannot be relocated from the UI.
- QI Flow performs no telemetry or automatic update checks.

### US21 — Install and upgrade on Windows · P2

As a consultant, I want a per-user installer so that I can run QI Flow on permitted Windows machines without administrator rights.

Acceptance criteria:

- Installation, launch, optional startup, upgrade, and uninstall work for the current user without elevation.
- Upgrade preserves the database, settings, backups, and active-state compatibility through explicit schema migrations.
- Uninstall behavior clearly distinguishes application removal from user-data removal.
- No global keyboard shortcuts are registered in iteration 1.

## Iteration 1 release acceptance

- All P0, P1, and P2 stories above meet their acceptance criteria.
- Automated tests cover calculations, rounding, validation, SQLite transactions/migrations, recovery, backup, restore, and export.
- A clean Windows-account test passes installation and the Start → Lunch → End lunch → Finish → edit → restart → export flow.
- Recovery tests cover crash, Windows shutdown, previous-day active state, sleep classification, corrupted database, and invalid rounded intervals.
- Google, Testhuset, and SAP capabilities are absent or clearly labelled as future work; no placeholder control implies they function.

## Review status

- Requirements and decision interview: approved.
- User stories: awaiting review.
- Development: US01–US04 implemented and verified; remaining stories not started.
