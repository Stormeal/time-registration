# Manual Windows Prerelease Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manually triggered GitHub Actions workflow that creates the Windows installer and updater package, then publishes both in a versioned prerelease.

**Architecture:** A small standard-library Python utility resolves and validates release versions and stamps the build checkout. A manually dispatched two-job workflow runs checks and packaging on Windows, transfers the built assets, then publishes the prerelease from a separate job with `contents: write`. README instructions explain the optional version input and manual promotion to stable/latest.

**Tech Stack:** Python 3.12, GitHub Actions, PowerShell, PyInstaller, Inno Setup 6, GitHub CLI, pytest.

**Spec:** `docs/superpowers/specs/2026-09-30-manual-prerelease-workflow-design.md`

## Global Constraints

- Start a release only when a maintainer explicitly runs the workflow.
- Allow a maintainer to enter a version or leave it blank for the default next patch version.
- Build and package with the repository's existing Windows installer script.
- Publish a prerelease with both user-downloadable installer and in-app updater assets.
- Keep the source branch unchanged when stamping the release version.
- Preserve the existing updater policy: prereleases are ignored until manually promoted to stable.
- The workflow has no push, tag, pull-request, or schedule trigger.
- It is intended to run from `main` only; a guard rejects a run dispatched from another branch.
- Versions use the project's existing three-part numeric format: `MAJOR.MINOR.PATCH`, without a leading `v` in the input. The release tag is `v<version>`.
- Use a Windows GitHub-hosted runner with Python 3.12.
- Pin GitHub Actions to reviewed full commit SHAs, not floating major-version tags.
- Do not add repository secrets; the release uses the scoped `GITHUB_TOKEN`.
- Set explicit workflow and job permissions, with release write access limited to publication.
- Do not publish an actual GitHub release as part of implementation verification.

## Review Focus

- A hand-entered version lower than the current package version or latest numeric tag must fail before build steps; Task 1 tests both comparisons.
- A blank input must advance patch from the greater of the checked-out project version and existing numeric tags; Task 1 tests tags ahead, project ahead, and no tags.
- A malformed or duplicate version must fail before modifying either version file; Task 1 tests the resolver and stamp function.
- A run dispatched from another branch must fail before stamping/building; Task 2 puts the branch guard first and statically validates it.
- A quality-gate, packaged smoke-check, missing/empty asset, or artifact-transfer failure must prevent release creation; Task 2 separates the publish job behind a successful build job and validates both artifact paths.

---

## Files and responsibilities

- Create `scripts/release_version.py`: standard-library version parser, resolver, version stamping, and a small CLI for workflow use.
- Create `tests/unit/test_release_version.py`: resolver, validation, and safe stamping tests without PySide6.
- Create `.github/workflows/prerelease.yml`: manual trigger, serialized two-job release pipeline, narrow permissions, and prerelease publication.
- Modify `README.md`: concise maintainer instructions for manual dispatch, version input/default, assets, and promotion.

### Task 1: Implement and test release version resolution/stamping

**Files:**
- Create: `scripts/release_version.py`
- Test: `tests/unit/test_release_version.py`

**Interfaces:**
- `resolve_release_version(requested: str | None, project_version: str, tags: Iterable[str]) -> str`
- `stamp_release_version(project_root: Path, version: str) -> None`
- CLI commands: `resolve --requested <value>` reads the checked-out `pyproject.toml` and Git tags and prints the selected version; `stamp --version <value>` updates only the `pyproject.toml` project version and `src/qi_flow/__init__.py` runtime version.
- Invalid input or I/O mismatch raises a concise `ValueError` for the CLI to print to stderr and exit nonzero.

- [x] **Step 1: Write failing resolver tests**

Test `resolve_release_version` for:
- Explicit valid version above both project and tag versions returns the normalized numeric version.
- Empty input increments patch from the greatest of project version and matching tags.
- No numeric tags falls back to the project version, then increments patch.
- Project version ahead of all tags becomes the default base.
- Tags with nonmatching formats are ignored.
- Malformed explicit input, leading `v`, duplicate/non-increasing input, and explicit input at or below project version raise `ValueError`.

- [x] **Step 2: Run resolver tests and confirm they fail because the module/API is missing**

Run: `python -m pytest tests/unit/test_release_version.py -q`  
Expected: collection failure for missing `scripts.release_version` import.

- [x] **Step 3: Implement parsing and resolution in `scripts/release_version.py`**

Use a strict regex for three numeric components. Compare integer tuples. Match only tags exactly shaped like `vMAJOR.MINOR.PATCH`. For defaults, use `max(project_version, matching_tags)` and increment the patch number. For explicit versions, require the value to exceed both the project version and every matching tag.

- [x] **Step 4: Write failing stamping tests**

Test `stamp_release_version` updates only the top-level project version in `pyproject.toml` and `__version__` in `src/qi_flow/__init__.py`, and rejects malformed versions or files with unexpected/missing version declarations without partially modifying either file.

- [x] **Step 5: Run stamping tests and confirm they fail before implementation**

