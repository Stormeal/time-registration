# Audit remediation verification — 2026-10-04

Implementation and automated verification are complete on `codex/audit-remediation-2026-10-03`.
Real release acceptance remains open. No working account, workplace hours or shared workbook was
modified during these checks. The original source checkout retains its existing state.

## Verified implementation

Source checkpoint: **`c8c5b35`**, following architecture/CI checkpoint `53c6375` and lifecycle
checkpoint `0be77bc`. Documentation reconciliation follows this source checkpoint. App version
remains **0.2.6**; no release was published. SQLite migrations are **0001 through 0007**;
previous migrations were not rewritten. V2 activation requires reviewed migration.

| Check | Result |
| --- | --- |
| PowerShell `scripts/check.ps1` | 672 passed in 83.16 s; 146 formatted files; Ruff clean; strict mypy clean for 76 source modules |
| Same gate in separately installed locked environment | 672 passed in 75.54 s; formatting, lint and types clean |
| `scripts/check-core.ps1` | 318 passed in 19.28 s; Qt, Playwright, Google, keyring and pytest-qt verified absent; plugin autoload disabled |
| Both full environments: `python -m pip check` | No broken requirements |
| Installer + update build in isolated output folder | Successful; bundled executable import smoke check passed; no installation performed |
| Fresh whole-branch review | No Critical findings; Important findings fixed with RED→GREEN regressions and the final full gate |

The dependency environments use Python **3.12.5** and the exact versions in
`requirements/windows-build.txt`; the minimal environment uses `requirements/core-check.txt`.
PR/push Windows CI and the manual prerelease workflow install the locked packages and run both
gates. CI includes intercepted Edge browser fixtures, uses no account credentials and publishes
only when the separate manual prerelease workflow is explicitly invoked.

Final review fixes:

- Valid remote resolution closes superseded conflicts atomically with materialization. A later
  fork, invalid candidate, missing ancestry or active local timer keeps explicit review required.
- Missing or unrepresentable imported endpoint values remain reviewable; a real dialog interaction
  can choose deletion and author a durable tombstone without losing the original observation.
- Import cannot finish an unresolved local lunch. Explicit conflict resolution also requires
  completed live parents and cannot attach a completed child to unrelated active local work.
- A server Retry-After beyond the representable date range cannot crash the scheduler; an explicit
  manual sync or connection reconfiguration remains available.

US44's unchanged endpoint-date, DST, identity/history and dirty-edit acceptance criteria passed
and the story is archived. US28–US32, US45 and US46 retain their outstanding release acceptance.
See the [implementation decisions](../audits/2026-10-04-implementation-decisions.md) for all recorded
rulings and the [runtime profile](../audits/2026-10-04-runtime-profile.md) for measured scan caching.

## Local build artifacts

Built from the source above under `.tmp/release-audit-final`; these are validation artifacts,
not a published or accepted release.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `installer/QI-Flow-Setup-0.2.6.exe` | 83,366,890 | `9E3C755E4953AC8309D7DD516A7DD831662D1BD9807CD65F9FB8415FDFB2B070` |
| `QI-Flow-Update.zip` | 124,041,163 | `994AF935B3E3E09B28A8C2686288E24E1562BA357DDB854EB846A68CD1311055` |

## Required real acceptance

These checks have not been performed and do not pass by inference from automated fixtures.

| Environment | Required check | What is needed |
| --- | --- | --- |
| Clean Windows account | Per-user install/launch/startup, simultaneous launch, Start → Lunch → End lunch → Finish → edit → restart → export; crash/previous-day/sleep/corrupt-database recovery; backup/restore; uninstall/data choices | A disposable Windows account with a separate application-data directory |
| Packaged Windows installation | Verified updater swap/relaunch, preflight refusal, preexisting recovery, interrupted replacement, rollback and uninstall preservation; retain database/settings/backups | Disposable installations and controlled interruption on the clean account |
| Private disposable Google workbook, two isolated clients | Real append interleaving, accepted write/response loss, propagated conflict resolution, all-participant V1 cutover, deletion/backup restore; preserve unrelated tabs/formulas | A scratch workbook and separately authorized clients; never fault-inject into a working sheet |
| Testhuset and DSB | Exact ISO week/year and task/allocation review, explicit differing-row choices, verified only-confirmed hours and DSB Send; no closure/approval/locking | Explicit user authorization for the concrete reviewed values and destination access |

A Testhuset/DSB change after reconciliation can still race the fill because those interfaces offer
no atomic compare-and-set. Exact row identity, immediate reconciliation and verified readback are
implemented safeguards; the destination limitation remains documented. If an acknowledged V1
migration history changes, use a fresh reviewed migration into a new private Sheet while retaining
the old Sheet and verified backups; in-place acknowledgement supersession is unsupported.
