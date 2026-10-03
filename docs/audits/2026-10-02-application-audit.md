# QI Flow application audit — 2 October 2026

**Current disposition — 4 October 2026:** A01–A18 are resolved and recorded in the
[resolved-issue archive and backlog review](2026-10-04-resolved-issues.md). This report retains
the original findings against the baseline; active stories still track required release acceptance.

Audit started 2 October and completed 3 October 2026. Audited commit: `61b507f6a306263effc701bc0eedb03ee9a01ecd`. Runtime/source version: `0.2.6`; release builds stamp their own version as documented. This was a review of the current application, not an implementation change.

The application has useful foundations: separate domain/application packages, injected business clocks and identifiers, versioned migrations, immediate timer persistence, and substantial interaction tests. The main risks are inconsistent validation when restoring/importing records, synchronization that can lose changes, and incomplete failure handling around desktop lifecycle and updates.

The audit found **18 actionable issues: 8 P1 and 10 P2**. P1 means fix before depending on the affected workflow; P2 means a reproducible correctness or reliability issue with narrower impact. No P0 issue was established. Known backlog items and previously documented editor limitations are listed separately.

**Verification and coverage**

- Read `REQUIREMENTS.md`, `DECISIONS.md`, `USER_STORIES.md`, and `ARCHITECTURE.md`, including later authorizations for integrations and updates.
- Reviewed domain rules, application services, SQLite migrations/adapters, history and undo, backups/exports, Google sync/OAuth, Testhuset/DSB adapters, UI/lifecycle, installer/updater, and release tooling. Parallel review findings were checked against source and independently reproduced where practical.
- Ran `.\scripts\check.ps1`: **258 tests passed in 42.28 seconds**; Ruff formatting, Ruff lint, and strict mypy also passed.
- Ran `.\.venv\Scripts\python.exe -m pip check`: **No broken requirements found**. This checks installed dependency consistency, not vulnerability status.
- Additional probes used temporary SQLite databases, synthetic OAuth data, fake Google gateways/API responses, real Qt widgets, and a local headless Edge fixture with network requests aborted. Existing application data and live workplace/Google accounts were not used.
- The audit does not establish clean-account installer behavior, packaged update/rollback behavior, or live authenticated registration success. Those remain release acceptance checks.

**Prioritized findings**

| ID | Priority | Finding | Primary location |
| --- | --- | --- | --- |
| A01 | P1 | Replacing the shared tab can erase records or concurrent changes | [google_sheets_sync.py](/C:/Users/Alex/source/time-registration/src/qi_flow/infrastructure/google_sheets_sync.py:60) |
| A02 | P1 | Conflict detection silently chooses between independent edits | [google_sync_service.py](/C:/Users/Alex/source/time-registration/src/qi_flow/application/google_sync_service.py:150) |
| A03 | P2 | An ended lunch is synced before its still-active parent | [google_sync_service.py](/C:/Users/Alex/source/time-registration/src/qi_flow/application/google_sync_service.py:112) |
| A04 | P1 | Sync imports bypass overlap and parent-containment rules | [google_sync_service.py](/C:/Users/Alex/source/time-registration/src/qi_flow/application/google_sync_service.py:164) |
| A05 | P1 | OAuth callback codes enter diagnostic logs | [logging.py](/C:/Users/Alex/source/time-registration/src/qi_flow/infrastructure/logging.py:22) |
| A06 | P1 | History restore can create invalid open intervals | [time_tracking.py](/C:/Users/Alex/source/time-registration/src/qi_flow/application/time_tracking.py:378) |
| A07 | P1 | A refused update can roll back an unrelated recovery folder | [update_helper.py](/C:/Users/Alex/source/time-registration/scripts/update_helper.py:163) |
| A08 | P1 | Simultaneous Windows launches can both become primary | [single_instance.py](/C:/Users/Alex/source/time-registration/src/qi_flow/infrastructure/single_instance.py:50) |
| A09 | P1 | DSB reads/writes the first daily row regardless of allocation | [dsb_browser.py](/C:/Users/Alex/source/time-registration/src/qi_flow/infrastructure/dsb_browser.py:244) |
| A10 | P2 | Google authorization can block the entire UI indefinitely | [settings_page.py](/C:/Users/Alex/source/time-registration/src/qi_flow/ui/settings_page.py:750) |
| A11 | P2 | Finish-and-close exits even when finishing fails | [exit_dialog.py](/C:/Users/Alex/source/time-registration/src/qi_flow/ui/exit_dialog.py:56) |
| A12 | P2 | Day-detail queries assume all Copenhagen days last 24 hours | [time_tracking.py](/C:/Users/Alex/source/time-registration/src/qi_flow/application/time_tracking.py:458) |
| A13 | P2 | Detailed exports include time outside the selected range | [csv_export.py](/C:/Users/Alex/source/time-registration/src/qi_flow/infrastructure/csv_export.py:82) |
| A14 | P2 | Long-running tray sessions do not receive daily backups | [bootstrap.py](/C:/Users/Alex/source/time-registration/src/qi_flow/bootstrap.py:135) |
| A15 | P2 | Later lunches in the same work session lose their reminder | [time_tracking.py](/C:/Users/Alex/source/time-registration/src/qi_flow/application/time_tracking.py:649) |
| A16 | P2 | Manual deductions use a hidden date unrelated to the selected parent | [manual_entry_dialog.py](/C:/Users/Alex/source/time-registration/src/qi_flow/ui/manual_entry_dialog.py:105) |
| A17 | P2 | The no-tray fallback hides the window without allowing exit | [main_window.py](/C:/Users/Alex/source/time-registration/src/qi_flow/ui/main_window.py:247) |
| A18 | P2 | Differing external hours default to replacement without a row choice | [testhuset_dialog.py](/C:/Users/Alex/source/time-registration/src/qi_flow/ui/testhuset_dialog.py:145) |

