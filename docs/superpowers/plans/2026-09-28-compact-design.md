# QI Flow Compact Design Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the selected compact PySide6 interface with TestHuset branding while preserving every existing feature and accepted safeguard.

**Architecture:** Retain the current application, domain, SQLite adapters, integration workers, and editing/recovery dialogs. Introduce one shared Qt theme owner and a compact horizontal navigation shell; migrate presentation and configuration in small tested tasks. Obtain Today totals through the existing application summary rules, never by calculating time in widgets.

**Tech Stack:** Python >=3.12, PySide6 >=6.8,<7, SQLite, pytest/pytest-qt, Ruff, mypy; existing packaging and browser fixture tests.

**Spec:** `docs/compact-design-review.md`, selected Compact utility POC, `DESIGN.md`, and revised D071/D074 in `DECISIONS.md`. Current request approves moving from that design review to implementation planning; implementation awaits this plan's review and execution-method selection.

## Global Constraints

- QI Flow remains a Windows desktop application using Python, PySide6, and SQLite.
- Use TestHuset orange `#F48F21` and warm grey `#695E4A`; primary transparent logo for light mode and white alternative for dark mode. Preserve logo proportions, without a backing panel.
- Theme follows Windows by default with System / Light / Dark overrides. Retain the existing QI app/tray icon.
- Labels are English; dates use `dd/MM/yyyy`; times use 24-hour format; week numbers follow ISO 8601.
- Use timezone-aware UTC internally and Europe/Copenhagen for calendar allocation. Use injected clocks and identifier generators in business behavior.
- Every timer transition is persisted before reporting success. Preserve 30-second timer Undo and 30-day recoverable history.
- Finish work remains unavailable during lunch. Unresolved previous-day/sleep ambiguity blocks timer actions but permits review.
- Preserve all functionality in the spec's matrix, including existing integration, backup, export, updater, and diagnostics controls. Do not complete unfinished integration stories as part of styling.
- Keep domain/application tests runnable without PySide6. No SQL in widgets, new schema migrations, telemetry, global shortcuts, or changes to external submission contracts.
- Retain current forms, explicit saves, confirmations, validation, credential boundaries, and tray lifecycle.
- Keep all nine Timesheet columns and every calendar day. Allow reflow/scrolling rather than hiding functions or shrinking essential text.
- No live workplace writes, production-data edits, app installation, or release publication during implementation verification.

## Review Focus

1. Changing theme while tracking or editing must retain current state, unsaved notes, and open dialogs (Task 1).
2. Long destination action labels and Windows scaling must leave navigation/actions reachable at small window sizes (Tasks 2/5).
3. Moving reminder controls must retain independently configurable thresholds and immediate use of saved settings without restart (Task 3).
4. A timer spanning midnight/DST must separate the active session timer from today's allocated total, including provisional rounding semantics (Task 4).
5. Editing and returning to Timesheet must refresh totals without losing the selected date/week or enabling a destination for the wrong week (Task 5).

## Execution setup

- [x] Confirm execution method and workspace choice with the user at this plan's review gate. Recommend native execution in the current checkout, preserving existing documentation/logo changes and the untracked frontend-design skill. If an isolated worktree is selected, use native Codex worktree tools and deliberately transfer the approved docs/assets; native creation does not copy uncommitted files. Never reset or clean unrelated changes.
- [x] Establish the development environment using README.md: `python -m venv .venv`, then `.\.venv\Scripts\python.exe -m pip install -e ".[dev]"`. Reuse a valid environment when available. Keep installer/tool installations outside this task.
- [x] Run `.\scripts\check.ps1` and record the baseline. The current interpreter lacks Ruff; an environment setup failure or failing baseline must be reported, not labelled green.
- [x] Use test-driven development for behavior changes. Extend existing fixtures or create `tests/ui/conftest.py` with a temporary SQLite database, a mutable injected UTC clock, deterministic unique IDs, and a fake startup adapter; never use the real database, registry, credentials, or network.
- [x] Track task checks/results and deviations beside this plan using the selected Superpowers execution workflow. Commit only task-owned files; preserve unrelated work.

