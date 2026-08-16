"""Tests for scripts/address-feedback-state.py.

Covers two independent surfaces described in
``skills/address-feedback/SKILL.md`` §§ "Run-state persistence" and
"Safety guards → Merge gates":

Part 1 — run-state functions:
  - ``init_state`` builds a fresh run-state dict with exactly the
    documented keys and zero/empty defaults.
  - ``is_continuation`` decides new-run vs. continuation by comparing
    ``owner_repo`` and ``target`` against a loaded state file.
  - ``check_caps`` enforces the loop's hard caps against persisted
    counters: ``tick_count`` uses a strict ``>``, ``act_rounds`` uses
    ``>=``, and ``tick_count`` is checked first when both are breached.
  - ``load_state_file`` / ``save_state_file`` round-trip a state dict
    to disk as JSON, fail closed (propagate) on a malformed file, and
    create missing parent directories on save.

Part 2 — ``evaluate_merge_gates`` evaluates the six independent
merge-gate checklist entries from § "Safety guards → Merge gates"
against pre-fetched PR signals (no gh/network calls).

All gh calls are out of scope for this module — every function under
test is pure, operating on plain dicts and (for the state-file pair)
the local filesystem via ``tmp_path``.

The module under test does not exist yet; every test in this file is
expected to fail with ``FileNotFoundError`` raised from
``spec.loader.exec_module`` in ``_load_state_module`` (module loading
happens per-test via ``setup_method``, not at import time, so all
tests collect and each reds individually rather than the whole file
failing to collect).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Module loading helper
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).parent.parent
STATE_SCRIPT = SCRIPTS_DIR / "address-feedback-state.py"


def _load_state_module() -> ModuleType:
    """Import address-feedback-state.py as a module.

    Uses ``importlib.util.spec_from_file_location`` because the
    script's filename contains hyphens and cannot be imported with a
    plain ``import`` statement, matching the pattern used for
    ``gh-quick-wins.py`` in ``test_gh_quick_wins.py``.

    Returns:
        The loaded address_feedback_state module object.

    Raises:
        FileNotFoundError: If the script does not exist yet — expected
            while this module is unimplemented.
    """
    spec = importlib.util.spec_from_file_location(
        "address_feedback_state", STATE_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Part 1 — init_state
# ---------------------------------------------------------------------------


class TestInitState:
    """init_state builds a fresh run-state dict with documented defaults."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_state_module()

    def test_returns_exactly_the_documented_key_set(self) -> None:
        """init_state's key set matches the spec exactly — no extras."""
        state = self.mod.init_state(
            run_id="run-1", owner_repo="owner/repo", target="42"
        )
        assert set(state.keys()) == {
            "run_id",
            "target",
            "owner_repo",
            "tick_count",
            "act_rounds",
            "last_findings",
            "act_pushes",
            "parked_issues",
        }

    def test_echoes_inputs_and_sets_zero_empty_defaults(self) -> None:
        """init_state echoes its args and zeros/empties the counters."""
        state = self.mod.init_state(
            run_id="run-2", owner_repo="acme/widgets", target="feature-x"
        )
        assert state["run_id"] == "run-2"
        assert state["owner_repo"] == "acme/widgets"
        assert state["target"] == "feature-x"
        assert state["tick_count"] == 0
        assert state["act_rounds"] == 0
        assert state["last_findings"] is None
        assert state["act_pushes"] == []
        assert state["parked_issues"] == []


# ---------------------------------------------------------------------------
# Part 1 — is_continuation
# ---------------------------------------------------------------------------


