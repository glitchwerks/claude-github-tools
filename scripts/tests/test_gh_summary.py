"""Tests for scripts/gh-summary.py and scripts/_gh_common.py.

Covers the key behaviors of the roadmap snapshot generator:
  - Epic detection: [Umbrella] title prefix AND meta label required
  - Checklist parse: - [x] vs - [ ] ratio; checklist absent fallback
  - Milestone completion: native API ratio formatting
  - Milestone descriptions: rendered in table, truncated at 100 chars
  - Orphan detection: no milestone AND not in any epic checklist #N
  - Output structure: 1-line summary, tables, empty-section handling
  - render_table: headers/rows rendered as markdown; empty rows → ""
  - run_gh_api: raises clear error on nonzero gh exit
  - _render_critical_issues: blocked/security/bug filter, configurable
  - _render_recent_releases: release table, empty repo → None, date format
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
SUMMARY_SCRIPT = SCRIPTS_DIR / "gh-summary.py"


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


def _load_summary() -> ModuleType:
    """Import gh-summary as a module, injecting the common module.

    Returns:
        The loaded gh_summary module object.
    """
    # _gh_common must be importable when gh-summary is loaded
    common_spec = importlib.util.spec_from_file_location(
        "_gh_common", COMMON_SCRIPT
    )
    assert common_spec is not None and common_spec.loader is not None
    common_mod = importlib.util.module_from_spec(common_spec)
    sys.modules["_gh_common"] = common_mod
    common_spec.loader.exec_module(common_mod)  # type: ignore[union-attr]

    spec = importlib.util.spec_from_file_location(
        "gh_summary", SUMMARY_SCRIPT
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
    body: str = "",
) -> dict[str, Any]:
    """Build a fake GitHub issue dict.

    Args:
        number: Issue number.
        title: Issue title string.
        labels: List of label name strings.
        milestone: Milestone dict or None.
        body: Issue body markdown text.

    Returns:
        Dict shaped like a GitHub REST API issue object.
    """
    return {
        "number": number,
        "title": title,
        "labels": [{"name": lbl} for lbl in labels],
        "milestone": milestone,
        "body": body,
        "updated_at": "2026-05-01T00:00:00Z",
        "html_url": f"https://github.com/owner/repo/issues/{number}",
    }


def _make_milestone(
    number: int,
    title: str,
    open_issues: int,
    closed_issues: int,
    description: str = "",
) -> dict[str, Any]:
    """Build a fake GitHub milestone dict.

    Args:
        number: Milestone number.
        title: Milestone title string.
        open_issues: Count of open issues.
        closed_issues: Count of closed issues.
        description: Optional milestone description (default empty string).

    Returns:
        Dict shaped like a GitHub REST API milestone object.
    """
    return {
        "number": number,
        "title": title,
        "open_issues": open_issues,
        "closed_issues": closed_issues,
        "description": description,
    }


def _make_completed_process(
    stdout: Any, returncode: int = 0
) -> MagicMock:
    """Build a fake subprocess.CompletedProcess for patching.

    Args:
        stdout: Value to set as the stdout attribute (serialized to JSON
            if not already a string).
        returncode: Exit code.

    Returns:
        MagicMock simulating subprocess.CompletedProcess.
    """
    cp = MagicMock()
    cp.returncode = returncode
    cp.stdout = json.dumps(stdout) if not isinstance(stdout, str) else stdout
    cp.stderr = ""
    return cp


# ---------------------------------------------------------------------------
# TestRenderTable — _gh_common.render_table
# ---------------------------------------------------------------------------


class TestRenderTable:
    """Tests for the shared render_table helper in _gh_common."""

    def test_renders_header_and_rows(self) -> None:
        """render_table produces a markdown table with header and rows."""
        mod = _load_common()
        result = mod.render_table(
            ["Col A", "Col B"],
            [["r1c1", "r1c2"], ["r2c1", "r2c2"]],
        )
        assert "| Col A | Col B |" in result
        assert "| r1c1 | r1c2 |" in result
        assert "| r2c1 | r2c2 |" in result

    def test_empty_rows_returns_empty_string(self) -> None:
        """render_table with no rows returns an empty string."""
        mod = _load_common()
        result = mod.render_table(["A", "B"], [])
        assert result == ""

    def test_separator_row_present(self) -> None:
        """render_table includes a separator row after the header."""
        mod = _load_common()
        result = mod.render_table(["X"], [["v"]])
        lines = result.splitlines()
        assert lines[1].startswith("|")
        assert "---" in lines[1]

    def test_right_align_column(self) -> None:
        """Columns with align='right' use ---: separator."""
        mod = _load_common()
        result = mod.render_table(
            ["Num"],
            [["42"]],
            align=["right"],
        )
        assert "---:" in result

    def test_center_align_column(self) -> None:
        """Columns with align='center' use :---: separator."""
        mod = _load_common()
        result = mod.render_table(
            ["Ctr"],
            [["v"]],
            align=["center"],
        )
        assert ":---:" in result


# ---------------------------------------------------------------------------
# TestRunGhApi — _gh_common.run_gh_api
# ---------------------------------------------------------------------------


class TestRunGhApi:
    """Tests for run_gh_api in _gh_common."""

    def test_returns_parsed_json_on_success(self) -> None:
        """run_gh_api parses and returns JSON from gh stdout."""
        mod = _load_common()
        payload = [{"number": 1, "title": "Test"}]
        cp = _make_completed_process(payload, returncode=0)
        with patch("subprocess.run", return_value=cp):
            result = mod.run_gh_api("repos/owner/repo/issues")
        assert result == payload

    def test_raises_on_nonzero_exit(self) -> None:
        """run_gh_api raises RuntimeError with stderr text on failure."""
        mod = _load_common()
        cp = MagicMock()
        cp.returncode = 1
        cp.stdout = ""
        cp.stderr = "gh: authentication required"
        with patch("subprocess.run", return_value=cp):
            try:
                mod.run_gh_api("repos/owner/repo/issues")
                assert False, "Expected RuntimeError"
            except RuntimeError as exc:
                assert "authentication required" in str(exc)

    def test_paginate_flag_passed(self) -> None:
        """run_gh_api with paginate=True passes --paginate to gh."""
        mod = _load_common()
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp) as mock_run:
            mod.run_gh_api("repos/owner/repo/issues", paginate=True)
        call_args = mock_run.call_args[0][0]
        assert "--paginate" in call_args

    def test_jq_filter_passed(self) -> None:
        """run_gh_api with jq='.foo' passes --jq to gh."""
        mod = _load_common()
        cp = _make_completed_process({"foo": "bar"}, returncode=0)
        with patch("subprocess.run", return_value=cp) as mock_run:
            mod.run_gh_api("repos/owner/repo/issues", jq=".foo")
        call_args = mock_run.call_args[0][0]
        assert "--jq" in call_args
        assert ".foo" in call_args


# ---------------------------------------------------------------------------
# TestEpicDetection — is_epic / detect_epics logic
# ---------------------------------------------------------------------------


class TestEpicDetection:
    """Epic detection: [Umbrella] title prefix AND meta label required."""

    def test_issue_with_umbrella_prefix_and_meta_label_is_epic(
        self,
    ) -> None:
        """Issue with [Umbrella] title AND meta label counts as an epic."""
        mod = _load_summary()
        issue = _make_issue(1, "[Umbrella] My epic", ["meta", "enhancement"])
        result = mod.is_epic(issue)
        assert result is True

    def test_issue_missing_meta_label_is_not_epic(self) -> None:
        """Issue with [Umbrella] title but without meta label is not epic."""
        mod = _load_summary()
        issue = _make_issue(2, "[Umbrella] Not an epic", ["enhancement"])
        result = mod.is_epic(issue)
        assert result is False

    def test_issue_missing_umbrella_prefix_is_not_epic(self) -> None:
        """Issue with meta label but without [Umbrella] prefix is not epic."""
        mod = _load_summary()
        issue = _make_issue(3, "Regular issue", ["meta"])
        result = mod.is_epic(issue)
        assert result is False

    def test_umbrella_prefix_is_case_sensitive(self) -> None:
        """[umbrella] prefix (lowercase) does not qualify as [Umbrella]."""
        mod = _load_summary()
        issue = _make_issue(4, "[umbrella] Case test", ["meta"])
        result = mod.is_epic(issue)
        assert result is False

    def test_other_bracket_prefix_not_umbrella(self) -> None:
        """Legacy bracket prefixes like [CI Hardening] are not [Umbrella]."""
        mod = _load_summary()
        issue = _make_issue(5, "[CI Hardening] Not an umbrella", ["meta"])
        result = mod.is_epic(issue)
        assert result is False

    def test_both_conditions_required(self) -> None:
        """Both [Umbrella] prefix AND meta label must be present."""
        mod = _load_summary()
        no_meta = _make_issue(6, "[Umbrella] No meta", [])
        assert mod.is_epic(no_meta) is False
        no_prefix = _make_issue(7, "Has meta but no prefix", ["meta"])
        assert mod.is_epic(no_prefix) is False


# ---------------------------------------------------------------------------
# TestChecklistParse — parse_checklist
# ---------------------------------------------------------------------------


class TestChecklistParse:
    """Checklist extraction from issue body markdown."""

    def test_counts_checked_and_total(self) -> None:
        """parse_checklist returns (checked, total) for body with boxes."""
        mod = _load_summary()
        body = "- [x] Done\n- [ ] Todo\n- [x] Also done\n"
        checked, total = mod.parse_checklist(body)
        assert checked == 2
        assert total == 3

    def test_all_unchecked(self) -> None:
        """parse_checklist returns (0, N) when no boxes are checked."""
        mod = _load_summary()
        body = "- [ ] A\n- [ ] B\n"
        checked, total = mod.parse_checklist(body)
        assert checked == 0
        assert total == 2

    def test_all_checked(self) -> None:
        """parse_checklist returns (N, N) when all boxes are checked."""
        mod = _load_summary()
        body = "- [x] A\n- [x] B\n- [x] C\n"
        checked, total = mod.parse_checklist(body)
        assert checked == 3
        assert total == 3

    def test_no_checkboxes_returns_none(self) -> None:
        """parse_checklist returns None when no checkbox syntax found."""
        mod = _load_summary()
        body = "Some text\n- regular list item\n"
        result = mod.parse_checklist(body)
        assert result is None

    def test_empty_body_returns_none(self) -> None:
        """parse_checklist returns None for an empty body."""
        mod = _load_summary()
        result = mod.parse_checklist("")
        assert result is None

    def test_none_body_returns_none(self) -> None:
        """parse_checklist returns None when body is None."""
        mod = _load_summary()
        result = mod.parse_checklist(None)
        assert result is None

    def test_indented_checkboxes_counted(self) -> None:
        """Indented checkbox lines (e.g. nested lists) are also counted."""
        mod = _load_summary()
        body = "  - [x] Nested done\n  - [ ] Nested todo\n"
        checked, total = mod.parse_checklist(body)
        assert checked == 1
        assert total == 2


# ---------------------------------------------------------------------------
# TestMilestoneCompletion — format_milestone_completion
# ---------------------------------------------------------------------------


class TestMilestoneCompletion:
    """Milestone completion ratio formatting."""

    def test_partial_completion(self) -> None:
        """Milestone with 2 closed / 4 total renders as '2/4 (50%)'."""
        mod = _load_summary()
        ms = _make_milestone(1, "Sprint 1", open_issues=2, closed_issues=2)
        result = mod.format_milestone_completion(ms)
        assert "2/4" in result
        assert "50%" in result

    def test_fully_closed_milestone(self) -> None:
        """Milestone with all closed renders as 100%."""
        mod = _load_summary()
        ms = _make_milestone(2, "Done", open_issues=0, closed_issues=5)
        result = mod.format_milestone_completion(ms)
        assert "5/5" in result
        assert "100%" in result

    def test_zero_issues_milestone(self) -> None:
        """Milestone with 0 total issues renders '0/0' without division."""
        mod = _load_summary()
        ms = _make_milestone(3, "Empty", open_issues=0, closed_issues=0)
        result = mod.format_milestone_completion(ms)
        assert "0/0" in result


# ---------------------------------------------------------------------------
# TestOrphanDetection — detect_orphans
# ---------------------------------------------------------------------------


class TestOrphanDetection:
    """Orphan = open issue with no milestone AND not in any epic checklist."""

    def test_issue_with_no_milestone_and_not_in_checklist_is_orphan(
        self,
    ) -> None:
        """Issue with no milestone and not referenced by any epic is orphan."""
        mod = _load_summary()
        issue = _make_issue(10, "Orphan", [], milestone=None)
        epics: list[dict[str, Any]] = []
        orphans = mod.detect_orphans([issue], epics)
        assert any(o["number"] == 10 for o in orphans)

    def test_issue_with_milestone_is_not_orphan(self) -> None:
        """Issue with a milestone assigned is never an orphan."""
        mod = _load_summary()
        ms = {"number": 1, "title": "Sprint 1"}
        issue = _make_issue(11, "Has milestone", [], milestone=ms)
        orphans = mod.detect_orphans([issue], [])
        assert not any(o["number"] == 11 for o in orphans)

    def test_issue_referenced_in_epic_checklist_is_not_orphan(
        self,
    ) -> None:
        """Issue referenced in an epic checklist (#N) is not orphaned."""
        mod = _load_summary()
        issue = _make_issue(12, "Referenced", [], milestone=None)
        epic = _make_issue(
            100,
            "[Umbrella] Epic",
            ["meta"],
            body="- [ ] #12 do the thing\n- [x] #99 other thing\n",
        )
        orphans = mod.detect_orphans([issue], [epic])
        assert not any(o["number"] == 12 for o in orphans)

    def test_issue_not_referenced_in_epic_body_is_orphan(self) -> None:
        """Issue with no milestone not in any epic checklist is an orphan."""
        mod = _load_summary()
        issue = _make_issue(13, "Not referenced", [], milestone=None)
        epic = _make_issue(
            101,
            "[Umbrella] Other epic",
            ["meta"],
            body="- [ ] #99 completely different issue\n",
        )
        orphans = mod.detect_orphans([issue], [epic])
        assert any(o["number"] == 13 for o in orphans)

    def test_epic_itself_never_an_orphan(self) -> None:
        """Epic issue is excluded from orphans even if it has no milestone."""
        mod = _load_summary()
        epic = _make_issue(
            200, "[Umbrella] Epic issue", ["meta"], milestone=None
        )
        orphans = mod.detect_orphans([epic], [epic])
        assert not any(o["number"] == 200 for o in orphans)


# ---------------------------------------------------------------------------
# TestMilestoneDescription — description column in Epics / Milestones table
# ---------------------------------------------------------------------------


class TestMilestoneDescription:
    """Milestone description rendered in the Epics / Milestones table."""

    def test_description_appears_in_output(self) -> None:
        """Milestone with a description shows it in the output."""
        mod = _load_summary()
        ms = _make_milestone(
            1, "Sprint 1", 2, 3, description="Ship the core API."
        )
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[], milestones=[ms], open_issues=[]
            )
        assert "Ship the core API." in output

    def test_empty_description_renders_blank_not_none(self) -> None:
        """Milestone with empty description renders blank, not 'None'."""
        mod = _load_summary()
        ms = _make_milestone(1, "Sprint 1", 0, 5, description="")
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[], milestones=[ms], open_issues=[]
            )
        assert "None" not in output

    def test_description_truncated_at_100_chars(self) -> None:
        """Description longer than 100 chars is truncated with ellipsis."""
        mod = _load_summary()
        long_desc = "A" * 105
        ms = _make_milestone(1, "Long", 0, 0, description=long_desc)
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[], milestones=[ms], open_issues=[]
            )
        assert "A" * 100 + "…" in output
        assert "A" * 105 not in output

    def test_description_exactly_100_chars_not_truncated(self) -> None:
        """Description of exactly 100 chars is not truncated."""
        mod = _load_summary()
        exact_desc = "B" * 100
        ms = _make_milestone(1, "Exact", 0, 0, description=exact_desc)
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[], milestones=[ms], open_issues=[]
            )
        assert exact_desc in output
        assert "…" not in output

    def test_null_description_renders_blank_not_none(self) -> None:
        """Milestone with description=None renders blank, not 'None'."""
        mod = _load_summary()
        ms = _make_milestone(1, "Sprint 2", 1, 1, description="")
        ms["description"] = None
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[], milestones=[ms], open_issues=[]
            )
        assert "None" not in output


