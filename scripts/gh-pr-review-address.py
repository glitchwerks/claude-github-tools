#!/usr/bin/env python3
"""Classify and filter GitHub pull-request review feedback.

The pure functions in this module operate on already-fetched GitHub data.
The CLI reads JSON from standard input and emits deterministic JSON results
for use by the ``gh-pr-review-address`` workflow.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from typing import Any


_HUNK_HEADER_RE: re.Pattern[str] = re.compile(
    r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@",
    re.MULTILINE,
)
# The P10-P19 upper bound is intentional: test_p30_does_not_falsely_match_p3 pins P30 as non-matching.
_CODEX_PRIORITY_RE: re.Pattern[str] = re.compile(r"\bP(?:[3-9]|1\d)\b")
_CLAUDE_JSON_LOW_RE: re.Pattern[str] = re.compile(
    r'"tier"\s*:\s*"low"', re.IGNORECASE
)
_CLAUDE_TEXT_LOW_RE: re.Pattern[str] = re.compile(
    r"\btier\s*:\s*low\b", re.IGNORECASE
)
_COPILOT_STYLE_RE: re.Pattern[str] = re.compile(
    r"\b(?:style|formatting|preference)\b", re.IGNORECASE
)
_COPILOT_CORRECTNESS_RE: re.Pattern[str] = re.compile(
    r"\b(?:bug|incorrect|broken)\b", re.IGNORECASE
)
_DEFAULT_BOT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "chatgpt-codex-connector[bot]",
        "coderabbitai[bot]",
        "copilot-pull-request-reviewer[bot]",
    }
)


def _patch_covers_line(patch: str | None, line: int) -> bool:
    """Return whether a unified-diff hunk covers a new-file line.

    Args:
        patch: Unified diff text, or None when GitHub omits the patch.
        line: New-file line number to locate.

    Returns:
        True when the line falls within any new-file hunk range.
    """
    if not patch:
        return False

    for match in _HUNK_HEADER_RE.finditer(patch):
        start = int(match.group(1))
        count = int(match.group(2) or 1)
        if start <= line <= start + count - 1:
            return True
    return False


def _parse_iso8601(value: str) -> datetime:
    """Parse an ISO 8601 timestamp, including a trailing ``Z``.

    Args:
        value: ISO 8601 timestamp string.

    Returns:
        Parsed datetime value.

    Raises:
        ValueError: If the timestamp is not valid ISO 8601.
    """
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _commit_touches_thread(
    thread: dict[str, Any],
    commits: list[dict[str, Any]],
    line: int | None,
) -> bool:
    """Return whether a later commit patch covers a thread's line.

    Args:
        thread: Review thread with path and first-comment timestamp.
        commits: Commit records containing timestamps and changed files.
        line: Current thread line, or its original-line fallback.

    Returns:
        True when a later commit changes the thread's file and line.
    """
    if line is None:
        return False

    comments = thread["comments"]["nodes"]
    if not comments:
        return False

    try:
        created_at = _parse_iso8601(comments[0]["createdAt"])
    except ValueError:
        return False

    for commit in commits:
        try:
            commit_date = _parse_iso8601(commit["date"])
        except ValueError:
            continue
        if commit_date <= created_at:
            continue
        for changed_file in commit.get("files", []):
            if changed_file.get("filename") != thread["path"]:
                continue
            if _patch_covers_line(changed_file.get("patch"), line):
                return True
    return False


def _sticky_blockers(reviews: list[dict[str, Any]]) -> list[str]:
    """Find reviewers whose latest verdict requests changes.

    Args:
        reviews: Pull-request review records.

    Returns:
        Reviewer logins in first-seen login order whose latest review is
        ``CHANGES_REQUESTED``.
    """
    latest_by_login: dict[str, dict[str, Any]] = {}
    for review in reviews:
        login = review["user"]["login"]
        current = latest_by_login.get(login)
        try:
            submitted_at = _parse_iso8601(review["submitted_at"])
            current_submitted_at = (
                _parse_iso8601(current["submitted_at"])
                if current is not None
                else None
            )
        except ValueError:
            continue
        if current_submitted_at is None or submitted_at > current_submitted_at:
            latest_by_login[login] = review

    return [
        login
        for login, review in latest_by_login.items()
        if review["state"] == "CHANGES_REQUESTED"
    ]


def compute_resolution_state(
    threads: list[dict[str, Any]],
    commits: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute thread resolution classifications and sticky blockers.

    Args:
        threads: Review threads with resolution and anchor information.
        commits: Commits with dates and per-file unified diff patches.
        reviews: Submitted pull-request reviews.

    Returns:
        Thread classifications and reviewer sticky-blocker logins.
    """
    classified_threads: list[dict[str, Any]] = []
    for thread in threads:
        line = (
            thread["line"]
            if thread["line"] is not None
            else thread["originalLine"]
        )
        if thread["isResolved"]:
            classification = "RESOLVED"
        elif thread["isOutdated"] or _commit_touches_thread(
            thread, commits, line
        ):
            classification = "CANDIDATE-ADDRESSED"
        else:
            classification = "OPEN"

        classified_threads.append(
            {
                "id": thread["id"],
                "classification": classification,
                "path": thread["path"],
                "line": line,
            }
        )

    return {
        "threads": classified_threads,
        "sticky_blockers": _sticky_blockers(reviews),
    }


