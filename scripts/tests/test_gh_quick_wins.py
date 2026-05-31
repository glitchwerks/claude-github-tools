"""Tests for scripts/gh-quick-wins.py.

Covers the deterministic exclusion filter:
  - Issues with excluded labels are filtered out
  - Issues with assignees are filtered out (any assignee)
  - Issues with > max_ac checkboxes are filtered out
  - Issues with "blocked by #N" / "depends on #N" prose are filtered out
  - Surviving issues have a ``signals`` sub-object with correct fields
  - ``touches_load_bearing`` is True for bodies/titles mentioning
    load-bearing paths
  - ``body_appears_drafted`` is True when body meets the heuristic criteria
  - ``comment_count`` is derived from the length of the ``comments`` array
  - ``days_since_update`` is computed from ``updatedAt``
  - Zero survivors: emits ``[]`` on stdout, exits 0
  - Non-zero gh exit code: exits non-zero with stderr passthrough

All gh calls are mocked via unittest.mock.patch on subprocess.run,
matching the pattern used in test_gh_summary.py.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Module loading helpers
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).parent.parent
COMMON_SCRIPT = SCRIPTS_DIR / "_gh_common.py"
QUICK_WINS_SCRIPT = SCRIPTS_DIR / "gh-quick-wins.py"


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


def _load_quick_wins() -> ModuleType:
    """Import gh-quick-wins as a module, injecting _gh_common.

    Returns:
        The loaded gh_quick_wins module object.
    """
    common_spec = importlib.util.spec_from_file_location(
        "_gh_common", COMMON_SCRIPT
    )
    assert common_spec is not None and common_spec.loader is not None
    common_mod = importlib.util.module_from_spec(common_spec)
    sys.modules["_gh_common"] = common_mod
    common_spec.loader.exec_module(common_mod)  # type: ignore[union-attr]

    spec = importlib.util.spec_from_file_location(
        "gh_quick_wins", QUICK_WINS_SCRIPT
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
    body: str = "",
    assignees: list[str] | None = None,
    comments: int = 0,
    updated_at: str = "2026-05-01T00:00:00Z",
    url: str | None = None,
) -> dict:
    """Build a minimal gh issue list JSON object.

    Args:
        number: Issue number.
        title: Issue title.
        labels: List of label name strings.
        body: Issue body markdown.
        assignees: List of assignee login strings. Defaults to empty.
        comments: Number of comments (simulates len of comments array).
        updated_at: ISO 8601 timestamp string for updatedAt.
        url: HTML URL. Defaults to a generated GitHub URL.

    Returns:
        Dict matching the shape emitted by ``gh issue list --json``.
    """
    return {
        "number": number,
        "title": title,
        "labels": [{"name": lbl} for lbl in labels],
        "body": body,
        "assignees": [{"login": a} for a in (assignees or [])],
        # gh issue list --json comments returns a scalar int (the count),
        # not an array of comment objects. Match production reality here.
        "comments": comments,
        "updatedAt": updated_at,
        "url": url or f"https://github.com/owner/repo/issues/{number}",
        "milestone": None,
    }


def _make_gh_response(issues: list[dict]) -> MagicMock:
    """Build a mock subprocess.run return value for gh api --paginate --slurp.

    Args:
        issues: List of issue dicts to serialize as a single-page response.

    Returns:
        MagicMock with returncode=0 and stdout set to the slurp JSON
        shape (array containing one page array).
    """
    mock = MagicMock()
    mock.returncode = 0
    # Wrap in outer array to match --slurp shape: [[...issues...]]
    mock.stdout = json.dumps([issues])
    mock.stderr = ""
    return mock


# ---------------------------------------------------------------------------
# Tests: exclusion filter — label-based
# ---------------------------------------------------------------------------


class TestExclusionByLabel:
    """Issues with excluded labels must not appear in output."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_quick_wins()

    def test_blocked_label_excluded(self) -> None:
        """Issue with 'blocked' label is excluded."""
        issues = [_make_issue(1, "Should be excluded", ["blocked"])]
        result = self.mod.apply_exclusion_filter(issues)
        assert result == []

    def test_deferred_label_excluded(self) -> None:
        """Issue with 'deferred' label is excluded."""
        issues = [_make_issue(2, "Deferred item", ["deferred"])]
        result = self.mod.apply_exclusion_filter(issues)
        assert result == []

    def test_epic_label_excluded(self) -> None:
        """Issue with 'epic' label is excluded."""
        issues = [_make_issue(3, "Big epic", ["epic"])]
        result = self.mod.apply_exclusion_filter(issues)
        assert result == []

    def test_meta_label_excluded(self) -> None:
        """Issue with 'meta' label is excluded."""
        issues = [_make_issue(4, "Meta issue", ["meta"])]
        result = self.mod.apply_exclusion_filter(issues)
        assert result == []

    def test_umbrella_label_excluded(self) -> None:
        """Issue with 'umbrella' label is excluded."""
        issues = [_make_issue(5, "Umbrella", ["umbrella"])]
        result = self.mod.apply_exclusion_filter(issues)
        assert result == []

    def test_needs_design_label_excluded(self) -> None:
        """Issue with 'needs-design' label is excluded."""
        issues = [_make_issue(6, "Needs design", ["needs-design"])]
        result = self.mod.apply_exclusion_filter(issues)
        assert result == []

    def test_needs_discussion_label_excluded(self) -> None:
        """Issue with 'needs-discussion' label is excluded."""
        issues = [_make_issue(7, "Needs discussion", ["needs-discussion"])]
        result = self.mod.apply_exclusion_filter(issues)
        assert result == []

    def test_wontfix_label_excluded(self) -> None:
        """Issue with 'wontfix' label is excluded."""
        issues = [_make_issue(8, "Won't fix", ["wontfix"])]
        result = self.mod.apply_exclusion_filter(issues)
        assert result == []

    def test_non_excluded_label_passes(self) -> None:
        """Issue with an unrelated label is not excluded."""
        issues = [_make_issue(9, "Good issue", ["documentation"])]
        result = self.mod.apply_exclusion_filter(issues)
        assert len(result) == 1
        assert result[0]["number"] == 9

    def test_mix_of_excluded_and_passing(self) -> None:
        """Only non-excluded issues survive when mixed with excluded ones."""
        issues = [
            _make_issue(1, "Blocked", ["blocked"]),
            _make_issue(2, "Good", ["documentation"]),
            _make_issue(3, "Epic", ["epic"]),
        ]
        result = self.mod.apply_exclusion_filter(issues)
        assert len(result) == 1
        assert result[0]["number"] == 2


