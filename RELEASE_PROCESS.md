# Release Process

How a release of `cal-scheduler-mcp` is cut and published. The mechanics
live entirely in this repo — no external secret or side-channel is needed.

## Overview

A release is a **tag push**. Pushing a tag matching `v*` (e.g. `v1.1.0`)
triggers the `Publish` workflow (`.github/workflows/publish.yml`), which
builds the distribution and uploads it to PyPI via Trusted Publishing (OIDC).
No API token is stored or needed.

Before the tag can be pushed, the version number must be bumped in the
canonical sources and the bump landed on `main` via PR. CI enforces that
all version sources agree on every PR, so a mismatched bump cannot merge.

## Where the version lives

Three files, one truth:

| File | Key |
|---|---|
| `pyproject.toml` | `[project].version` |
| `uv.lock` | `[[package]] cal-scheduler-mcp.version` (auto-generated) |
| `server.json` | `.version` and `.packages[0].version` |

The script `scripts/check_version_alignment.py` asserts they are identical.
It runs in CI on every PR (the `version-alignment` job in `ci.yml`) and can
be invoked locally at any time.

## Making a release

### 1. Branch from `main`

```bash
git checkout main
git pull
git checkout -b release/vX.Y.Z
```

### 2. Bump the version

Edit `pyproject.toml`:
```
version = "X.Y.Z"
```

Edit `server.json` (two locations — `.version` and `.packages[0].version`):
```json
"version": "X.Y.Z",
```

Regenerate the lockfile so `uv.lock` tracks the new version:
```bash
uv lock
```

### 3. Verify alignment

```bash
python scripts/check_version_alignment.py
```

Expected output:
```
OK: all version sources agree on X.Y.Z
```

### 4. Open a PR

Push the branch, open a PR against `main`. CI will run the
version-alignment check automatically. The PR cannot merge unless
it passes — the `ci-gate` required check enforces this.

### 5. Merge to `main`

Once approved and green, merge the PR.

### 6. Tag and push

```bash
git checkout main
git pull
git tag vX.Y.Z
git push origin vX.Y.Z
```

The tag push triggers `Publish`. The workflow:
- Verifies the tag (stripped of `v`) matches `pyproject.toml` version
- Builds sdist + wheel (`uv build`)
- Publishes to PyPI (`pypa/gh-action-pypi-publish`)
- Uploads build artifacts as a workflow run attachment

### 7. Verify publication

Check PyPI: `https://pypi.org/project/cal-scheduler-mcp/X.Y.Z/`

## CI guardrails

| Check | Where | What it prevents |
|---|---|---|
| `version-alignment` | `ci.yml` (every PR) | Merging a version bump where sources disagree |
| Tag-version match | `publish.yml` (tag push) | Publishing a tag whose name doesn't match `pyproject.toml` |
| `skip-existing: true` | `publish.yml` | Accidentally re-publishing an already-uploaded version |

## Version scheme

`MAJOR.MINOR.PATCH` ([SemVer](https://semver.org)).