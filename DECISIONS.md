# QI Flow — iteration 1 decision record

Version: 1.0 · Updated: 2026-09-15 · Status: confirmed

This file records the shared understanding reached during the design interview. These decisions apply to iteration 1 unless explicitly revised in a later change-log entry.

## Product and scope

| ID | Confirmed decision |
| --- | --- |
| D001 | The application is named **QI Flow**. |
| D002 | Iteration 1 is a local Windows application built with Python, PySide6, and SQLite. |
| D003 | Google Sheets synchronization, cross-machine behavior, Testhuset, SAP, authentication, and workplace submission are deferred. |
| D004 | The application installs for the current Windows user without requiring administrator rights. |
| D005 | Iteration 1 has no telemetry and no automatic update mechanism. |

## Work, lunch, and time calculation

| ID | Confirmed decision |
| --- | --- |
| D010 | Start work creates one continuous work session. Starting again after Finish creates another session for the same day. |
| D011 | Start lunch creates a lunch interval inside the active work session. The work-session span continues while lunch is active; lunch is deducted from net work. |
| D012 | A session can contain multiple lunch intervals. Finish work is unavailable until the active lunch has been ended explicitly. |
| D013 | Long Windows sleep can be classified as included work or as a separately labelled deducted break; it is not automatically treated as lunch. |
| D014 | A session crossing midnight remains one user-visible session while totals are allocated across both dates at midnight. Office status is copied to the second date and remains editable. |
| D015 | Elapsed time uses precise instants and Europe/Copenhagen time, including Danish daylight-saving transitions. |

## Rounding and editing

| ID | Confirmed decision |
| --- | --- |
| D020 | Rounding options are 1, 5, 10, and 15 minutes; the default is 5 minutes. Setting changes apply only to future button actions. |
| D021 | Button actions use normal nearest-interval rounding. Actual button-press timestamps are retained as metadata. |
| D022 | Active timers display actual elapsed time. Rounding is applied when the interval is completed. Ordinary timesheets show rounded effective values; actual timestamps are available in entry details. |
| D023 | Manual entry accepts exact minute values without applying automatic rounding. Future-dated entries are blocked. |
| D024 | If rounding produces an invalid or zero-length interval, show the result and require correction; do not invent duration. |
| D025 | Users can add, edit, and delete completed work and lunch intervals. Active-session start and the daily note can be edited while the session is running if containment rules remain valid. |
| D026 | Overlapping work sessions, lunches or breaks outside their parent session, end-before-start, and multiple open intervals are blocked. Only timer actions create open intervals. |
| D027 | Timer actions offer Undo for 30 seconds. Deleted records and previous edited values remain recoverable for 30 days. Completed time data otherwise remains indefinitely. |
| D028 | Form edits require Save. Closing a form with unsaved changes asks whether to discard them. |

## Tray, startup, and recovery

| ID | Confirmed decision |
| --- | --- |
| D030 | Closing the main window minimizes QI Flow to the tray and does not affect the current session. |
| D031 | The process exits only through **Close app** in the tray context menu. With an active session, offer Keep running and close, Finish work and close, or Cancel; Cancel is selected by default. |
| D032 | Keeping a session running after process exit pauses reminders. Reopening calculates elapsed time from persisted timestamps. Finishing through the exit dialog saves the rounded finish and exits. |
| D033 | Left-clicking the tray icon opens a compact panel showing state, net time, session start, active lunch duration, the valid timer action, Add entry, and Open timesheet. |
| D034 | The tray context menu contains Open QI Flow, state/net time, the valid timer action, Add entry, Start with Windows, Settings, and Close app. |
| D035 | Start with Windows is optional and initially disabled. Automatic startup normally remains in the tray; it opens recovery when an unfinished previous-day session exists. Manual launch opens the full window. |
| D036 | Only one process can use the live database. A second launch focuses the existing QI Flow window. |
| D037 | Every timer action is persisted immediately. A crash or Windows shutdown leaves active timestamps recoverable. |
| D038 | An unfinished previous-day session must be resolved before starting another. Recovery offers set finish time, delete, continue, or review timesheet; it does not suggest a finish time. |
| D039 | Windows sleep longer than a configurable threshold, default 30 minutes, requires Include as work, Exclude as break, or Decide later. Decide later permits viewing but disables timer actions. Sleep detection can be disabled. |

## Timesheet and daily information

| ID | Confirmed decision |
| --- | --- |
| D040 | The monthly view lists every calendar day, including weekends, grouped by ISO Monday–Sunday week number. |
| D041 | Each day shows first start, final finish, session count, total lunch, net time, office status, and a notes indicator. Selecting a day exposes its intervals for editing. |
| D042 | Office status belongs to a date. Checked means office, unchecked means remote; a day without work displays no workplace status. New days default to unchecked. |
| D043 | A daily office value or note may be created before time is logged. Notes belong to the day, support multiline Unicode text, and have a generous technical limit. |
| D044 | The default weekly target is 37 hours. A week can override its own target without changing the default. Summaries show remaining or excess hours neutrally. |
| D045 | Decimal hours appear in weekly/monthly summaries and exports; active timers and entry forms primarily use hours and minutes. |