**A01 — Remote replacement can erase shared records**

The Sheets adapter clears `QI_FLOW_SYNC_V1!A:Z` and updates it in a second request. If the second request fails, the shared tab remains empty. A fake API probe that succeeded at clear and raised during update produced `remote-after-update-failure: []`.

The service also reads the remote collection once and replaces the whole collection later (`google_sync_service.py:101–109`). If another machine adds a record between those calls, that new record disappears. A controlled gateway added 2 October after the read; the completed sync left only the 1 October record.

Replace this protocol with a recoverable publication strategy and concurrency handling. A single clear/write batch can address interruption between requests, but **does not by itself resolve simultaneous writers**. Use a defined revision/base protocol, preserve conflicting versions, and verify the published generation before reporting success. Add interrupted-write and interleaved-two-client tests. This affects D101 and US29.

**A02 — Revision counts do not establish ancestry**

`_merge` reports a conflict only when revision numbers are equal. Two machines independently editing revision 1 can produce revisions 2 and 3; the revision-3 record silently wins, even when revision 2 contains an independent change. The controlled merge selected task `33-44` and discarded task `11-22` without a conflict.

There is a second bypass at lines 152–155: any payload without `source` is treated as an old-format row. Current `day_details` payloads deliberately contain no `source`, so different notes/location values at the same revision also silently select the local record. The probe replaced a remote office/note with the local remote/note.

Track the last shared base/version or equivalent causal information. Identify legacy payloads by explicit schema and record kind, not absence of a work-session field. Preserve both independent edits for resolution. Add tests for unequal-revision concurrent edits and same-revision day details. Explicit resolution is known unfinished work, but silent selection is unsafe implemented behavior.

**A03 — Completed deductions can be published without their parent**

`_local_records` separately filters sessions and deductions by their own end timestamp. During an active work session, ending lunch publishes the deduction while excluding its parent. The source-machine probe exported only `['deduction']`; a fresh machine then failed with `IntegrityError: FOREIGN KEY constraint failed`.

Publish deductions only when their parent is eligible for sync, and validate references before any local import. Add the active-work → completed-lunch → fresh-machine-sync regression. This directly conflicts with US29's local-active-timer boundary.

**A04 — Imported records bypass application invariants**

`_apply_remote_records` writes entities directly to repositories. Entity constructors validate individual timestamp fields, but do not enforce aggregate overlap or deduction containment. Two machines can independently create overlapping sessions with different IDs; sync accepts both.

A real SQLite probe imported a second ID for the same one-hour span, producing two completed sessions and **7,200 net seconds**. Ordinary manual-entry use cases reject this overlap.

Validate the merged aggregate before committing it: work overlaps, deduction overlap/containment, eligible parents, future times, and effective-boundary consistency. Surface reconciliation choices rather than importing an invalid timesheet. Reuse owning-layer validation instead of duplicating weaker rules in sync. This affects D026 and US29.

**A05 — Third-party OAuth request logging defeats the privacy boundary**

QI Flow installs an INFO handler on the root logger. The installed `google_auth_oauthlib.flow._WSGIRequestHandler.log_message` forwards the complete callback request line to its INFO logger. That line contains OAuth `code` and `state` query parameters. The OAuth adapter invokes this server at `google_oauth.py:74`.

