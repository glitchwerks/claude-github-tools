"""Tests for scripts/gh-pr-review-address.py.

Covers the three deterministic subcommands extracted from
``skills/gh-pr-review-address/SKILL.md`` per issue #34:

- ``resolution-state`` — three-axis PR review-thread resolution
  (Axis A: ``isResolved``; Axis B: ``isOutdated`` or a later commit's
  patch touching the thread's line; Axis C: per-reviewer sticky
  ``CHANGES_REQUESTED`` verdicts).
- ``suppression-candidates`` — bot-specific nit/cosmetic pattern
  matching against inline review comments.
- ``resolvable-threads`` — Mode A/B filter selecting unresolved,
  bot-authored threads eligible for auto-resolution.

Each subcommand is authored against a pure function that the script's
thin CLI wrapper calls, matching the ``apply_exclusion_filter`` /
``main`` separation in ``gh-quick-wins.py``:

- ``compute_resolution_state(threads, commits, reviews)``
- ``_patch_covers_line(patch, line)`` (private helper, tested directly
  per issue #34's request to fixture patch-header parsing on its own)
- ``classify_suppression_candidates(comments)``
- ``filter_resolvable_threads(threads, mode="B", bot_allowlist=None)``

All ``gh`` calls are mocked or simply never invoked, since these are
pure-function tests — matching the pattern used in
``test_gh_quick_wins.py``.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Module loading helpers
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).parent.parent
COMMON_SCRIPT = SCRIPTS_DIR / "_gh_common.py"
PR_REVIEW_ADDRESS_SCRIPT = SCRIPTS_DIR / "gh-pr-review-address.py"


def _load_module() -> ModuleType:
    """Import gh-pr-review-address as a module, injecting _gh_common.

    The script is expected to reuse ``_gh_common.run_gh_api`` (per issue
    #34's technical notes), so ``_gh_common`` is registered in
    ``sys.modules`` before the target module is executed, exactly as
    ``test_gh_quick_wins.py`` does for ``gh-quick-wins.py``.

    Returns:
        The loaded gh_pr_review_address module object.

    Raises:
        FileNotFoundError: If ``gh-pr-review-address.py`` does not yet
            exist on disk (expected during the red phase).
    """
    common_spec = importlib.util.spec_from_file_location(
        "_gh_common", COMMON_SCRIPT
    )
    assert common_spec is not None and common_spec.loader is not None
    common_mod = importlib.util.module_from_spec(common_spec)
    sys.modules["_gh_common"] = common_mod
    common_spec.loader.exec_module(common_mod)  # type: ignore[union-attr]

    spec = importlib.util.spec_from_file_location(
        "gh_pr_review_address", PR_REVIEW_ADDRESS_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Fixture helpers — resolution-state
# ---------------------------------------------------------------------------


def _make_thread_comment(
    database_id: int,
    login: str,
    created_at: str,
    body: str = "",
) -> dict:
    """Build a GraphQL reviewThreads comment node.

    Args:
        database_id: The comment's REST-equivalent numeric id.
        login: The comment author's GitHub login.
        created_at: ISO 8601 creation timestamp.
        body: Comment body text.

    Returns:
        Dict matching a ``comments.nodes[]`` entry.
    """
    return {
        "databaseId": database_id,
        "author": {"login": login},
        "createdAt": created_at,
        "body": body,
    }


def _make_thread(
    thread_id: str,
    is_resolved: bool,
    is_outdated: bool,
    path: str,
    line: int | None,
    original_line: int | None,
    comments: list[dict],
) -> dict:
    """Build a GraphQL reviewThreads node.

    Args:
        thread_id: The thread's GraphQL node id.
        is_resolved: Value of ``isResolved``.
        is_outdated: Value of ``isOutdated``.
        path: File path the thread is anchored to.
        line: Current line number, or None if the anchor moved.
        original_line: Original line number at comment time.
        comments: List of comment node dicts (see ``_make_thread_comment``).

    Returns:
        Dict matching a ``reviewThreads.nodes[]`` entry.
    """
    return {
        "id": thread_id,
        "isResolved": is_resolved,
        "isOutdated": is_outdated,
        "path": path,
        "line": line,
        "originalLine": original_line,
        "comments": {"nodes": comments},
    }


def _make_commit(
    sha: str,
    date: str,
    files: list[dict],
) -> dict:
    """Build a PR commit entry with bundled per-file patch text.

    Args:
        sha: Commit SHA.
        date: ISO 8601 commit date.
        files: List of ``{"filename": ..., "patch": ...}`` dicts, mirroring
            ``gh api repos/.../commits/<sha> --jq '.files[]'``. A file
            dict may omit the ``patch`` key entirely (GitHub omits it for
            binary/large files).

    Returns:
        Dict with ``sha``, ``date``, and ``files`` keys.
    """
    return {"sha": sha, "date": date, "files": files}


def _make_review(
    login: str,
    state: str,
    submitted_at: str,
    commit_id: str = "deadbeef",
) -> dict:
    """Build a PR review entry.

    Args:
        login: Reviewer's GitHub login.
        state: Review state, e.g. ``"APPROVED"``, ``"CHANGES_REQUESTED"``,
            or ``"COMMENTED"``.
        submitted_at: ISO 8601 submission timestamp.
        commit_id: SHA the review targeted.

    Returns:
        Dict matching a ``pulls/<N>/reviews`` entry.
    """
    return {
        "user": {"login": login},
        "state": state,
        "submitted_at": submitted_at,
        "commit_id": commit_id,
    }


# ---------------------------------------------------------------------------
# Fixture helpers — patch hunk headers
# ---------------------------------------------------------------------------

# Standard unified-diff hunk header covering new-file lines 10-17
# (start=10, count=8).
_PATCH_SINGLE_HUNK = (
    "@@ -10,6 +10,8 @@ def foo():\n"
    " context line\n"
    "-old line\n"
    "+new line\n"
    "+another new line\n"
    " more context\n"
)

# Hunk header with the count omitted — real diffs emit this for a
# single-line hunk (equivalent to +5,1), and a naive `\+(\d+),(\d+)`
# regex misses it.
_PATCH_COUNT_OMITTED_HUNK = "@@ -5 +5 @@ def bar():\n-x\n+y\n"

# Two hunks: first covers new-file lines 1-2, second covers 50-53.
_PATCH_MULTI_HUNK = (
    "@@ -1,2 +1,2 @@\n a\n-b\n+c\n@@ -50,3 +50,4 @@\n d\n+e\n f\n g\n"
)


# ---------------------------------------------------------------------------
# Tests: _patch_covers_line helper
# ---------------------------------------------------------------------------


class TestPatchCoversLineHelper:
    """The Axis B line-touch helper must parse unified-diff hunk headers."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_module()

    def test_line_inside_single_hunk_range_is_covered(self) -> None:
        """A line within a standard '+start,count' hunk range is covered."""
        assert self.mod._patch_covers_line(_PATCH_SINGLE_HUNK, 12) is True

    def test_line_outside_single_hunk_range_is_not_covered(self) -> None:
        """A line outside every hunk range is not covered."""
        assert self.mod._patch_covers_line(_PATCH_SINGLE_HUNK, 20) is False

    def test_count_omitted_hunk_header_is_parsed(self) -> None:
        """A '+N' hunk header (count omitted, implies count=1) is parsed."""
        assert (
            self.mod._patch_covers_line(_PATCH_COUNT_OMITTED_HUNK, 5) is True
        )

    def test_count_omitted_hunk_header_excludes_adjacent_line(self) -> None:
        """A '+N' hunk header does not cover the line immediately after it."""
        assert (
            self.mod._patch_covers_line(_PATCH_COUNT_OMITTED_HUNK, 6) is False
        )

    def test_line_in_second_of_multiple_hunks_is_covered(self) -> None:
        """A line covered only by a later hunk in a multi-hunk patch is
        covered."""
        assert self.mod._patch_covers_line(_PATCH_MULTI_HUNK, 52) is True

    def test_line_between_hunks_is_not_covered(self) -> None:
        """A line that falls between two hunks' ranges is not covered."""
        assert self.mod._patch_covers_line(_PATCH_MULTI_HUNK, 10) is False

    def test_empty_patch_is_not_covered(self) -> None:
        """An empty-string patch never covers any line."""
        assert self.mod._patch_covers_line("", 5) is False

    def test_missing_patch_is_not_covered(self) -> None:
        """A None patch (GitHub omits 'patch' for binary/large files)
        never covers any line and must not raise."""
        assert self.mod._patch_covers_line(None, 5) is False


# ---------------------------------------------------------------------------
# Tests: resolution-state — Axis A (isResolved)
# ---------------------------------------------------------------------------


class TestAxisAResolved:
    """isResolved == true always classifies as RESOLVED."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_module()

    def test_resolved_thread_classified_resolved(self) -> None:
        """A thread with isResolved=True is RESOLVED regardless of
        isOutdated or any commit activity."""
        thread = _make_thread(
            "T1",
            is_resolved=True,
            is_outdated=True,
            path="src/foo.py",
            line=10,
            original_line=10,
            comments=[
                _make_thread_comment(
                    1, "coderabbitai[bot]", "2026-08-01T00:00:00Z"
                )
            ],
        )
        result = self.mod.compute_resolution_state(
            threads=[thread], commits=[], reviews=[]
        )
        assert result["threads"][0]["classification"] == "RESOLVED"


# ---------------------------------------------------------------------------
# Tests: resolution-state — Axis B (isOutdated / commit-patch match)
# ---------------------------------------------------------------------------


class TestAxisBCandidateAddressed:
    """Unresolved threads are CANDIDATE-ADDRESSED via isOutdated or a
    later commit whose patch touches the thread's line, else OPEN."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_module()

    def test_outdated_thread_is_candidate_addressed(self) -> None:
        """isOutdated=True alone classifies as CANDIDATE-ADDRESSED, with
        no matching commit required."""
        thread = _make_thread(
            "T2",
            is_resolved=False,
            is_outdated=True,
            path="src/foo.py",
            line=None,
            original_line=10,
            comments=[
                _make_thread_comment(
                    2, "coderabbitai[bot]", "2026-08-01T00:00:00Z"
                )
            ],
        )
        result = self.mod.compute_resolution_state(
            threads=[thread], commits=[], reviews=[]
        )
        assert result["threads"][0]["classification"] == "CANDIDATE-ADDRESSED"

    def test_not_outdated_but_later_commit_touches_line_is_candidate(
        self,
    ) -> None:
        """isOutdated=False but a commit dated after the first comment
        touches the thread's file/line region is CANDIDATE-ADDRESSED."""
        thread = _make_thread(
            "T3",
            is_resolved=False,
            is_outdated=False,
            path="src/foo.py",
            line=12,
            original_line=12,
            comments=[
                _make_thread_comment(
                    3, "coderabbitai[bot]", "2026-08-01T00:00:00Z"
                )
            ],
        )
        commit = _make_commit(
            "abc123",
            "2026-08-02T00:00:00Z",
            [{"filename": "src/foo.py", "patch": _PATCH_SINGLE_HUNK}],
        )
        result = self.mod.compute_resolution_state(
            threads=[thread], commits=[commit], reviews=[]
        )
        assert result["threads"][0]["classification"] == "CANDIDATE-ADDRESSED"

    def test_matching_commit_before_comment_date_stays_open(self) -> None:
        """A commit whose patch covers the line but is dated BEFORE the
        thread's first comment does not count as addressing it — the
        thread stays OPEN. This is the date-ordering guard: a naive
        implementation that ignores commit dates would misclassify this
        as CANDIDATE-ADDRESSED."""
        thread = _make_thread(
            "T4",
            is_resolved=False,
            is_outdated=False,
            path="src/foo.py",
            line=12,
            original_line=12,
            comments=[
                _make_thread_comment(
                    4, "coderabbitai[bot]", "2026-08-05T00:00:00Z"
                )
            ],
        )
        commit = _make_commit(
            "priorcommit",
            "2026-08-01T00:00:00Z",  # before the comment
            [{"filename": "src/foo.py", "patch": _PATCH_SINGLE_HUNK}],
        )
        result = self.mod.compute_resolution_state(
            threads=[thread], commits=[commit], reviews=[]
        )
        assert result["threads"][0]["classification"] == "OPEN"

    def test_no_touching_commit_stays_open(self) -> None:
        """A genuinely untouched thread (not outdated, no matching
        commit) is OPEN."""
        thread = _make_thread(
            "T5",
            is_resolved=False,
            is_outdated=False,
            path="src/foo.py",
            line=12,
            original_line=12,
            comments=[
                _make_thread_comment(
                    5, "coderabbitai[bot]", "2026-08-01T00:00:00Z"
                )
            ],
        )
        # A later commit exists but touches a different file entirely.
        commit = _make_commit(
            "def456",
            "2026-08-02T00:00:00Z",
            [{"filename": "src/other.py", "patch": _PATCH_SINGLE_HUNK}],
        )
        result = self.mod.compute_resolution_state(
            threads=[thread], commits=[commit], reviews=[]
        )
        assert result["threads"][0]["classification"] == "OPEN"

    def test_output_line_falls_back_to_original_line_when_line_null(
        self,
    ) -> None:
        """When isOutdated moved the anchor and 'line' is null, the
        reported line falls back to 'originalLine'."""
        thread = _make_thread(
            "T6",
            is_resolved=False,
            is_outdated=True,
            path="src/foo.py",
            line=None,
            original_line=42,
            comments=[
                _make_thread_comment(
                    6, "coderabbitai[bot]", "2026-08-01T00:00:00Z"
                )
            ],
        )
        result = self.mod.compute_resolution_state(
            threads=[thread], commits=[], reviews=[]
        )
        assert result["threads"][0]["line"] == 42

    def test_output_line_uses_current_line_when_present(self) -> None:
        """When 'line' is present, it is used as-is (no fallback)."""
        thread = _make_thread(
            "T7",
            is_resolved=False,
            is_outdated=False,
            path="src/foo.py",
            line=17,
            original_line=10,
            comments=[
                _make_thread_comment(
                    7, "coderabbitai[bot]", "2026-08-01T00:00:00Z"
                )
            ],
        )
        result = self.mod.compute_resolution_state(
            threads=[thread], commits=[], reviews=[]
        )
        assert result["threads"][0]["line"] == 17


# ---------------------------------------------------------------------------
# Tests: resolution-state — Axis C (sticky blockers)
# ---------------------------------------------------------------------------


class TestAxisCStickyBlockers:
    """Per-reviewer latest-review-wins verdict, independent of thread
    state."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_module()

    def test_changes_requested_with_no_later_approval_is_sticky(
        self,
    ) -> None:
        """A reviewer whose latest review is CHANGES_REQUESTED, with no
        later APPROVED from them, is a sticky blocker."""
        reviews = [
            _make_review("alice", "CHANGES_REQUESTED", "2026-08-01T00:00:00Z")
        ]
        result = self.mod.compute_resolution_state(
            threads=[], commits=[], reviews=reviews
        )
        assert result["sticky_blockers"] == ["alice"]

    def test_changes_requested_then_later_approved_clears(self) -> None:
        """CHANGES_REQUESTED followed by a later APPROVED from the same
        reviewer clears the block — not a sticky blocker."""
        reviews = [
            _make_review("alice", "CHANGES_REQUESTED", "2026-08-01T00:00:00Z"),
            _make_review("alice", "APPROVED", "2026-08-02T00:00:00Z"),
        ]
        result = self.mod.compute_resolution_state(
            threads=[], commits=[], reviews=reviews
        )
        assert result["sticky_blockers"] == []

    def test_approved_then_later_changes_requested_is_sticky(self) -> None:
        """An earlier APPROVED does not clear a later CHANGES_REQUESTED
        from the same reviewer — latest state wins, not 'ever approved'."""
        reviews = [
            _make_review("alice", "APPROVED", "2026-08-01T00:00:00Z"),
            _make_review("alice", "CHANGES_REQUESTED", "2026-08-02T00:00:00Z"),
        ]
        result = self.mod.compute_resolution_state(
            threads=[], commits=[], reviews=reviews
        )
        assert result["sticky_blockers"] == ["alice"]

    def test_comment_only_reviewer_is_not_a_blocker(self) -> None:
        """A reviewer whose only review is COMMENTED is non-blocking at
        the verdict axis."""
        reviews = [_make_review("bob", "COMMENTED", "2026-08-01T00:00:00Z")]
        result = self.mod.compute_resolution_state(
            threads=[], commits=[], reviews=reviews
        )
        assert result["sticky_blockers"] == []

    def test_reviews_are_grouped_per_reviewer_login(self) -> None:
        """Sticky-blocker classification is computed independently per
        reviewer login: one sticky and one cleared reviewer yields
        exactly one sticky blocker."""
        reviews = [
            _make_review("alice", "CHANGES_REQUESTED", "2026-08-01T00:00:00Z"),
            _make_review("bob", "CHANGES_REQUESTED", "2026-08-01T00:00:00Z"),
            _make_review("bob", "APPROVED", "2026-08-02T00:00:00Z"),
        ]
        result = self.mod.compute_resolution_state(
            threads=[], commits=[], reviews=reviews
        )
        assert result["sticky_blockers"] == ["alice"]


# ---------------------------------------------------------------------------
# Tests: resolution-state — reconciliation invariant
# ---------------------------------------------------------------------------


class TestResolutionStateReconciliation:
    """The three classifications must exhaustively partition the thread
    set, on a fixture that exercises all of them together."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_module()

    def test_mixed_fixture_reconciles(self) -> None:
        """A fixture with one RESOLVED, one outdated CANDIDATE-ADDRESSED,
        one commit-matched CANDIDATE-ADDRESSED, and one OPEN thread
        reconciles: total == RESOLVED + CANDIDATE-ADDRESSED + OPEN, every
        input thread appears exactly once in the output, and every
        classification is one of the three valid literals."""
        threads = [
            _make_thread(
                "R1",
                is_resolved=True,
                is_outdated=False,
                path="a.py",
                line=1,
                original_line=1,
                comments=[
                    _make_thread_comment(
                        101, "coderabbitai[bot]", "2026-08-01T00:00:00Z"
                    )
                ],
            ),
            _make_thread(
                "R2",
                is_resolved=False,
                is_outdated=True,
                path="b.py",
                line=None,
                original_line=5,
                comments=[
                    _make_thread_comment(
                        102, "coderabbitai[bot]", "2026-08-01T00:00:00Z"
                    )
                ],
            ),
            _make_thread(
                "R3",
                is_resolved=False,
                is_outdated=False,
                path="c.py",
                line=12,
                original_line=12,
                comments=[
                    _make_thread_comment(
                        103, "coderabbitai[bot]", "2026-08-01T00:00:00Z"
                    )
                ],
            ),
            _make_thread(
                "R4",
                is_resolved=False,
                is_outdated=False,
                path="d.py",
                line=20,
                original_line=20,
                comments=[
                    _make_thread_comment(
                        104, "coderabbitai[bot]", "2026-08-01T00:00:00Z"
                    )
                ],
            ),
        ]
        commit_touching_c = _make_commit(
            "abc999",
            "2026-08-02T00:00:00Z",
            [{"filename": "c.py", "patch": _PATCH_SINGLE_HUNK}],
        )
        result = self.mod.compute_resolution_state(
            threads=threads, commits=[commit_touching_c], reviews=[]
        )

        out_threads = result["threads"]
        assert len(out_threads) == len(threads)

        valid = {"RESOLVED", "CANDIDATE-ADDRESSED", "OPEN"}
        classifications = [t["classification"] for t in out_threads]
        assert all(c in valid for c in classifications)

        resolved = sum(1 for c in classifications if c == "RESOLVED")
        candidate = sum(
            1 for c in classifications if c == "CANDIDATE-ADDRESSED"
        )
        open_count = sum(1 for c in classifications if c == "OPEN")
        assert len(out_threads) == resolved + candidate + open_count

        by_id = {t["id"]: t["classification"] for t in out_threads}
        assert by_id["R1"] == "RESOLVED"
        assert by_id["R2"] == "CANDIDATE-ADDRESSED"
        assert by_id["R3"] == "CANDIDATE-ADDRESSED"
        assert by_id["R4"] == "OPEN"


# ---------------------------------------------------------------------------
# Fixture helpers — suppression-candidates
# ---------------------------------------------------------------------------


def _make_review_comment(comment_id: int, login: str, body: str) -> dict:
    """Build a simplified inline review comment for suppression matching.

    Args:
        comment_id: The comment's REST id.
        login: The comment author's GitHub login.
        body: Comment body text.

    Returns:
        Dict with ``comment_id``, ``author_login``, and ``body`` keys.
    """
    return {"comment_id": comment_id, "author_login": login, "body": body}


# ---------------------------------------------------------------------------
# Tests: suppression-candidates
# ---------------------------------------------------------------------------


class TestCoderabbitNitpickSuppression:
    """coderabbitai[bot]: bodies starting with 'Nitpick:' or 'Nit:'
    (leading whitespace allowed) are suppression candidates."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_module()

    def test_nitpick_prefix_suppressed(self) -> None:
        """A body starting with 'Nitpick:' is suppressed."""
        comments = [
            _make_review_comment(
                1,
                "coderabbitai[bot]",
                "Nitpick: reorder these imports for clarity.",
            )
        ]
        result = self.mod.classify_suppression_candidates(comments)
        assert result[0]["suppress_candidate"] is True
        assert result[0]["matched_rule"] is not None

    def test_nit_prefix_with_leading_whitespace_suppressed(self) -> None:
        """A 'Nit:' prefix with leading whitespace is still suppressed."""
        comments = [
            _make_review_comment(
                2,
                "coderabbitai[bot]",
                "   Nit: minor rename suggestion.",
            )
        ]
        result = self.mod.classify_suppression_candidates(comments)
        assert result[0]["suppress_candidate"] is True

    def test_body_not_starting_with_nit_prefix_not_suppressed(self) -> None:
        """A coderabbitai body that merely mentions nit-picking, but does
        not start with the prefix, is not suppressed."""
        comments = [
            _make_review_comment(
                3,
                "coderabbitai[bot]",
                "This introduces a bug in the nit-picking logic.",
            )
        ]
        result = self.mod.classify_suppression_candidates(comments)
        assert result[0]["suppress_candidate"] is False
        assert result[0]["matched_rule"] is None


class TestCodexPriorityTagSuppression:
    """chatgpt-codex-connector[bot]: bodies tagged P3 or lower are
    suppression candidates."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_module()

    def test_p3_tag_suppressed(self) -> None:
        """A body tagged P3 is suppressed."""
        comments = [
            _make_review_comment(
                10,
                "chatgpt-codex-connector[bot]",
                "Minor style issue (P3): consider renaming.",
            )
        ]
        result = self.mod.classify_suppression_candidates(comments)
        assert result[0]["suppress_candidate"] is True

    def test_p4_tag_suppressed(self) -> None:
        """A body tagged P4 is suppressed."""
        comments = [
            _make_review_comment(
                11,
                "chatgpt-codex-connector[bot]",
                "P4 - very low priority nit.",
            )
        ]
        result = self.mod.classify_suppression_candidates(comments)
        assert result[0]["suppress_candidate"] is True

    def test_p1_tag_not_suppressed(self) -> None:
        """A body tagged P1 (higher priority) is not suppressed."""
        comments = [
            _make_review_comment(
                12,
                "chatgpt-codex-connector[bot]",
                "Critical (P1): null pointer dereference.",
            )
        ]
        result = self.mod.classify_suppression_candidates(comments)
        assert result[0]["suppress_candidate"] is False

    def test_p30_does_not_falsely_match_p3(self) -> None:
        """A 'P30' token must not be misread as the P3 tag (word-boundary
        edge case for the \\bP[3-9]\\b style regex)."""
        comments = [
            _make_review_comment(
                13,
                "chatgpt-codex-connector[bot]",
                "Version bump to P30 required before release.",
            )
        ]
        result = self.mod.classify_suppression_candidates(comments)
        assert result[0]["suppress_candidate"] is False


class TestClaudeRunnerLowTierSuppression:
    """claude-action-runner[bot]: findings tagged the schema's 'low' tier
    are suppression candidates.

    Detection rule: a case-insensitive match for either the JSON-shaped
    marker ``"tier": "low"`` (with flexible whitespace around the colon)
    or the plain-text marker ``Tier: LOW`` — covering both a raw findings
    payload and a summarized rendering of it.
    """

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_module()

    def test_json_shaped_low_tier_marker_suppressed(self) -> None:
        """A body containing '"tier": "low"' is suppressed."""
        comments = [
            _make_review_comment(
                20,
                "claude-action-runner[bot]",
                '{"findings": [{"tier": "low", "message": "trivial nit"}]}',
            )
        ]
        result = self.mod.classify_suppression_candidates(comments)
        assert result[0]["suppress_candidate"] is True

    def test_plain_text_low_tier_marker_suppressed(self) -> None:
        """A body using the plain-text 'Tier: LOW' marker (any casing)
        is also suppressed."""
        comments = [
            _make_review_comment(
                21,
                "claude-action-runner[bot]",
                "Tier: LOW (trivial formatting nit)",
            )
        ]
        result = self.mod.classify_suppression_candidates(comments)
        assert result[0]["suppress_candidate"] is True

    def test_high_tier_not_suppressed(self) -> None:
        """A body tagged tier 'high' is not suppressed."""
        comments = [
            _make_review_comment(
                22,
                "claude-action-runner[bot]",
                '{"findings": [{"tier": "high", "message": "serious bug"}]}',
            )
        ]
        result = self.mod.classify_suppression_candidates(comments)
        assert result[0]["suppress_candidate"] is False


class TestCopilotStyleHeuristicSuppression:
    """copilot-pull-request-reviewer[bot]: suggestions framed as
    style/formatting preferences with no correctness claim are
    suppression candidates.

    Heuristic: suppress only when a style-trigger keyword ('style',
    'formatting', 'preference') is present AND no correctness/bug
    keyword ('bug', 'incorrect', 'broken') is present. Absence of both
    keyword sets is treated as ambiguous and NOT suppressed — the third
    fixture below pins this AND-not-OR resolution.
    """

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_module()

    def test_clearly_style_only_comment_suppressed(self) -> None:
        """A clearly style-only suggestion is suppressed."""
        comments = [
            _make_review_comment(
                30,
                "copilot-pull-request-reviewer[bot]",
                "This is a style preference — consider consistent "
                "formatting here.",
            )
        ]
        result = self.mod.classify_suppression_candidates(comments)
        assert result[0]["suppress_candidate"] is True

    def test_clearly_correctness_comment_not_suppressed(self) -> None:
        """A comment naming a real bug is not suppressed, even though it
        also uses the word 'formatting' in passing."""
        comments = [
            _make_review_comment(
                31,
                "copilot-pull-request-reviewer[bot]",
                "This formatting change is broken; the loop now produces "
                "incorrect results due to an off-by-one bug.",
            )
        ]
        result = self.mod.classify_suppression_candidates(comments)
        assert result[0]["suppress_candidate"] is False

    def test_ambiguous_comment_with_neither_keyword_not_suppressed(
        self,
    ) -> None:
        """A comment with neither a style trigger nor a bug keyword is
        not suppressed (heuristic requires a positive style signal, not
        merely the absence of a negative one)."""
        comments = [
            _make_review_comment(
                32,
                "copilot-pull-request-reviewer[bot]",
                "Consider adding a docstring explaining this parameter.",
            )
        ]
        result = self.mod.classify_suppression_candidates(comments)
        assert result[0]["suppress_candidate"] is False


class TestSuppressionNonMatchingAuthors:
    """Human authors and unlisted bots are never suppressed, even if
    their body text matches a pattern from the bot-specific table."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_module()

    def test_human_author_never_suppressed(self) -> None:
        """A human author's comment is never suppressed, even when its
        body matches a bot's suppression pattern verbatim."""
        comments = [
            _make_review_comment(
                40, "alice", "Nitpick: consider renaming this variable."
            )
        ]
        result = self.mod.classify_suppression_candidates(comments)
        assert result[0]["suppress_candidate"] is False
        assert result[0]["matched_rule"] is None

    def test_unlisted_bot_never_suppressed(self) -> None:
        """A bot login not present in the suppression table is never
        suppressed."""
        comments = [
            _make_review_comment(
                41, "some-other-bot[bot]", "Nitpick: whatever you like."
            )
        ]
        result = self.mod.classify_suppression_candidates(comments)
        assert result[0]["suppress_candidate"] is False
        assert result[0]["matched_rule"] is None


class TestSuppressionOutputShape:
    """Every input comment produces exactly one output record with the
    required keys, and each bot's positive match yields a distinct
    matched_rule (spelling itself is not pinned — issue #34 requires
    only that the rule be recorded, not any specific string)."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_module()

    def test_output_has_one_record_per_input_comment(self) -> None:
        """The output list has exactly one entry per input comment, each
        carrying comment_id, author_login, suppress_candidate, and
        matched_rule."""
        comments = [
            _make_review_comment(1, "alice", "Looks good."),
            _make_review_comment(2, "coderabbitai[bot]", "Nit: tiny tweak."),
        ]
        result = self.mod.classify_suppression_candidates(comments)
        assert len(result) == 2
        for record in result:
            assert set(record.keys()) >= {
                "comment_id",
                "author_login",
                "suppress_candidate",
                "matched_rule",
            }

    def test_four_bot_positive_matches_yield_distinct_matched_rules(
        self,
    ) -> None:
        """Each of the four bots' positive-match fixture yields a
        matched_rule distinct from the other three."""
        comments = [
            _make_review_comment(
                50, "coderabbitai[bot]", "Nitpick: small thing."
            ),
            _make_review_comment(
                51,
                "chatgpt-codex-connector[bot]",
                "Minor (P3): small thing.",
            ),
            _make_review_comment(
                52,
                "claude-action-runner[bot]",
                '{"tier": "low", "message": "small thing"}',
            ),
            _make_review_comment(
                53,
                "copilot-pull-request-reviewer[bot]",
                "This is a style preference for formatting.",
            ),
        ]
        result = self.mod.classify_suppression_candidates(comments)
        assert all(r["suppress_candidate"] is True for r in result)
        matched_rules = [r["matched_rule"] for r in result]
        assert all(rule is not None for rule in matched_rules)
        assert len(set(matched_rules)) == 4


# ---------------------------------------------------------------------------
# Fixture helpers — resolvable-threads
# ---------------------------------------------------------------------------


def _make_filter_thread(
    thread_id: str,
    is_resolved: bool,
    is_outdated: bool,
    first_author_login: str,
) -> dict:
    """Build a reviewThreads node shaped for the resolvable-threads
    filter (only the first comment's author is needed).

    Args:
        thread_id: The thread's GraphQL node id.
        is_resolved: Value of ``isResolved``.
        is_outdated: Value of ``isOutdated``.
        first_author_login: Login of the thread's first comment author.

    Returns:
        Dict matching the Step 4.5 enumeration query shape.
    """
    return {
        "id": thread_id,
        "isResolved": is_resolved,
        "isOutdated": is_outdated,
        "comments": {"nodes": [{"author": {"login": first_author_login}}]},
    }


_DEFAULT_ALLOWLISTED_BOT = "coderabbitai[bot]"


# ---------------------------------------------------------------------------
# Tests: resolvable-threads — Mode B (default, conservative)
# ---------------------------------------------------------------------------


class TestResolvableThreadsModeB:
    """Mode B additionally requires isOutdated == true."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_module()

    def test_not_yet_outdated_bot_thread_excluded(self) -> None:
        """Mode B excludes an unresolved bot thread that is not yet
        outdated."""
        thread = _make_filter_thread(
            "F1",
            is_resolved=False,
            is_outdated=False,
            first_author_login=_DEFAULT_ALLOWLISTED_BOT,
        )
        result = self.mod.filter_resolvable_threads([thread], mode="B")
        assert result["resolvable_thread_ids"] == []

    def test_outdated_bot_thread_included(self) -> None:
        """Mode B includes an unresolved, outdated bot thread."""
        thread = _make_filter_thread(
            "F2",
            is_resolved=False,
            is_outdated=True,
            first_author_login=_DEFAULT_ALLOWLISTED_BOT,
        )
        result = self.mod.filter_resolvable_threads([thread], mode="B")
        assert result["resolvable_thread_ids"] == ["F2"]

    def test_mode_b_is_the_default(self) -> None:
        """Omitting the mode argument defaults to Mode B's conservative
        behavior (not-yet-outdated bot thread excluded)."""
        thread = _make_filter_thread(
            "F2B",
            is_resolved=False,
            is_outdated=False,
            first_author_login=_DEFAULT_ALLOWLISTED_BOT,
        )
        result = self.mod.filter_resolvable_threads([thread])
        assert result["resolvable_thread_ids"] == []


class TestResolvableThreadsModeA:
    """Mode A is more permissive: no isOutdated requirement."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_module()

    def test_not_outdated_bot_thread_included_in_mode_a(self) -> None:
        """Mode A includes an unresolved bot thread even when it is not
        yet outdated."""
        thread = _make_filter_thread(
            "F3",
            is_resolved=False,
            is_outdated=False,
            first_author_login=_DEFAULT_ALLOWLISTED_BOT,
        )
        result = self.mod.filter_resolvable_threads([thread], mode="A")
        assert result["resolvable_thread_ids"] == ["F3"]


class TestResolvableThreadsHumanExcluded:
    """A human-authored thread is excluded in both modes, regardless of
    isOutdated."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_module()

    def test_human_thread_excluded_in_mode_b(self) -> None:
        """A human-authored, outdated, unresolved thread is excluded in
        Mode B."""
        thread = _make_filter_thread(
            "F4",
            is_resolved=False,
            is_outdated=True,
            first_author_login="alice",
        )
        result = self.mod.filter_resolvable_threads([thread], mode="B")
        assert result["resolvable_thread_ids"] == []

    def test_human_thread_excluded_in_mode_a(self) -> None:
        """A human-authored, outdated, unresolved thread is excluded in
        Mode A too."""
        thread = _make_filter_thread(
            "F5",
            is_resolved=False,
            is_outdated=True,
            first_author_login="alice",
        )
        result = self.mod.filter_resolvable_threads([thread], mode="A")
        assert result["resolvable_thread_ids"] == []


class TestResolvableThreadsResolvedExcluded:
    """An already-resolved thread is excluded in both modes."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_module()

    def test_resolved_thread_excluded_in_mode_b(self) -> None:
        """A resolved, outdated, bot-authored thread is excluded in
        Mode B."""
        thread = _make_filter_thread(
            "F6",
            is_resolved=True,
            is_outdated=True,
            first_author_login=_DEFAULT_ALLOWLISTED_BOT,
        )
        result = self.mod.filter_resolvable_threads([thread], mode="B")
        assert result["resolvable_thread_ids"] == []

    def test_resolved_thread_excluded_in_mode_a(self) -> None:
        """A resolved, outdated, bot-authored thread is excluded in
        Mode A too."""
        thread = _make_filter_thread(
            "F7",
            is_resolved=True,
            is_outdated=True,
            first_author_login=_DEFAULT_ALLOWLISTED_BOT,
        )
        result = self.mod.filter_resolvable_threads([thread], mode="A")
        assert result["resolvable_thread_ids"] == []


class TestResolvableThreadsCustomAllowlist:
    """The bot allow-list is configurable per issue #34's technical
    notes ('the user may add or remove bot logins')."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_module()

    def test_custom_allowlisted_login_is_honored(self) -> None:
        """A login not in the default allow-list is honored when passed
        explicitly via bot_allowlist."""
        thread = _make_filter_thread(
            "F8",
            is_resolved=False,
            is_outdated=True,
            first_author_login="custom-bot[bot]",
        )
        result = self.mod.filter_resolvable_threads(
            [thread], mode="B", bot_allowlist={"custom-bot[bot]"}
        )
        assert result["resolvable_thread_ids"] == ["F8"]

    def test_login_outside_custom_allowlist_excluded(self) -> None:
        """A login not present in an explicitly-passed custom allow-list
        is excluded, even if it is one of the defaults."""
        thread = _make_filter_thread(
            "F9",
            is_resolved=False,
            is_outdated=True,
            first_author_login=_DEFAULT_ALLOWLISTED_BOT,
        )
        result = self.mod.filter_resolvable_threads(
            [thread], mode="B", bot_allowlist={"custom-bot[bot]"}
        )
        assert result["resolvable_thread_ids"] == []


# ---------------------------------------------------------------------------
# Tests: CLI surface — subcommand wiring
# ---------------------------------------------------------------------------


class TestCliSubcommandWiring:
    """The three subcommand names must be wired into main()'s argparse
    dispatch, since SKILL.md's rewrite will invoke them by name."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_module()

    def test_resolution_state_subcommand_help_exits_0(self) -> None:
        """'resolution-state --help' is recognized and exits 0."""
        with pytest.raises(SystemExit) as exc:
            self.mod.main(["resolution-state", "--help"])
        assert exc.value.code == 0

    def test_suppression_candidates_subcommand_help_exits_0(self) -> None:
        """'suppression-candidates --help' is recognized and exits 0."""
        with pytest.raises(SystemExit) as exc:
            self.mod.main(["suppression-candidates", "--help"])
        assert exc.value.code == 0

    def test_resolvable_threads_subcommand_help_exits_0(self) -> None:
        """'resolvable-threads --help' is recognized and exits 0."""
        with pytest.raises(SystemExit) as exc:
            self.mod.main(["resolvable-threads", "--help"])
        assert exc.value.code == 0

    def test_bogus_subcommand_exits_nonzero(self) -> None:
        """An unrecognized subcommand name exits non-zero (argparse's
        usual 'invalid choice' behavior)."""
        with pytest.raises(SystemExit) as exc:
            self.mod.main(["not-a-real-subcommand"])
        assert exc.value.code != 0


# ---------------------------------------------------------------------------
# Regression tests: code-reviewer gaps on PR #40 (issue follow-up)
#
# These four groups pin gaps a code-reviewer pass found that the 52
# pre-existing tests above did not catch:
#
# 1. The Codex priority-tag suppression regex only matches P3-P9, so a
#    P10+ tag is not suppressed even though it should be treated the
#    same as any other low-priority tag.
# 2. ``filter_resolvable_threads`` indexes ``comments.nodes[0]`` without
#    checking for an empty list, raising IndexError on a commentless
#    thread instead of simply excluding it.
# 3. The Axis B helper (``_commit_touches_thread``) makes the same
#    unchecked ``comments.nodes[0]`` access, raising IndexError instead
#    of treating a commentless thread as OPEN (there is no comment
#    timestamp to compare a commit date against).
# 4. ``main()`` has no error handling around ``json.load(sys.stdin)`` or
#    the required-key lookups on the parsed payload, so malformed input
#    surfaces as a raw, unhandled traceback instead of a clean non-zero
#    exit with a stderr message.
# ---------------------------------------------------------------------------


class TestCodexPriorityTagSuppressionP10Plus:
    """The Codex priority-tag suppression rule must also cover
    double-digit (P10+) priority tags, not just the single-digit P3-P9
    range the current regex encodes."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_module()

    def test_p10_tag_suppressed(self) -> None:
        """A body tagged P10 is suppressed, the same as P3-P9.

        Currently FAILS: ``_CODEX_PRIORITY_RE`` is ``\\bP[3-9]\\b``,
        which requires a single digit and never matches 'P10'.
        """
        comments = [
            _make_review_comment(
                60,
                "chatgpt-codex-connector[bot]",
                "Low priority (P10): consider a docstring tweak.",
            )
        ]
        result = self.mod.classify_suppression_candidates(comments)
        assert result[0]["suppress_candidate"] is True

    def test_p15_tag_suppressed(self) -> None:
        """A body tagged P15 is also suppressed.

        Currently FAILS for the same reason as the P10 case above.
        """
        comments = [
            _make_review_comment(
                61,
                "chatgpt-codex-connector[bot]",
                "P15 - cosmetic naming nit.",
            )
        ]
        result = self.mod.classify_suppression_candidates(comments)
        assert result[0]["suppress_candidate"] is True


class TestFilterResolvableThreadsEmptyComments:
    """A thread with no comment nodes must be excluded from the
    resolvable set, not crash the enumeration."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_module()

    def test_thread_with_no_comments_is_excluded_not_crashed(self) -> None:
        """A thread whose 'comments.nodes' list is empty is skipped
        (treated as non-matching) rather than raising IndexError while
        looking up the first comment's author.

        Currently FAILS: ``filter_resolvable_threads`` accesses
        ``thread["comments"]["nodes"][0]`` unconditionally, so this
        fixture raises IndexError instead of returning cleanly.
        """
        thread = _make_filter_thread(
            "F10",
            is_resolved=False,
            is_outdated=True,
            first_author_login=_DEFAULT_ALLOWLISTED_BOT,
        )
        thread["comments"] = {"nodes": []}
        result = self.mod.filter_resolvable_threads([thread], mode="B")
        assert result["resolvable_thread_ids"] == []


class TestCommitTouchesThreadEmptyComments:
    """The Axis B commit-touch helper must not crash on a thread with
    no comment nodes; per the fix contract, it treats such a thread as
    not-touched (OPEN), since there is no comment timestamp available
    to compare a commit date against."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_module()

    def test_no_comments_returns_false_not_indexerror(self) -> None:
        """``_commit_touches_thread`` returns False (not-touched) for a
        thread with an empty 'comments.nodes' list, instead of raising
        IndexError while reading the first comment's timestamp.

        Currently FAILS: the helper does
        ``comments[0]["createdAt"]`` unconditionally.
        """
        thread = {"path": "src/foo.py", "comments": {"nodes": []}}
        commits = [
            _make_commit(
                "abc123",
                "2026-08-02T00:00:00Z",
                [{"filename": "src/foo.py", "patch": _PATCH_SINGLE_HUNK}],
            )
        ]
        assert self.mod._commit_touches_thread(thread, commits, 12) is False

    def test_no_comments_thread_classified_open_via_public_api(self) -> None:
        """Through the public ``compute_resolution_state`` entry point, a
        commentless, non-outdated, unresolved thread with a
        line-touching commit is classified OPEN rather than crashing —
        pinning the 'treated as OPEN' contract end to end.

        Currently FAILS: the underlying IndexError propagates out of
        ``compute_resolution_state`` instead of yielding OPEN.
        """
        thread = _make_thread(
            "T8",
            is_resolved=False,
            is_outdated=False,
            path="src/foo.py",
            line=12,
            original_line=12,
            comments=[],
        )
        commit = _make_commit(
            "abc123",
            "2026-08-02T00:00:00Z",
            [{"filename": "src/foo.py", "patch": _PATCH_SINGLE_HUNK}],
        )
        result = self.mod.compute_resolution_state(
            threads=[thread], commits=[commit], reviews=[]
        )
        assert result["threads"][0]["classification"] == "OPEN"


class TestCliMalformedStdinHandling:
    """main() must handle malformed stdin JSON and missing required
    payload keys as clean CLI errors (non-zero exit, stderr message),
    not let raw exceptions escape as unhandled tracebacks."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_module()

    @staticmethod
    def _run_main_capturing_exit(mod: ModuleType, argv: list[str]) -> Any:
        """Run main() and return its exit signal, whichever shape it
        takes.

        ``main()``'s documented contract is to *return* an int exit
        code; ``sys.exit(main())`` only wraps that in the
        ``if __name__ == "__main__"`` block, which direct calls to
        ``main()`` never execute. A correct error-handling fix may
        legitimately choose either ``return <nonzero>`` or
        ``sys.exit(<nonzero>)`` internally, so this helper accepts
        either without pinning one implementation shape.

        Args:
            mod: The loaded gh_pr_review_address module.
            argv: CLI arguments to pass to ``main()``.

        Returns:
            The int exit code, from either a direct return value or a
            caught ``SystemExit``'s code.
        """
        try:
            return mod.main(argv)
        except SystemExit as exc:
            return exc.code

    def test_invalid_json_on_stdin_exits_nonzero_with_clear_message(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Invalid JSON on stdin produces a non-zero exit (by return
        value or SystemExit) and a clear stderr message, not an
        unhandled JSONDecodeError traceback.

        Currently FAILS: ``main()`` calls ``json.load(sys.stdin)`` with
        no surrounding error handling, so this fixture lets a raw
        ``json.JSONDecodeError`` escape instead of signaling a clean
        non-zero exit.
        """
        monkeypatch.setattr(sys, "stdin", io.StringIO("not valid json {{{"))
        exit_code = self._run_main_capturing_exit(
            self.mod, ["suppression-candidates"]
        )
        assert exit_code not in (0, None)
        captured = capsys.readouterr()
        assert captured.err.strip() != ""

    def test_missing_required_key_exits_nonzero_naming_the_field(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Valid JSON missing a required top-level key (here,
        'resolution-state' invoked with only 'threads', missing
        'commits' and 'reviews') exits non-zero (by return value or
        SystemExit) and identifies a missing field on stderr, instead
        of raising a bare KeyError.

        Currently FAILS: ``main()`` indexes
        ``input_data["commits"]`` / ``input_data["reviews"]`` directly,
        so this fixture lets a raw ``KeyError`` escape instead of
        signaling a clean, descriptive non-zero exit.
        """
        monkeypatch.setattr(
            sys, "stdin", io.StringIO(json.dumps({"threads": []}))
        )
        exit_code = self._run_main_capturing_exit(
            self.mod, ["resolution-state"]
        )
        assert exit_code not in (0, None)
        captured = capsys.readouterr()
        assert ("commits" in captured.err) or ("reviews" in captured.err)
