# Testhuset invoiced-slot repair — 2026-10-05

## Problem and cause

The week-40 preview reported "A date/task slot is missing". Read-only inspection of EazyProject
confirmed that invoiced day cells render a `.ws-form-control-number` span in place of an input.
QI Flow found the task row but required an input with a constructed date/task ID for every day.
The previous isolated fixture contained only editable fields, so it missed this case.

## Change and acceptance criteria

This repair belongs to US27, with the original acceptance criteria retained in
`USER_STORIES_ARCHIVE.md`. It preserves selected-week validation, reviewed confirmation,
untouched matching/kept values, verified individual saves, and manual week closure.

- Read numeric locked/invoiced values from the exact task row and weekday in the verified ISO week.
- Require one task row, seven day cells, and one unambiguous read-only value; missing or malformed
  layouts stop rather than guessing a value or another task.
- Permit a mixed week to preview and fill editable days while matching/kept invoiced days stay
  unchanged.
- Reject a replacement of locked/invoiced hours before writing that slot, identifying the date
  and directing the user to Keep or arrange a correction with Testhuset.
- Do not treat the unavailable `-` marker as zero hours.
- Preserve save-response verification and stop-on-failure behavior. Earlier verified saves may
  remain if a later replacement fails; a fresh review is required before retrying.

## Verification

The original adapter reproduced both the missing-slot preview and the missing-input write
timeout against isolated browser fixtures before the fix. Browser regression tests cover
mixed editable/invoiced cells, weekday identity, unavailable cells, malformed layouts, and
preview/reconciliation/fill with real SQLite application services. All browser test requests
are intercepted locally. Live inspection changed no hours or approval state.

The required PowerShell quality gate, `scripts/check.ps1`, passed: formatting, Ruff, mypy, and
all 683 tests on the latest `main`, including explicit Keep/Replace service decisions.
Deployment to the installed app and a live confirmed fill are separate from
this source repair.