class TestIsContinuation:
    """is_continuation decides new-run vs. continuation for a loaded
    state file."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_state_module()

    def test_none_state_is_not_a_continuation(self) -> None:
        """No prior state file (None) means a new run."""
        result = self.mod.is_continuation(
            None, owner_repo="owner/repo", target="42"
        )
        assert result is False

    def test_matching_owner_repo_and_target_is_a_continuation(self) -> None:
        """Matching owner_repo and target means load and continue."""
        existing = {"owner_repo": "owner/repo", "target": "42"}
        result = self.mod.is_continuation(
            existing, owner_repo="owner/repo", target="42"
        )
        assert result is True

    def test_mismatched_owner_repo_is_not_a_continuation(self) -> None:
        """A different owner_repo means treat as a new/reset run."""
        existing = {"owner_repo": "other/repo", "target": "42"}
        result = self.mod.is_continuation(
            existing, owner_repo="owner/repo", target="42"
        )
        assert result is False

    def test_mismatched_target_is_not_a_continuation(self) -> None:
        """A different target means treat as a new/reset run."""
        existing = {"owner_repo": "owner/repo", "target": "43"}
        result = self.mod.is_continuation(
            existing, owner_repo="owner/repo", target="42"
        )
        assert result is False

    def test_both_mismatched_is_not_a_continuation(self) -> None:
        """Both owner_repo and target differing is still not a
        continuation."""
        existing = {"owner_repo": "other/repo", "target": "43"}
        result = self.mod.is_continuation(
            existing, owner_repo="owner/repo", target="42"
        )
        assert result is False


# ---------------------------------------------------------------------------
# Part 1 — check_caps
# ---------------------------------------------------------------------------


class TestCheckCaps:
    """check_caps enforces MAX_TICKS (strict >) and MAX_ACT_ROUNDS (>=),
    checking tick_count first when both are breached."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_state_module()

    def test_under_both_caps_is_not_capped(self) -> None:
        """Counters comfortably under both caps -> not capped, no
        reason."""
        state = {"tick_count": 1, "act_rounds": 1}
        result = self.mod.check_caps(state, max_ticks=8, max_act_rounds=5)
        assert result == {"capped": False, "reason": None}

    def test_tick_count_exactly_at_max_is_not_capped(self) -> None:
        """tick_count == max_ticks is NOT capped — the operator is
        strictly '>', matching the skill's exact wording."""
        state = {"tick_count": 8, "act_rounds": 0}
        result = self.mod.check_caps(state, max_ticks=8, max_act_rounds=5)
        assert result["capped"] is False
        assert result["reason"] is None

    def test_tick_count_one_over_max_is_capped(self) -> None:
        """tick_count == max_ticks + 1 is capped with reason
        'tick_count'."""
        state = {"tick_count": 9, "act_rounds": 0}
        result = self.mod.check_caps(state, max_ticks=8, max_act_rounds=5)
        assert result == {"capped": True, "reason": "tick_count"}

    def test_act_rounds_exactly_at_max_is_capped(self) -> None:
        """act_rounds == max_act_rounds IS capped — the operator is
        '>=', unlike tick_count's strict '>'."""
        state = {"tick_count": 0, "act_rounds": 5}
        result = self.mod.check_caps(state, max_ticks=8, max_act_rounds=5)
        assert result == {"capped": True, "reason": "act_rounds"}

    def test_act_rounds_one_under_max_is_not_capped(self) -> None:
        """act_rounds == max_act_rounds - 1 is not capped."""
        state = {"tick_count": 0, "act_rounds": 4}
        result = self.mod.check_caps(state, max_ticks=8, max_act_rounds=5)
        assert result["capped"] is False
        assert result["reason"] is None

    def test_both_caps_breached_reports_tick_count_first(self) -> None:
        """When both tick_count and act_rounds are breached, reason is
        'tick_count' — checked first, matching step-0 ordering."""
        state = {"tick_count": 9, "act_rounds": 5}
        result = self.mod.check_caps(state, max_ticks=8, max_act_rounds=5)
        assert result == {"capped": True, "reason": "tick_count"}


# ---------------------------------------------------------------------------
# Part 1 — load_state_file / save_state_file
# ---------------------------------------------------------------------------