# ---------------------------------------------------------------------------
# TestMilestoneDescriptionSanitization
# ---------------------------------------------------------------------------


class TestMilestoneDescriptionSanitization:
    """Sanitization of table-breaking chars in milestone description cells."""

    def test_pipe_in_description_is_escaped(self) -> None:
        """Description containing '|' renders escaped so table row is intact."""
        mod = _load_summary()
        ms = _make_milestone(
            1, "Sprint 1", 0, 0, description="before|after"
        )
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[], milestones=[ms], open_issues=[]
            )
        assert "before|after" not in output
        assert r"before\|after" in output or "before&#124;after" in output

    def test_newline_in_description_becomes_space(self) -> None:
        """Description containing a newline renders as a space."""
        mod = _load_summary()
        ms = _make_milestone(
            1, "Sprint 1", 0, 0, description="line one\nline two"
        )
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[], milestones=[ms], open_issues=[]
            )
        assert "line one line two" in output
        assert "line one\nline two" not in output

    def test_pipe_and_newline_both_sanitized(self) -> None:
        """Description with both '|' and newline is fully sanitized."""
        mod = _load_summary()
        ms = _make_milestone(
            1, "Sprint 2", 0, 0, description="a|b\nc|d"
        )
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[], milestones=[ms], open_issues=[]
            )
        assert "a|b\nc|d" not in output
        assert "a|b" not in output

    def test_sanitization_applied_before_truncation(self) -> None:
        """Sanitizer runs before the 100-char truncation limit is applied."""
        mod = _load_summary()
        raw_desc = "A" * 50 + "|" + "B" * 59
        ms = _make_milestone(1, "Long", 0, 0, description=raw_desc)
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[], milestones=[ms], open_issues=[]
            )
        assert raw_desc not in output
        assert "…" in output
        assert "A" * 50 in output


