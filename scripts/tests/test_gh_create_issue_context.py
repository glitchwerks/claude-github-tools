"""Tests for scripts/gh-create-issue-context.py.

Covers the key behaviors of ``gather_context``, the consolidated
context-gathering call for the ``gh-create-issue`` skill's Phase 2
(replacing 4-5 separate ``gh`` invocations with one JSON payload):

  - Happy path: all five keys present, fixture data flows through
    unchanged for open_issues/open_prs/labels/milestones, and the
    overlap search result is populated.
  - search_keywords=None (or omitted): overlap_search is [] and the
    search-specific run_gh_api call is skipped entirely (no wasted
    gh call).
  - open_issues filtering: items carrying a "pull_request" key (the
    GitHub /issues endpoint quirk of also returning PRs) are excluded,
    mirroring gh-summary.py's existing PR-exclusion behavior.
  - Error propagation: a run_gh_api failure (RuntimeError) bubbles up
    from gather_context rather than being swallowed or partially
    returned.
  - Pagination: run_gh_api is called with paginate=True for the four
    list endpoints (open_issues, open_prs, labels, milestones).

All gh calls are mocked by patching ``run_gh_api`` directly (NOT
subprocess.run) since gather_context calls the already-tested
run_gh_api helper and should be exercised at that boundary. The patch
covers both ``import _gh_common; _gh_common.run_gh_api(...)`` and
``from _gh_common import run_gh_api`` call shapes (see
_patched_run_gh_api below) since either is a legal way to satisfy the
spec.
"""

from __future__ import annotations

import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Module loading helpers
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).parent.parent
COMMON_SCRIPT = SCRIPTS_DIR / "_gh_common.py"
CONTEXT_SCRIPT = SCRIPTS_DIR / "gh-create-issue-context.py"


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