class TestStateFileRoundTrip:
    """load_state_file / save_state_file persist run-state as JSON on
    disk, failing closed on malformed input."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_state_module()

    def test_save_then_load_round_trips_equal_dict(
        self, tmp_path: Path
    ) -> None:
        """A saved state dict, when loaded back, equals the original.

        Includes a non-ASCII (em dash) character in a string field to
        force UTF-8-safe encoding on both the write and read paths —
        this repo's Windows default codec (cp1252) silently corrupts
        such content instead of raising.
        """
        state: dict[str, Any] = {
            "run_id": "run-abc",
            "target": "42",
            "owner_repo": "owner/repo",
            "tick_count": 3,
            "act_rounds": 1,
            "last_findings": "nit — unresolved",
            "act_pushes": [{"round": 1, "sha": "deadbeef"}],
            "parked_issues": [101, 102],
        }
        state_path = tmp_path / "address-feedback-pr42.json"

        self.mod.save_state_file(str(state_path), state)
        loaded = self.mod.load_state_file(str(state_path))

        assert loaded == state

        # Independently re-read the raw file (bypassing this module's own
        # load_state_file) to prove the on-disk bytes are genuinely UTF-8
        # and decode the em dash correctly — not merely symmetric with a
        # matching cp1252 write/read pair, which would silently pass a
        # round-trip-only assertion. See the Windows cp1252 footgun in
        # the python skill.
        reloaded = json.loads(state_path.read_text(encoding="utf-8"))
        assert reloaded["last_findings"] == "nit — unresolved"

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        """load_state_file returns None when the path does not exist."""
        missing_path = tmp_path / "does-not-exist.json"
        result = self.mod.load_state_file(str(missing_path))
        assert result is None

    def test_malformed_json_raises_json_decode_error(
        self, tmp_path: Path
    ) -> None:
        """A malformed state file raises JSONDecodeError rather than
        being swallowed into a fallback empty dict — the skill's
        explicit fail-closed rule."""
        bad_path = tmp_path / "malformed.json"
        bad_path.write_text("{not valid json", encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            self.mod.load_state_file(str(bad_path))

    def test_save_creates_missing_parent_directory(
        self, tmp_path: Path
    ) -> None:
        """save_state_file creates parent dirs that don't exist yet
        (e.g. a not-yet-created .tmp/ directory)."""
        nested_path = tmp_path / "sub" / ".tmp" / "address-feedback-pr7.json"
        state = {
            "run_id": "run-nested",
            "target": "7",
            "owner_repo": "owner/repo",
            "tick_count": 0,
            "act_rounds": 0,
            "last_findings": None,
            "act_pushes": [],
            "parked_issues": [],
        }

        self.mod.save_state_file(str(nested_path), state)

        assert nested_path.exists()
        loaded = self.mod.load_state_file(str(nested_path))
        assert loaded == state


# ---------------------------------------------------------------------------
# Part 2 — evaluate_merge_gates fixture helpers
# ---------------------------------------------------------------------------


def _make_signals(**overrides: Any) -> dict[str, Any]:
    """Build an all-gates-passing PR signals dict, with overrides.

    Args:
        **overrides: Fields to override from the all-passing baseline.

    Returns:
        Dict matching the ``signals`` shape documented for
        ``evaluate_merge_gates``, with every gate passing unless a
        field was overridden to break it.
    """
    signals: dict[str, Any] = {
        "pr_state": "OPEN",
        "merged_at": None,
        "viewer_permission": "WRITE",
        "viewer_can_update": True,
        "merge_commit_ci_conclusion": "SUCCESS",
        "has_changes_requested": False,
        "unresolved_human_thread_count": 0,
        "merge_state_status": "CLEAN",
        "pr_body": "Ordinary PR description.",
    }
    signals.update(overrides)
    return signals


_GATE_NAMES: tuple[str, ...] = (
    "pr_open",
    "effective_permission",
    "ci_green_on_merge_commit",
    "no_changes_requested",
    "merge_state_status_ok",
    "no_do_not_merge_banner",
)


def _gate(result: dict[str, Any], name: str) -> dict[str, Any]:
    """Look up a single gate entry from an evaluate_merge_gates result
    by name.

    Args:
        result: The dict returned by ``evaluate_merge_gates``.
        name: The gate's ``name`` field to find.

    Returns:
        The matching gate dict.

    Raises:
        AssertionError: If no gate with that name is present.
    """
    matches = [g for g in result["gates"] if g["name"] == name]
    assert len(matches) == 1, f"expected exactly one gate named {name!r}"
    return matches[0]


def _assert_only_gate_failed(result: dict[str, Any], failed_name: str) -> None:
    """Assert exactly one named gate failed and all others passed.

    Args:
        result: The dict returned by ``evaluate_merge_gates``.
        failed_name: The single gate name expected to have failed.
    """
    assert result["all_passed"] is False
    for name in _GATE_NAMES:
        gate = _gate(result, name)
        assert isinstance(gate["detail"], str)
        expected_passed = name != failed_name
        assert gate["passed"] is expected_passed, (
            f"gate {name!r} passed={gate['passed']!r}, "
            f"expected {expected_passed!r}"
        )


# ---------------------------------------------------------------------------
# Part 2 — evaluate_merge_gates: all-pass baseline
# ---------------------------------------------------------------------------


class TestEvaluateMergeGatesAllPass:
    """An all-clean signals fixture passes every gate."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_state_module()

    def test_all_gates_pass_on_clean_signals(self) -> None:
        """All six gates pass and all_passed is True."""
        result = self.mod.evaluate_merge_gates(_make_signals())
        assert result["all_passed"] is True
        assert len(result["gates"]) == 6
        for name in _GATE_NAMES:
            gate = _gate(result, name)
            assert gate["passed"] is True
            assert isinstance(gate["detail"], str)