# ---------------------------------------------------------------------------
# TestCriticalIssues — _render_critical_issues and render_report integration
# ---------------------------------------------------------------------------


class TestCriticalIssues:
    """Critical / blocked issues subsection rendering."""

    def test_no_matching_issues_omits_section(self) -> None:
        """When no issues match critical labels, the section is omitted."""
        mod = _load_summary()
        issues = [_make_issue(1, "Normal issue", ["enhancement"])]
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[], milestones=[], open_issues=issues
            )
        assert "Critical" not in output
        assert "blocked" not in output

    def test_blocked_issue_appears_in_section(self) -> None:
        """Issue with 'blocked' label appears in Critical / blocked section."""
        mod = _load_summary()
        issues = [_make_issue(5, "Cannot proceed", ["blocked"])]
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[], milestones=[], open_issues=issues
            )
        assert "Critical" in output or "blocked" in output
        assert "#5" in output
        assert "Cannot proceed" in output

    def test_security_issue_appears_in_section(self) -> None:
        """Issue with 'security' label appears in the critical section."""
        mod = _load_summary()
        issues = [_make_issue(7, "CVE patch needed", ["security"])]
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[], milestones=[], open_issues=issues
            )
        assert "CVE patch needed" in output or "security" in output

    def test_bug_issue_appears_in_section(self) -> None:
        """Issue with 'bug' label appears in the critical section."""
        mod = _load_summary()
        issues = [_make_issue(9, "Bad crash", ["bug"])]
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[], milestones=[], open_issues=issues
            )
        assert "Bad crash" in output or "bug" in output

    def test_matching_labels_shown_in_row(self) -> None:
        """Each critical row shows the matching label(s)."""
        mod = _load_summary()
        issues = [_make_issue(11, "Multi", ["blocked", "bug"])]
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[], milestones=[], open_issues=issues
            )
        assert "blocked" in output
        assert "bug" in output

    def test_render_critical_issues_empty_returns_none(self) -> None:
        """_render_critical_issues with no matches returns None."""
        mod = _load_summary()
        issues = [_make_issue(1, "Normal", ["enhancement"])]
        result = mod._render_critical_issues(
            issues, critical_labels={"blocked", "security", "bug"}
        )
        assert result is None

    def test_render_critical_issues_returns_string_when_matches(
        self,
    ) -> None:
        """_render_critical_issues returns a non-empty string when issues match."""
        mod = _load_summary()
        issues = [_make_issue(3, "Stuck", ["blocked"])]
        result = mod._render_critical_issues(
            issues, critical_labels={"blocked", "security", "bug"}
        )
        assert isinstance(result, str)
        assert len(result) > 0

    def test_issue_url_linked_in_output(self) -> None:
        """Issue number in critical section is a markdown link to the URL."""
        mod = _load_summary()
        issues = [_make_issue(42, "Critical bug", ["bug"])]
        result = mod._render_critical_issues(
            issues, critical_labels={"blocked", "security", "bug"}
        )
        assert result is not None
        assert "https://github.com/owner/repo/issues/42" in result
        assert "#42" in result


