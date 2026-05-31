"""Tests for scripts/gh-refresh-issues.py.

Covers the key behaviors of the deterministic open-issues report:
  - No-filter: all open issues grouped by milestone, correct order
  - Label filter (positional arg): only matching issues appear
  - --prs flag: PRs included with a Type column; drafts shown correctly
  - Empty result: exit 0 with a friendly message
  - Malformed API response: null milestone / missing fields handled
  - No-milestone bucket renders LAST
  - Within-group sort: oldest createdAt first
  - Summary line format at the end of output
  - run_gh_api errors surface as nonzero exit with stderr text

All gh calls are mocked via unittest.mock.patch on subprocess.run,
matching the pattern used in test_gh_summary.py.
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
REFRESH_SCRIPT = SCRIPTS_DIR / "gh-refresh-issues.py"


def _load_common() -> ModuleType:
    """Import _gh_common as a module.

    Returns:
        The loaded _gh_common module object.
    """
    spec = importlib.util.spec_from_file_location("_gh_common", COMMON_SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _load_refresh() -> ModuleType:
    """Import gh-refresh-issues as a module, injecting _gh_common.

    Returns:
        The loaded gh_refresh_issues module object.
    """
    common_spec = importlib.util.spec_from_file_location(
        "_gh_common", COMMON_SCRIPT
    )
    assert common_spec is not None and common_spec.loader is not None
    common_mod = importlib.util.module_from_spec(common_spec)
    sys.modules["_gh_common"] = common_mod
    common_spec.loader.exec_module(common_mod)  # type: ignore[union-attr]

    spec = importlib.util.spec_from_file_location(
        "gh_refresh_issues", REFRESH_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_issue(
    number: int,
    title: str,
    labels: list[str],
    milestone: dict[str, Any] | None = None,
    created_at: str = "2026-05-01T00:00:00Z",
    assignees: list[str] | None = None,
) -> dict[str, Any]:
    """Build a fake GitHub issue dict as returned by the REST API.

    Args:
        number: Issue number.
        title: Issue title string.
        labels: List of label name strings.
        milestone: Milestone dict or None.
        created_at: ISO 8601 creation timestamp.
        assignees: List of login strings; defaults to empty (Unassigned).

    Returns:
        Dict shaped like a GitHub REST API issue object.
    """
    return {
        "number": number,
        "title": title,
        "labels": [{"name": lbl} for lbl in labels],
        "milestone": milestone,
        "createdAt": created_at,
        "created_at": created_at,
        "html_url": f"https://github.com/owner/repo/issues/{number}",
        "url": f"https://github.com/owner/repo/issues/{number}",
        "assignees": [{"login": a} for a in (assignees or [])],
    }


def _make_pr(
    number: int,
    title: str,
    labels: list[str],
    milestone: dict[str, Any] | None = None,
    created_at: str = "2026-05-01T00:00:00Z",
    assignees: list[str] | None = None,
    is_draft: bool = False,
) -> dict[str, Any]:
    """Build a fake GitHub PR dict as returned by the REST API.

    Args:
        number: PR number.
        title: PR title string.
        labels: List of label name strings.
        milestone: Milestone dict or None.
        created_at: ISO 8601 creation timestamp.
        assignees: List of login strings; defaults to empty (Unassigned).
        is_draft: True if this is a draft PR.

    Returns:
        Dict shaped like a GitHub REST API pull request object.
        Includes ``pull_request`` key to match the /issues endpoint shape.
    """
    return {
        "number": number,
        "title": title,
        "labels": [{"name": lbl} for lbl in labels],
        "milestone": milestone,
        "createdAt": created_at,
        "created_at": created_at,
        "html_url": f"https://github.com/owner/repo/pull/{number}",
        "url": f"https://github.com/owner/repo/pull/{number}",
        "assignees": [{"login": a} for a in (assignees or [])],
        "draft": is_draft,
        "pull_request": {
            "url": (
                f"https://api.github.com/repos/owner/repo/pulls/{number}"
            )
        },
    }


def _make_milestone_dict(number: int, title: str) -> dict[str, Any]:
    """Build a minimal milestone dict for embedding in issue/PR objects.

    Args:
        number: Milestone number.
        title: Milestone title string.

    Returns:
        Dict with ``number`` and ``title`` fields.
    """
    return {"number": number, "title": title}


def _make_completed_process(
    stdout: Any, returncode: int = 0, stderr: str = ""
) -> MagicMock:
    """Build a fake subprocess.CompletedProcess for patching.

    Args:
        stdout: Value to set as the stdout attribute (serialized to JSON
            if not already a string).
        returncode: Exit code.
        stderr: Text for the stderr attribute.

    Returns:
        MagicMock simulating subprocess.CompletedProcess.
    """
    cp = MagicMock()
    cp.returncode = returncode
    cp.stdout = json.dumps(stdout) if not isinstance(stdout, str) else stdout
    cp.stderr = stderr
    return cp


# ---------------------------------------------------------------------------
# TestGroupByMilestone — grouping and ordering
# ---------------------------------------------------------------------------


class TestGroupByMilestone:
    """Milestone grouping: correct keys, no-milestone last."""

    def test_issues_grouped_under_their_milestone(self) -> None:
        """Issues with a milestone appear under that milestone heading."""
        mod = _load_refresh()
        ms = _make_milestone_dict(1, "Sprint 1")
        issues = [_make_issue(10, "Fix bug", [], milestone=ms)]
        result = mod.render_report(issues=issues, prs=[], include_prs=False)
        assert "Sprint 1" in result
        assert "#10" in result

    def test_no_milestone_bucket_renders_last(self) -> None:
        """Issues with no milestone appear after all milestoned groups."""
        mod = _load_refresh()
        ms = _make_milestone_dict(2, "Active sprint")
        issues = [
            _make_issue(1, "No milestone issue", [], milestone=None),
            _make_issue(2, "Sprint issue", [], milestone=ms),
        ]
        result = mod.render_report(issues=issues, prs=[], include_prs=False)
        pos_sprint = result.index("Active sprint")
        pos_no_ms = result.index("No milestone")
        assert pos_sprint < pos_no_ms, (
            "'No milestone' group must render after all milestoned groups"
        )

    def test_milestones_render_in_number_ascending_order(self) -> None:
        """Milestones render in ascending milestone-number order."""
        mod = _load_refresh()
        ms3 = _make_milestone_dict(3, "Third")
        ms1 = _make_milestone_dict(1, "First")
        ms2 = _make_milestone_dict(2, "Second")
        issues = [
            _make_issue(10, "Issue A", [], milestone=ms3),
            _make_issue(11, "Issue B", [], milestone=ms1),
            _make_issue(12, "Issue C", [], milestone=ms2),
        ]
        result = mod.render_report(issues=issues, prs=[], include_prs=False)
        pos_first = result.index("First")
        pos_second = result.index("Second")
        pos_third = result.index("Third")
        assert pos_first < pos_second < pos_third

    def test_within_group_sort_oldest_first(self) -> None:
        """Within each milestone group, issues sort by createdAt ascending."""
        mod = _load_refresh()
        ms = _make_milestone_dict(1, "Sprint")
        issues = [
            _make_issue(
                20, "Newer", [], milestone=ms, created_at="2026-05-10T00:00:00Z"
            ),
            _make_issue(
                21, "Older", [], milestone=ms, created_at="2026-04-01T00:00:00Z"
            ),
        ]
        result = mod.render_report(issues=issues, prs=[], include_prs=False)
        pos_older = result.index("Older")
        pos_newer = result.index("Newer")
        assert pos_older < pos_newer, (
            "Oldest issue should appear first in group"
        )

    def test_per_group_heading_includes_count(self) -> None:
        """Each milestone heading includes the open-item count."""
        mod = _load_refresh()
        ms = _make_milestone_dict(1, "Sprint 1")
        issues = [
            _make_issue(1, "A", [], milestone=ms),
            _make_issue(2, "B", [], milestone=ms),
        ]
        result = mod.render_report(issues=issues, prs=[], include_prs=False)
        assert "Sprint 1 (2 open)" in result


# ---------------------------------------------------------------------------
# TestLabelFilter — positional label_filter argument
# ---------------------------------------------------------------------------


class TestLabelFilter:
    """Label filtering: only issues with the label appear."""

    def test_filter_by_label_includes_matching(self) -> None:
        """Issues carrying the label_filter label appear in output."""
        mod = _load_refresh()
        issues = [_make_issue(1, "Bug report", ["bug"])]
        filtered = mod.apply_label_filter(issues, "bug")
        assert len(filtered) == 1

    def test_filter_by_label_excludes_non_matching(self) -> None:
        """Issues without the label_filter label are excluded."""
        mod = _load_refresh()
        issues = [
            _make_issue(1, "Bug", ["bug"]),
            _make_issue(2, "Feature", ["enhancement"]),
        ]
        filtered = mod.apply_label_filter(issues, "bug")
        numbers = [i["number"] for i in filtered]
        assert 1 in numbers
        assert 2 not in numbers

    def test_none_label_filter_returns_all(self) -> None:
        """apply_label_filter with label=None returns all items unchanged."""
        mod = _load_refresh()
        issues = [
            _make_issue(1, "One", ["bug"]),
            _make_issue(2, "Two", ["feature"]),
        ]
        result = mod.apply_label_filter(issues, None)
        assert len(result) == 2

    def test_no_label_cell_rendered_as_em_dash(self) -> None:
        """Issues with no labels show an em-dash in the Labels column."""
        mod = _load_refresh()
        issues = [_make_issue(1, "Unlabeled issue", [])]
        result = mod.render_report(issues=issues, prs=[], include_prs=False)
        assert "—" in result

    def test_labels_rendered_as_inline_code(self) -> None:
        """Labels appear as backtick-wrapped inline code in the table."""
        mod = _load_refresh()
        issues = [_make_issue(1, "Labeled", ["bug", "meta"])]
        result = mod.render_report(issues=issues, prs=[], include_prs=False)
        assert "`bug`" in result
        assert "`meta`" in result


# ---------------------------------------------------------------------------
# TestPrsFlag — --prs behavior
# ---------------------------------------------------------------------------


class TestPrsFlag:
    """--prs: PRs interleave with issues in the same milestone groups."""

    def test_prs_excluded_when_flag_not_set(self) -> None:
        """Without --prs, PR items do not appear in the output."""
        mod = _load_refresh()
        pr = _make_pr(99, "My PR", [])
        result = mod.render_report(issues=[], prs=[pr], include_prs=False)
        assert "#99" not in result

    def test_prs_included_when_flag_set(self) -> None:
        """With --prs, PR items appear in the output."""
        mod = _load_refresh()
        pr = _make_pr(99, "My PR", [])
        result = mod.render_report(issues=[], prs=[pr], include_prs=True)
        assert "#99" in result

    def test_prs_flag_adds_type_column(self) -> None:
        """With --prs, a Type column appears in every milestone table."""
        mod = _load_refresh()
        pr = _make_pr(1, "Draft PR", [], is_draft=False)
        result = mod.render_report(issues=[], prs=[pr], include_prs=True)
        assert "Type" in result

    def test_draft_pr_shows_pr_draft_type(self) -> None:
        """Draft PRs render 'PR (draft)' in the Type column."""
        mod = _load_refresh()
        pr = _make_pr(5, "WIP feature", [], is_draft=True)
        result = mod.render_report(issues=[], prs=[pr], include_prs=True)
        assert "PR (draft)" in result

    def test_non_draft_pr_shows_pr_type(self) -> None:
        """Non-draft PRs render 'PR' in the Type column."""
        mod = _load_refresh()
        pr = _make_pr(6, "Ready PR", [], is_draft=False)
        result = mod.render_report(issues=[], prs=[pr], include_prs=True)
        assert "| PR |" in result or "| PR\n" in result or " PR " in result

    def test_issues_show_issue_type_when_prs_enabled(self) -> None:
        """Issues render 'Issue' in the Type column when --prs is active."""
        mod = _load_refresh()
        issue = _make_issue(7, "A bug", [])
        result = mod.render_report(
            issues=[issue], prs=[], include_prs=True
        )
        assert "Issue" in result

    def test_issues_without_type_column_when_prs_disabled(self) -> None:
        """Issues-only mode does not include a Type column."""
        mod = _load_refresh()
        issue = _make_issue(7, "A bug", [])
        result = mod.render_report(
            issues=[issue], prs=[], include_prs=False
        )
        assert "Type" not in result


# ---------------------------------------------------------------------------
# TestEmptyResult — graceful handling of empty results
# ---------------------------------------------------------------------------


class TestEmptyResult:
    """Empty-result handling: friendly message, exit 0."""

    def test_empty_issues_no_crash(self) -> None:
        """render_report with no issues/PRs returns a string without raising."""
        mod = _load_refresh()
        result = mod.render_report(issues=[], prs=[], include_prs=False)
        assert isinstance(result, str)

    def test_empty_issues_prints_no_issues_message(self) -> None:
        """render_report with no items includes a 'no open issues' message."""
        mod = _load_refresh()
        result = mod.render_report(issues=[], prs=[], include_prs=False)
        lower = result.lower()
        assert (
            "no open" in lower or "0 open" in lower or "total: 0" in lower
        )

    def test_main_returns_zero_on_empty_result(self) -> None:
        """main() exits 0 when gh returns an empty issue list."""
        mod = _load_refresh()
        repo_cp = _make_completed_process("owner/repo")
        issues_cp = _make_completed_process([])
        prs_cp = _make_completed_process([])

        def _side_effect(cmd: list[str], **kwargs: Any) -> MagicMock:
            joined = " ".join(cmd)
            if "repo view" in joined or "nameWithOwner" in joined:
                return repo_cp
            if "pulls" in joined:
                return prs_cp
            return issues_cp

        with patch("subprocess.run", side_effect=_side_effect):
            code = mod.main([])
        assert code == 0


# ---------------------------------------------------------------------------
# TestMalformedApiResponse — graceful handling of unexpected shapes
# ---------------------------------------------------------------------------


class TestMalformedApiResponse:
    """Malformed gh API responses don't crash; null milestone handled."""

    def test_null_milestone_goes_to_no_milestone_bucket(self) -> None:
        """Issue with milestone=null ends up in the No milestone group."""
        mod = _load_refresh()
        issue = _make_issue(42, "Null milestone", [], milestone=None)
        result = mod.render_report(
            issues=[issue], prs=[], include_prs=False
        )
        assert "No milestone" in result
        assert "#42" in result

    def test_issue_missing_assignees_key_renders_unassigned(self) -> None:
        """Issue dict without 'assignees' key renders 'Unassigned'."""
        mod = _load_refresh()
        issue: dict[str, Any] = {
            "number": 55,
            "title": "Minimal issue",
            "labels": [],
            "milestone": None,
            "createdAt": "2026-05-01T00:00:00Z",
            "html_url": "https://github.com/owner/repo/issues/55",
            "url": "https://github.com/owner/repo/issues/55",
        }
        result = mod.render_report(
            issues=[issue], prs=[], include_prs=False
        )
        assert "Unassigned" in result

    def test_issue_missing_created_at_does_not_crash(self) -> None:
        """Issue with malformed/missing createdAt doesn't raise."""
        mod = _load_refresh()
        issue: dict[str, Any] = {
            "number": 77,
            "title": "No date",
            "labels": [],
            "milestone": None,
            "createdAt": "",
            "html_url": "https://github.com/owner/repo/issues/77",
            "url": "https://github.com/owner/repo/issues/77",
            "assignees": [],
        }
        result = mod.render_report(
            issues=[issue], prs=[], include_prs=False
        )
        assert "#77" in result


