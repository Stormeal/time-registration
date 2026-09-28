# QI Flow compact design review

Date: 28/09/2026. Status: compact implementation and independent review complete.

## Agreed brief

The user prefers the Compact utility POC and wants TestHuset branding using the supplied
transparent light and dark logos. Functionality and a good user experience take priority over
making the window smaller. A required action may move to an appropriate screen, but must remain
discoverable and retain its acceptance criteria. The POC is not a complete functional specification.

Brand sources are preserved unchanged in `docs/design-assets/logo_primary.png` and
`docs/design-assets/logo_alternative.png`. Both are 1135 × 220 transparent PNGs. The primary
contains orange `#F48F21` and warm grey `#695E4A`; the alternative contains white artwork. Use
the latter on the dark header, without an opaque backing panel. Preserve aspect ratio and the
QI Flow product name. Do not automatically replace the app or tray icon with the full wordmark.

## Functionality preservation matrix

Sources: REQUIREMENTS.md, DECISIONS.md, active USER_STORIES.md, archived acceptance criteria,
and the current UI handlers. Later authorized integration decisions supersede the original
iteration-1 exclusions. Existing implementation and unfinished backlog are separate: a redesign
must preserve the former, without claiming to complete the latter.

| Area and criteria | Access in the compact design | POC coverage and required verification |
| --- | --- | --- |
| Work/lunch timers; US01–US04, D010–D027, R20–R23 | Today retains valid state-specific actions, active net time, session start, lunch duration, and 30-second Undo. | Simulated only. Preserve multiple sessions/lunches, persisted transitions before success, rounding, actual/effective boundaries, and restart recovery. Finishing during active lunch remains unavailable with a visible explanation. |
| Manual corrections; US05, US22, R31 | Add entry opens the existing work/lunch/sleep-break form. Selected-day Edit sessions retains the full editor and active-start correction. | POC only edits one example work session and has simplified validation. Preserve exact minutes, parent-session choice, containment/overlap checks, active-state restrictions, and unsaved-change confirmation. Never adopt the POC's artificial evening-only restrictions. |
| Delete/history; US06 | Selected-day editor retains Delete, confirmation, and View recoverable history with version restoration. | Missing from POC. Preserve 30-day history, selected-version restore, and recalculation of totals. |
| Daily context; US07, D042–D043 | Office status and multiline notes remain on Today and selected-day editing. | Partially simulated. Preserve notes before work, default remote status, independent date context, cross-midnight office copying, explicit Save, and discard safeguards where currently provided. |
| Sleep/previous-day recovery; US04, US08 | Existing resolution dialogs remain reachable; Today shows a clear blocked state and a resolution action when required. | Missing from POC. Include/exclude/decide-later decisions remain explicit; unresolved ambiguity disables timer actions but permits review. No invented finish time or automatic time deduction. |
| Tray/lifecycle/startup; US09–US11, D030–D039 | Keep the tray panel and context menu, Close app, startup toggle, and recovery entry points. | Not represented in a browser POC. Window close still hides to tray; safe exit choices, reminders-paused explanation, single instance, and persisted active state remain unchanged. |
| Monthly review; US12, US26, R19, R38 | Timesheet retains month navigation and every calendar day in ISO-week groups. Keep date, start, finish, session count, lunch, net, decimal hours, office status, and note indicator visible or immediately accessible. | POC shows only two sample weeks and omits several fields. Full month, weekends, multiple-session counts, missing entries, and provisional active totals must be verified. Initial implementation should retain the existing table fields. |
| Weekly targets; US13, D044 | Timesheet keeps a selected-week target override and neutral remaining/over-target text; Settings keeps the default target. | POC only displays a fixed 37h target. Preserve per-week editing independently from default target, including ISO weeks crossing month/year boundaries. |
| Reminders/help; US14, US23, R25, R32 | Settings must retain both enable toggles, independent threshold controls, sleep enable/threshold, rounding, and concise hover help. Notifications retain snooze and Open QI Flow. | POC displays fixed reminder thresholds. Moving controls from Today requires explicit documentation of the new location and UI tests for saving/reloading each option. Retain 15/30/60-minute snooze and explanatory sleep help. |
| Backups/restore; US15–US16, R26 | Settings retains folder selection/save, status, available backup dates, Back up now, and guided Restore. Persistent backup failure remains discoverable while tracking. | Placeholder only. Preserve 30 daily backups, safety copy, restore confirmation/restart, active-session safeguards, and corrupt-database handling. |
| CSV export; US17, R29 | Preserve Settings' summary/detailed exports and selected week/month/all-history scopes. A contextual Timesheet shortcut may be added later. | Sample summary download only. Existing formats, date-range selection, detailed deductions, decimal commas, and exclusion of deleted/raw metadata remain required. Do not remove the Settings route during the first visual pass. |
| Setup/formats/privacy; US18–US20, D065–D073 | Retain first-run defaults, System/Light/Dark options, optional startup, application-data path, and Open log folder. | Partial appearance demonstration. Verify English labels, Danish dates/24-hour times, keyboard operation, Windows scaling, theme contrast, no telemetry, and privacy-safe diagnostics. |
| TestHuset; US25–US27, R34–R38 | Settings retains task scanning, scan week, default task, opt-in save/forget Windows sign-in. Session editor retains task overrides. Timesheet retains selected-week reviewed fill. | Review button is a placeholder. Preserve confirmation, per-slot conflict choices, stale-preview rejection, save verification, cancellation/cleanup, and manual week closure. No new registration-state claims without stored evidence. |
| Google sync; US28–US29, D099–D101 | Settings retains Sheet/client configuration, OAuth client handling, authorize/disconnect, sync action, progress and status. | Placeholder only. Preserve current behavior and privacy boundaries. Conflict resolution, additional record types, and scheduled sync still need their own backlog work; the redesign does not mark them complete. |
| DSB; US30–US31, D102–D103 | Settings retains opt-in, allocation scan/week/default. Timesheet retains selected-week review and explicitly confirmed Send flow. | Placeholder only. Preserve enabled/disabled availability, per-day choices, uncertainty handling, and manual approval/locking. Branch allowlisting remains US31 backlog work; do not claim it exists from a visual mockup. |
| Distribution/updater; US21, US32, R30 | Preserve current update-check, status, confirmation, and recovery controls in Settings, plus installer behavior. | Not represented. Keep verified updates, per-user installation, data preservation, and rollback. Published-asset and clean-account smoke checks remain open. |