def _suppression_rule(author_login: str, body: str) -> str | None:
    """Return the matching bot-specific suppression rule, if any.

    Args:
        author_login: Exact GitHub login of the comment author.
        body: Inline review comment body.

    Returns:
        A distinct rule label for a positive match, otherwise None.
    """
    if author_login == "coderabbitai[bot]":
        stripped_body = body.lstrip()
        if stripped_body.startswith(("Nitpick:", "Nit:")):
            return "coderabbit-nitpick-prefix"
    elif author_login == "chatgpt-codex-connector[bot]":
        if _CODEX_PRIORITY_RE.search(body):
            return "codex-low-priority-tag"
    elif author_login == "claude-action-runner[bot]":
        if _CLAUDE_JSON_LOW_RE.search(body) or _CLAUDE_TEXT_LOW_RE.search(
            body
        ):
            return "claude-low-tier-marker"
    elif author_login == "copilot-pull-request-reviewer[bot]":
        has_style = _COPILOT_STYLE_RE.search(body) is not None
        has_correctness = _COPILOT_CORRECTNESS_RE.search(body) is not None
        if has_style and not has_correctness:
            return "copilot-style-only"
    return None


def classify_suppression_candidates(
    comments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Classify comments using bot-specific suppression patterns.

    Args:
        comments: Simplified inline review comment records.

    Returns:
        One suppression classification record per input comment.
    """
    results: list[dict[str, Any]] = []
    for comment in comments:
        matched_rule = _suppression_rule(
            comment["author_login"], comment["body"]
        )
        results.append(
            {
                "comment_id": comment["comment_id"],
                "author_login": comment["author_login"],
                "suppress_candidate": matched_rule is not None,
                "matched_rule": matched_rule,
            }
        )
    return results


def filter_resolvable_threads(
    threads: list[dict[str, Any]],
    mode: str = "B",
    bot_allowlist: set[str] | None = None,
) -> dict[str, list[str]]:
    """Select unresolved bot-authored threads eligible for resolution.

    Args:
        threads: Simplified review thread records.
        mode: ``A`` for all eligible unresolved threads or ``B`` to also
            require an outdated thread.
        bot_allowlist: Optional exact author-login allowlist. Defaults to
            the supported review bots when None.

    Returns:
        Mapping containing an input-ordered list of resolvable thread
        identifiers.
    """
    effective_allowlist = (
        _DEFAULT_BOT_ALLOWLIST if bot_allowlist is None else bot_allowlist
    )
    resolvable_thread_ids: list[str] = []
    for thread in threads:
        comments = thread["comments"]["nodes"]
        if not comments:
            continue
        author_login = comments[0]["author"]["login"]
        if thread["isResolved"]:
            continue
        if author_login not in effective_allowlist:
            continue
        if mode == "B" and not thread["isOutdated"]:
            continue
        resolvable_thread_ids.append(thread["id"])
    return {"resolvable_thread_ids": resolvable_thread_ids}


def _build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser.

    Returns:
        Configured argument parser with all supported subcommands.
    """
    parser = argparse.ArgumentParser(
        description="Classify GitHub pull-request review feedback."
    )
    parser.add_argument(
        "--repo",
        metavar="OWNER/REPO",
        default=None,
        help="Target repository as owner/name (reserved for future use).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "resolution-state",
        help="Compute review-thread resolution state.",
    )
    subparsers.add_parser(
        "suppression-candidates",
        help="Classify bot comments for suppression.",
    )
    resolvable_parser = subparsers.add_parser(
        "resolvable-threads",
        help="Select threads eligible for automatic resolution.",
    )
    resolvable_parser.add_argument(
        "--mode",
        choices=("A", "B"),
        default="B",
        help="Resolution mode (default: B).",
    )
    return parser


def emit_output(result: Any) -> None:
    """Write a JSON result to standard output using UTF-8.

    Args:
        result: JSON-serializable result value.
    """
    payload = json.dumps(result, ensure_ascii=False)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(  # type: ignore[attr-defined]
            encoding="utf-8", errors="replace"
        )
    print(payload)


def main(argv: list[str] | None = None) -> int:
    """Run the pull-request review classification CLI.

    Args:
        argv: Optional argument list for testing; defaults to sys.argv.

    Returns:
        Exit code 0 after emitting the selected classification result.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON input: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "resolution-state":
            result = compute_resolution_state(
                threads=input_data["threads"],
                commits=input_data["commits"],
                reviews=input_data["reviews"],
            )
        elif args.command == "suppression-candidates":
            result = classify_suppression_candidates(
                comments=input_data["comments"]
            )
        else:
            result = filter_resolvable_threads(
                threads=input_data["threads"], mode=args.mode
            )
    except KeyError as exc:
        print(
            f"error: missing required input field: {exc.args[0]}",
            file=sys.stderr,
        )
        return 2

    emit_output(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