# ---------------------------------------------------------------------------
# TestTableShape — column layout
# ---------------------------------------------------------------------------


class TestTableShape:
    """Table shape: correct columns in issues-only and issues+PRs modes."""

    def test_issues_only_columns(self) -> None:
        """Issues-only table has #, Title, Labels, Assignee, Created cols."""
        mod = _load_refresh()
        issue = _make_issue(1, "Test issue", ["bug"], assignees=["alice"])
        result = mod.render_report(
            issues=[issue], prs=[], include_prs=False
        )
        assert "# |" in result or "| #" in result
        assert "Title" in result
        assert "Labels" in result
        assert "Assignee" in result
        assert "Created" in result

    def test_assignee_shows_login(self) -> None:
        """The Assignee cell shows the GitHub login when assigned."""
        mod = _load_refresh()
        issue = _make_issue(2, "Assigned issue", [], assignees=["bob"])
        result = mod.render_report(
            issues=[issue], prs=[], include_prs=False
        )
        assert "bob" in result

    def test_unassigned_shows_unassigned(self) -> None:
        """The Assignee cell shows 'Unassigned' when no assignees."""
        mod = _load_refresh()
        issue = _make_issue(3, "Unassigned issue", [])
        result = mod.render_report(
            issues=[issue], prs=[], include_prs=False
        )
        assert "Unassigned" in result

    def test_created_date_is_yyyy_mm_dd(self) -> None:
        """The Created cell shows only the YYYY-MM-DD portion."""
        mod = _load_refresh()
        issue = _make_issue(
            4, "Dated issue", [], created_at="2026-03-15T12:34:56Z"
        )
        result = mod.render_report(
            issues=[issue], prs=[], include_prs=False
        )
        assert "2026-03-15" in result

    def test_issue_number_is_hyperlinked(self) -> None:
        """Issue number appears as a markdown link [#N](url)."""
        mod = _load_refresh()
        issue = _make_issue(88, "Linked", [])
        result = mod.render_report(
            issues=[issue], prs=[], include_prs=False
        )
        assert "[#88]" in result

    def test_title_truncated_at_60_chars(self) -> None:
        """Titles longer than 60 characters are truncated with ellipsis."""
        mod = _load_refresh()
        long_title = "A" * 65
        issue = _make_issue(9, long_title, [])
        result = mod.render_report(
            issues=[issue], prs=[], include_prs=False
        )
        assert long_title not in result
        assert "…" in result or "..." in result