### Task 1: Shared theme and transparent brand assets

**Files:** Create `src/qi_flow/ui/theme.py`, `tests/ui/test_theme.py`; copy unchanged source logos from `docs/design-assets/` to `src/qi_flow/assets/`; modify `src/qi_flow/bootstrap.py`, `src/qi_flow/ui/main_window.py` and `src/qi_flow/ui/settings_page.py`.

**Interfaces:** `ThemeManager(app: QApplication)` owns the application palette/style and connects to Qt's `QStyleHints.colorSchemeChanged`. `apply(preference: str) -> None` accepts `system`, `light`, `dark`; `resolved_theme: str` and `changed = Signal(str)` expose the effective mode. `logo_path(theme: str) -> Path` resolves packaged PNG resources using `importlib.resources.files`. `MainWindow` owns the manager, applies the saved preference, and connects Settings' existing `preferences_saved` signal to reapply it. The constructor remains compatible with current bootstrap/tests.

- [x] Write failing tests `test_saved_theme_changes_logo_without_recreating_pages`, `test_system_theme_tracks_colour_scheme_but_override_does_not`, and `test_theme_change_preserves_unsaved_note_and_open_editor`. Assert the existing page/editor instances and entered text survive; light/dark resource paths differ and both load non-null PNGs. Simulate Qt colour-scheme notification locally. If an import is missing initially, introduce the empty interface before verifying meaningful assertions fail.
- [x] Run `.\.venv\Scripts\python.exe -m pytest tests/ui/test_theme.py -q`; confirm failures on missing behavior.
- [x] Implement the manager and resource loading. Use Segoe UI, 14–15px-equivalent body text, modest 4–6px corner rounding, consistent spacing, orange primary actions with dark text, neutral secondary controls, readable disabled/focus/selection states, and transparent logo labels. Theme standard controls, menus, tables, tooltips, and dialogs coherently; do not style every button as primary. Remove bootstrap's conflicting hard-coded teal stylesheet.
- [x] Run the new tests and `tests/ui/test_updates_ui.py`; require zero failures. Confirm logo assets remain bundled by the existing assets `--add-data` rule rather than adding a second asset path.
- [ ] Commit the shared-theme change and record results. Acceptance: D071, D074, US19; theme switching changes appearance without resetting functional state.

### Task 2: Compact horizontal shell and navigation

**Files:** Modify `src/qi_flow/ui/main_window.py`, `tests/ui/test_main_window.py`; create `tests/ui/test_compact_layout.py`.

**Interfaces:** Replace the sidebar navigation with a `QTabBar` using the same page order/index mapping. Preserve `reveal()`, `show_timesheet()`, `show_settings()` and all constructor dependencies. Preserve `applicationVersion`; move version text to a quiet footer. Update existing tests from `currentRow()` to tab `currentIndex()` without weakening the page-selection assertions.

- [x] Write failing interaction tests `test_horizontal_tabs_select_existing_pages`, `test_tray_routes_reveal_correct_page_after_tab_replacement`, and `test_compact_header_keeps_tabs_reachable_at_narrow_width`. Click native controls and assert current page/widget identity and visibility. The narrow test resizes to 640×520 and checks controls fit the header's available area; avoid screenshot-pixel assertions.
- [x] Run `.\.venv\Scripts\python.exe -m pytest tests/ui/test_main_window.py tests/ui/test_compact_layout.py -q`; confirm new tests fail before layout changes.
- [x] Implement an approximately 890×640 initial window, branded top header, horizontal tabs, and content beneath. Permit natural resize; when necessary place branding on a separate row instead of compressing tabs. Give Today and Settings scrollable wrappers, retaining the Timesheet table's own scrolling. Preserve service wiring, DSB availability signal, startup routes, close-to-tray, and version display.
- [x] Run the task tests plus `tests/ui/test_exit_dialog.py`, `tests/ui/test_single_instance.py`, and `tests/ui/test_tray.py`; require zero failures.
- [ ] Commit and record results. Acceptance: D074, US09–US11; every navigation route and lifecycle behavior survives.