# ---------------------------------------------------------------------------
# Tests: exclusion filter — assignee-based
# ---------------------------------------------------------------------------


class TestExclusionByAssignee:
    """Issues with any assignee must be excluded."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_quick_wins()

    def test_issue_with_assignee_excluded(self) -> None:
        """Issue assigned to anyone is excluded."""
        issues = [_make_issue(10, "Assigned issue", [], assignees=["alice"])]
        result = self.mod.apply_exclusion_filter(issues)
        assert result == []

    def test_issue_with_multiple_assignees_excluded(self) -> None:
        """Issue with multiple assignees is excluded."""
        issues = [
            _make_issue(11, "Multi-assigned", [], assignees=["alice", "bob"])
        ]
        result = self.mod.apply_exclusion_filter(issues)
        assert result == []

    def test_unassigned_issue_passes(self) -> None:
        """Issue with no assignees passes the filter."""
        issues = [_make_issue(12, "No assignee", [], assignees=[])]
        result = self.mod.apply_exclusion_filter(issues)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Tests: exclusion filter — AC checkbox count
# ---------------------------------------------------------------------------


class TestExclusionByCheckboxCount:
    """Issues with more than max_ac checkboxes must be excluded."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_quick_wins()

    def _body_with_checkboxes(self, count: int) -> str:
        """Build a body with exactly ``count`` unchecked checkboxes.

        Args:
            count: Number of ``- [ ]`` lines to include.

        Returns:
            Markdown body string.
        """
        lines = [f"- [ ] Item {i}" for i in range(count)]
        return "\n".join(lines)

    def test_five_checkboxes_passes_default_threshold(self) -> None:
        """Exactly 5 checkboxes passes the default threshold of > 5."""
        body = self._body_with_checkboxes(5)
        issues = [_make_issue(20, "Five ACs", [], body=body)]
        result = self.mod.apply_exclusion_filter(issues)
        assert len(result) == 1

    def test_six_checkboxes_excluded_by_default(self) -> None:
        """Six checkboxes exceeds the default threshold and is excluded."""
        body = self._body_with_checkboxes(6)
        issues = [_make_issue(21, "Six ACs", [], body=body)]
        result = self.mod.apply_exclusion_filter(issues)
        assert result == []

    def test_custom_max_ac_threshold(self) -> None:
        """Custom max_ac overrides the default exclusion threshold."""
        body = self._body_with_checkboxes(3)
        issues = [_make_issue(22, "Three ACs", [], body=body)]
        # With max_ac=2, 3 checkboxes should be excluded
        result = self.mod.apply_exclusion_filter(issues, max_ac=2)
        assert result == []

    def test_checked_checkboxes_also_count(self) -> None:
        """Checked checkboxes (- [x]) count toward the exclusion total."""
        body = (
            "- [x] Done 1\n- [x] Done 2\n- [x] Done 3\n"
            "- [ ] Todo 4\n- [ ] Todo 5\n- [ ] Todo 6"
        )
        issues = [_make_issue(23, "Mixed checkboxes", [], body=body)]
        result = self.mod.apply_exclusion_filter(issues)
        assert result == []