# ---------------------------------------------------------------------------
# TestSummaryLine — footer format
# ---------------------------------------------------------------------------


class TestSummaryLine:
    """Summary line format: Total count, milestone count, fetched timestamp."""

    def test_summary_line_present(self) -> None:
        """render_report output ends with a **Total:** summary line."""
        mod = _load_refresh()
        issue = _make_issue(1, "One issue", [])
        result = mod.render_report(
            issues=[issue], prs=[], include_prs=False
        )
        assert "**Total:" in result

    def test_summary_line_includes_open_count(self) -> None:
        """Summary line mentions the total number of open items."""
        mod = _load_refresh()
        issues = [_make_issue(i, f"Issue {i}", []) for i in range(3)]
        result = mod.render_report(issues=issues, prs=[], include_prs=False)
        assert "3 open" in result

    def test_summary_no_milestone_group_counted_in_milestones(
        self,
    ) -> None:
        """No-milestone group is counted in M milestones when non-empty."""
        mod = _load_refresh()
        issue = _make_issue(1, "No ms", [], milestone=None)
        result = mod.render_report(
            issues=[issue], prs=[], include_prs=False
        )
        assert "1 milestone" in result

    def test_summary_excludes_no_milestone_when_empty(self) -> None:
        """When no-milestone group is empty, it is not counted in milestones."""
        mod = _load_refresh()
        ms = _make_milestone_dict(1, "Sprint")
        issue = _make_issue(1, "In sprint", [], milestone=ms)
        result = mod.render_report(
            issues=[issue], prs=[], include_prs=False
        )
        assert "1 milestone" in result

    def test_summary_line_includes_fetched_timestamp(self) -> None:
        """Summary line includes 'fetched' with a timestamp."""
        mod = _load_refresh()
        issue = _make_issue(1, "Issue", [])
        result = mod.render_report(
            issues=[issue], prs=[], include_prs=False
        )
        assert "fetched" in result