### Task 3: Settings owns all tracking configuration

**Files:** Modify `src/qi_flow/ui/settings_page.py` and `src/qi_flow/ui/today_page.py`; create `tests/ui/test_settings_preferences.py`.

**Interfaces:** Reuse `app_preferences()`, `save_app_preferences(AppPreferencesView)`, `reminder_settings()`, `set_reminder_settings(ReminderSettingsView)` and `preferences_saved`. Add Settings fields `_work_reminder_enabled`, `_work_reminder_minutes`, `_lunch_reminder_enabled`, `_lunch_reminder_minutes`, matching the current Today ranges. Keep existing controls/handlers for backup, export, diagnostics, Google, TestHuset, DSB, updates and startup.

- [x] Write `test_settings_saves_both_reminder_thresholds_and_enable_flags`: edit work to 480 minutes and lunch to 35, disable only lunch, press Save, reconstruct Settings, and assert `ReminderSettingsView(True, 480, False, 35)` from the service and matching controls.
- [x] Write `test_settings_preserves_rounding_sleep_target_and_startup` and `test_reminder_change_takes_effect_without_restarting_today`: use real application persistence and clock advancement; assert saved values and due reminder behavior. Add `test_relocated_controls_retain_explanatory_tooltips` including the non-destructive sleep explanation.
- [x] Run `.\.venv\Scripts\python.exe -m pytest tests/ui/test_settings_preferences.py -q`; verify meaningful failures.
- [x] Implement a Tracking group with rounding, weekly target, sleep enable/threshold, work/lunch enable/threshold controls and concise help. Appearance/startup and all existing groups remain accessible through Settings scrolling. Preserve explicit Save behavior and complete first-run setup only on save. Avoid introducing tabbed subsections or moving export/integrations in this pass.
- [x] Only after Settings has parity, remove Today configuration form controls and their obsolete persistence handlers. Keep sleep detection/prompt behavior and service settings reads. Update existing tests that access relocated controls to assert their new Settings interaction, preserving the original behavior assertion.
- [x] Run the new tests, `tests/ui/test_today_sleep.py`, `tests/ui/test_updates_ui.py`, and `tests/ui/test_testhuset_ui.py`; require zero failures.
- [ ] Commit and document new control locations, retaining archived criteria as history. Acceptance: US03, US08, US14, US18, US23 and spec matrix configuration parity.

### Task 4: Compact Today with authoritative totals

**Files:** Modify `src/qi_flow/application/time_tracking.py`, `src/qi_flow/ui/today_page.py`, `src/qi_flow/ui/main_window.py`; create `tests/integration/test_today_summary.py`, `tests/ui/test_today_compact.py`.

**Interfaces:** Add `today_summary() -> DaySummaryView` to the application service, using injected `_when(None)`, Copenhagen date, and existing `_summaries_for_range(date, date + timedelta(days=1))`. Preserve that calculation's existing effective/provisional semantics. Today uses its date for `weekly_progress(IsoWeek(...))`, daily context and `completed_sessions_for_day(date)`. Add `open_timesheet_requested = Signal()` to Today and connect to `MainWindow.show_timesheet`. Active session/lunch elapsed display continues using actual timestamps from `active_state()`.

