# Work time tracker — requirements

Version: 0.6 · Updated: 2026-10-04 · Status: approved scope; audit implementation verified, release acceptance open

## Goal and context

A fast personal Windows desktop application for recording work across multiple machines and registering weekly hours with Testhuset and DSB. The user is a Testhuset consultant assigned to DSB. All machines run Windows; installation on the work PC is permitted by the user’s stated circumstances.

## Review workflow

1. Review this document and `DESIGN.md`, including the visual concept.
2. Record the user's approval or requested changes below.
3. Only after approval, write user stories together.
4. Begin development after the user-story step and user direction.

This document records approved scope. Current implementation status and outstanding real-environment
acceptance are tracked below and in `USER_STORIES.md`; stable requirement IDs are retained.

Detailed product decisions are recorded in `DECISIONS.md`. Implementable iteration 1 behavior and acceptance criteria are recorded in `USER_STORIES.md`.

## Confirmed user requirements

| ID | Requirement | Implementation status |
| --- | --- | --- |
| R01 | Installed Windows desktop application; everyday use outside a browser. Fast and small in feel. | Complete |
| R02 | Start work records the current start time; Finish records the end time. This is the primary input method. | Complete |
| R03 | Manually add and correct time when tracking was forgotten. | Complete |
| R04 | Support multiple work intervals per day, including evening work; sum them into daily hours. | Complete |
| R05 | **Start lunch** begins a separate lunch interval that continues until **End lunch** is pressed. The surrounding work session remains continuous, while completed lunch time is deducted from net worked hours. | Complete |
| R06 | View logged hours for every day of each month within the application. | Complete |
| R07 | Share one synchronized timesheet across the work laptop and personal desktop. | In progress |
| R08 | Use a private Google Sheet as shared storage; synchronize on opening, eligible completed changes and periodically. Closing cancels bounded work and preserves pending changes for the next opening. | In progress |
| R09 | Provide button-driven weekly registration to Testhuset at https://testhuset.eazyproject.net/dashboard.aspx. | In progress |
| R10 | Provide reviewed button-driven weekly registration through the DSB browser timesheet and its Send action. SAP automation remains deferred. | In progress |
| R11 | Python is the preferred language following discussion. | Complete |
| R12 | Persist requirements and design for review across sessions; approval precedes user stories and development. | Complete |
| R13 | Use the existing timesheet workbook for synchronized storage while preserving its current monthly sheets, formulas, and formatting. | In progress |
| R14 | Workplace writes require explicit reviewed confirmation; Testhuset closure and DSB approval/locking remain manual. Google synchronization never submits workplace registrations. | In progress |
| R15 | When workplace authentication is required, prompt the user to log in before automation navigates and fills the registration workflow. | Complete |
| R16 | The first iteration operates locally on one Windows machine and does not include Google Sheets synchronization. Cross-machine synchronization remains a later requirement. | Complete |
| R17 | With a system tray, window close minimizes QI Flow without ending tracking. Close app and, without a tray, window close use shared confirmation; failed Finish or cancellation keeps the application accessible. | Complete |
| R18 | While lunch is active, only **End lunch** ends the lunch interval. **Finish work** is unavailable until lunch has ended. | Complete |
| R19 | The monthly timesheet is a list grouped by calendar week number. | Complete |
| R20 | Button actions use configurable 1, 5, 10, or 15-minute rounding, defaulting to 5 minutes. Timer-created work starts use the previous boundary only when it is nearer than the next boundary; otherwise the actual start is kept. Work finishes round up; completed lunch boundaries use nearest rounding. Live timers use actual time, completed entries use effective times, and manual entries accept exact minutes. | Complete |
| R21 | Detect unfinished previous-day sessions and long Windows sleep intervals, requiring the user to resolve ambiguous time before further timer actions. | Complete |
| R22 | Support multiple lunch intervals and deducted sleep-break intervals within one continuous work session. | Complete |
| R23 | Offer a 30-second undo for timer actions and retain deleted or changed entry history for 30 days. | Complete |
| R24 | Support optional Windows startup, single-instance behavior, a compact tray panel, and recovery-focused startup behavior. | Complete |
| R25 | Provide configurable work and lunch reminders, defaulting to 9 elapsed hours and 45 lunch minutes, with user-selected snooze. | Complete |
| R26 | Create daily SQLite backups, retain 30, support a selectable backup folder and guided restoration, and never replace an unreadable database silently. | Complete |
| R27 | Use a 37-hour default weekly target with per-week overrides and neutral remaining/over-target feedback. | Complete |
| R28 | Use English UI text with Danish formats, ISO Monday–Sunday weeks, Europe/Copenhagen time, and correct daylight-saving elapsed-time calculations. | Complete |
| R29 | Export summary and detailed UTF-8 semicolon-separated CSV for a week, month, or all history, using Danish decimal commas. | Complete |
| R30 | Install and update per Windows user without administrator rights. QI Flow may check for and install verified releases without requiring the user to manually download and run the installer for each update. Updates must preserve user data and support recovery if an update fails. Retain data indefinitely, keep limited privacy-safe local diagnostics, and include no telemetry. | In progress |
| R31 | Allow completed work sessions and lunch/break deductions to be corrected from Timesheet using exact manual times, while preserving validation and 30-day recovery history. | Complete |
| R32 | Provide concise English hover help for each configurable Today option, including explicit non-destructive sleep-detection behavior. | Complete |
| R33 | Use a consistent, modern teal QI Flow icon in the app, tray, packaged executable, Start menu, and installer. | Complete |
| R34 | Testhuset authentication is user-managed by default. A user may opt in to save a sign-in in Windows Credential Manager for their Windows account; QI Flow never writes it to its files, backups, exports, or logs, and never stores cookies or session tokens. | Complete |
| R35 | Let a user scan Testhuset weekly-sheet project/task rows through a temporary authenticated browser session and cache only display names and stable task identifiers locally. | Complete |
| R36 | Let each user choose a default Testhuset task and apply per-session overrides from the latest scanned task list. | Complete |
| R37 | Preview and explicitly confirm Testhuset weekly timesheet fills, write two-decimal period-separated hours, resolve differing existing values per slot, verify saves, and leave week closure manual. | Complete |
| R38 | Show each Timesheet day’s rounded net duration as period-separated decimal hours for Testhuset review. | Complete |
| R39 | Let the user choose which scanned Testhuset project/task branches count toward DSB registration. DSB preview and fill exclude sessions assigned to all other branches while preserving them in QI Flow and Testhuset totals. | In progress |

