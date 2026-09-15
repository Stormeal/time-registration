# Work time tracker — design concept

Version: 0.5 · Updated: 2026-09-15 · Status: iteration 1 approved; user stories drafted

## Direction

Product name: **QI Flow**. A quiet Windows utility with Segoe UI, restrained teal emphasis, generous readable spacing, and light/dark appearance following the system. The main action is always visible. The existing concept is a simulated interface created under the earlier working name “Worktime”; implementation should use QI Flow throughout.

Iteration 1 is a local-only application. It stores time in SQLite on one Windows machine. Google synchronization and workplace integrations are later milestones and must not delay validation of the core tracking experience.

Interactive review concept: `C:/Users/ast/.codex/visualizations/2026/09/15/01a0a470-e0c4-7e70-8f5f-fd475064ad08/worktime-concept.html`.

## Navigation and screens

### Today — R02–R05

- Date, working/lunch/stopped label, net worked duration.
- Start work when stopped; Start lunch or Finish work when working; End lunch while on lunch. Finish work is unavailable until lunch ends.
- The work session keeps its original start and eventual finish across lunch. Lunch is stored as a separate nested interval and deducted from the session's net time.
- Session details distinguish the continuous gross work span, deducted lunch intervals, and net worked duration; add/edit opens a compact form with date, type, start/end and notes.
- Office checkbox is proposed, based on the reference sheet.
- Primary timer shows worked time, not elapsed wall time including lunch. Lunch state additionally shows lunch duration.

### Timesheet — R04, R06

- Month/year navigation; daily list grouped by calendar week number, using Monday–Sunday weeks.
- Date, worked duration, lunch deduction and per-destination registration status.
- Selecting a day reveals intervals and editing; unlogged days remain visible, including weekends.
- Days without records are distinguished from incomplete running entries. Monthly totals include worked time only; active time is labelled provisional.
- Prefer a list over a calendar grid because intervals and submission status need readable space.

### Submit week — R09–R10

- Week selection; day-by-day net totals in hours/minutes and decimal hours.
- Two destination sections: Testhuset / eazyProject and DSB / SAP Logon.
- Each shows destination mapping and independent status. Unknown codes are left unconfigured, never invented.
- Preview → save draft → explicit **Submit week** action → progress → verified result. Login-required, draft-saved, submitted, failed, uncertain and changed-since-submission are distinct states.
- Final submission can only begin from a direct user press of **Submit week**. Sync, app startup, app shutdown, timers and background automation cannot trigger it.
- Retry reconciles actual destination data before writing. A click alone is not evidence of success.
- The concept preview stops at destination setup; real submission is intentionally unavailable.

### Connections — R07–R10

- Google account/workbook, connection status, last sync and pending changes.
- Testhuset login status; DSB availability on this machine.
- Sync now; show actionable offline, authorization and conflict states.
- No secrets displayed. When a destination requires authentication, Worktime prompts the user to log in. The proposed default is to enter credentials in the destination's own browser or SAP window and never persist workplace passwords; the precise SSO/MFA flow is deferred until integration inspection.
- This screen is outside iteration 1. The first iteration may show a simple “Local data” status, but it does not offer nonfunctional connection controls.

### Tray companion — proposed P08

A small popover with current state, net duration, Start/Lunch/Finish and Open timesheet. It mirrors main-window state. Closing the main window minimizes to the tray and leaves active intervals untouched. The application exits only from **Close app** in the tray icon's right-click menu; active timestamps are saved before exit so reopening can recover them.

## Interaction rules proposed for approval

1. Start → working. Start lunch → lunch runs within the same continuous work session. End lunch → working. Finish → stopped and calculates net time as the work-session span minus completed lunch intervals.
2. Starting again adds another work interval, preserving earlier work that day.
3. Finish work is unavailable while lunch is active; the user ends lunch explicitly before finishing work. Manual edits recalculate totals; invalid end-before-start and overlapping intervals require correction. Cross-midnight entries must be supported explicitly, not mistaken for invalid same-day time.
4. Sync state is secondary to tracking controls. Offline operation never blocks a local save.
5. Conflicts show both versions with device/time context; user chooses or corrects them. No automatic last-write-wins promise.
6. Submission status belongs to a specific version of a week and destination; later edits require reconciliation.

## Architecture sketch (proposed, not implementation)

Iteration 1: Windows UI → local service layer → SQLite database.

Later: synchronization worker ↔ structured app-data tabs in the existing private Google workbook. Existing month tabs and formulas remain intact.

Week review → Testhuset adapter (candidate: Playwright) / DSB adapter (candidate: SAP GUI Scripting).

Google stores shared time data and submission records; authentication sessions stay on each device. Workplace automation saves drafts by default. Final submission is never triggered merely by synchronizing and requires the explicit **Submit week** action.

## Visual concept coverage

The interactive concept demonstrates Today, simulated lunch/finish/start state changes, manual entry, a selectable September daily list, a weekly submission preview and connection placeholders. Sample entries and connection labels are illustrative. It is not a functional prototype of synchronization or workplace automation. Compact tray layout is specified above but not separately mocked.

## Review checklist

- [x] Review requirements and design direction.
- [x] Confirm use of the existing Google workbook.
- [x] Confirm draft-by-default and explicit weekly submission behavior.
- [x] Decide lunch/finish and close/quit behavior.
- [x] Confirm a monthly list grouped by calendar week number.
- [ ] Confirm the later weekly workplace-submission preview during integration design.
- [x] Defer login/navigation details until each integration is inspected.
- [x] Record explicit approval before creating user stories.

## Recorded decisions

- Use the existing timesheet workbook. Add structured internal tabs without altering the current monthly presentation.
- Saving workplace entries creates or updates drafts. Pressing **Submit week** is the sole trigger for final submission.
- Prompt for login when Testhuset or SAP requires authentication. Determine the exact browser/SAP, SSO and MFA handling during integration work; do not assume workplace credentials can or should be stored.
- Keep iteration 1 local-only. Add Google Sheets synchronization after the core tracker and timesheet have been validated.
- Closing the window minimizes to tray without ending any interval. Only **Close app** in the tray context menu exits the process.
- Keep a work session continuous across lunch. Track lunch separately from **Start lunch** to **End lunch**, deduct it from net hours, and require lunch to end before work can finish.
- Present the monthly timesheet as a list grouped by calendar week number.
- Apply the complete iteration 1 decisions in `DECISIONS.md`, including rounding, recovery, reminders, backups, export, Danish formats, privacy and packaging.

## Implementation specification

- `DECISIONS.md` is the detailed decision record produced by the design interview.
- `USER_STORIES.md` defines prioritized iteration 1 behavior and acceptance criteria.
- If a future proposal conflicts with an accepted decision, update the decision record explicitly rather than silently changing behavior.

## References checked during brainstorming

- Qt desktop deployment: https://doc.qt.io/qtforpython-6.8/deployment/index.html
- Google desktop authorization example: https://developers.google.com/workspace/sheets/api/quickstart/python
- Playwright Python: https://playwright.dev/python/docs/library
- SAP GUI Scripting: https://help.sap.com/docs/SUPPORT_CONTENT/atopics/3354081614.html

These support candidate approaches, not proof that the user's workplace integrations are available.