- [x] Write application tests `test_today_summary_sums_multiple_sessions_with_deductions`, `test_today_summary_allocates_cross_midnight_session_only_to_today`, `test_today_summary_uses_copenhagen_day_at_dst_boundary`, and `test_today_summary_keeps_existing_provisional_rounding_semantics`. Fixed instants and explicit expected seconds must distinguish live actual session time from effective allocated day totals. Watch assertions fail before implementing the query.
- [x] Write UI tests `test_compact_today_work_lunch_finish_and_undo_flow`, `test_today_total_survives_finish_and_second_session`, `test_pending_recovery_disables_actions_and_can_be_reopened`, `test_today_context_save_preserves_unicode_note`, and `test_unsaved_note_survives_timer_refresh`. Use the real temporary service, clock advancement and state assertions; use modal-dialog stubs only at the OS/user-choice boundary.
- [x] Run `.\.venv\Scripts\python.exe -m pytest tests/integration/test_today_summary.py tests/ui/test_today_compact.py -q`; confirm failures correspond to the new query/layout behavior.
- [x] Implement compact status/timer row and state-specific primary action, secondary Finish where valid, visible lunch duration and disabled explanation during lunch, and accessible Undo using the service's actual deadline. Add daily net/lunch and weekly-target summary row, provisional labels, completed-session list/Edit sessions route, Add entry, Open timesheet and office/note Save. Label allocated totals as effective/rounded while the active session remains actual time; do not imply all displayed totals have identical rounding semantics.
- [x] Preserve recovery dialogs; add a visible Resolve action that reopens a deferred sleep decision by clearing only the UI deferral flag. Don't clear persisted ambiguity except through the service. Refresh labels without replacing editors or changing unsaved notes. When Copenhagen date changes, avoid silently overwriting unsaved daily context; ask whether to save/discard before loading another day's values. Do not duplicate per-session assignment/deletion/history forms.
- [x] Run task tests plus `tests/ui/test_today_sleep.py`, `tests/ui/test_testhuset_ui.py`, `tests/ui/test_tray.py`, and the full integration directory; require zero failures.
- [ ] Commit and record results. Acceptance: US01–US08, US13, US22; authoritative totals and unchanged timer/correction safeguards.

### Task 5: Readable complete Timesheet and consistent dialogs/tray

**Files:** Modify `src/qi_flow/ui/timesheet_page.py` and `src/qi_flow/ui/tray_panel.py`; create `tests/ui/test_timesheet_compact.py`; extend `tests/ui/test_compact_layout.py`. Existing dialogs receive the shared theme and retain their controls.

**Interfaces:** Preserve `_COLUMNS`, existing month navigation, target editing, destination routes, date/week data roles and `refresh_dsb_availability()`. Add `showEvent(event: QShowEvent) -> None` to refresh when returning to Timesheet, restoring selection by date/ISO-week identity. Continue using current application summaries and integration dialog factories.

- [x] Write `test_month_retains_all_days_and_required_columns`, `test_week_target_override_does_not_change_default`, `test_edit_return_refreshes_totals_and_preserves_selected_week`, `test_destination_buttons_follow_selected_iso_week_and_dsb_opt_in`, and `test_long_destination_labels_remain_reachable_in_compact_window`. Check September's 30 days including weekends; test a December/January ISO-week boundary too.
- [x] Run `.\.venv\Scripts\python.exe -m pytest tests/ui/test_timesheet_compact.py tests/ui/test_compact_layout.py -q`; new refresh/selection and compact layout tests must fail before the corresponding changes.
- [x] Implement compact month toolbar, readable week grouping, right-aligned durations/decimals and restrained date/selection emphasis. Retain all nine columns, notes/office indicators, weekly target control, Add entry/Edit sessions and independent destination actions. Use wrapped or separate action rows for long labels, and horizontal table scrolling where necessary. Preserve provisional markers. Keep full correction/assignment/history dialogs rather than implementing the POC's simplified details editor.
- [x] Apply matching control roles/spacing to the tray panel without changing tray commands, popup ownership, placement or state logic. Mark destructive actions distinctly only where relevant; never convert Finish work into deletion-style danger styling.
- [x] Run the new tests and all `tests/ui`; require zero failures, including fixture-backed registration, safe exit, history restoration and updater tests.
- [ ] Commit and record results. Acceptance: US09, US12–US13, US22, US26–US27 and current DSB availability/selected-week contracts.

### Task 6: Regression gate, visual inspection and documentation

**Files:** Update `DESIGN.md`, `docs/compact-design-review.md`, `DECISIONS.md` only if a behavior decision changed, and `USER_STORIES.md`/archive for a dedicated compact-redesign story with criteria from Tasks 1–5. No unrelated backlog stories move to complete.