### Example calculation

September 3, 2026: 08:00–15:30 plus 19:45–20:45 = 8h 30m = 8.5 decimal hours, with no lunch deduction in this example. The supplied spreadsheet formula `=SUM(24*E18)` converts a duration to decimal hours; it does not parse intervals in Notes. The app should store intervals explicitly.

## Approved supporting behavior

| ID | Proposal | Supports |
| --- | --- | --- |
| P01 | PySide6 UI and local SQLite storage; save each action before attempting network synchronization. | R01–R08 |
| P02 | Sync after changes and periodically while open, plus opening/closing; expose Sync now and last successful sync. Closing sync is best effort. | R07–R08 |
| P03 | Keep pending offline changes and retry after reconnection; restarting the app preserves running work/lunch timestamps. | R02, R05, R07 |
| P04 | Detect conflicting edits and overlapping work from different machines; never silently discard an edit. Exact concurrency design requires validation. | R07–R08 |
| P05 | Add dedicated structured tabs to the existing private spreadsheet, with stable record IDs, revision/device details and deletion markers. Preserve the existing formatted month tabs, formulas, and history. | R08, R13 |
| P06 | Week review shows daily net hours and destination-specific project/activity mappings. Automation may save drafts for either destination; only the explicit **Submit week** action may perform final submission. | R09–R10, R14 |
| P07 | Track outcomes independently per destination; verify success, reconcile uncertain results before retry, and flag changes made after submission. Prevent duplicate registrations. | R09–R10 |
| P08 | Tray access, notes, office-day checkbox, and clear working/lunch/stopped states. | R01–R06 |
| P09 | Store Google authorization locally using appropriate Windows protection. Do not store workplace passwords in the spreadsheet. Proposed default: let the user enter workplace credentials directly into the destination browser or SAP login window and complete MFA where required; confirm the exact flow during integration work. | R08–R10, R15 |
| P10 | Store precise timestamps; show durations as hours/minutes and decimal hours where useful. Default date grouping to Europe/Copenhagen; agree rounding before submission. | R04, R06, R09–R10 |