# ---------------------------------------------------------------------------
# Tests: exclusion filter — blocked-by prose
# ---------------------------------------------------------------------------


class TestExclusionByBlockedProse:
    """Issues with 'blocked by #N' or 'depends on #N' prose are excluded."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_quick_wins()

    def test_blocked_by_prose_excluded(self) -> None:
        """Body containing 'blocked by #123' is excluded."""
        issues = [
            _make_issue(
                30,
                "Has blocker",
                [],
                body="This is blocked by #123 merging first.",
            )
        ]
        result = self.mod.apply_exclusion_filter(issues)
        assert result == []

    def test_depends_on_prose_excluded(self) -> None:
        """Body containing 'depends on #456' is excluded."""
        issues = [
            _make_issue(
                31,
                "Has dependency",
                [],
                body="This depends on #456 before we can proceed.",
            )
        ]
        result = self.mod.apply_exclusion_filter(issues)
        assert result == []

    def test_blocked_by_case_insensitive(self) -> None:
        """'Blocked By #N' (title case) is also excluded."""
        issues = [
            _make_issue(
                32,
                "Case insensitive",
                [],
                body="Blocked By #99 to complete.",
            )
        ]
        result = self.mod.apply_exclusion_filter(issues)
        assert result == []

    def test_plain_blocked_mention_without_hash_passes(self) -> None:
        """'blocked by' without an issue reference does not trigger exclusion."""
        issues = [
            _make_issue(
                33,
                "Unblocked",
                [],
                body="This is not blocked by external work.",
            )
        ]
        result = self.mod.apply_exclusion_filter(issues)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Tests: signals computation
# ---------------------------------------------------------------------------


class TestSignalsComputation:
    """The ``signals`` sub-object must contain correct computed values."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_quick_wins()

    def test_signals_object_present(self) -> None:
        """Surviving issue has a 'signals' key."""
        issues = [_make_issue(40, "Plain issue", [])]
        result = self.mod.apply_exclusion_filter(issues)
        assert len(result) == 1
        assert "signals" in result[0]

    def test_signals_has_required_keys(self) -> None:
        """Signals object contains all four required fields."""
        issues = [_make_issue(41, "Signals issue", [])]
        result = self.mod.apply_exclusion_filter(issues)
        signals = result[0]["signals"]
        assert "touches_load_bearing" in signals
        assert "body_appears_drafted" in signals
        assert "comment_count" in signals
        assert "days_since_update" in signals

    def test_comment_count_derived_from_comments_array_length(self) -> None:
        """comment_count equals the length of the comments array."""
        issues = [_make_issue(42, "Commented issue", [], comments=5)]
        result = self.mod.apply_exclusion_filter(issues)
        assert result[0]["signals"]["comment_count"] == 5

    def test_comment_count_zero_when_no_comments(self) -> None:
        """comment_count is 0 when comments array is empty."""
        issues = [_make_issue(43, "Uncommented", [], comments=0)]
        result = self.mod.apply_exclusion_filter(issues)
        assert result[0]["signals"]["comment_count"] == 0

    def test_comment_count_handles_list_shape_for_gh_issue_view(
        self,
    ) -> None:
        """_comment_count handles array-of-objects shape from gh issue view."""
        issue = {
            "number": 100,
            "title": "List-shape comments",
            "labels": [],
            "body": "",
            "assignees": [],
            "comments": [{"id": 1}, {"id": 2}, {"id": 3}],
            "updatedAt": "2026-05-27T00:00:00Z",
            "url": "https://github.com/owner/repo/issues/100",
            "milestone": None,
        }
        result = self.mod.apply_exclusion_filter([issue])
        assert result[0]["signals"]["comment_count"] == 3

    def test_days_since_update_computed_from_updated_at(self) -> None:
        """days_since_update is a non-negative integer derived from updatedAt."""
        issues = [
            _make_issue(
                44,
                "Old issue",
                [],
                updated_at="2020-01-01T00:00:00Z",
            )
        ]
        result = self.mod.apply_exclusion_filter(issues)
        days = result[0]["signals"]["days_since_update"]
        assert isinstance(days, int)
        assert days > 365  # definitely more than a year ago

    def test_label_set_contains_issue_labels(self) -> None:
        """label_set contains the names of all labels on the issue."""
        issues = [_make_issue(45, "Labeled", ["bug", "documentation"])]
        result = self.mod.apply_exclusion_filter(issues)
        assert set(result[0]["signals"]["label_set"]) == {
            "bug",
            "documentation",
        }


