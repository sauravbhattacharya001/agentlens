"""Regression tests for the packaged project metadata in ``pyproject.toml``.

These guard the bits of packaging that are easy to break silently and only
surface at *publish* time (or, worse, after a release has shipped to PyPI):

* the declared ``version`` drifting from ``agentlens.__version__`` -- the
  ``publish-pypi.yml`` workflow rejects this for ``release`` events, but a
  mismatch should also fail fast in the normal test run;
* ``[project.urls]`` GitHub links pointing at a branch that is not the
  repository's default branch. A stale ``blob/main/CHANGELOG.md`` link (the
  default branch is ``master``) renders an outdated changelog on the PyPI
  project page even though every other URL is correct.

The parsing deliberately avoids adding a runtime/test dependency: it uses the
stdlib ``tomllib`` (Python >= 3.11), falls back to ``tomli`` if that happens to
be installed, and finally to a tiny regex reader so the test still runs on the
3.9/3.10 leg of the CI matrix.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import agentlens

# sdk/tests/test_package_metadata.py -> sdk/pyproject.toml
PYPROJECT_PATH = Path(__file__).resolve().parent.parent / "pyproject.toml"

# The GitHub default branch for this repository. Project URLs that embed a
# branch (``/blob/<branch>/`` or ``/tree/<branch>/``) must use this branch so
# they resolve to current content rather than a stale snapshot.
DEFAULT_BRANCH = "master"

_GITHUB_BRANCH_RE = re.compile(
    r"github\.com/[^/]+/[^/]+/(?:blob|tree|raw)/(?P<branch>[^/]+)/"
)


def _load_pyproject() -> dict:
    """Parse ``pyproject.toml`` with the best TOML reader available.

    Returns the full document as a dict. Falls back to a minimal reader that
    only understands the ``[project]`` scalars and the ``[project.urls]`` table
    when no TOML library is importable (older CI matrix legs).
    """
    raw = PYPROJECT_PATH.read_bytes()

    try:
        import tomllib  # Python >= 3.11
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

    if tomllib is None:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError:
            tomllib = None  # type: ignore[assignment]

    if tomllib is not None:
        return tomllib.loads(raw.decode("utf-8"))

    return _fallback_parse(raw.decode("utf-8"))


def _fallback_parse(text: str) -> dict:
    """Minimal TOML reader for the subset this test needs (no deps).

    Understands top-level ``key = "value"`` scalars inside ``[project]`` and the
    string entries of ``[project.urls]``. This is intentionally limited; it
    exists only so the test can run where neither ``tomllib`` nor ``tomli`` is
    available.
    """
    project: dict[str, object] = {}
    urls: dict[str, str] = {}
    section: str | None = None
    kv_re = re.compile(r'^\s*([A-Za-z0-9_.-]+)\s*=\s*"([^"]*)"\s*$')

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip()
            continue
        match = kv_re.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        if section == "project.urls":
            urls[key] = value
        elif section == "project":
            project[key] = value

    if urls:
        project["urls"] = urls
    return {"project": project}


@pytest.fixture(scope="module")
def project_table() -> dict:
    data = _load_pyproject()
    assert "project" in data, "pyproject.toml is missing the [project] table"
    return data["project"]


def test_pyproject_exists() -> None:
    assert PYPROJECT_PATH.is_file(), f"expected packaging file at {PYPROJECT_PATH}"


def test_version_matches_runtime(project_table: dict) -> None:
    """The declared package version must equal the importable runtime version.

    ``publish-pypi.yml`` also enforces ``tag == pyproject version`` for releases;
    this catches an in-repo drift between ``pyproject.toml`` and
    ``agentlens.__version__`` before a release is ever cut.
    """
    declared = project_table.get("version")
    assert declared, "pyproject.toml [project].version is missing"
    assert declared == agentlens.__version__, (
        f"version drift: pyproject.toml={declared!r} but "
        f"agentlens.__version__={agentlens.__version__!r}"
    )


def test_version_is_semver_like(project_table: dict) -> None:
    declared = project_table.get("version", "")
    assert re.match(r"^\d+\.\d+\.\d+", declared), (
        f"version {declared!r} is not MAJOR.MINOR.PATCH"
    )


def test_required_metadata_present(project_table: dict) -> None:
    assert project_table.get("name") == "agentlens"
    description = project_table.get("description") or ""
    assert description.strip(), "[project].description must not be empty"
    # license is a table {text = "MIT"} under tomllib, or absent under the
    # regex fallback -- only assert when the real parser populated it.
    license_field = project_table.get("license")
    if isinstance(license_field, dict):
        assert license_field.get("text") == "MIT"


def test_project_urls_use_default_branch(project_table: dict) -> None:
    """Any GitHub URL that pins a branch must pin the default branch.

    Regression guard for the ``Changelog`` URL that pointed at ``blob/main/``
    while the default branch is ``master``; ``main`` is a stale branch, so that
    link served an outdated CHANGELOG on the PyPI project page.
    """
    urls = project_table.get("urls")
    assert isinstance(urls, dict) and urls, "[project.urls] must be defined"

    offenders = []
    for name, url in urls.items():
        match = _GITHUB_BRANCH_RE.search(url)
        if match and match.group("branch") != DEFAULT_BRANCH:
            offenders.append(f"{name} -> {url} (branch {match.group('branch')!r})")

    assert not offenders, (
        "project URLs reference a non-default branch (expected "
        f"{DEFAULT_BRANCH!r}): " + "; ".join(offenders)
    )


def test_changelog_url_is_present_and_well_formed(project_table: dict) -> None:
    urls = project_table.get("urls", {})
    changelog = urls.get("Changelog")
    assert changelog, "[project.urls].Changelog should be set so PyPI links the changelog"
    assert changelog.startswith("https://github.com/sauravbhattacharya001/agentlens/")
    assert changelog.endswith("/CHANGELOG.md")
    assert f"/blob/{DEFAULT_BRANCH}/" in changelog