A synthetic callback passed through the actual dependency handler and QI Flow's logging setup wrote both the synthetic authorization code and state into `qi-flow.log`. No real credentials were involved. The behavior is also visible in the [Google OAuth library source](https://googleapis.dev/python/google-auth-oauthlib/latest/_modules/google_auth_oauthlib/flow.html).

Limit diagnostics to explicitly approved application events and suppress/redact callback request logging. Add a privacy regression that invokes the real request handler and asserts callback codes, states, tokens, and URLs are absent. Sanitizing only application exception messages is insufficient. This violates the credential/logging rules and D100.

**A06 — History restoration bypasses validation for open before-images**

Open session restoration checks only whether another session is active; it skips completed-session overlap checks. Open deduction restoration checks only for another deduction under that parent; it does not require the parent to remain active or validate the open deduction's span.

Two normal recovery/history sequences were reproduced against SQLite:

1. Delete a running session with lunch, restore the work session, finish it, then restore the lunch before-image. The saved lunch becomes open under a completed parent. Starting lunch in a later work session fails with the global `one_active_deduction` unique constraint. The orphan lunch is also omitted from the completed-deduction editor.
2. Delete an open 08:00 session, add a completed 09:00–10:00 session, and restore the original active session at noon. Restore accepts the overlap and the day totals become **5 hours across a 4-hour span**.

Run the same aggregate validation for restoration as for editing. Reject an open deduction under a completed parent and require explicit recovery when restoring an active span that intersects saved work. Add both sequences to integration tests. This affects D026–D027.

**A07 — Updater rollback does not belong to the attempted update**

`apply_update` refuses to run when `QI Flow.previous` already exists. `main` nevertheless treats any existing folder with that name as its own rollback copy after catching the error, replaces the current installation, and deletes the failed/current folder.

In a temporary installation, the current executable contained `current-good` and the existing recovery executable contained `older-release`. The updater reported “A previous update recovery folder already exists,” returned failure, but replaced the current executable with `older-release` and consumed the recovery folder.

Track whether this attempt actually performed a swap and identify its recovery directory explicitly. Validation/preflight failures must not enter the post-swap rollback path. Test `main`, not just `apply_update`, including stale recovery directories and cleanup failures. This affects D005 and US32.

**A08 — The Windows socket is not an exclusive process lock**