- [x] Add a meaningful `tests/ui/test_compact_end_to_end.py` flow: start/lunch/end/finish, second session, manual correction, reconstruct the service/window using the same temporary database, export summary and detailed CSV into temporary files, and verify the expected persisted times/totals/deductions. Retain existing tests for history, safe exit, sync and registration; this flow does not pretend to test live external services.
- [x] Run `.\scripts\check.ps1` from PowerShell: formatting, lint, strict mypy and full pytest must all pass. Report every failing test by name and whether it predates this work; don't weaken checks or rewrite assertions merely to match the redesign.
- [x] Launch Qt with an explicitly temporary data root using a review harness, not the production `run()` entry point. Capture real Today stopped/working/lunch, full Timesheet, Settings lower sections, correction/history and tray-panel views in light/dark themes. Inspect at 100%, 125%, 150%, and 200% scaling and a 640×520 window; record any unverified scaling rather than claiming coverage.
- [x] Build a wheel using existing project tooling or `pip wheel --no-deps .` into ignored temporary output; inspect the ZIP to confirm both logo PNGs are included. Use the existing PyInstaller assets rule for future release packaging; do not require installing Inno Setup or publishing a release to complete the redesign.
- [x] Perform the selected Superpowers independent review, resolve important findings with failing regression tests first, then rerun the quality gate after fixes. Confirm the matrix's original controls remain reachable. Record reviewer findings and deferred limitations.
- [ ] Update docs with the final screen/control locations, acceptance criteria, checks run and actual outcomes. Commit task-owned changes and present the implementation for review. Leave installer clean-account and live integration smoke checks explicitly open where still unverified.

## Plan self-review

The plan covers all preservation-matrix areas: theme/shell, tracking/configuration, authoritative
summaries, complete monthly review, unchanged editing/history/tray/integration/export/recovery,
and packaging verification. The only application-layer addition is `today_summary()`; it reuses
existing rules. No repository/schema or integration contract changes are planned. Existing
controls are retained before layout simplification, particularly reminder thresholds and table
fields. All five Review Focus cases have named owning-task tests or documented visual checks.

No plan task copies demo timestamps, simplified validation, fixed targets, or placeholder
registration behavior into production. CSS/browser POC verification is not Qt verification.

## Review and execution decision

Recommended: native execution in the current checkout, with a final independent reviewer. The
tasks share the same Qt shell, theme and tests; serial implementation keeps their dependencies
clear. Preserve the current uncommitted review docs and supplied logos. If isolation is preferred,
use a managed worktree after the user selects it, carrying those inputs deliberately.

Alternative: subagent-driven execution with a fresh implementer and reviewer for each task.
This adds review depth at greater context cost. Implementation begins only after the user
reviews this plan and chooses an execution approach, as required by Superpowers writing-plans.

## Execution outcome — 28/09/2026

All six implementation tasks and acceptance checks completed. Final quality gate:184 tests pass,
Ruff format/lint pass, strict mypy53 source files pass. Native offscreen Qt previews cover both
modes, all principal screens/dialogs/tray, 640x520 and scale factors1/1.25/1.5/2. Wheel logos match
supplied bytes. The one independent reviewer found startup recovery routing and an overnight
list inconsistency; both were reproduced RED, fixed GREEN, then verified by the full184-test gate.

Minor deviations: added active_lunch_seconds() alongside today_summary() to keep the live lunch
clock injected; preserved Testhuset assignment support in Today's existing session editor;
month heading reflows onto its own row; short destination actions use selected week/year in the
summary rather than in each caption. No time, storage or external integration rules changed.

Per-task commits were deferred because Git author identity was not configured. The user later
requested a consolidated commit and push on codex/compact-design; the existing author identity
from the repository's latest commit is reused for that command without changing global settings.
The execution ledger is retained locally. Manual Windows monitor/keyboard, clean-account installer
and authenticated live integration checks remain explicitly open release checks.