Run: `python -m pytest tests/unit/test_release_version.py -q`  
Expected: stamping test failures while resolver tests pass.

- [x] **Step 6: Implement atomic, validated version stamping and the CLI**

Validate the requested version before file edits. Read both files and confirm exactly one intended declaration in each. Prepare both updated contents before writing; use temporary files and replace them only after both preparations succeed. The resolve CLI obtains `project_version` from `pyproject.toml` and tags using `git tag --list` in the project root.

- [x] **Step 7: Run focused tests and project checks**

Run: `python -m pytest tests/unit/test_release_version.py -q`  
Expected: all version utility tests pass. Also run `python -m ruff check scripts/release_version.py tests/unit/test_release_version.py` and `python -m mypy scripts/release_version.py`.

- [x] **Step 8: Commit the version utility and its tests**

### Task 2: Add the manually dispatched Windows build and prerelease workflow

**Files:**
- Create: `.github/workflows/prerelease.yml`
- Consumes: `scripts/release_version.py` CLI from Task 1

- [x] **Step 1: Implement the workflow trigger and build job**

Declare `workflow_dispatch` with an optional string `version` input. Serialize runs with a fixed concurrency group and `cancel-in-progress: false`. The Windows build job must:

1. Reject `github.ref != refs/heads/main` before version stamping or build setup.
2. Check out the dispatched commit with full history/tags and persisted credentials disabled.
3. Set up Python 3.12 using an official action pinned to a verified full commit SHA.
4. Resolve the requested/default version through `scripts/release_version.py`, stamp the build checkout, and publish the version as a job output.
5. Install project dev dependencies into `.venv`, install PyInstaller, and install Inno Setup 6.
6. Run `scripts/check.ps1`, then `scripts/build-installer.ps1 -Version <version>`.
7. Fail if `dist/installer/QI-Flow-Setup-<version>.exe` or `dist/QI-Flow-Update.zip` is missing or empty.
8. Copy only those two files into a clean `release-assets/` directory at its root, then upload that directory using the official artifact action pinned to a verified full commit SHA. Do not upload the full `dist/` tree.

- [x] **Step 2: Add the publish job with limited write permissions**

Run a separate publish job only after the build succeeds. Give it `contents: write` and no other repository write permissions. Use the official artifact download action pinned to a verified full commit SHA and download to `release-assets/`. With `GITHUB_TOKEN` exposed as `GH_TOKEN`, use the runner's GitHub CLI to create `v<version>` on the dispatched commit, title it `QI Flow v<version>`, enable prerelease, generate release notes, and attach `release-assets/QI-Flow-Setup-<version>.exe` and `release-assets/QI-Flow-Update.zip`. Do not use shell interpolation of the workflow input; pass the validated job output through environment variables/arguments.

- [x] **Step 3: Validate workflow syntax, policy, and helper integration**

Run: `actionlint .github/workflows/prerelease.yml`  
Expected: exit code 0. Confirm action references are full 40-character SHAs for verified official actions, the workflow permissions are explicit, there are no automatic triggers, and a failed build cannot reach publication.

Run: `python scripts/release_version.py resolve --requested ""`  
Expected: print a valid next patch version without changing the checkout. In a temporary copy, run resolve/stamp with an explicit version and verify both declarations match and `git diff` contains only the two temporary version-file edits.

- [x] **Step 4: Commit the workflow**

### Task 3: Document release operations and complete verification

**Files:**
- Modify: `README.md`
- Uses: `.github/workflows/prerelease.yml` and `scripts/release_version.py` from Tasks 1–2

- [ ] **Step 1: Add maintainer instructions to README**

Document that the workflow becomes manually runnable after merged to the default branch; dispatch it from `main` in Actions → Manual Windows Prerelease. Explain blank input default, how to supply `MAJOR.MINOR.PATCH`, the prerelease tag/title, both asset names, no repository version commit, and how to edit a reviewed release to stable/latest so the app updater can see it.

- [ ] **Step 2: Run the complete project quality gate**

Run from PowerShell: `./scripts/check.ps1`  
Expected: Ruff formatting/lint, strict mypy, and the complete pytest suite pass.

- [ ] **Step 3: Run focused workflow/version verification**

Run: `python -m pytest tests/unit/test_release_version.py -q` and `actionlint .github/workflows/prerelease.yml`  
Expected: all version cases pass and workflow lint exits 0. Confirm the README asset names match `scripts/build-installer.ps1`, `scripts/build-update-package.py`, and the updater's expected release asset name.

- [ ] **Step 4: Review final diff and limitations**

Confirm no `GITHUB_TOKEN` is persisted, no source version files are changed in the repository, no release/tag was created during verification, and only the intended workflow, helper/tests, and README documentation changed for this feature. Record that a real hosted Windows prerelease run and manual stable promotion remain maintainer release operations.

- [ ] **Step 5: Commit README and final task documentation**

## Execution

Execution approval: user approved the plan and chose Native on 2026-09-30.