# ---------------------------------------------------------------------------
# TestCriticalLabelsFlag — --critical-labels CLI argument
# ---------------------------------------------------------------------------


class TestCriticalLabelsFlag:
    """--critical-labels flag parsing and default behavior."""

    def test_default_critical_labels_set(self) -> None:
        """Parser default for --critical-labels includes blocked, security, bug."""
        mod = _load_summary()
        parser = mod._build_parser()
        args = parser.parse_args([])
        raw = args.critical_labels
        labels = {lbl.strip() for lbl in raw.split(",")} if raw else set()
        assert "blocked" in labels
        assert "security" in labels
        assert "bug" in labels

    def test_custom_critical_labels_parsed(self) -> None:
        """--critical-labels accepts a comma-separated override."""
        mod = _load_summary()
        parser = mod._build_parser()
        args = parser.parse_args(["--critical-labels", "wontfix,stale"])
        raw = args.critical_labels
        labels = {lbl.strip() for lbl in raw.split(",")}
        assert "wontfix" in labels
        assert "stale" in labels
        assert "blocked" not in labels

    def test_label_counts_function_absent(self) -> None:
        """_render_label_counts must NOT exist on the gh-summary module."""
        mod = _load_summary()
        assert not hasattr(mod, "_render_label_counts"), (
            "_render_label_counts was not deleted; it must be removed "
            "per acceptance criteria"
        )


