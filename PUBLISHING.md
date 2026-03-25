# Publishing Guide

AgentLens has automated publishing pipelines for both the Python SDK (PyPI) and the Node.js backend (npm).

## Python SDK → PyPI

### Setup (one-time)

1. **Create a PyPI account** at https://pypi.org/account/register/ (if you don't have one)
2. **Configure Trusted Publisher** on PyPI (no API tokens needed):
   - Go to https://pypi.org/manage/account/publishing/
   - Add a new pending publisher:
     - **PyPI project name:** `agentlens`
     - **Owner:** `sauravbhattacharya001`
     - **Repository:** `agentlens`
     - **Workflow name:** `publish-pypi.yml`
     - **Environment name:** `pypi`
3. **(Optional) TestPyPI**: Repeat at https://test.pypi.org/manage/account/publishing/ with environment `testpypi`
4. **Create GitHub environments**:
   - Go to repo Settings → Environments
   - Create `pypi` environment (optionally add required reviewers for safety)
   - Create `testpypi` environment

### Publishing

- **Automatic:** Creating a GitHub Release triggers PyPI publishing
- **Manual (TestPyPI):** Go to Actions → "Publish Python SDK to PyPI" → Run workflow → select `testpypi`
- **Manual (PyPI):** Go to Actions → "Publish Python SDK to PyPI" → Run workflow → select `pypi`

### Version Bumps

Update the version in two places before releasing:
- `sdk/pyproject.toml` → `version = "X.Y.Z"`
- `sdk/agentlens/__init__.py` → `__version__ = "X.Y.Z"`

## Node.js Backend → npm

### Setup (one-time)

1. **Create an npm account** at https://www.npmjs.com/signup (if you don't have one)
2. **Generate an access token:**
   - Go to https://www.npmjs.com/settings/~/tokens
   - Create a new **Automation** token (for CI/CD)
3. **Add the token to GitHub Secrets:**
   - Go to repo Settings → Secrets and variables → Actions
   - Add a new secret: `NPM_TOKEN` = your npm automation token
4. **Create GitHub environment:**
   - Go to repo Settings → Environments
   - Create `npm` environment (optionally add required reviewers)

### Publishing

- **Automatic:** Creating a GitHub Release triggers npm publishing
- **Manual (dry-run):** Go to Actions → "Publish Backend to npm" → Run workflow → select `dry-run`
- **Manual (npm):** Go to Actions → "Publish Backend to npm" → Run workflow → select `npm`

### Version Bumps

Update the version in `backend/package.json` before releasing:
```bash
cd backend && npm version patch  # or minor/major
```

## Automated Releases with Release Please

This repo uses [Release Please](https://github.com/googleapis/release-please) to automate versioning and releases.

### How it works

1. **Use conventional commits** on `main`:
   - `feat: ...` → minor version bump
   - `fix: ...` → patch version bump
   - `feat!: ...` or `BREAKING CHANGE:` → major version bump
2. **Release Please** automatically opens a "Release PR" that:
   - Bumps versions in `sdk/pyproject.toml`, `sdk/agentlens/__init__.py`, and `backend/package.json`
   - Updates `CHANGELOG.md` from commit messages
3. **Merge the Release PR** → a GitHub Release is created automatically
4. The release triggers both `publish-pypi.yml` and `publish-npm.yml`

### Manual Release Checklist (if not using Release Please)

1. Update version numbers (see above)
2. Update `CHANGELOG.md` with new entries
3. Commit: `git commit -am "chore: bump version to vX.Y.Z"`
4. Create a GitHub Release with tag `vX.Y.Z`
5. Both publish workflows trigger automatically
6. Verify on [PyPI](https://pypi.org/project/agentlens/) and [npm](https://www.npmjs.com/package/agentlens-backend)