## Delivery iterations

### Iteration 1 — local time tracking

- Windows desktop and tray application using Python and PySide6.
- Local SQLite storage on the current machine.
- Start/finish work, lunch timing, multiple daily sessions, notes, office status, and manual corrections.
- Daily, weekly, and monthly list views with net worked hours; monthly entries are grouped by calendar week number.
- Close-to-tray behavior with an explicit **Close app** command in the tray menu.
- Local recovery after application restart and basic validation of incomplete or overlapping entries.
- Configurable rounding, reminders, sleep recovery, automatic backups, restore, CSV export, and a 37-hour weekly target.
- English interface with Danish regional formats and Europe/Copenhagen time handling.
- No Google authorization, Google Sheets reads/writes, cross-machine synchronization, Testhuset automation, SAP automation, or final workplace submission.

### Later iterations

- Synchronize through structured tabs in the existing Google workbook.
- Add draft and explicit final-submission workflows for Testhuset and DSB after inspecting their authentication and registration interfaces.

## Integration feasibility and limitations

- Optional Google Sheets V2 sync uses immutable causal records, atomic local capture, verified readback and explicit conflict resolution. Existing V1 data requires verified all-participant migration; real two-client workbook acceptance remains open.
- Later decisions authorize Google, Testhuset and DSB integrations beyond the original local-only iteration. Existing workbook tabs and formulas remain outside QI Flow's owned structured tabs.
- Testhuset and DSB use temporary Edge sessions with user-managed login, explicit differing-row choices and verified destination saves. DSB includes only selected scanned Testhuset branches and uses Send without approval or locking. Local browser fixtures pass; live reviewed-fill acceptance remains open.
- SAP GUI scripting remains deferred. No SAP connector, telemetry or automatic update installation is introduced by the audit remediation.
- The desktop tracker can operate independently of workplace connectivity; submissions must run on a machine with access to the destination.
- A browser can still be needed for Google authorization and Testhuset submission even though the tracker is a desktop application.

## Open decisions for review

| ID | Question | Suggested starting point |
| --- | --- | --- |
| Q01 | Existing Google workbook or a separate private workbook? | **Resolved:** use the existing workbook and add structured app-data tabs without changing existing month tabs. |
| Q02 | Does closing the window minimize to tray? What does explicit Quit do to a running session? | **Resolved:** closing minimizes to tray without changing the active session. With a tray, use **Close app**; without a tray, window close and the visible action use the same confirmation. Failed Finish and Cancel keep the window accessible. Active timestamps are persisted before exit. |
| Q03 | How should Finish work behave while on lunch? | **Resolved:** lunch is a separate interval inside one continuous work session and only **End lunch** stops it. **Finish work** is unavailable during lunch. |
| Q04 | What happens for forgotten timers, overlapping entries, midnight and conflicting offline edits? | Offer explicit correction; no automatic idle-time deductions. Define reconciliation before sync implementation. |
| Q05 | Do workplaces require rounding, activity/project codes, comments, or different hour totals? | Preserve raw time; configure each destination after inspecting workflows. |
| Q06 | Does registration mean saving a draft or final weekly submission in each workplace? | **Resolved:** save drafts by default; final submission requires the user to press **Submit week** explicitly. Confirm how each destination exposes draft/final actions during integration work. |
| Q07 | Import existing history or keep it only in the current spreadsheet? | Optional import, outside initial scope unless requested. |