# ---------------------------------------------------------------------------
# Tests: touches_load_bearing signal
# ---------------------------------------------------------------------------


class TestTouchesLoadBearing:
    """touches_load_bearing is True when body or title mentions load-bearing
    paths."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_quick_wins()

    def test_claude_md_in_body(self) -> None:
        """Body mentioning CLAUDE.md sets touches_load_bearing=True."""
        issues = [
            _make_issue(50, "Update something", [], body="Edit CLAUDE.md rules")
        ]
        result = self.mod.apply_exclusion_filter(issues)
        assert result[0]["signals"]["touches_load_bearing"] is True

    def test_agents_path_in_title(self) -> None:
        """Title mentioning 'agents/' sets touches_load_bearing=True."""
        issues = [_make_issue(51, "Fix agents/ config", [])]
        result = self.mod.apply_exclusion_filter(issues)
        assert result[0]["signals"]["touches_load_bearing"] is True

    def test_skills_path_in_body(self) -> None:
        """Body mentioning 'skills/' sets touches_load_bearing=True."""
        issues = [
            _make_issue(52, "Update skill", [], body="Add a new skills/ entry")
        ]
        result = self.mod.apply_exclusion_filter(issues)
        assert result[0]["signals"]["touches_load_bearing"] is True

    def test_hooks_in_body(self) -> None:
        """Body mentioning 'hooks/' sets touches_load_bearing=True."""
        issues = [
            _make_issue(53, "Hook issue", [], body="Modify hooks/ behavior")
        ]
        result = self.mod.apply_exclusion_filter(issues)
        assert result[0]["signals"]["touches_load_bearing"] is True

    def test_deploy_py_in_body(self) -> None:
        """Body mentioning 'deploy.py' sets touches_load_bearing=True."""
        issues = [
            _make_issue(54, "Deploy fix", [], body="Change deploy.py logic")
        ]
        result = self.mod.apply_exclusion_filter(issues)
        assert result[0]["signals"]["touches_load_bearing"] is True

    def test_unrelated_body_is_false(self) -> None:
        """Body with no load-bearing paths sets touches_load_bearing=False."""
        issues = [
            _make_issue(
                55,
                "Add README example",
                [],
                body="Add an example to the README",
            )
        ]
        result = self.mod.apply_exclusion_filter(issues)
        assert result[0]["signals"]["touches_load_bearing"] is False


# ---------------------------------------------------------------------------
# Tests: body_appears_drafted signal
# ---------------------------------------------------------------------------


class TestBodyAppearsDrafted:
    """body_appears_drafted is True for long bodies with structural content."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_quick_wins()

    def test_short_body_is_false(self) -> None:
        """Short body (< 800 chars) is not considered drafted."""
        issues = [_make_issue(60, "Short issue", [], body="Brief description.")]
        result = self.mod.apply_exclusion_filter(issues)
        assert result[0]["signals"]["body_appears_drafted"] is False

    def test_long_body_with_code_block_is_true(self) -> None:
        """Body > 800 chars with a fenced code block is considered drafted."""
        body = "x" * 801 + "\n```python\nprint('hello')\n```"
        issues = [_make_issue(61, "Code issue", [], body=body)]
        result = self.mod.apply_exclusion_filter(issues)
        assert result[0]["signals"]["body_appears_drafted"] is True

    def test_long_body_with_table_is_true(self) -> None:
        """Body > 800 chars with a markdown table is considered drafted."""
        body = "x" * 801 + "\n| Col1 | Col2 |\n|---|---|\n| a | b |"
        issues = [_make_issue(62, "Table issue", [], body=body)]
        result = self.mod.apply_exclusion_filter(issues)
        assert result[0]["signals"]["body_appears_drafted"] is True

    def test_long_body_with_many_headers_is_true(self) -> None:
        """Body > 800 chars with > 3 ### headers is considered drafted."""
        headers = "\n### Sec1\n### Sec2\n### Sec3\n### Sec4\n"
        body = "x" * 801 + headers
        issues = [_make_issue(63, "Headers issue", [], body=body)]
        result = self.mod.apply_exclusion_filter(issues)
        assert result[0]["signals"]["body_appears_drafted"] is True

    def test_long_body_without_structural_content_is_false(self) -> None:
        """Long body (> 800 chars) with no code/table/headers is not drafted."""
        body = "x" * 900  # long but structureless prose
        issues = [_make_issue(64, "Long prose", [], body=body)]
        result = self.mod.apply_exclusion_filter(issues)
        assert result[0]["signals"]["body_appears_drafted"] is False