# ---------------------------------------------------------------------------
# TestFetchData — gh API call patterns
# ---------------------------------------------------------------------------


class TestFetchData:
    """Verify gh API endpoints are called correctly (mocked subprocess)."""

    def _make_side_effect(
        self,
        issues: list[dict[str, Any]],
        prs: list[dict[str, Any]] | None = None,
        repo_name: str = "owner/repo",
    ) -> Any:
        """Build a side_effect function for subprocess.run mocking.

        Args:
            issues: Issues to return from the issues endpoint.
            prs: PRs to return from the pulls endpoint.
            repo_name: The owner/repo string returned by gh repo view.

        Returns:
            Callable for use with unittest.mock.patch side_effect.
        """
        prs = prs or []

        def _side(cmd: list[str], **kwargs: Any) -> MagicMock:
            joined = " ".join(cmd)
            if "nameWithOwner" in joined or "repo view" in joined:
                return _make_completed_process(repo_name)
            if "pulls" in joined:
                return _make_completed_process(prs)
            return _make_completed_process(issues)

        return _side

    def test_fetch_data_calls_issues_endpoint(self) -> None:
        """fetch_data calls the /issues endpoint on the GitHub API."""
        mod = _load_refresh()
        side = self._make_side_effect([])
        with patch("subprocess.run", side_effect=side) as mock_run:
            mod.fetch_data(repo="owner/repo", include_prs=False)
        calls = [" ".join(c[0][0]) for c in mock_run.call_args_list]
        assert any("issues" in c for c in calls)

    def test_fetch_data_calls_pulls_endpoint_when_prs_enabled(
        self,
    ) -> None:
        """fetch_data calls the /pulls endpoint when include_prs=True."""
        mod = _load_refresh()
        side = self._make_side_effect([])
        with patch("subprocess.run", side_effect=side) as mock_run:
            mod.fetch_data(repo="owner/repo", include_prs=True)
        calls = [" ".join(c[0][0]) for c in mock_run.call_args_list]
        assert any("pulls" in c for c in calls)

    def test_fetch_data_skips_pulls_endpoint_when_prs_disabled(
        self,
    ) -> None:
        """fetch_data does NOT call /pulls endpoint when include_prs=False."""
        mod = _load_refresh()
        side = self._make_side_effect([])
        with patch("subprocess.run", side_effect=side) as mock_run:
            mod.fetch_data(repo="owner/repo", include_prs=False)
        calls = [" ".join(c[0][0]) for c in mock_run.call_args_list]
        assert not any("pulls" in c for c in calls)

    def test_fetch_data_excludes_prs_from_issues_endpoint(self) -> None:
        """fetch_data strips PR items from the /issues endpoint response."""
        mod = _load_refresh()
        pr_as_issue = _make_pr(1, "A PR", [])
        real_issue = _make_issue(2, "Real issue", [])
        side = self._make_side_effect([pr_as_issue, real_issue])
        with patch("subprocess.run", side_effect=side):
            issues, _ = mod.fetch_data(
                repo="owner/repo", include_prs=False
            )
        numbers = [i["number"] for i in issues]
        assert 2 in numbers
        assert 1 not in numbers

    def test_gh_failure_raises_runtime_error(self) -> None:
        """fetch_data propagates RuntimeError on gh API failure."""
        mod = _load_refresh()
        fail_cp = _make_completed_process(
            "", returncode=1, stderr="auth error"
        )
        with patch("subprocess.run", return_value=fail_cp):
            try:
                mod.fetch_data(repo="owner/repo", include_prs=False)
                assert False, "Expected RuntimeError"
            except RuntimeError as exc:
                assert "auth error" in str(exc)

    def test_main_returns_nonzero_on_gh_failure(self) -> None:
        """main() returns nonzero when gh API fails."""
        mod = _load_refresh()
        fail_cp = _make_completed_process(
            "", returncode=1, stderr="auth error"
        )
        with patch("subprocess.run", return_value=fail_cp):
            code = mod.main([])
        assert code != 0