# ---------------------------------------------------------------------------
# TestRenderRecentReleases — _render_recent_releases
# ---------------------------------------------------------------------------


class TestRenderRecentReleases:
    """Recent releases section rendering."""

    def _make_release(
        self, tag: str, published: str, name: str
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

    def test_no_releases_returns_none(self) -> None:
        """_render_recent_releases returns None when repo has no releases."""
        mod = _load_summary()
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            result = mod._render_recent_releases()
        assert result is None

    def test_populated_releases_render_table(self) -> None:
        """Releases are rendered as a markdown table."""
        mod = _load_summary()
        releases = [
            self._make_release("v1.2.0", "2026-05-01T12:00:00Z", "May release"),
            self._make_release(
                "v1.1.0", "2026-04-01T12:00:00Z", "April release"
            ),
        ]
        cp = _make_completed_process(releases, returncode=0)
        with patch("subprocess.run", return_value=cp):
            result = mod._render_recent_releases()
        assert result is not None
        assert "v1.2.0" in result
        assert "v1.1.0" in result
        assert "May release" in result

    def test_date_formatted_as_yyyy_mm_dd(self) -> None:
        """Published date is rendered as YYYY-MM-DD (not full ISO string)."""
        mod = _load_summary()
        releases = [
            self._make_release(
                "v2.0.0", "2026-03-15T08:30:00Z", "March release"
            ),
        ]
        cp = _make_completed_process(releases, returncode=0)
        with patch("subprocess.run", return_value=cp):
            result = mod._render_recent_releases()
        assert result is not None
        assert "2026-03-15" in result
        assert "T08:30:00Z" not in result

    def test_section_header_present(self) -> None:
        """The section header '### Recent releases' is present."""
        mod = _load_summary()
        releases = [
            self._make_release("v1.0.0", "2026-01-01T00:00:00Z", "First"),
        ]
        cp = _make_completed_process(releases, returncode=0)
        with patch("subprocess.run", return_value=cp):
            result = mod._render_recent_releases()
        assert result is not None
        assert "### Recent releases" in result

    def test_gh_release_list_called_with_correct_args(self) -> None:
        """gh release list is called with --limit 5 and --json flags."""
        mod = _load_summary()
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp) as mock_run:
            mod._render_recent_releases()
        call_args = mock_run.call_args[0][0]
        joined = " ".join(call_args)
        assert "release" in joined
        assert "list" in joined
        assert "--limit" in joined
        assert "5" in joined

    def test_gh_failure_returns_none(self) -> None:
        """gh release list failure causes _render_recent_releases to return None."""
        mod = _load_summary()
        cp = MagicMock()
        cp.returncode = 1
        cp.stdout = ""
        cp.stderr = "not a git repository"
        with patch("subprocess.run", return_value=cp):
            result = mod._render_recent_releases()
        assert result is None


