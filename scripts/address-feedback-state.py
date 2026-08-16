#!/usr/bin/env python3
"""Manage address-feedback run state and evaluate PR merge gates."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def init_state(run_id: str, owner_repo: str, target: str) -> dict[str, Any]:
    """Build a fresh address-feedback run state.

    Args:
        run_id: Unique identifier for the run.
        owner_repo: Repository slug in ``owner/repo`` form.
        target: Pull request number or branch target.

    Returns:
        A new state dict with counters and collections reset.
    """
    return {
        "run_id": run_id,
        "target": target,
        "owner_repo": owner_repo,
        "tick_count": 0,
        "act_rounds": 0,
        "last_findings": None,
        "act_pushes": [],
        "parked_issues": [],
    }


def is_continuation(
    existing_state: dict[str, Any] | None,
    owner_repo: str,
    target: str,
) -> bool:
    """Determine whether existing state belongs to the requested target.

    Args:
        existing_state: Previously loaded run state, or None.
        owner_repo: Repository slug requested for the current run.
        target: Pull request number or branch requested for the current run.

    Returns:
        True when both repository and target match; otherwise False.
    """
    if existing_state is None:
        return False
    return (
        existing_state["owner_repo"] == owner_repo
        and existing_state["target"] == target
    )


def check_caps(
    state: dict[str, Any],
    max_ticks: int,
    max_act_rounds: int,
) -> dict[str, Any]:
    """Check persisted loop counters against their hard caps.

    Tick count is checked first and breaches only when strictly greater
    than its cap. Act rounds breach when equal to or greater than their cap.

    Args:
        state: Run state containing ``tick_count`` and ``act_rounds``.
        max_ticks: Maximum tick count before the next tick is capped.
        max_act_rounds: Maximum number of action rounds.

    Returns:
        A dict containing the capped flag and the first breached reason.
    """
    if state["tick_count"] > max_ticks:
        return {"capped": True, "reason": "tick_count"}
    if state["act_rounds"] >= max_act_rounds:
        return {"capped": True, "reason": "act_rounds"}
    return {"capped": False, "reason": None}


def load_state_file(path: str) -> dict[str, Any] | None:
    """Load a UTF-8 JSON state file.

    Args:
        path: Path to the state file.

    Returns:
        The parsed state dict, or None when the file does not exist.

    Raises:
        json.JSONDecodeError: If the file does not contain valid JSON.
        OSError: If the file cannot be inspected or read.
    """
    state_path = Path(path)
    if not state_path.exists():
        return None
    with state_path.open(encoding="utf-8") as state_file:
        return json.load(state_file)


def save_state_file(path: str, state: dict[str, Any]) -> None:
    """Save run state as UTF-8 JSON, creating parent directories.

    Args:
        path: Destination path for the state file.
        state: Run state to serialize.

    Raises:
        OSError: If directories or the state file cannot be written.
        TypeError: If the state contains a non-serializable value.
    """
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.open("w", encoding="utf-8") as state_file:
        json.dump(state, state_file, ensure_ascii=False)


def evaluate_merge_gates(signals: dict[str, Any]) -> dict[str, Any]:
    """Evaluate the six independent merge gates for a pull request.

    Args:
        signals: Pre-fetched pull request signals used by the gates.

    Returns:
        A dict containing six gate results and an aggregate pass flag.
    """
    pr_open = (
        signals["pr_state"] == "OPEN" and signals["merged_at"] is None
    )
    effective_permission = (
        signals["viewer_permission"] in {"WRITE", "ADMIN", "MAINTAIN"}
        and signals["viewer_can_update"] is True
    )
    ci_green = signals["merge_commit_ci_conclusion"] == "SUCCESS"
    no_changes_requested = (
        signals["has_changes_requested"] is not True
        and signals["unresolved_human_thread_count"] <= 0
    )
    merge_state_ok = signals["merge_state_status"] in {
        "CLEAN",
        "HAS_HOOKS",
        "UNSTABLE",
    }
    pr_body = signals["pr_body"]
    no_do_not_merge_banner = (
        pr_body is None or "do not merge" not in pr_body.lower()
    )

    gates = [
        {
            "name": "pr_open",
            "passed": pr_open,
            "detail": (
                "PR is open and unmerged."
                if pr_open
                else "PR is not open or has already been merged."
            ),
        },
        {
            "name": "effective_permission",
            "passed": effective_permission,
            "detail": (
                "Viewer has sufficient permission and can update the PR."
                if effective_permission
                else "Viewer lacks sufficient permission or update access."
            ),
        },
        {
            "name": "ci_green_on_merge_commit",
            "passed": ci_green,
            "detail": (
                "Merge-commit CI concluded successfully."
                if ci_green
                else "Merge-commit CI did not conclude successfully."
            ),
        },
        {
            "name": "no_changes_requested",
            "passed": no_changes_requested,
            "detail": (
                "No changes-requested review or unresolved human thread."
                if no_changes_requested
                else "Changes are requested or human threads remain open."
            ),
        },
        {
            "name": "merge_state_status_ok",
            "passed": merge_state_ok,
            "detail": (
                "Merge state status is accepted."
                if merge_state_ok
                else "Merge state status is not accepted."
            ),
        },
        {
            "name": "no_do_not_merge_banner",
            "passed": no_do_not_merge_banner,
            "detail": (
                "PR body has no do-not-merge banner."
                if no_do_not_merge_banner
                else "PR body contains a do-not-merge banner."
            ),
        },
    ]
    return {
        "gates": gates,
        "all_passed": all(gate["passed"] for gate in gates),
    }


def _build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns:
        Configured argument parser with three JSON-producing subcommands.
    """
    parser = argparse.ArgumentParser(
        description="Manage address-feedback state and merge gates."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-state")
    init_parser.add_argument("run_id")
    init_parser.add_argument("owner_repo")
    init_parser.add_argument("target")

    caps_parser = subparsers.add_parser("check-caps")
    caps_parser.add_argument("state", help="State object as JSON.")
    caps_parser.add_argument("max_ticks", type=int)
    caps_parser.add_argument("max_act_rounds", type=int)

    gates_parser = subparsers.add_parser("evaluate-merge-gates")
    gates_parser.add_argument("signals", help="PR signals object as JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command-line wrapper and print JSON to stdout.

    Args:
        argv: Optional argument list; defaults to ``sys.argv``.

    Returns:
        Process exit code 0 after a successful command.
    """
    args = _build_parser().parse_args(argv)
    if args.command == "init-state":
        result = init_state(args.run_id, args.owner_repo, args.target)
    elif args.command == "check-caps":
        result = check_caps(
            json.loads(args.state),
            args.max_ticks,
            args.max_act_rounds,
        )
    else:
        result = evaluate_merge_gates(json.loads(args.signals))

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