# ---------------------------------------------------------------------------
# Tests: zero survivors
# ---------------------------------------------------------------------------


class TestZeroSurvivors:
    """When all issues are excluded, output is [] and exit code is 0."""

    def test_all_excluded_returns_empty_list(self) -> None:
        """apply_exclusion_filter returns empty list when all excluded."""
        mod = _load_quick_wins()
        issues = [
            _make_issue(70, "Epic", ["epic"]),
            _make_issue(71, "Blocked", ["blocked"]),
        ]
        result = mod.apply_exclusion_filter(issues)
        assert result == []


# ---------------------------------------------------------------------------
# Tests: main() entry point
# ---------------------------------------------------------------------------


def test_main_exits_0_on_success() -> None:
    """main() exits 0 when gh returns valid data."""
    mod = _load_quick_wins()
    issues = [_make_issue(80, "Good issue", ["documentation"])]
    mock_result = _make_gh_response(issues)
    with patch("subprocess.run", return_value=mock_result):
        exit_code = mod.main(["--repo", "owner/repo"])
    assert exit_code == 0


def test_main_emits_json_on_stdout(capsys) -> None:
    """main() prints valid JSON to stdout."""
    mod = _load_quick_wins()
    issues = [_make_issue(81, "Output issue", [])]
    mock_result = _make_gh_response(issues)
    with patch("subprocess.run", return_value=mock_result):
        mod.main(["--repo", "owner/repo"])
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert isinstance(parsed, list)