## Not currently requested

Payroll/invoice/bonus calculation, automatic activity surveillance, automatic idle deductions, calendar integration, team administration, mobile apps, or migration to a paid hosted database. The screenshot is reference data, not an instruction to reproduce every spreadsheet feature.

## Approval and change log

- 2026-09-15: v0.1 drafted from conversation.
- 2026-09-15: v0.2 records use of the existing workbook, draft-by-default workplace registration, explicit final submission, and deferred authentication investigation. Requirements/design reviewed positively; ready for user stories. Development: not started.
- 2026-09-15: v0.3 defines iteration 1 as a local-only tracker. Google Sheets sync and workplace integrations remain planned for later iterations.
- 2026-09-15: v0.4 resolves tray closing, continuous work sessions with separately deducted lunch, and monthly grouping by calendar week number.
- 2026-09-15: v0.5 records the completed design interview in `DECISIONS.md` and adds the approved iteration 1 behaviors R20–R30. User stories drafted; development not started.
- 2026-09-17: v0.1.1 fixes completion of short timer lunches, lets a selected completed session
  receive a manual lunch deduction from its editor, and keeps Today’s sleep threshold aligned with
  saved Settings.
- 2026-09-17: v0.2.0 authorizes optional Google Sheets synchronization. The user supplies a private
  Sheet URL and desktop OAuth client ID; OAuth authorization occurs locally on every machine.
- 2026-09-19: DSB weekly registration is authorized with an explicit per-day review and DSB
  **Send** action. Week approval/locking remains manual; live DSB verification is still required.
- 2026-09-20: v0.2.1 bundles the Google Sheets OAuth and API dependencies in the Windows
  installer; this fixes Google authorization on packaged installations.
- 2026-09-20: Timer-created work sessions use outward rounding: start rounds down and finish
  rounds up to the configured boundary. Lunch deductions retain nearest-boundary rounding.
- 2026-10-02: Timer-created starts now use the earlier boundary only when it is the nearer one;
  otherwise the actual start is kept. At 15-minute precision, 08:06 becomes 08:00 and 08:40
  remains 08:40. Finishes still round up and lunches still use nearest-boundary rounding.
- 2026-10-02: Epic G in-app updates preserve the installed Windows uninstaller. New per-user
  installers remove their own optional Start with Windows registration on uninstall while keeping
  user data and backups.
- 2026-09-20: DSB hours are limited by a user-managed allowlist of scanned Testhuset branches.
  Sessions assigned to other or unresolved branches remain recorded but are excluded from DSB.
- 2026-09-27: R30 revised at the user's request to permit verified in-app updates without a
  manual installer download/run for each release. No telemetry remains in scope; Epic G US32
  defines the updater acceptance criteria.
- 2026-09-20: Google sync reads and merges the structured tab before writing it. A fresh machine
  imports completed work sessions and deductions rather than clearing remote history; divergent
  records at the same revision stop synchronization instead of being silently overwritten.
- Future sessions: read this file and `DESIGN.md` first; update decisions and statuses explicitly. Do not infer approval from the existence of these documents.

## Epic I delivery — 17/09/2026

US25–US27 are implemented as an authorized post-iteration-1 integration. The app opens a
temporary Edge session for direct user login, scans task names/IDs, supports default and
per-session assignment, previews daily decimal hours, requires conflict choices and explicit
fill confirmation, and verifies each save response. Week closure remains manual.

This supersedes the earlier Testhuset deferral only for the confirmed Epic I scope. Google
Sheets, SAP and final workplace submission remain outside this change. Testhuset sign-ins can be
saved only through the user’s opt-in Windows Credential Manager setting.
Automated tests verify the browser contract with isolated fixtures. Live navigation and page
structure were inspected read-only; a first real fill remains a release smoke check.