Both launches can complete their failed probe before either calls `listen`. The code assumes the second listen then fails. On Windows, Qt permits two servers on the same named pipe; `removeServer` does nothing there. [Qt's QLocalServer documentation](https://doc.qt.io/qtforpython-6/PySide6/QtNetwork/QLocalServer.html#PySide6.QtNetwork.QLocalServer.listen) explicitly describes this behavior.

A controlled probe-race simulation using the actual Windows Qt servers returned `first_acquired=True` and `second_acquired=True`. Both application processes can consequently open the same live database.

Acquire a genuine exclusive per-data-directory lock before initializing the database; use the socket for focus notification. Add a simultaneous-process startup test, rather than only testing a second launch after the first already listens. This affects D036.

**A09 — DSB row identity ignores the allocation**

`_entry_row` locates a date heading and chooses the first following entry row. It never matches the requested task/allocation. `read` therefore reads the wrong allocation; `write_verified` can change that row's allocation and hours.

A local Edge fixture contained Allocation A = 4.00 and Allocation B = 2.00 on the same date. Reading B returned **4.00** and selected row `allocation-a`. All fixture network requests were aborted.

Match the complete date plus allocation identity and require a unique match. Handle an absent allocation row explicitly without repurposing another saved allocation. Add multiple-allocation read/write and missing-row tests. This is separate from unfinished DSB branch allowlisting in US31.

**A10 — OAuth authorization is an unbounded main-thread operation**

Settings calls `GoogleOAuthStore.authorize` synchronously. The adapter calls `run_local_server(port=0, open_browser=True)` without a timeout. The installed dependency's default is `timeout_seconds=None`. Closing the authorization browser without completing the callback leaves the UI waiting, preventing timer controls and normal exit.

Use a managed worker with timeout/cancellation and cleanup, and return a clear cancelled/expired result. Verify UI responsiveness when the browser is closed or never sends a callback. This was established by the call path and installed dependency signature; no live authorization was attempted.

**A11 — Finish-and-close treats a failed finish as success**

`ExitCoordinator` suppresses `DomainError` and emits `exit_confirmed` regardless. A pending sleep decision causes `finish_work` to fail, yet the application exits while the timer is still running. The Qt/SQLite probe emitted `[True]` for exit and confirmed `still_active=True`.

Keep the application open and show the failure/recovery path when finishing fails. Emit exit only after successful persistence or an explicit keep-running choice. Add the pending-sleep and invalid-finish cases to exit interaction tests. This affects D031–D032 and immediate-persistence guarantees.

**A12 — DST-day details use a fixed UTC duration**

`completed_sessions_for_day` converts local midnight to UTC and adds 24 hours. The Copenhagen calendar day on 25 October 2026 lasts 25 hours. A 23:15–23:45 session that day was present in the daily summary (**1,800 seconds**) but absent from the details query (**0 sessions**). The spring transition similarly extends the query into the next calendar day.

Convert the next **local** date's midnight separately to UTC, as the summary code already does. Add tests for the first and final hours of both DST transition days. This affects D015.

**A13 — Detailed export does not respect calendar-range boundaries**

The exporter selects intersecting sessions, then outputs each entire session and all its deductions without clipping or splitting. An October export of 30 September 23:00–1 October 01:00 produced a row dated **30 September with 7,200 seconds**; October's summary correctly contained **3,600 seconds**. Deductions outside the export range can also be included.

Clip all work/deduction spans to the requested range and split them at Copenhagen midnight, or explicitly represent full dates and a clearly defined alternative export contract. Under the current selected-period contract, summary and detailed totals must reconcile. Add midnight, week/month-boundary, and out-of-range-deduction tests. This affects D063–D064.

**A14 — Daily backups stop while the process remains open**

The only production callers of `ensure_daily_backup` are bootstrap startup and the manual Back up now action (`settings_page.py:889`). There is no day-change scheduler or recurring retry. A tray process kept open over multiple days consequently retains its startup-day backup while later work accumulates. A failed automatic backup likewise has no automatic retry while open.

Schedule the check at local day changes and bounded retry intervals; keep backup work from blocking timer interactions. Test advancing an injected clock across a day boundary without restarting. Also reset or re-evaluate the daily-success marker when the backup folder changes, so a healthy old-folder marker does not leave the newly selected folder empty. This affects D060–D061.

**A15 — Lunch reminders are suppressed by work-session identity**

Notification and snooze state are keyed to the parent work session for both work and lunch reminders. After a first lunch reminder, a later lunch within the same continuous work session stays suppressed. A two-lunch probe produced first reminders `['lunch']`, then `[]` after the second lunch exceeded 45 minutes.

Key lunch reminder state to deduction identity and clear/expire it appropriately when that lunch ends. Preserve work reminder state on the parent session. Add two-lunch and snooze-transition tests. This affects D012 and D050–D051.

**A16 — Manual lunch uses the hidden form date**

Changing the type to Lunch or Break hides the date control and displays a parent-session selector. Selecting a parent does not update the hidden date. `_save` still combines both times with that hidden value.

Opening Add entry for 2 October, selecting a completed 1 October work session, and entering 12:00–12:30 yielded “Lunches and breaks must stay inside their work session.” The hidden date remained 2 October.

Keep the date visible/editable for deductions, or derive the date from the selected parent with explicit support for overnight parents. Add a cross-date parent-selection interaction test.

**A17 — No-tray fallback leaves no exit route**

Bootstrap enables `quitOnLastWindowClosed` when the tray is unavailable, but `MainWindow.closeEvent` always ignores the close and hides the window. A real Qt probe returned `window.close() == False` and `visible == False`. Since the close was rejected, the fallback does not execute the intended last-window lifecycle and there is no tray Close app action.

Make close behavior depend on actual tray availability and route non-tray closing through the exit coordinator. Test rejected/cancelled exit, successful exit, and active timers under the no-tray branch.

**A18 — External differences are preselected for replacement**

Every differing preview row receives a combo with Replace first and Keep second; Fill becomes enabled immediately. The current interaction test explicitly asserts the default is `True` (`test_testhuset_ui.py:369–371`). Thus one Fill click accepts replacement of all differences without making each row's required choice.

This is a requirements mismatch with D097's explicit per-slot keep-or-replace decision, rather than an unconfirmed write: final confirmation still exists. Add an unselected choice and disable Fill until each differing row has a decision, or explicitly revise the product decision if the desired policy is default replacement. Update the interaction test to reflect the approved policy.

**Code-quality and best-practice improvements**

1. **Make invariants reusable across all entry paths.** Restore, sync, editing, and recovery currently have different validation strengths. Introduce a small shared application/domain policy for a session plus its deductions and invoke it inside each transactional mutation. Favor this over copying validation into another adapter.
2. **Move runtime construction back to bootstrap.** `settings_page.py:785–786` constructs `GoogleSyncService` and `GoogleSheetsSync` using the private `self._service._uow_factory`. Inject a sync operation through an application port instead. UI code should issue commands and display results, not discover persistence internals or instantiate adapters.
3. **Split oversized modules by responsibility.** `time_tracking.py` is 1,334 lines and Settings is 1,118. Separate history/recovery, reminders, calendar queries, and settings-category widgets without fragmenting small domain rules into unnecessary classes. Keep an approachable facade for existing callers.
4. **Manage worker lifetime and failures explicitly.** Give authorization, sync, update, and browser operations consistent cancellation/shutdown ownership. Expose structured failures such as offline, conflict, invalid records, cancelled, and uncertain external save. The current sync worker reduces every exception to a generic retry message, obscuring conflicts that retry cannot resolve.
5. **Extend architecture enforcement.** The current tests protect domain/application imports but do not enforce the documented UI/composition and infrastructure-to-UI boundaries. Add recursive checks for those boundaries and verify pure business tests run without importing Qt. Prefer maintained protocols over the incomplete timer-only UI service contract.
6. **Improve test coverage at failure boundaries.** Add the regressions above. In particular, test updater `main`, interleaved sync clients, history restoration of open snapshots, aggregate validation on import, real callback logging, and long-lived processes across day changes. Avoid spending the next testing effort on more incidental widget text/layout assertions.
7. **Make release builds reproducible and routine checks automatic.** Dependencies and PyInstaller are installed from broad ranges with no committed lock/constraints file. Record a tested dependency set for builds and add a push/PR quality-gate workflow; the current workflow runs only for manual prerelease dispatch. Include relevant scripts in lint/type checks and validate installer behavior through executable smoke tests instead of only script-text assertions.
8. **Reduce redundant synchronous database work after profiling.** Today refreshes every second and recomputes both today's and weekly summaries; backup Settings refresh integrity-checks the backup list. Separate per-second elapsed-display updates from slower historical queries and perform heavier backup inspection outside interaction handlers. Measure before introducing caching, and invalidate results after changes.
9. **Refresh operational documentation.** README still describes Google/SAP as deferred and several implemented areas as shells. Requirements contain many old “Not started” statuses. Use the active backlog as the authoritative status and remove stale setup/feature descriptions. Keep previously deferred and later-authorized scope clear.

**Known gaps and previously documented limitations**

- US28–US29 remain in progress: explicit conflict-resolution UI, automatic sync scheduling, and complete sync status/record coverage are not finished. The findings above address unsafe existing behavior, independently of those remaining features.
- US31 is explicitly not started. DSB totals currently lack the approved Testhuset-branch allowlist and excluded/unresolved-session review. Completing this is necessary before treating DSB totals as branch-filtered.
- US21, US30, and US32 retain clean-account install/uninstall, live DSB, and packaged update/rollback smoke checks. Automated tests do not close those acceptance items.
- Cross-midnight time-only correction and switching rows with unsaved interval edits are already acknowledged at [USER_STORIES_ARCHIVE.md:698](/C:/Users/Alex/source/time-registration/USER_STORIES_ARCHIVE.md:698). The editor builds both endpoints from one date and omits intersecting sessions that began on another date. Preserve endpoint dates and prompt before discarding row edits in a follow-up; these are existing limitations, not new audit discoveries.
- The suspected QMessageBox identity-comparison failure was **not reproduced** with the installed PySide6: an actual Yes click returned the same enum object. Prefer equality for clarity/compatibility, but it is not included as a confirmed defect.

**Suggested repair order**

1. Protect secrets and data first: A05 logging; A01–A04 synchronization publication, conflicts, references, and aggregate validation; A06 restoration.
2. Repair application/update identity and external targeting: A07 updater rollback, A08 exclusive instance lock, A09 DSB allocation targeting. Complete US31 before branch-filtered DSB registration is relied on.
3. Repair desktop failure handling: A10 authorization, A11 finish-and-close, A17 no-tray exit, A18 review choices.
4. Correct calendar/export behavior and recurring resilience: A12–A16, then the known overnight editor limitations.
5. Refactor the affected seams as fixes require them, add the missing failure regressions and CI coverage, and perform the remaining packaged/live release checks.

Application source and tests were left unchanged. This report is the only added repository artifact.
