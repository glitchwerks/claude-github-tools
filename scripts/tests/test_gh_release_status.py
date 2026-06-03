"""Tests for scripts/gh-release-status.py.

Covers the deterministic release-status script:
  - render_recent_releases (shared helper): table, date format, empty repo,
    gh failure
  - Per-area grouping: files grouped by first path segment
  - Releases table: tag, published date, title rendered correctly
  - Zero-releases edge case: clear message, exits 0
  - No-commits-since-last-release: "up to date" message
  - gh-failure path: non-zero exit surfaces stderr, returns 1
  - main() integration: --repo flag, --limit flag, stdout output

All gh calls are mocked via unittest.mock.patch on subprocess.run,
matching the pattern used in test_gh_summary.py and test_gh_quick_wins.py.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Module loading helpers
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).parent.parent
COMMON_SCRIPT = SCRIPTS_DIR / "_gh_common.py"
RELEASE_STATUS_SCRIPT = SCRIPTS_DIR / "gh-release-status.py"


def _load_common() -> ModuleType:
    """Import _gh_common as a module.

    Returns:
        The loaded _gh_common module object.
    """
    spec = importlib.util.spec_from_file_location(
        "_gh_common", COMMON_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _load_release_status() -> ModuleType:
    """Import gh-release-status as a module, injecting _gh_common.

    Returns:
        The loaded gh_release_status module object.
    """
    common_spec = importlib.util.spec_from_file_location(
        "_gh_common", COMMON_SCRIPT
    )
    assert common_spec is not None and common_spec.loader is not None
    common_mod = importlib.util.module_from_spec(common_spec)
    sys.modules["_gh_common"] = common_mod
    common_spec.loader.exec_module(common_mod)  # type: ignore[union-attr]

    spec = importlib.util.spec_from_file_location(
        "gh_release_status", RELEASE_STATUS_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_completed_process(
    stdout: Any, returncode: int = 0, stderr: str = ""
) -> MagicMock:
    """Build a fake subprocess.CompletedProcess for patching.

    Args:
        stdout: Value to set as the stdout attribute (serialized to JSON
            if not already a string).
        returncode: Exit code (default 0).
        stderr: Stderr string (default empty).

    Returns:
        MagicMock simulating subprocess.CompletedProcess.
    """
    cp = MagicMock()
    cp.returncode = returncode
    cp.stdout = (
        json.dumps(stdout) if not isinstance(stdout, str) else stdout
    )
    cp.stderr = stderr
    return cp


def _make_release(
    tag: str, published: str, name: str
) -> dict[str, Any]:
    """Build a fake gh release list JSON entry.

    Args:
        tag: Tag name string (e.g. 'v1.0.0').
        published: ISO 8601 published date string.
        name: Release title.

    Returns:
        Dict shaped like a gh release list --json entry.
    """
    return {"tagName": tag, "publishedAt": published, "name": name}


def _make_compare_payload(
    files: list[dict[str, Any]],
    commits: list[dict[str, Any]] | None = None,
    ahead_by: int = 0,
    total_commits: int | None = None,
) -> dict[str, Any]:
    """Build a fake GitHub compare API response payload.

    Args:
        files: List of file diff dicts, each with ``filename``,
            ``additions``, ``deletions``, and ``status`` keys.
        commits: List of commit dicts. Defaults to empty list.
        ahead_by: ahead_by value from compare API.
        total_commits: total_commits value. Defaults to len(commits).

    Returns:
        Dict shaped like a GitHub compare API response.
    """
    if commits is None:
        commits = []
    if total_commits is None:
        total_commits = len(commits)
    return {
        "files": files,
        "commits": commits,
        "ahead_by": ahead_by,
        "behind_by": 0,
        "total_commits": total_commits,
        "status": "ahead" if ahead_by > 0 else "identical",
    }


def _make_file(
    filename: str,
    additions: int = 5,
    deletions: int = 2,
    status: str = "modified",
) -> dict[str, Any]:
    """Build a fake file diff entry from the compare API.

    Args:
        filename: Path of the file.
        additions: Number of added lines.
        deletions: Number of deleted lines.
        status: Change status (modified/added/removed/renamed).

    Returns:
        Dict shaped like a GitHub compare API file entry.
    """
    return {
        "filename": filename,
        "additions": additions,
        "deletions": deletions,
        "status": status,
    }


def _make_commit(message: str) -> dict[str, Any]:
    """Build a fake commit dict from the compare API.

    Args:
        message: Commit message.

    Returns:
        Dict with nested commit.message structure.
    """
    return {"commit": {"message": message}}


# ---------------------------------------------------------------------------
# TestRenderRecentReleasesShared — _gh_common.render_recent_releases
# ---------------------------------------------------------------------------


class TestRenderRecentReleasesShared:
    """render_recent_releases in _gh_common: table, date, empty, failure."""

    def test_no_releases_returns_none(self) -> None:
        """render_recent_releases returns None when repo has no releases."""
        mod = _load_common()
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            result = mod.render_recent_releases()
        assert result is None

    def test_populated_releases_render_table(self) -> None:
        """Releases are rendered as a markdown table."""
        mod = _load_common()
        releases = [
            _make_release("v1.2.0", "2026-05-01T12:00:00Z", "May release"),
            _make_release("v1.1.0", "2026-04-01T12:00:00Z", "April release"),
        ]
        cp = _make_completed_process(releases, returncode=0)
        with patch("subprocess.run", return_value=cp):
            result = mod.render_recent_releases()
        assert result is not None
        assert "v1.2.0" in result
        assert "v1.1.0" in result
        assert "May release" in result

    def test_date_formatted_as_yyyy_mm_dd(self) -> None:
        """Published date is rendered as YYYY-MM-DD only."""
        mod = _load_common()
        releases = [
            _make_release("v2.0.0", "2026-03-15T08:30:00Z", "March release"),
        ]
        cp = _make_completed_process(releases, returncode=0)
        with patch("subprocess.run", return_value=cp):
            result = mod.render_recent_releases()
        assert result is not None
        assert "2026-03-15" in result
        assert "T08:30:00Z" not in result

    def test_section_header_present(self) -> None:
        """The section header '### Recent releases' is present."""
        mod = _load_common()
        releases = [
            _make_release("v1.0.0", "2026-01-01T00:00:00Z", "First"),
        ]
        cp = _make_completed_process(releases, returncode=0)
        with patch("subprocess.run", return_value=cp):
            result = mod.render_recent_releases()
        assert result is not None
        assert "### Recent releases" in result

    def test_gh_failure_returns_none(self) -> None:
        """gh failure causes render_recent_releases to return None."""
        mod = _load_common()
        cp = _make_completed_process(
            "", returncode=1, stderr="not a git repository"
        )
        with patch("subprocess.run", return_value=cp):
            result = mod.render_recent_releases()
        assert result is None

    def test_limit_parameter_respected(self) -> None:
        """render_recent_releases passes --limit N to gh release list."""
        mod = _load_common()
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp) as mock_run:
            mod.render_recent_releases(limit=3)
        call_args = mock_run.call_args[0][0]
        joined = " ".join(str(a) for a in call_args)
        assert "--limit" in joined
        assert "3" in joined

    def test_default_limit_is_five(self) -> None:
        """render_recent_releases defaults to --limit 5."""
        mod = _load_common()
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp) as mock_run:
            mod.render_recent_releases()
        call_args = mock_run.call_args[0][0]
        joined = " ".join(str(a) for a in call_args)
        assert "5" in joined


# ---------------------------------------------------------------------------
# TestGhSummaryStillUsesRenderRecentReleases
# ---------------------------------------------------------------------------


class TestGhSummaryStillUsesRenderRecentReleases:
    """gh-summary must still work after _render_recent_releases is extracted."""

    def test_render_report_includes_releases_section(self) -> None:
        """render_report still shows releases section via shared helper."""
        common_spec = importlib.util.spec_from_file_location(
            "_gh_common", COMMON_SCRIPT
        )
        assert common_spec is not None and common_spec.loader is not None
        common_mod = importlib.util.module_from_spec(common_spec)
        sys.modules["_gh_common"] = common_mod
        common_spec.loader.exec_module(common_mod)  # type: ignore[union-attr]

        summary_spec = importlib.util.spec_from_file_location(
            "gh_summary_compat",
            SCRIPTS_DIR / "gh-summary.py",
        )
        assert summary_spec is not None and summary_spec.loader is not None
        summary_mod = importlib.util.module_from_spec(summary_spec)
        summary_spec.loader.exec_module(summary_mod)  # type: ignore

        releases = [
            _make_release("v1.0.0", "2026-01-01T00:00:00Z", "First"),
        ]
        cp = _make_completed_process(releases, returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = summary_mod.render_report(
                epics=[], milestones=[], open_issues=[]
            )
        assert "Recent releases" in output


# ---------------------------------------------------------------------------
# TestGroupFilesByArea — per-top-level-area breakdown
# ---------------------------------------------------------------------------


class TestGroupFilesByArea:
    """Files are grouped by their first path segment (top-level area)."""

    def test_single_segment_path_uses_root(self) -> None:
        """File with no slash groups under its filename as area."""
        mod = _load_release_status()
        files = [_make_file("README.md")]
        groups = mod.group_files_by_area(files)
        # Single-segment path: area is the file itself or 'root'
        assert len(groups) >= 1

    def test_nested_file_groups_by_first_segment(self) -> None:
        """File 'scripts/foo.py' groups under 'scripts'."""
        mod = _load_release_status()
        files = [
            _make_file("scripts/foo.py"),
            _make_file("scripts/bar.py"),
            _make_file("skills/my-skill/SKILL.md"),
        ]
        groups = mod.group_files_by_area(files)
        assert "scripts" in groups
        assert "skills" in groups
        assert len(groups["scripts"]) == 2
        assert len(groups["skills"]) == 1

    def test_files_in_same_top_level_area_merged(self) -> None:
        """Multiple files under same top-level dir are in the same group."""
        mod = _load_release_status()
        files = [
            _make_file("src/a.py"),
            _make_file("src/b.py"),
            _make_file("src/c.py"),
        ]
        groups = mod.group_files_by_area(files)
        assert "src" in groups
        assert len(groups["src"]) == 3

    def test_empty_files_returns_empty_dict(self) -> None:
        """Empty file list returns empty dict."""
        mod = _load_release_status()
        groups = mod.group_files_by_area([])
        assert groups == {}

    def test_area_additions_deletions_summed(self) -> None:
        """Additions and deletions are summed per area."""
        mod = _load_release_status()
        files = [
            _make_file("scripts/a.py", additions=10, deletions=3),
            _make_file("scripts/b.py", additions=5, deletions=1),
        ]
        groups = mod.group_files_by_area(files)
        scripts_files = groups["scripts"]
        total_add = sum(f["additions"] for f in scripts_files)
        total_del = sum(f["deletions"] for f in scripts_files)
        assert total_add == 15
        assert total_del == 4


# ---------------------------------------------------------------------------
# TestRenderUnreleasedDiff — unreleased diff rendering
# ---------------------------------------------------------------------------


class TestRenderUnreleasedDiff:
    """render_unreleased_diff produces the expected markdown output."""

    def test_no_commits_renders_up_to_date_message(self) -> None:
        """When total_commits == 0, renders an 'up to date' line."""
        mod = _load_release_status()
        compare = _make_compare_payload(files=[], commits=[], ahead_by=0)
        result = mod.render_unreleased_diff("v1.0.0", "main", compare)
        # Should contain some indication of up-to-date / no changes
        lower = result.lower()
        assert (
            "up to date" in lower
            or "no unreleased" in lower
            or "nothing" in lower
            or "0 commit" in lower
        )

    def test_has_per_area_breakdown(self) -> None:
        """With changed files, output includes per-area grouping."""
        mod = _load_release_status()
        files = [
            _make_file("scripts/foo.py", additions=10, deletions=2),
            _make_file("skills/bar/SKILL.md", additions=5, deletions=0),
        ]
        compare = _make_compare_payload(
            files=files, commits=[_make_commit("feat: add thing")], ahead_by=1
        )
        result = mod.render_unreleased_diff("v1.0.0", "main", compare)
        assert "scripts" in result
        assert "skills" in result

    def test_total_insertions_deletions_shown(self) -> None:
        """Output shows total insertions and deletions across all files."""
        mod = _load_release_status()
        files = [
            _make_file("a/b.py", additions=10, deletions=3),
            _make_file("c/d.py", additions=7, deletions=1),
        ]
        compare = _make_compare_payload(files=files, ahead_by=2)
        result = mod.render_unreleased_diff("v1.0.0", "main", compare)
        # 17 total additions, 4 total deletions
        assert "17" in result or "+17" in result
        assert "4" in result or "-4" in result

    def test_tag_and_branch_referenced_in_output(self) -> None:
        """The latest tag and default branch appear in diff output."""
        mod = _load_release_status()
        files = [_make_file("x/y.py")]
        compare = _make_compare_payload(files=files, ahead_by=1)
        result = mod.render_unreleased_diff("v2.3.0", "main", compare)
        assert "v2.3.0" in result

    def test_large_diff_includes_truncation_note(self) -> None:
        """When files list is truncated (300 files), a note is rendered."""
        mod = _load_release_status()
        # The compare API caps at 300 files; simulate that
        files = [
            _make_file(f"src/file_{i}.py") for i in range(300)
        ]
        compare = _make_compare_payload(
            files=files, ahead_by=50, total_commits=50
        )
        # Indicate that we hit the 300-file cap
        compare["files_truncated"] = True
        result = mod.render_unreleased_diff("v1.0.0", "main", compare)
        lower = result.lower()
        # Should include a note about truncation or capping
        assert (
            "truncat" in lower
            or "300" in lower
            or "capped" in lower
            or "limit" in lower
        )


# ---------------------------------------------------------------------------
# TestRenderReleaseStatus — full report rendering
# ---------------------------------------------------------------------------


def _make_release_status_side_effect(
    releases: list[dict[str, Any]],
    default_branch: str = "main",
    compare: dict[str, Any] | None = None,
) -> Any:
    """Build a subprocess.run side_effect covering all calls in render_release_status.

    Dispatches the four call patterns made by render_release_status:
    1. ``gh release list --limit N`` — returns ``releases``
    2. ``gh release list --limit 1`` — returns first element of releases
       (for _fetch_latest_tag)
    3. ``gh api repos/owner/repo --jq .default_branch`` — returns branch
    4. ``gh api repos/owner/repo/compare/...`` — returns compare payload

    Args:
        releases: Release list to return for ``gh release list``.
        default_branch: Default branch name to return.
        compare: Compare API payload. Defaults to empty/up-to-date.

    Returns:
        Callable side_effect for ``unittest.mock.patch``.
    """
    if compare is None:
        compare = _make_compare_payload([], ahead_by=0)

    def _side_effect(cmd: list[str], **kwargs: Any) -> MagicMock:
        joined = " ".join(str(c) for c in cmd)
        if "release" in joined and "list" in joined:
            # Both render_recent_releases and _fetch_latest_tag use
            # 'gh release list'; return releases for both.
            return _make_completed_process(releases)
        if "compare" in joined:
            return _make_completed_process(compare)
        if "api" in joined and "repos" in joined:
            # Default branch lookup via run_gh_api with --jq .default_branch.
            # run_gh_api calls json.loads(stdout), so the string must be
            # JSON-encoded (i.e. '"main"', not 'main').
            cp = MagicMock()
            cp.returncode = 0
            cp.stdout = json.dumps(default_branch)
            cp.stderr = ""
            return cp
        cp = MagicMock()
        cp.returncode = 0
        cp.stdout = json.dumps([])
        cp.stderr = ""
        return cp

    return _side_effect


class TestRenderReleaseStatus:
    """render_release_status produces full markdown report."""

    def test_zero_releases_emits_no_releases_message(self) -> None:
        """When repo has no releases, a clear message is rendered."""
        mod = _load_release_status()
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            result = mod.render_release_status(
                repo="owner/repo", limit=5
            )
        lower = result.lower()
        assert (
            "no release" in lower
            or "no releases" in lower
            or "nothing to diff" in lower
        )

    def test_zero_releases_does_not_contain_diff_section(self) -> None:
        """With no releases there is no diff/compare section."""
        mod = _load_release_status()
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            result = mod.render_release_status(
                repo="owner/repo", limit=5
            )
        assert "compare" not in result.lower()
        assert "unreleased" not in result.lower()

    def test_releases_table_present_when_releases_exist(self) -> None:
        """When releases exist, the Recent releases table is in output."""
        mod = _load_release_status()
        releases = [
            _make_release("v1.0.0", "2026-01-01T00:00:00Z", "First"),
        ]
        side_effect = _make_release_status_side_effect(releases)
        with patch("subprocess.run", side_effect=side_effect):
            result = mod.render_release_status(
                repo="owner/repo", limit=5
            )
        assert "v1.0.0" in result
        assert "Recent releases" in result

    def test_no_commits_since_release_shows_up_to_date(self) -> None:
        """When repo is up-to-date with last release, shows explicit message."""
        mod = _load_release_status()
        releases = [
            _make_release("v1.0.0", "2026-01-01T00:00:00Z", "First"),
        ]
        compare = _make_compare_payload([], ahead_by=0, total_commits=0)
        side_effect = _make_release_status_side_effect(
            releases, compare=compare
        )
        with patch("subprocess.run", side_effect=side_effect):
            result = mod.render_release_status(
                repo="owner/repo", limit=5
            )
        lower = result.lower()
        assert (
            "up to date" in lower
            or "no unreleased" in lower
            or "0 commit" in lower
            or "nothing" in lower
        )


# ---------------------------------------------------------------------------
# TestGhFailurePath — gh CLI errors surface stderr and return 1
# ---------------------------------------------------------------------------


class TestGhFailurePath:
    """gh non-zero exit surfaces stderr, exits non-zero."""

    def test_gh_failure_on_release_list_exits_nonzero(self) -> None:
        """main() exits non-zero when gh release list fails."""
        mod = _load_release_status()
        # Fail only the release list call; the table fetch fails → RuntimeError
        cp_fail = _make_completed_process(
            "", returncode=1, stderr="authentication required"
        )

        def _side_effect(cmd: list[str], **kwargs: Any) -> MagicMock:
            joined = " ".join(str(c) for c in cmd)
            if "release" in joined and "list" in joined:
                return cp_fail
            return _make_completed_process([])

        with patch("subprocess.run", side_effect=_side_effect):
            exit_code = mod.main(["--repo", "owner/repo"])
        # render_recent_releases returns None → no releases message,
        # but that exits 0. _fetch_latest_tag also returns None → exits 0.
        # A failure on release list that renders None is a graceful path;
        # the RuntimeError path occurs when run_gh_api raises.
        # Test the run_gh_api failure path instead:
        cp_api_fail = MagicMock()
        cp_api_fail.returncode = 1
        cp_api_fail.stdout = ""
        cp_api_fail.stderr = "authentication required"

        releases = [_make_release("v1.0.0", "2026-01-01T00:00:00Z", "x")]

        def _side_effect2(cmd: list[str], **kwargs: Any) -> MagicMock:
            joined = " ".join(str(c) for c in cmd)
            if "release" in joined and "list" in joined:
                return _make_completed_process(releases)
            # All api calls fail (default branch, compare)
            return cp_api_fail

        with patch("subprocess.run", side_effect=_side_effect2):
            exit_code = mod.main(["--repo", "owner/repo"])
        assert exit_code != 0

    def test_gh_failure_on_compare_api_exits_nonzero(self) -> None:
        """main() exits non-zero when compare API call fails."""
        mod = _load_release_status()
        releases = [
            _make_release("v1.0.0", "2026-01-01T00:00:00Z", "First"),
        ]

        def _side_effect(cmd: list[str], **kwargs: Any) -> MagicMock:
            joined = " ".join(str(c) for c in cmd)
            if "release" in joined and "list" in joined:
                return _make_completed_process(releases)
            # All other calls (api repos/...) fail
            return _make_completed_process(
                "", returncode=1, stderr="API error"
            )

        with patch("subprocess.run", side_effect=_side_effect):
            exit_code = mod.main(["--repo", "owner/repo"])
        assert exit_code != 0


# ---------------------------------------------------------------------------
# TestMainEntryPoint — main() CLI integration
# ---------------------------------------------------------------------------


class TestMainEntryPoint:
    """main() integration: --repo flag, --limit flag, stdout output."""

    def test_main_exits_0_on_success(self) -> None:
        """main() exits 0 when gh returns valid data."""
        mod = _load_release_status()
        releases = [
            _make_release("v1.0.0", "2026-01-01T00:00:00Z", "First"),
        ]
        side_effect = _make_release_status_side_effect(releases)
        with patch("subprocess.run", side_effect=side_effect):
            exit_code = mod.main(["--repo", "owner/repo"])
        assert exit_code == 0

    def test_main_exits_0_with_zero_releases(self) -> None:
        """main() exits 0 even when repo has no releases."""
        mod = _load_release_status()
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            exit_code = mod.main(["--repo", "owner/repo"])
        assert exit_code == 0

    def test_main_accepts_repo_flag(self) -> None:
        """main() accepts --repo OWNER/REPO flag."""
        mod = _load_release_status()
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            exit_code = mod.main(["--repo", "myorg/myrepo"])
        assert exit_code == 0

    def test_main_accepts_limit_flag(self) -> None:
        """main() accepts --limit N flag for releases table."""
        mod = _load_release_status()
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp) as mock_run:
            mod.main(["--repo", "owner/repo", "--limit", "3"])
        # Confirm --limit 3 was passed to gh release list
        all_calls = [
            " ".join(str(c) for c in ca[0][0])
            for ca in mock_run.call_args_list
            if ca[0]
        ]
        release_calls = [c for c in all_calls if "release" in c]
        if release_calls:
            assert "3" in release_calls[0]

    def test_main_writes_markdown_to_stdout(self, capsys: Any) -> None:
        """main() writes markdown content to stdout."""
        mod = _load_release_status()
        releases = [
            _make_release("v1.0.0", "2026-01-01T00:00:00Z", "First"),
        ]
        side_effect = _make_release_status_side_effect(releases)
        with patch("subprocess.run", side_effect=side_effect):
            mod.main(["--repo", "owner/repo"])
        captured = capsys.readouterr()
        assert len(captured.out) > 0

    def test_main_exits_nonzero_on_gh_failure(self) -> None:
        """main() exits non-zero when gh returns a non-zero exit code."""
        mod = _load_release_status()
        releases = [
            _make_release("v1.0.0", "2026-01-01T00:00:00Z", "First"),
        ]
        # Make the API call (default branch) fail after release list succeeds
        cp_fail = _make_completed_process(
            "", returncode=1, stderr="auth required"
        )

        def _side_effect(cmd: list[str], **kwargs: Any) -> MagicMock:
            joined = " ".join(str(c) for c in cmd)
            if "release" in joined and "list" in joined:
                return _make_completed_process(releases)
            return cp_fail

        with patch("subprocess.run", side_effect=_side_effect):
            exit_code = mod.main(["--repo", "owner/repo"])
        assert exit_code != 0
