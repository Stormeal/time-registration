# Manual Windows Prerelease Workflow

Date: 2026-09-30  
Status: Approved design  
Repository: `Stormeal/time-registration`

## Purpose

Provide a repeatable, manually triggered GitHub Actions workflow that builds the Windows application, installer, and in-app updater package, then publishes them as a versioned GitHub prerelease. The owner reviews that prerelease and decides whether to promote it to the latest stable release.

## Goals

- Start a release only when a maintainer explicitly runs the workflow.
- Allow a maintainer to enter a version or leave it blank for the default next patch version.
- Build and package with the repository's existing Windows installer script.
- Publish a prerelease with both user-downloadable installer and updater assets.
- Keep the source branch unchanged when stamping the release version.
- Preserve the existing updater policy: prereleases are ignored until manually promoted to stable.

## Non-goals

- Automatically running on pushes, tags, pull requests, or schedules.
- Committing or pushing a version bump to the source branch.
- Automatically promoting a prerelease to stable/latest.
- Publishing a stable release directly from the workflow.
- Changing QI Flow's existing in-app update behavior.
- Running an authenticated production integration or clean-account installer test as part of this release flow.

## Workflow behavior

### Trigger and source

Add one workflow under `.github/workflows/` using `workflow_dispatch`, with one optional string input named `version`. It is intended to run from `main` only; a guard rejects a run dispatched from another branch. GitHub exposes the manual run after the workflow is merged into the repository's default branch.

The workflow has no push, tag, pull-request, or schedule trigger. Runs are serialized so two release attempts do not race while selecting or publishing the same version.

### Version selection and validation

Versions use the project's existing three-part numeric format: `MAJOR.MINOR.PATCH`, without a leading `v` in the input. The release tag is `v<version>`.

- Read the current version from `pyproject.toml` and find the greatest existing numeric tag matching
  either the historical `MAJOR.MINOR.PATCH` form or the new `vMAJOR.MINOR.PATCH` form.
- If `version` is supplied, validate its exact format and require it to be greater than both the current project version and the greatest matching existing version tag.
- If it is blank, use the greater of the project version and the greatest matching tag as the base, then increment its patch component. When no matching tags exist, this naturally uses the project version as the base.
- Reject an already-existing tag before starting the build.

The chosen version is stamped into the runner's build copy of `pyproject.toml` and `src/qi_flow/__init__.py` before installing the project. Existing packaging checks continue to enforce that both declarations agree. These edits are not committed or pushed.

### Build and checks

Use a Windows GitHub-hosted runner with Python 3.12. The build job checks out the selected `main` commit with tags, prepares the virtual environment expected by the existing packaging script, installs project development dependencies and PyInstaller, installs Inno Setup 6, stamps the release version, and runs `scripts/check.ps1`.

After the quality gate passes, run `scripts/build-installer.ps1 -Version <version>`. That script already builds the app bundle, includes the updater helper, smoke-checks the packaged application, creates the updater ZIP, and invokes Inno Setup for the per-user installer. The release must contain:

- `dist/installer/QI-Flow-Setup-<version>.exe`
- `dist/QI-Flow-Update.zip`

Fail the build if either expected asset is absent or empty. Upload the two files as a workflow artifact for the publish job.

### Prerelease publication

Use a separate publish job after successful build and checks. Grant the build job read-only repository contents access; grant only the publish job `contents: write`. The publish job downloads the workflow artifact and uses the built-in GitHub CLI with `GITHUB_TOKEN` to create a release on the checked-out commit:

- Tag: `v<version>`
- Title: `QI Flow v<version>`
- Prerelease: enabled
- Release notes: GitHub-generated notes
- Assets: installer executable and `QI-Flow-Update.zip`

A failed build or quality gate must never create a tag or release. The existing `ReleaseClient` consumes GitHub's SHA-256 digest metadata for `QI-Flow-Update.zip`; verify the uploaded updater asset name remains exactly compatible.

### Manual promotion

The prerelease is a review artifact. After testing, the maintainer can edit that GitHub release, clear its prerelease status, and select or confirm that it is the latest release. This is a manual action and does not trigger a second build. Once it is stable, the existing QI Flow updater can discover it through the stable release endpoint.

### Documentation

Update the README with the manual trigger location, optional version input, default patch behavior, generated asset names, and manual promotion step. Explain that the workflow does not commit version changes and that prereleases are ignored by the in-app updater until promoted.

## Security and operational constraints

- Pin GitHub Actions to reviewed full commit SHAs, not floating major-version tags.
- Do not add repository secrets; the release uses the scoped `GITHUB_TOKEN`.
- Set explicit workflow and job permissions, with release write access limited to publication.
- Pass the input version through environment variables or action outputs rather than interpolating untrusted text into shell source.
- Reject malformed, duplicate, or non-increasing versions before installing/building.
- The manual trigger requires repository write access, as provided by GitHub's `workflow_dispatch` mechanism.

## Acceptance criteria

1. The workflow is available in GitHub Actions as a manual run and has no automatic event triggers.
2. A run dispatched from a non-`main` branch fails before building.
3. A supplied valid higher version determines both package metadata, installer filename, and release tag.
4. An omitted version increments the greatest existing numeric release tag's patch component; with no numeric tag it increments the project version's patch component.
5. Invalid, duplicate, or non-increasing versions fail before the packaging steps.
6. The branch checkout remains unchanged after version stamping.
7. The Windows job runs the project quality gate and the existing packaging script; a failed check or packaged smoke test prevents publication.
8. A successful run publishes one prerelease containing the installer and updater ZIP under the expected names.
9. A prerelease remains ignored by the in-app updater; manually promoting it to stable/latest makes it eligible through the current updater behavior.
10. README instructions explain how to run the workflow, select a version, download artifacts, and promote a tested prerelease.

## Verification plan

- Unit-test version resolution for explicit versions, patch defaults, no-tag fallback, malformed versions, lower versions, and duplicate tags.
- Test version stamping updates both declarations and rejects unsupported version text without changing unrelated file content.
- Validate workflow YAML and action references; verify branch and permission guards by inspection or a local workflow linter.
- Run the repository's `scripts/check.ps1` in the build job.
- Run the existing installer script on a Windows environment and assert the installer and updater ZIP are present and non-empty.
- Verify the prerelease command includes the correct tag, title, generated release notes, target commit, prerelease flag, and both assets.
- Do not publish an actual GitHub release as part of implementation verification.

## GitHub references

- [Manually running a workflow](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)
- [Managing releases in a repository](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)
- [REST API endpoints for releases](https://docs.github.com/en/rest/releases/releases)