## Recommended migration boundaries

Start with shared colours, typography, logos, and the compact navigation shell. Keep existing
pages, dialogs, service calls, and entry points. Then adjust Today, Timesheet, and Settings one
tightly related change at a time. Do not replace the PySide6 application with the browser POC.

For Today, move configuration only after Settings exposes every value and help text, including
configurable reminders. Keep the editor dialogs rather than replacing them with the POC's reduced
forms. For Timesheet, keep all required columns and month navigation before considering a new
details pane. Keep backup/export/integration controls available throughout the transition.

Domain/application/infrastructure behaviour, migrations, timestamp semantics, local-first saves,
and integration contracts remain outside visual changes. Where layout work requires a new query
(for example today's total separate from the active session), add it at the application boundary
and test it there; do not invent UI-only totals or bypass existing time rules.

## Acceptance gate for each application change

- Map touched functions to this matrix and the original story acceptance criteria.
- Add meaningful UI interaction tests for relocated controls, state transitions, and save/cancel.
- Verify Start → Lunch → End lunch → Finish → another session → edit → restart → export,
  including timer Undo and recoverable edit/delete history.
- Verify blocked recovery, tray/main consistency, active-start correction, and safe exit.
- Verify full month/ISO weeks, weekly target overrides, provisional time, and both CSV formats.
- Verify Settings access to reminders, sleep, theme, startup, backups, diagnostics, integrations,
  and updates. Exercise integration contracts with fixtures, without sending real hours.
- Check light/dark logo selection, keyboard focus, disabled-state explanations, and 100%, 125%,
  150%, and 200% Windows scaling. Allow reflow/scrolling instead of shrinking text or hiding actions.
- Run `scripts/check.ps1`. The development environment now includes the required tools; final
  checks cover formatting, lint, strict type checking and all tests.

## Implementation evidence

The six-task plan is in `docs/superpowers/plans/2026-09-28-compact-design.md`. The original 155-test
baseline passed before product changes. New tests cover live theme/state preservation, navigation,
relocated option persistence, active lunch and allocated totals (including midnight/DST/rounding),
draft protection, complete month/ISO-week selection, narrow layout and restart/correction/CSV flow.
No domain rules, migrations or external registration contracts changed. New application queries
`today_summary()` and `active_lunch_seconds()` use the existing rules and injected clock.

Real Qt widgets were rendered against an isolated temporary database, with Windows Segoe UI,
in light/dark modes at 890×750 and 640×520 logical sizes. The narrow window fits at Qt scale factors
1, 1.25, 1.5 and 2. Today/Settings scroll vertically, and the complete Timesheet scrolls in both
directions. Captures include stopped/working/lunch, full Settings, editor/history and tray views.
These are offscreen Qt checks; a manual Windows monitor/keyboard pass and clean-account installer
test remain release checks. Both logo PNGs were verified byte-for-byte in a built wheel.

The redesign does not complete Google synchronization/conflicts, DSB filtering, published update
assets, or live integration/installer smoke checks. Those backlog/release criteria remain open.

Final `scripts/check.ps1`: 184 tests pass; formatting, Ruff and strict mypy (53 source files) pass.
The independent reviewer found startup recovery routing and an overnight-list inconsistency.
Both were reproduced with failing tests, fixed, and included in the passing full gate. Recovery
navigation waits until the shell is connected; continued work remains visible with its original
start date. US33 is archived with its criteria. Implementation is on `codex/compact-design`.