# ---------------------------------------------------------------------------
# TestOutputStructure — render_report output shape
# ---------------------------------------------------------------------------


class TestOutputStructure:
    """Output: 1-line state-of-the-union + markdown tables."""

    def test_output_has_summary_line(self) -> None:
        """render_report output begins with a state-of-the-union line."""
        mod = _load_summary()
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[],
                milestones=[],
                open_issues=[],
            )
        lines = output.strip().splitlines()
        non_empty = [ln for ln in lines if ln.strip()]
        assert len(non_empty) > 0
        first = non_empty[0]
        assert "epic" in first.lower() or "milestone" in first.lower()

    def test_output_has_epics_milestones_section(self) -> None:
        """render_report output contains an Epics / Milestones section."""
        mod = _load_summary()
        epics = [_make_issue(1, "[Umbrella] E1", ["meta"], body="- [x] #2\n")]
        milestones = [_make_milestone(1, "MS1", 2, 3)]
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=epics, milestones=milestones, open_issues=[]
            )
        assert "Epics" in output or "Milestones" in output or "Epic" in output

    def test_output_has_no_label_counts_section(self) -> None:
        """render_report output does NOT contain Open issues by label section."""
        mod = _load_summary()
        issues = [_make_issue(1, "Bug", ["bug"])]
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[], milestones=[], open_issues=issues
            )
        assert "Open issues by label" not in output

    def test_output_no_releases_section_when_no_releases(self) -> None:
        """When repo has no releases the section header is absent."""
        mod = _load_summary()
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[], milestones=[], open_issues=[]
            )
        assert "Recent releases" not in output

    def test_output_has_releases_section_when_present(self) -> None:
        """When releases exist the Recent releases section is in output."""
        mod = _load_summary()
        releases = [
            {
                "tagName": "v1.0.0",
                "publishedAt": "2026-01-01T00:00:00Z",
                "name": "First",
            }
        ]
        cp = _make_completed_process(releases, returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[], milestones=[], open_issues=[]
            )
        assert "Recent releases" in output

    def test_section_order_epics_critical_releases(self) -> None:
        """Section order: Epics/Milestones → Critical issues → Releases."""
        mod = _load_summary()
        releases = [
            {
                "tagName": "v1.0.0",
                "publishedAt": "2026-01-01T00:00:00Z",
                "name": "First",
            }
        ]
        cp = _make_completed_process(releases, returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[],
                milestones=[],
                open_issues=[_make_issue(1, "X", ["blocked"])],
            )
        epics_pos = output.find("Epics / Milestones")
        critical_pos = output.find("Critical")
        releases_pos = output.find("Recent releases")
        assert epics_pos != -1
        assert critical_pos != -1
        assert releases_pos != -1
        assert epics_pos < critical_pos
        assert critical_pos < releases_pos

    def test_empty_inputs_no_crash(self) -> None:
        """render_report with no epics/milestones/issues doesn't crash."""
        mod = _load_summary()
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[], milestones=[], open_issues=[]
            )
        assert isinstance(output, str)

    def test_epics_table_includes_checklist_ratio(self) -> None:
        """Epic row shows checklist completion ratio."""
        mod = _load_summary()
        epic = _make_issue(
            1, "[Umbrella] Epic1", ["meta"], body="- [x] A\n- [ ] B\n"
        )
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[epic], milestones=[], open_issues=[]
            )
        assert "1/2" in output

    def test_epics_table_checklist_absent_fallback(self) -> None:
        """Epic row shows 'checklist absent' when no checkboxes in body."""
        mod = _load_summary()
        epic = _make_issue(
            2, "[Umbrella] NoChecklist", ["meta"], body="Some text"
        )
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[epic], milestones=[], open_issues=[]
            )
        assert "checklist absent" in output

    def test_summary_line_mentions_open_issues_count(self) -> None:
        """Summary line reflects total open issue count."""
        mod = _load_summary()
        issues = [_make_issue(i, f"Issue {i}", []) for i in range(1, 6)]
        cp = _make_completed_process([], returncode=0)
        with patch("subprocess.run", return_value=cp):
            output = mod.render_report(
                epics=[], milestones=[], open_issues=issues
            )
        first_line = output.strip().splitlines()[0]
        assert "5" in first_line


