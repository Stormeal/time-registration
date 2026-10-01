# Epic G Update Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve uninstall support and user data through in-app updates, recover from interrupted replacement, and prevent legacy clients from selecting the new update package.

**Architecture:** Inno owns a stable launcher and uninstall files in the per-user install root. The replaceable app bundle lives in `current`; the launcher also acts as the update helper and owns the transaction lock, journal, trial launch, rollback, and cleanup. A new fixed GitHub asset name separates the new package protocol from the legacy updater.

**Tech Stack:** Python 3.12 standard library, PySide6, PyInstaller, Inno Setup 6, PowerShell, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-01-epic-g-update-recovery-design.md`

## Global Constraints

- Install and update per Windows user without elevation; preserve database, settings, backups, task cache, logs, and active state in AppData.
- Keep user-initiated update checks, explicit apply confirmation, SHA-256 verification, bounded downloads, and no telemetry.
- New releases publish only `QI-Flow-Update-v2.zip`; never publish the legacy `QI-Flow-Update.zip` name again.
- The GitHub workflow remains manual and creates a prerelease; stable promotion remains manual after release smoke checks.
- Domain and application tests remain runnable without PySide6. Run `scripts/check.ps1` before claiming the change verified.

## File map

- `scripts/update_runtime.py`: standard-library install layout, transaction journal, same-volume swap, recovery, lock, and cleanup.
- `scripts/update_helper.py`: legacy helper removed once the stable launcher replaces its build and tests.
- `scripts/qi_flow_launcher.py`: stable outer executable; normal launch and `--apply-update` mode, process wait, trial launch and readiness timeout, user-facing failure.
- `src/qi_flow/bootstrap.py`: write a transaction-specific readiness marker after successful initialization and first event-loop turn.
- `src/qi_flow/ui/settings_page.py`: invoke the stable launcher for an update instead of copying the old helper.
- `src/qi_flow/infrastructure/startup.py`: write the stable launcher path for a frozen installation.
- `installer/QIFlow.iss` and `scripts/build-installer.ps1`: stable launcher, `current` bundle, legacy installer migration, uninstall cleanup.
- `src/qi_flow/infrastructure/updates.py`, `scripts/build-update-package.py`, `.github/workflows/prerelease.yml`, and `README.md`: v2 asset contract and migration instructions.
- Tests live beside the owning layer: `tests/integration/test_update_helper.py`, `tests/integration/test_update_recovery.py`, `tests/integration/test_update_launcher.py`, `tests/ui/test_updates_ui.py`, `tests/unit/test_startup.py`, `tests/unit/test_packaging.py`, and `tests/unit/test_updates.py`.

## Review Focus

1. A process stops after `current` moves but before replacement: ordinary launcher use restores the old bundle; test in Task 1.
2. A healthy update leaves an undeletable previous folder: a later update still works without deleting the live bundle; test in Task 1.
3. A journal contains malformed or escaping paths: recovery refuses to touch paths outside the install root; test in Task 1.
4. A user opens QI Flow during an update: the second launch waits for the update lock, then launches the committed bundle; test in Task 2.
5. A new release migrates SQLite before trial startup fails: the old version can reopen the migrated database, or the release is blocked; test and release gate in Task 5.

---

### Task 1: Transactional bundle replacement and recovery

**Files:**
- Create: `scripts/update_runtime.py`
- Modify: `tests/integration/test_update_helper.py`
- Create: `tests/integration/test_update_recovery.py`

**Interfaces:**
- `apply_update(archive: Path, install_root: Path, expected_sha256: str, trial: Callable[[Path, str], bool]) -> None` verifies, stages, swaps `install_root/current`, runs `trial(executable, transaction_id)`, then commits or restores.
- `recover_install(install_root: Path) -> str` returns `"unchanged"`, `"restored"`, or `"cleanup_pending"`; it validates journal paths and directory state before any rename or deletion.
- `retry_cleanup(install_root: Path) -> None` removes only uniquely named, committed previous bundles and records failures without blocking the live bundle.
- `install_lock(install_root: Path)` is a Windows per-user file lock context manager used by update and normal launch; a terminated process releases it.

- [ ] **Step 1: Write failing tests** for successful swap, bad digest, unsafe archive path, interruption after each rename, incomplete trial launch, stale committed previous folder, malformed journal path, and untouched AppData. Simulate process termination with an injected exception that bypasses the helper's ordinary error handler, then call `recover_install` as a new invocation.
- [ ] **Step 2: Run** `python -m pytest tests/integration/test_update_helper.py tests/integration/test_update_recovery.py -q` and confirm each new case fails for missing behavior.
- [ ] **Step 3: Implement** the interfaces above with atomic journal writes, unique staging/previous directories under the install root, same-volume renames, and idempotent recovery. Verify the archive hash and member paths before the first live rename. Never treat an unknown previous directory as safe to delete.
- [ ] **Step 4: Rerun** those integration tests and confirm success. Commit only Task 1 files.

### Task 2: Stable launcher, readiness, and UI handoff

**Files:**
- Create: `scripts/qi_flow_launcher.py`
- Modify: `src/qi_flow/bootstrap.py`
- Modify: `src/qi_flow/ui/settings_page.py`
- Modify: `tests/ui/test_updates_ui.py`
- Create: `tests/integration/test_update_launcher.py`

**Interfaces:**
- Launcher default mode acquires `install_lock`, calls `recover_install`, retries safe cleanup, and starts `current/QI Flow.exe` with forwarded launch arguments.
- Launcher `--apply-update --pid <int> --archive <path> --sha256 <hex>` waits for the old app, then calls `apply_update`; its trial callback starts the new app with `--update-ready <transaction-id>` and accepts only a matching readiness marker while that process stays alive.
- `bootstrap.run` removes `--update-ready` before constructing `QApplication` and writes the matching marker only after migrations, composition, and the first event-loop turn.

- [ ] **Step 1: Write failing launcher tests** for normal start after an interrupted swap, trial readiness success/failure, concurrent normal launch waiting on the update lock, and an invalid or mismatched readiness token. Write a UI test that the confirmed download invokes the stable launcher in the install root and quits only after successful dispatch.
- [ ] **Step 2: Run** `python -m pytest tests/integration/test_update_launcher.py tests/ui/test_updates_ui.py -q` and confirm the new tests fail for the expected missing behavior.
- [ ] **Step 3: Implement** the launcher, app readiness marker, and Settings handoff. The launcher displays an actionable restore or recovery-installer message on failure. Keep all process and file operations out of the UI thread.
- [ ] **Step 4: Rerun** the focused tests, verify `QI Flow.exe --smoke-check` remains available, and commit Task 2 files.

### Task 3: Installer layout, migration, startup, and uninstall

**Files:**
- Modify: `installer/QIFlow.iss`
- Modify: `scripts/build-installer.ps1`
- Delete: `scripts/update_helper.py` after its build reference and tests have moved to the stable launcher.
- Modify: `src/qi_flow/infrastructure/startup.py`
- Modify: `tests/unit/test_packaging.py`
- Modify: `tests/unit/test_startup.py`

**Interfaces:**
- Installer places `QI Flow Launcher.exe` and Inno uninstall files in `{app}`, and the bundle in `{app}\current`; Start menu, desktop, and postinstall launch use the launcher.
- The frozen startup command points to the outer launcher with `--start-minimized`. Migration rewrites an enabled legacy Run entry only when it points at this install; uninstall removes only that installation's Run entry.
- Inno uninstall deletes the entire app-only `current` and updater-working directories; AppData is excluded.

- [ ] **Step 1: Write failing tests** for launcher placement, shortcut targets, bundle-only cleanup, frozen startup command, migration of a matching legacy Run command, preservation of an unrelated Run command, and uninstall registration cleanup. Use a real installer smoke check for Inno behavior; source-text tests alone do not prove it.
- [ ] **Step 2: Run** `python -m pytest tests/unit/test_packaging.py tests/unit/test_startup.py -q` and confirm the new assertions fail for the existing layout.
- [ ] **Step 3: Update** PyInstaller to build the stable one-file launcher and Inno to install the layout. Preserve the existing AppId and uninstall record during an installer upgrade; remove only known legacy application files after the new launch path is usable.
- [ ] **Step 4: Rerun** focused tests and build the installer with `scripts/build-installer.ps1 -OutputRoot <isolated writable path>`. Verify the generated installer contains launcher and `current` bundle, then commit Task 3 files.

### Task 4: v2 release asset and migration instructions

**Files:**
- Modify: `src/qi_flow/infrastructure/updates.py`
- Modify: `scripts/build-update-package.py`
- Modify: `.github/workflows/prerelease.yml`
- Modify: `README.md`
- Modify: `tests/unit/test_updates.py`
- Modify: `tests/unit/test_packaging.py`
- Modify: `tests/ui/test_updates_ui.py`
- Modify: `USER_STORIES.md` and `ARCHITECTURE.md` only for implemented behavior and remaining release checks.

**Interfaces:**
- `ReleaseClient` accepts only `QI-Flow-Update-v2.zip` from the fixed GitHub repository and continues to verify digest, size, and version.
- The build script emits that exact v2 filename; the workflow validates and publishes the installer plus v2 ZIP and never emits the legacy ZIP.

- [ ] **Step 1: Write failing tests** showing a newer release with only the legacy asset is rejected, a v2 asset is accepted, and the package/workflow output uses only the v2 filename.
- [ ] **Step 2: Run** `python -m pytest tests/unit/test_updates.py tests/unit/test_packaging.py tests/ui/test_updates_ui.py -q` and confirm the new cases fail for the old asset contract.
- [ ] **Step 3: Change** the client, package builder, workflow, and README. Explain the one-time installer migration and the legacy client's missing-package message. Keep manual prerelease creation and manual stable promotion.
- [ ] **Step 4: Rerun** focused tests; inspect the workflow's two published asset paths and the built ZIP name, then commit Task 4 files.

### Task 5: Full verification and release handoff

**Files:**
- Modify: `USER_STORIES.md`, `REQUIREMENTS.md`, and `ARCHITECTURE.md` for actual verification evidence and remaining manual checks.
- Create: `docs/epic-g-release-smoke.md` with exact Windows release steps and expected outcomes.

- [ ] **Step 1: Run** `scripts/check.ps1` from PowerShell and resolve failures introduced by this change. Report any existing environment permission failure separately from a test failure.
- [ ] **Step 2: On a clean standard-user Windows account**, test fresh install, optional startup, in-app update, relaunch, uninstall, retained AppData, installer migration of a legacy installation, interruption between renames, cleanup failure followed by another update, and previous-version database compatibility. Record observed outcomes, not inferred ones.
- [ ] **Step 3: Update** story status and release documentation with verified evidence. Leave Epic G open for any smoke check that cannot be run. Review `git diff --check` and the complete diff, then commit documentation.

## Execution handoff

Implement tasks in order in a managed worktree. Native execution is preferred because the launcher protocol, installer layout, and release asset name share one migration boundary. Do not publish or promote a GitHub release during implementation; that remains a separate release action after the Windows smoke gate.