def _load_context() -> ModuleType:
    """Import gh-create-issue-context as a module, injecting _gh_common.

    Returns:
        The loaded gh_create_issue_context module object.
    """
    common_spec = importlib.util.spec_from_file_location(
        "_gh_common", COMMON_SCRIPT
    )
    assert common_spec is not None and common_spec.loader is not None
    common_mod = importlib.util.module_from_spec(common_spec)
    sys.modules["_gh_common"] = common_mod
    common_spec.loader.exec_module(common_mod)  # type: ignore[union-attr]

    spec = importlib.util.spec_from_file_location(
        "gh_create_issue_context", CONTEXT_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# run_gh_api mocking helpers
# ---------------------------------------------------------------------------


@contextmanager
def _patched_run_gh_api(
    mod: ModuleType, side_effect: Any
) -> Iterator[MagicMock]:
    """Patch run_gh_api regardless of how gather_context imported it.

    gather_context may call either ``_gh_common.run_gh_api(...)``
    (qualified attribute access) or, if it used
    ``from _gh_common import run_gh_api``, a bare ``run_gh_api(...)``
    bound directly into its own module namespace. Only patching
    ``_gh_common.run_gh_api`` would leave the second shape making real
    ``gh`` calls. Both patch targets share one MagicMock so all calls,
    regardless of which binding shape was used, land in a single call
    log for assertions.

    Args:
        mod: The loaded gh_create_issue_context module.
        side_effect: Callable (or exception) to attach as the shared
            mock's side_effect.

    Yields:
        The shared MagicMock recording all run_gh_api calls.
    """
    mock_api = MagicMock(side_effect=side_effect)
    with patch("_gh_common.run_gh_api", mock_api):
        with patch.object(mod, "run_gh_api", mock_api, create=True):
            yield mock_api


def _call_path(call: Any) -> str:
    """Return the ``path`` argument from a run_gh_api mock call.

    Handles both positional (``run_gh_api("repos/x/issues", ...)``)
    and keyword (``run_gh_api(path="repos/x/issues", ...)``) call
    shapes, either of which is a legal way to satisfy the spec.

    Args:
        call: A single entry from ``mock.call_args_list``.

    Returns:
        The path string passed to that call.
    """
    return call.args[0] if call.args else call.kwargs["path"]


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_issue(
    number: int,
    title: str,
    is_pr: bool = False,
) -> dict[str, Any]:
    """Build a fake GitHub issue (or PR-shaped issue) dict.

    Args:
        number: Issue number.
        title: Issue title string.
        is_pr: When True, includes the "pull_request" key that the
            GitHub REST /issues endpoint attaches to PR items so it
            can be used to exercise the PR-exclusion filter.

    Returns:
        Dict shaped like a GitHub REST API issue object.
    """
    item: dict[str, Any] = {
        "number": number,
        "title": title,
        "html_url": f"https://github.com/owner/repo/issues/{number}",
        "labels": [],
    }
    if is_pr:
        item["pull_request"] = {
            "url": (f"https://api.github.com/repos/owner/repo/pulls/{number}")
        }
    return item


def _make_pr(number: int, title: str) -> dict[str, Any]:
    """Build a fake GitHub PR dict as returned by the /pulls endpoint.

    Args:
        number: PR number.
        title: PR title string.

    Returns:
        Dict shaped like a GitHub REST API pull request object.
    """
    return {
        "number": number,
        "title": title,
        "html_url": f"https://github.com/owner/repo/pull/{number}",
    }


def _make_label(name: str) -> dict[str, Any]:
    """Build a fake GitHub label dict.

    Args:
        name: Label name string.

    Returns:
        Dict shaped like a GitHub REST API label object.
    """
    return {"name": name, "color": "ededed", "description": ""}


def _make_milestone(number: int, title: str) -> dict[str, Any]:
    """Build a fake GitHub milestone dict.

    Args:
        number: Milestone number.
        title: Milestone title string.

    Returns:
        Dict shaped like a GitHub REST API milestone object.
    """
    return {"number": number, "title": title, "state": "open"}


def _make_run_gh_api_side_effect(
    open_issues: list[dict[str, Any]],
    open_prs: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    milestones: list[dict[str, Any]],
    search_results: list[dict[str, Any]] | None = None,
) -> Any:
    """Build a side_effect callable for patching run_gh_api.

    Routes on the ``path`` argument to return the appropriate fixture,
    mirroring the five endpoints gather_context is specified to call.

    Args:
        open_issues: Fixture returned for the ``repos/{repo}/issues``
            path.
        open_prs: Fixture returned for the ``repos/{repo}/pulls`` path.
        labels: Fixture returned for the ``repos/{repo}/labels`` path.
        milestones: Fixture returned for the ``repos/{repo}/milestones``
            path.
        search_results: Fixture returned for the search path (any path
            containing "search"). Defaults to an empty list.

    Returns:
        Callable suitable for use as a MagicMock's side_effect.
    """

    def _side_effect(*args: Any, **kwargs: Any) -> Any:
        path = args[0] if args else kwargs["path"]
        if "search" in path:
            return search_results if search_results is not None else []
        if "pulls" in path:
            return open_prs
        if "labels" in path:
            return labels
        if "milestones" in path:
            return milestones
        if "issues" in path:
            return open_issues
        raise AssertionError(f"unexpected run_gh_api path: {path!r}")

    return _side_effect


# ---------------------------------------------------------------------------
# TestGatherContextHappyPath
# ---------------------------------------------------------------------------


class TestGatherContextHappyPath:
    """gather_context with search_keywords provided: all keys populated."""

    def test_all_five_keys_present(self) -> None:
        """The returned dict has exactly the five documented keys."""
        mod = _load_context()
        side_effect = _make_run_gh_api_side_effect(
            open_issues=[], open_prs=[], labels=[], milestones=[]
        )
        with _patched_run_gh_api(mod, side_effect):
            result = mod.gather_context(
                repo="owner/repo", search_keywords="auth bug"
            )
        assert set(result.keys()) == {
            "overlap_search",
            "open_issues",
            "open_prs",
            "labels",
            "milestones",
        }

    def test_open_issues_flows_through_unchanged(self) -> None:
        """open_issues fixture data (non-PR items) passes through as-is."""
        mod = _load_context()
        issues = [_make_issue(1, "Bug A"), _make_issue(2, "Bug B")]
        side_effect = _make_run_gh_api_side_effect(
            open_issues=issues, open_prs=[], labels=[], milestones=[]
        )
        with _patched_run_gh_api(mod, side_effect):
            result = mod.gather_context(
                repo="owner/repo", search_keywords="bug"
            )
        assert result["open_issues"] == issues

    def test_open_prs_flows_through_unchanged(self) -> None:
        """open_prs fixture data passes through unchanged."""
        mod = _load_context()
        prs = [_make_pr(10, "Fix thing")]
        side_effect = _make_run_gh_api_side_effect(
            open_issues=[], open_prs=prs, labels=[], milestones=[]
        )
        with _patched_run_gh_api(mod, side_effect):
            result = mod.gather_context(
                repo="owner/repo", search_keywords="thing"
            )
        assert result["open_prs"] == prs

    def test_labels_flows_through_unchanged(self) -> None:
        """labels fixture data passes through unchanged."""
        mod = _load_context()
        labels = [_make_label("bug"), _make_label("enhancement")]
        side_effect = _make_run_gh_api_side_effect(
            open_issues=[], open_prs=[], labels=labels, milestones=[]
        )
        with _patched_run_gh_api(mod, side_effect):
            result = mod.gather_context(repo="owner/repo", search_keywords="x")
        assert result["labels"] == labels

    def test_milestones_flows_through_unchanged(self) -> None:
        """milestones fixture data passes through unchanged."""
        mod = _load_context()
        milestones = [_make_milestone(1, "Sprint 1")]
        side_effect = _make_run_gh_api_side_effect(
            open_issues=[], open_prs=[], labels=[], milestones=milestones
        )
        with _patched_run_gh_api(mod, side_effect):
            result = mod.gather_context(repo="owner/repo", search_keywords="x")
        assert result["milestones"] == milestones

    def test_overlap_search_populated_with_matches(self) -> None:
        """overlap_search contains the search fixture when keywords given."""
        mod = _load_context()
        search_hits = [_make_issue(99, "Duplicate report")]
        side_effect = _make_run_gh_api_side_effect(
            open_issues=[],
            open_prs=[],
            labels=[],
            milestones=[],
            search_results=search_hits,
        )
        with _patched_run_gh_api(mod, side_effect):
            result = mod.gather_context(
                repo="owner/repo", search_keywords="duplicate report"
            )
        assert result["overlap_search"] == search_hits

    def test_search_endpoint_called_when_keywords_given(self) -> None:
        """A run_gh_api call touches a 'search' path when keywords are
        provided (positive mirror of the skip-when-absent behavior)."""
        mod = _load_context()
        side_effect = _make_run_gh_api_side_effect(
            open_issues=[], open_prs=[], labels=[], milestones=[]
        )
        with _patched_run_gh_api(mod, side_effect) as mock_run:
            mod.gather_context(repo="owner/repo", search_keywords="auth bug")
        called_paths = [_call_path(c) for c in mock_run.call_args_list]
        assert any("search" in p for p in called_paths), (
            f"expected a search-path call; got: {called_paths}"
        )


# ---------------------------------------------------------------------------
# TestGatherContextNoSearchKeywords
# ---------------------------------------------------------------------------


class TestGatherContextNoSearchKeywords:
    """gather_context with search_keywords=None skips the search call."""

    def test_overlap_search_is_empty_list(self) -> None:
        """overlap_search is [] when search_keywords is None."""
        mod = _load_context()
        side_effect = _make_run_gh_api_side_effect(
            open_issues=[], open_prs=[], labels=[], milestones=[]
        )
        with _patched_run_gh_api(mod, side_effect):
            result = mod.gather_context(
                repo="owner/repo", search_keywords=None
            )
        assert result["overlap_search"] == []

    def test_overlap_search_is_empty_list_when_omitted(self) -> None:
        """overlap_search is [] when search_keywords is omitted entirely."""
        mod = _load_context()
        side_effect = _make_run_gh_api_side_effect(
            open_issues=[], open_prs=[], labels=[], milestones=[]
        )
        with _patched_run_gh_api(mod, side_effect):
            result = mod.gather_context(repo="owner/repo")
        assert result["overlap_search"] == []

    def test_search_endpoint_not_called(self) -> None:
        """No run_gh_api call is made for the search path when keywords
        are absent — the search gh call must be skipped, not wasted."""
        mod = _load_context()
        side_effect = _make_run_gh_api_side_effect(
            open_issues=[], open_prs=[], labels=[], milestones=[]
        )
        with _patched_run_gh_api(mod, side_effect) as mock_run:
            mod.gather_context(repo="owner/repo", search_keywords=None)
        called_paths = [_call_path(c) for c in mock_run.call_args_list]
        assert not any("search" in p for p in called_paths), (
            f"search endpoint should not be called; got paths: {called_paths}"
        )

    def test_call_count_excludes_search_call(self) -> None:
        """Exactly 4 run_gh_api calls are made (no search call) when
        search_keywords is None."""
        mod = _load_context()
        side_effect = _make_run_gh_api_side_effect(
            open_issues=[], open_prs=[], labels=[], milestones=[]
        )
        with _patched_run_gh_api(mod, side_effect) as mock_run:
            mod.gather_context(repo="owner/repo", search_keywords=None)
        assert mock_run.call_count == 4


# ---------------------------------------------------------------------------
# TestGatherContextPrExclusion
# ---------------------------------------------------------------------------


class TestGatherContextPrExclusion:
    """open_issues excludes items carrying a 'pull_request' key."""

    def test_pr_shaped_item_filtered_out_of_open_issues(self) -> None:
        """An /issues item with a 'pull_request' key is excluded."""
        mod = _load_context()
        real_issue = _make_issue(1, "Real issue")
        pr_as_issue = _make_issue(2, "A PR", is_pr=True)
        side_effect = _make_run_gh_api_side_effect(
            open_issues=[real_issue, pr_as_issue],
            open_prs=[],
            labels=[],
            milestones=[],
        )
        with _patched_run_gh_api(mod, side_effect):
            result = mod.gather_context(repo="owner/repo")
        numbers = [i["number"] for i in result["open_issues"]]
        assert 1 in numbers
        assert 2 not in numbers

    def test_all_pr_shaped_items_filtered_leaves_empty_list(self) -> None:
        """open_issues is [] when every fetched item is PR-shaped."""
        mod = _load_context()
        pr_as_issue = _make_issue(5, "Only a PR", is_pr=True)
        side_effect = _make_run_gh_api_side_effect(
            open_issues=[pr_as_issue],
            open_prs=[],
            labels=[],
            milestones=[],
        )
        with _patched_run_gh_api(mod, side_effect):
            result = mod.gather_context(repo="owner/repo")
        assert result["open_issues"] == []


# ---------------------------------------------------------------------------
# TestGatherContextErrorPropagation
# ---------------------------------------------------------------------------


class TestGatherContextErrorPropagation:
    """A run_gh_api failure propagates rather than being swallowed."""

    def test_open_prs_failure_propagates_runtime_error(self) -> None:
        """RuntimeError from the open_prs fetch bubbles out of
        gather_context unmodified."""
        mod = _load_context()

        def _side_effect(*args: Any, **kwargs: Any) -> Any:
            path = args[0] if args else kwargs["path"]
            if "pulls" in path:
                raise RuntimeError("gh api failed (exit 1): auth error")
            return []

        with _patched_run_gh_api(mod, _side_effect):
            try:
                mod.gather_context(repo="owner/repo")
                assert False, "Expected RuntimeError to propagate"
            except RuntimeError as exc:
                assert "auth error" in str(exc)

    def test_partial_data_not_returned_on_failure(self) -> None:
        """gather_context does not return a partial dict when a later
        fetch fails — the exception replaces any return value."""
        mod = _load_context()

        def _side_effect(*args: Any, **kwargs: Any) -> Any:
            path = args[0] if args else kwargs["path"]
            if "milestones" in path:
                raise RuntimeError("boom")
            return []

        with _patched_run_gh_api(mod, _side_effect):
            raised = False
            try:
                mod.gather_context(repo="owner/repo")
            except RuntimeError:
                raised = True
            assert raised, "Expected RuntimeError to propagate"


# ---------------------------------------------------------------------------
# TestGatherContextPagination
# ---------------------------------------------------------------------------


class TestGatherContextPagination:
    """run_gh_api is called with paginate=True for the 4 list endpoints."""

    def test_open_issues_call_uses_paginate_true(self) -> None:
        """The open_issues fetch passes paginate=True."""
        mod = _load_context()
        side_effect = _make_run_gh_api_side_effect(
            open_issues=[], open_prs=[], labels=[], milestones=[]
        )
        with _patched_run_gh_api(mod, side_effect) as mock_run:
            mod.gather_context(repo="owner/repo")
        calls = {
            _call_path(c): c.kwargs.get("paginate")
            for c in mock_run.call_args_list
        }
        issues_calls = [
            v
            for path, v in calls.items()
            if "issues" in path and "search" not in path
        ]
        assert issues_calls and all(v is True for v in issues_calls)

    def test_open_prs_call_uses_paginate_true(self) -> None:
        """The open_prs fetch passes paginate=True."""
        mod = _load_context()
        side_effect = _make_run_gh_api_side_effect(
            open_issues=[], open_prs=[], labels=[], milestones=[]
        )
        with _patched_run_gh_api(mod, side_effect) as mock_run:
            mod.gather_context(repo="owner/repo")
        calls = {
            _call_path(c): c.kwargs.get("paginate")
            for c in mock_run.call_args_list
        }
        prs_calls = [v for path, v in calls.items() if "pulls" in path]
        assert prs_calls and all(v is True for v in prs_calls)

    def test_labels_call_uses_paginate_true(self) -> None:
        """The labels fetch passes paginate=True."""
        mod = _load_context()
        side_effect = _make_run_gh_api_side_effect(
            open_issues=[], open_prs=[], labels=[], milestones=[]
        )
        with _patched_run_gh_api(mod, side_effect) as mock_run:
            mod.gather_context(repo="owner/repo")
        calls = {
            _call_path(c): c.kwargs.get("paginate")
            for c in mock_run.call_args_list
        }
        labels_calls = [v for path, v in calls.items() if "labels" in path]
        assert labels_calls and all(v is True for v in labels_calls)

    def test_milestones_call_uses_paginate_true(self) -> None:
        """The milestones fetch passes paginate=True."""
        mod = _load_context()
        side_effect = _make_run_gh_api_side_effect(
            open_issues=[], open_prs=[], labels=[], milestones=[]
        )
        with _patched_run_gh_api(mod, side_effect) as mock_run:
            mod.gather_context(repo="owner/repo")
        calls = {
            _call_path(c): c.kwargs.get("paginate")
            for c in mock_run.call_args_list
        }
        milestones_calls = [
            v for path, v in calls.items() if "milestones" in path
        ]
        assert milestones_calls and all(v is True for v in milestones_calls)

    def test_all_four_list_endpoints_paginated_in_one_call(self) -> None:
        """A single gather_context call passes paginate=True for all of
        open_issues, open_prs, labels, and milestones together."""
        mod = _load_context()
        side_effect = _make_run_gh_api_side_effect(
            open_issues=[], open_prs=[], labels=[], milestones=[]
        )
        with _patched_run_gh_api(mod, side_effect) as mock_run:
            mod.gather_context(repo="owner/repo", search_keywords="anything")
        non_search_calls = [
            c for c in mock_run.call_args_list if "search" not in _call_path(c)
        ]
        assert len(non_search_calls) == 4
        assert all(c.kwargs.get("paginate") is True for c in non_search_calls)