## Reminders

| ID | Confirmed decision |
| --- | --- |
| D050 | Reminders are configurable and initially enabled: long work after 9 elapsed hours including lunch, and long lunch after 45 minutes. |
| D051 | Notifications show relevant elapsed and net time and offer Open QI Flow or Remind later with 15, 30, or 60-minute snooze. State-changing Finish/End actions occur inside QI Flow. |

## Backups, export, and diagnostics

| ID | Confirmed decision |
| --- | --- |
| D060 | QI Flow creates one complete SQLite backup per day and retains the latest 30 daily backups. Active state and settings are included. |
| D061 | The backup folder is visible and user-selectable, including a OneDrive folder. Backup failure never blocks tracking and remains visible until a backup succeeds. |
| D062 | Guided restore shows the backup date, requires no unresolved active session, creates a safety copy, requires confirmation, restores, and restarts QI Flow. An unreadable live database is never silently replaced; offer the newest valid backup. |
| D063 | CSV export supports the selected week, selected month, or all history. Summary export has one row per day; detailed export separates work sessions, lunches, and deducted breaks. |
| D064 | CSV is UTF-8 and semicolon-separated, uses `dd/MM/yyyy`, `HH:mm`, and Danish decimal commas, and excludes deleted records and actual unrounded press metadata. |
| D065 | Live data and settings use the standard private Windows application-data folder. The path is visible in Settings but is not user-movable. |
| D066 | Keep about seven days of local diagnostic logs, excluding notes and time-entry contents where possible. Settings provides Open log folder. Windows account security protects local data; there is no QI Flow PIN in iteration 1. |

## Appearance and first run

| ID | Confirmed decision |
| --- | --- |
| D070 | Use English UI labels with Danish date/time conventions, 24-hour time, ISO week numbers, and Europe/Copenhagen time. |
| D071 | Follow the Windows light/dark setting by default, with Light and Dark overrides. Retain the restrained teal visual direction and create a simple QI app/tray icon. |
| D072 | First launch presents one compact setup screen with defaults: 5-minute rounding, 37-hour target, startup disabled, 9-hour and 45-minute reminders enabled, 30-minute sleep prompt enabled, System theme, and 30 daily backups. |
| D073 | Global keyboard shortcuts are outside iteration 1. |
| D090 | Testhuset login is performed directly by the user in a temporary Playwright browser session. QI Flow never persists credentials, browser cookies, or tokens; diagnostic logs exclude authentication and time-entry contents. |
| D091 | Testhuset task scanning follows Dashboard → Timer, km & udlæg → Ugeseddel for the selected ISO week. It refreshes a JSON cache of project/task display names and stable page identifiers without writing hours. |
| D092 | No Testhuset task is preconfigured. Each user chooses a default from a successful scan; completed sessions may override it. |
| D093 | Testhuset fill values use exactly two decimal places with a period. Existing comma values are read as decimals; ambiguous multi-period values are rejected. Different values require a per-slot keep-or-replace choice. |
| D094 | QI Flow shows and explicitly confirms a Testhuset fill preview, verifies each page save response, and does not automate irreversible week closure. |
| D095 | Epic I is authorized as a post-iteration-1 integration on 2026-09-17. D003 continues to defer Google and SAP, but no longer defers the US25–US27 Testhuset scope. |
| D096 | Session overrides are stable Testhuset task/project row IDs. Sessions without overrides resolve the current default when previewing, including historical sessions. Assignment changes preserve timestamps and participate in the existing 30-day recovery history. |
| D097 | Every differing slot, including blank/zero, requires an explicit keep-or-replace choice. Task/day net seconds are summed before decimal rounding (nearest hundredth, half up). Only previewed slots are written; unrelated destination values remain untouched. |
| D098 | Testhuset browser failures stop immediately without blind retries or rollback writes. Reopen a fresh preview to reconcile partial saves. Temporary non-persistent Edge contexts are closed on success, cancellation and handled failure. |

## Completion standard

| ID | Confirmed decision |
| --- | --- |
| D080 | Iteration 1 is complete only after the installer works on a clean Windows account and the Start → Lunch → End lunch → Finish → edit → restart → export flow passes. |
| D081 | Verification covers crash recovery, Windows shutdown, unfinished previous-day recovery, sleep resolution, backup/restore, invalid intervals, and database migration/recovery behavior. |

## Change log

- 2026-09-15: v1.0 confirmed after four design-interview rounds and explicit shared-understanding confirmation.
- 2026-09-17: Epic I implementation authorized. User-managed browser login (D090) takes precedence
  over collecting credentials in QI Flow. Live weekly-sheet structure inspected without hour writes.
- 2026-09-17: v0.1.1 completes a timer-created lunch shorter than its rounding interval using its
  known actual boundaries, rather than leaving lunch active or inventing rounded time. The session
  editor now adds lunch directly to the selected completed session, and saving Settings refreshes
  Today’s sleep controls immediately.