# ---------------------------------------------------------------------------
# Part 2 — evaluate_merge_gates: single-gate failures
# ---------------------------------------------------------------------------


class TestEvaluateMergeGatesSingleFailures:
    """Flipping exactly one signal fails exactly its own gate and no
    others."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_state_module()

    def test_pr_open_fails_when_pr_state_is_not_open(self) -> None:
        """pr_open fails when pr_state != 'OPEN'."""
        signals = _make_signals(pr_state="CLOSED")
        result = self.mod.evaluate_merge_gates(signals)
        _assert_only_gate_failed(result, "pr_open")

    def test_pr_open_fails_when_merged_at_is_set(self) -> None:
        """pr_open fails when merged_at is not None, even if pr_state
        still reads OPEN."""
        signals = _make_signals(
            pr_state="OPEN", merged_at="2026-08-01T00:00:00Z"
        )
        result = self.mod.evaluate_merge_gates(signals)
        _assert_only_gate_failed(result, "pr_open")

    def test_effective_permission_fails_on_read_permission(self) -> None:
        """effective_permission fails when viewer_permission is READ,
        even with viewer_can_update True."""
        signals = _make_signals(
            viewer_permission="READ", viewer_can_update=True
        )
        result = self.mod.evaluate_merge_gates(signals)
        _assert_only_gate_failed(result, "effective_permission")

    def test_effective_permission_fails_when_viewer_cannot_update(
        self,
    ) -> None:
        """effective_permission fails when viewer_can_update is False,
        even with a sufficient permission level."""
        signals = _make_signals(
            viewer_permission="WRITE", viewer_can_update=False
        )
        result = self.mod.evaluate_merge_gates(signals)
        _assert_only_gate_failed(result, "effective_permission")

    def test_ci_green_on_merge_commit_fails_on_non_success_conclusion(
        self,
    ) -> None:
        """ci_green_on_merge_commit fails when the merge-commit CI
        conclusion is not SUCCESS."""
        signals = _make_signals(merge_commit_ci_conclusion="FAILURE")
        result = self.mod.evaluate_merge_gates(signals)
        _assert_only_gate_failed(result, "ci_green_on_merge_commit")

    def test_no_changes_requested_fails_on_sticky_changes_requested(
        self,
    ) -> None:
        """no_changes_requested fails when has_changes_requested is
        True."""
        signals = _make_signals(has_changes_requested=True)
        result = self.mod.evaluate_merge_gates(signals)
        _assert_only_gate_failed(result, "no_changes_requested")

    def test_no_changes_requested_fails_on_unresolved_human_threads(
        self,
    ) -> None:
        """no_changes_requested fails when
        unresolved_human_thread_count > 0, even with no sticky
        CHANGES_REQUESTED verdict."""
        signals = _make_signals(
            has_changes_requested=False,
            unresolved_human_thread_count=2,
        )
        result = self.mod.evaluate_merge_gates(signals)
        _assert_only_gate_failed(result, "no_changes_requested")

    def test_merge_state_status_ok_fails_on_dirty(self) -> None:
        """merge_state_status_ok fails when merge_state_status is
        DIRTY."""
        signals = _make_signals(merge_state_status="DIRTY")
        result = self.mod.evaluate_merge_gates(signals)
        _assert_only_gate_failed(result, "merge_state_status_ok")

    def test_merge_state_status_ok_fails_on_blocked(self) -> None:
        """merge_state_status_ok fails when merge_state_status is
        BLOCKED."""
        signals = _make_signals(merge_state_status="BLOCKED")
        result = self.mod.evaluate_merge_gates(signals)
        _assert_only_gate_failed(result, "merge_state_status_ok")

    def test_merge_state_status_ok_fails_on_behind(self) -> None:
        """merge_state_status_ok fails when merge_state_status is
        BEHIND."""
        signals = _make_signals(merge_state_status="BEHIND")
        result = self.mod.evaluate_merge_gates(signals)
        _assert_only_gate_failed(result, "merge_state_status_ok")

    def test_no_do_not_merge_banner_fails_when_banner_present(
        self,
    ) -> None:
        """no_do_not_merge_banner fails when the literal banner text
        is present in pr_body."""
        signals = _make_signals(
            pr_body="Ready to go.\n\nDO NOT MERGE until release freeze ends."
        )
        result = self.mod.evaluate_merge_gates(signals)
        _assert_only_gate_failed(result, "no_do_not_merge_banner")


# ---------------------------------------------------------------------------
# Part 2 — evaluate_merge_gates: merge_state_status_ok accepted values
# ---------------------------------------------------------------------------


class TestMergeStateStatusOkAcceptedValues:
    """merge_state_status_ok passes for CLEAN, HAS_HOOKS, and UNSTABLE."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_state_module()

    @pytest.mark.parametrize("status", ["CLEAN", "HAS_HOOKS", "UNSTABLE"])
    def test_accepted_status_passes(self, status: str) -> None:
        """Each accepted mergeStateStatus value passes the gate."""
        signals = _make_signals(merge_state_status=status)
        result = self.mod.evaluate_merge_gates(signals)
        assert _gate(result, "merge_state_status_ok")["passed"] is True


