# Compact usability follow-up

User authorization: seven concrete changes requested with screenshots, 28/09/2026.

1. Launch at approximately 640×860 logical client size, matching the tall compact screenshot.
2. Hover highlights the complete visible Timesheet row; preserve selection and ISO-week groups.
3. Give session editing a dedicated table pane and scrollable correction/context/task controls,
   meaningful selection guidance, readable columns, and all existing save/delete/history actions.
4. Daily note opens a separate explicit Save/Cancel dialog. Preserve office context, drafts,
   date rollover and discard safeguards; use the same note editor from session corrections.
5. Center the month between Previous/Next, reflowing when narrow rather than clipping controls.
6. Settings fields ignore wheel changes and let the parent page scroll, including when focused;
   keyboard entry, arrows and dropdown choices continue to work.
7. Today offers Start at… when stopped and Change start… when running. Use persisted application
   commands and the injected clock; reject future/overlapping starts, keep exact chosen minutes,
   preserve deductions and session identity during corrections, and retain recovery blocking.

Implement serially in the existing authorized checkout. Add meaningful failing interaction and
application tests, verify them after changes, run scripts/check.ps1, inspect actual Qt previews
in light/dark and narrow/scaled layouts, then obtain one independent read-only review. Record
acceptance and any release limitations. No changes to external integration contracts or schemas.

## Verification outcome

Five reported UI regressions reproduced as failing tests before changes. Start-time application
query tests failed before implementation and now pass; selected past elapsed time, exact manual
minutes, future/overlap rejection and injected Copenhagen clock are covered. Note midnight binding
failed before adding the editing-date guard; wheel forwarding failed before routing events to the
page viewport. Both now pass. Row hover checks inspect per-cell delegate states after real mouse
movement, and native Qt preview confirms full-row appearance. Existing editor/tray/history tests
remain green. Final quality gate: **203 tests pass**, Ruff formatting/lint pass, strict mypy passes
for 56 source files. One independent review found a stale Change-start recovery bypass and an
untouched-editor discard prompt. Both were reproduced by failing tests, fixed in one pass and
included in the passing full gate. Recovery blocking is rechecked at the application boundary;
dirty interval checks compare visible minutes against the loaded controls rather than seconds.
The user subsequently requested commit and push on the existing branch. The author identity from
the repository's latest commit is reused per command without changing global Git settings.

Qt previews cover light/dark, the 640×860 launch size, a narrow 640×520 window and scale factors
1/1.25/1.5/2. Live production data, Windows monitor/keyboard and installer/external integration
smoke checks were not used or claimed. Existing time-only cross-midnight correction and switching
selection with unsaved interval edits remain older editor limitations outside this follow-up.