# ---------------------------------------------------------------------------
# TestGhApiIntegration — gh api call patterns in gh-summary main flow
# ---------------------------------------------------------------------------


class TestGhApiIntegration:
    """Verify gh api endpoint calls (mocked subprocess) in build_report_data."""

    def _make_run_side_effect(
        self,
        issues: list[dict[str, Any]],
        milestones: list[dict[str, Any]],
        repo_name: str = "owner/repo",
    ) -> Any:
        """Build a side_effect function for subprocess.run mocking.

        Args:
            issues: List of issue dicts to return for issue API calls.
            milestones: List of milestone dicts to return for milestone
                calls.
            repo_name: owner/name string returned by gh repo view.

        Returns:
            Callable side_effect for use with unittest.mock.patch.
        """

        def _side_effect(cmd: list[str], **kwargs: Any) -> MagicMock:
            joined = " ".join(cmd)
            if "milestones" in joined:
                return _make_completed_process(milestones)
            if "issues" in joined or "repo view" in joined.lower():
                if "repo view" in joined.lower() or "nameWithOwner" in joined:
                    return _make_completed_process(
                        {"nameWithOwner": repo_name}
                    )
                return _make_completed_process(issues)
            return _make_completed_process({"nameWithOwner": repo_name})

        return _side_effect

    def test_build_report_data_calls_issues_endpoint(self) -> None:
        """build_report_data calls the GitHub issues API endpoint."""
        mod = _load_summary()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process([])
            try:
                mod.build_report_data("owner/repo")
            except Exception:
                pass
        all_calls = [" ".join(c[0][0]) for c in mock_run.call_args_list]
        assert any("issues" in c for c in all_calls)

    def test_build_report_data_calls_milestones_endpoint(self) -> None:
        """build_report_data calls the GitHub milestones API endpoint."""
        mod = _load_summary()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process([])
            try:
                mod.build_report_data("owner/repo")
            except Exception:
                pass
        all_calls = [" ".join(c[0][0]) for c in mock_run.call_args_list]
        assert any("milestones" in c for c in all_calls)