def test_main_exits_0_with_empty_json_when_all_excluded(capsys) -> None:
    """main() exits 0 and emits [] when all issues are excluded."""
    mod = _load_quick_wins()
    issues = [_make_issue(82, "Epic", ["epic"])]
    mock_result = _make_gh_response(issues)
    with patch("subprocess.run", return_value=mock_result):
        exit_code = mod.main(["--repo", "owner/repo"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == []


def test_main_exits_nonzero_on_gh_failure() -> None:
    """main() exits non-zero when gh returns a non-zero exit code."""
    mod = _load_quick_wins()
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "authentication required"
    with patch("subprocess.run", return_value=mock_result):
        exit_code = mod.main(["--repo", "owner/repo"])
    assert exit_code != 0


def test_main_exits_nonzero_on_malformed_gh_json() -> None:
    """main() exits non-zero when gh returns success but malformed JSON."""
    mod = _load_quick_wins()
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "{not valid json"
    mock_result.stderr = ""
    with patch("subprocess.run", return_value=mock_result):
        exit_code = mod.main(["--repo", "owner/repo"])
    assert exit_code != 0


def test_surviving_issues_have_signals(capsys) -> None:
    """Surviving issues in main() output have a 'signals' key."""
    mod = _load_quick_wins()
    issues = [_make_issue(83, "Good issue", [], comments=2)]
    mock_result = _make_gh_response(issues)
    with patch("subprocess.run", return_value=mock_result):
        mod.main(["--repo", "owner/repo"])
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert len(parsed) == 1
    assert "signals" in parsed[0]
    assert parsed[0]["signals"]["comment_count"] == 2


# ---------------------------------------------------------------------------
# Tests: checkbox regex — alternate bullet styles
# ---------------------------------------------------------------------------


class TestCheckboxBulletStyles:
    """Checkboxes using *, + bullets must be counted identically to -
    bullets."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_quick_wins()

    def test_asterisk_unchecked_counted(self) -> None:
        """'* [ ] item' unchecked checkbox is counted by the filter."""
        body = "\n".join(f"* [ ] Item {i}" for i in range(6))
        issues = [_make_issue(200, "Asterisk unchecked", [], body=body)]
        result = self.mod.apply_exclusion_filter(issues)
        assert result == [], "6 '* [ ]' checkboxes should exceed max_ac=5"

    def test_asterisk_checked_counted(self) -> None:
        """'* [x] item' checked checkbox is counted by the filter."""
        body = "\n".join(f"* [x] Done {i}" for i in range(6))
        issues = [_make_issue(201, "Asterisk checked", [], body=body)]
        result = self.mod.apply_exclusion_filter(issues)
        assert result == [], "6 '* [x]' checkboxes should exceed max_ac=5"

    def test_plus_unchecked_counted(self) -> None:
        """'+ [ ] item' unchecked checkbox is counted by the filter."""
        body = "\n".join(f"+ [ ] Item {i}" for i in range(6))
        issues = [_make_issue(202, "Plus unchecked", [], body=body)]
        result = self.mod.apply_exclusion_filter(issues)
        assert result == [], "6 '+ [ ]' checkboxes should exceed max_ac=5"

    def test_plus_checked_counted(self) -> None:
        """'+ [x] item' checked checkbox is counted by the filter."""
        body = "\n".join(f"+ [x] Done {i}" for i in range(6))
        issues = [_make_issue(203, "Plus checked", [], body=body)]
        result = self.mod.apply_exclusion_filter(issues)
        assert result == [], "6 '+ [x]' checkboxes should exceed max_ac=5"

    def test_mixed_bullet_styles_counted_together(self) -> None:
        """Mixed -, *, + bullets all count toward the same threshold."""
        body = (
            "- [ ] Dash item 1\n"
            "- [ ] Dash item 2\n"
            "* [ ] Star item 1\n"
            "* [ ] Star item 2\n"
            "+ [ ] Plus item 1\n"
            "+ [ ] Plus item 2\n"
        )
        issues = [_make_issue(204, "Mixed bullets", [], body=body)]
        result = self.mod.apply_exclusion_filter(issues)
        assert result == [], (
            "Mixed bullets summing to 6 should exceed max_ac=5"
        )

    def test_five_mixed_bullets_passes(self) -> None:
        """Exactly 5 mixed-style checkboxes does not exceed the threshold."""
        body = (
            "- [ ] Dash item\n"
            "* [ ] Star item\n"
            "+ [ ] Plus item\n"
            "* [x] Star done\n"
            "+ [x] Plus done\n"
        )
        issues = [_make_issue(205, "Five mixed bullets", [], body=body)]
        result = self.mod.apply_exclusion_filter(issues)
        assert len(result) == 1, "Exactly 5 checkboxes should pass max_ac=5"


# ---------------------------------------------------------------------------
# Tests: full-backlog pagination
# ---------------------------------------------------------------------------


def _make_paginated_gh_api_response(
    pages: list[list[dict]],
) -> MagicMock:
    """Build a mock subprocess.run return value for gh api --paginate --slurp.

    Args:
        pages: List of pages; each page is a list of issue dicts.

    Returns:
        A MagicMock whose stdout is the ``--slurp`` JSON shape
        (array-of-arrays).
    """
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = json.dumps(pages)
    mock.stderr = ""
    return mock


class TestPaginatedFetch:
    """fetch_issues must page through the full backlog."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_quick_wins()

    def test_all_issues_from_multiple_pages_are_returned(self) -> None:
        """fetch_issues returns issues from all pages combined."""
        page1 = [_make_issue(i, f"Page-1 issue {i}", []) for i in range(1, 101)]
        page2 = [
            _make_issue(i, f"Page-2 issue {i}", []) for i in range(101, 201)
        ]
        page3 = [
            _make_issue(i, f"Page-3 issue {i}", []) for i in range(201, 251)
        ]
        mock_result = _make_paginated_gh_api_response([page1, page2, page3])

        with patch("subprocess.run", return_value=mock_result):
            issues = self.mod.fetch_issues(repo="owner/repo", limit=5000)

        assert len(issues) == 250

    def test_prs_are_filtered_out_from_gh_api_response(self) -> None:
        """Items with a 'pull_request' key are excluded from results."""
        real_issue = _make_issue(1, "Real issue", [])
        pr_item = {
            **_make_issue(2, "A pull request", []),
            "pull_request": {
                "url": "https://github.com/owner/repo/pull/2"
            },
        }
        mock_result = _make_paginated_gh_api_response([[real_issue, pr_item]])

        with patch("subprocess.run", return_value=mock_result):
            issues = self.mod.fetch_issues(repo="owner/repo", limit=5000)

        assert len(issues) == 1
        assert issues[0]["number"] == 1

    def test_fetch_issues_uses_gh_api_paginate_flag(self) -> None:
        """fetch_issues invokes gh api with --paginate flag."""
        mock_result = _make_paginated_gh_api_response(
            [[_make_issue(1, "x", [])]]
        )

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            self.mod.fetch_issues(repo="owner/repo", limit=5000)

        call_args = mock_run.call_args
        cmd = call_args[0][0]
        cmd_str = " ".join(str(c) for c in cmd)
        assert "--paginate" in cmd_str, (
            f"Expected --paginate in command, got: {cmd_str}"
        )


# ---------------------------------------------------------------------------
# Tests: REST field normalization
# ---------------------------------------------------------------------------


def _make_rest_issue(
    number: int,
    title: str,
    labels: list[str],
    body: str = "",
    assignees: list[str] | None = None,
    comments: int = 0,
    updated_at: str = "2026-05-01T00:00:00Z",
    html_url: str | None = None,
) -> dict:
    """Build a REST-shaped issue dict matching the GitHub /issues API response.

    Args:
        number: Issue number.
        title: Issue title.
        labels: List of label name strings.
        body: Issue body markdown.
        assignees: List of assignee login strings. Defaults to empty.
        comments: Number of comments (scalar int, REST API shape).
        updated_at: ISO 8601 timestamp in REST format (``updated_at`` key).
        html_url: HTML URL. Defaults to a generated GitHub URL.

    Returns:
        Dict matching the shape returned by ``GET /repos/{owner}/{repo}/issues``.
    """
    return {
        "number": number,
        "title": title,
        "labels": [{"name": lbl} for lbl in labels],
        "body": body,
        "assignees": [{"login": a} for a in (assignees or [])],
        "comments": comments,
        "updated_at": updated_at,
        "html_url": html_url
        or f"https://github.com/owner/repo/issues/{number}",
        "milestone": None,
    }


class TestRestFieldNormalization:
    """fetch_issues must normalize REST snake_case fields to camelCase."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_quick_wins()

    def test_normalize_rest_issue_maps_updated_at_to_updated_at_camel(
        self,
    ) -> None:
        """_normalize_rest_issue copies updated_at into updatedAt."""
        raw = _make_rest_issue(1, "REST issue", [])
        normalized = self.mod._normalize_rest_issue(raw)
        assert normalized["updatedAt"] == raw["updated_at"]

    def test_normalize_rest_issue_maps_html_url_to_url(self) -> None:
        """_normalize_rest_issue copies html_url into url."""
        raw = _make_rest_issue(2, "REST issue", [])
        normalized = self.mod._normalize_rest_issue(raw)
        assert normalized["url"] == raw["html_url"]

    def test_normalize_rest_issue_preserves_other_fields(self) -> None:
        """_normalize_rest_issue leaves all other fields intact."""
        raw = _make_rest_issue(3, "REST preserve", ["bug"], body="Some body")
        normalized = self.mod._normalize_rest_issue(raw)
        assert normalized["number"] == 3
        assert normalized["title"] == "REST preserve"
        assert normalized["body"] == "Some body"
        assert normalized["labels"] == [{"name": "bug"}]

    def test_fetch_issues_normalizes_rest_fields(self) -> None:
        """fetch_issues returns issues with updatedAt and url fields set."""
        rest_issue = _make_rest_issue(
            10,
            "REST shaped issue",
            [],
            updated_at="2024-03-15T12:00:00Z",
            html_url="https://github.com/owner/repo/issues/10",
        )
        mock_result = _make_paginated_gh_api_response([[rest_issue]])

        with patch("subprocess.run", return_value=mock_result):
            issues = self.mod.fetch_issues(repo="owner/repo", limit=5000)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.get("updatedAt") == "2024-03-15T12:00:00Z", (
            "updatedAt missing — REST updated_at not normalized"
        )
        assert (
            issue.get("url") == "https://github.com/owner/repo/issues/10"
        ), "url missing — REST html_url not normalized"

    def test_fetch_issues_rest_fields_enable_freshness_ranking(
        self,
    ) -> None:
        """Normalized REST responses allow days_since_update to rank correctly."""
        old_issue = _make_rest_issue(
            20, "Old issue", [], updated_at="2020-01-01T00:00:00Z"
        )
        new_issue = _make_rest_issue(
            21, "New issue", [], updated_at="2026-05-26T00:00:00Z"
        )
        mock_result = _make_paginated_gh_api_response(
            [[old_issue, new_issue]]
        )

        with patch("subprocess.run", return_value=mock_result):
            issues = self.mod.fetch_issues(repo="owner/repo", limit=5000)

        survivors = self.mod.apply_exclusion_filter(issues)
        by_number = {s["number"]: s for s in survivors}

        old_days = by_number[20]["signals"]["days_since_update"]
        new_days = by_number[21]["signals"]["days_since_update"]

        assert old_days > 365, (
            f"Old issue should have days_since_update > 365, got {old_days}"
        )
        assert new_days < 30, (
            f"New issue should have days_since_update < 30, got {new_days}"
        )
        assert old_days > new_days, (
            "Old issue must rank as staler than new issue"
        )