# ---------------------------------------------------------------------------
# Part 2 — evaluate_merge_gates: no_do_not_merge_banner edge cases
# ---------------------------------------------------------------------------


class TestNoDoNotMergeBanner:
    """no_do_not_merge_banner: None body passes; match is case-insensitive."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_state_module()

    def test_none_pr_body_passes(self) -> None:
        """A None pr_body is treated as having no banner."""
        signals = _make_signals(pr_body=None)
        result = self.mod.evaluate_merge_gates(signals)
        assert _gate(result, "no_do_not_merge_banner")["passed"] is True
        assert result["all_passed"] is True

    def test_case_insensitive_banner_match_still_fails(self) -> None:
        """'do NOT Merge' (mixed case) still trips the banner gate."""
        signals = _make_signals(pr_body="Draft — do NOT Merge yet, still WIP.")
        result = self.mod.evaluate_merge_gates(signals)
        _assert_only_gate_failed(result, "no_do_not_merge_banner")


# ---------------------------------------------------------------------------
# Part 2 — evaluate_merge_gates: multiple simultaneous failures
# ---------------------------------------------------------------------------


class TestEvaluateMergeGatesMultipleFailures:
    """Several failing gates are each reported independently and
    order-independently."""

    def setup_method(self) -> None:
        """Load module fresh for each test."""
        self.mod = _load_state_module()

    def test_three_failing_gates_each_reflect_their_own_condition(
        self,
    ) -> None:
        """pr_open, ci_green_on_merge_commit, and no_do_not_merge_banner
        fail simultaneously while the other three still pass —
        looked up by name, not list position."""
        signals = _make_signals(
            pr_state="MERGED",
            merged_at="2026-08-01T00:00:00Z",
            merge_commit_ci_conclusion="FAILURE",
            pr_body="DO NOT MERGE — investigating regression.",
        )
        result = self.mod.evaluate_merge_gates(signals)

        assert result["all_passed"] is False

        failing = {
            "pr_open",
            "ci_green_on_merge_commit",
            "no_do_not_merge_banner",
        }
        for name in _GATE_NAMES:
            gate = _gate(result, name)
            expected_passed = name not in failing
            assert gate["passed"] is expected_passed, (
                f"gate {name!r} passed={gate['passed']!r}, "
                f"expected {expected_passed!r}"
            )
