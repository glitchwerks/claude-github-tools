#!/usr/bin/env python3
"""Deterministic exclusion filter for low-blast-radius GitHub issues.

Fetches all open issues in the current (or specified) repo via the ``gh``
CLI, applies a multi-criteria exclusion filter to remove high-risk or
in-progress items, and emits the surviving candidates as JSON on stdout.
Each surviving issue includes a ``signals`` sub-object for the downstream
LLM ranking pass in the ``gh-quick-wins`` skill.

Output (to stdout):
    JSON array of surviving issue objects.  Each object is the original
    ``gh issue list`` dict augmented with a ``signals`` key containing:

    - ``touches_load_bearing`` (bool): body or title mentions a path that
      affects every future session (CLAUDE.md, agents/, skills/, hooks/,
      deploy.py).
    - ``body_appears_drafted`` (bool): body is long (>800 chars) AND
      contains a fenced code block, markdown table, or >3 ``###`` headers.
    - ``comment_count`` (int): number of comments on the issue.
    - ``days_since_update`` (int): days since the issue was last updated.
    - ``label_set`` (list[str]): names of all labels on the issue.

Emits ``[]`` and exits 0 when zero issues survive the filter.

Usage::

    gh-quick-wins.py [--repo OWNER/REPO] [--limit N] [--max-ac N]

Exit 0 on success, non-zero on ``gh`` failure.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Labels that unconditionally exclude an issue from the quick-wins list.
_EXCLUDED_LABELS: frozenset[str] = frozenset(
    [
        "blocked",
        "deferred",
        "epic",
        "meta",
        "umbrella",
        "needs-design",
        "needs-discussion",
        "wontfix",
    ]
)

#: Default maximum number of AC checkboxes before an issue is excluded.
_DEFAULT_MAX_AC: int = 5

#: Safety ceiling on the total number of issues fetched.
_DEFAULT_LIMIT: int = 5000

#: Regex matching GitHub-flavoured task-list checkboxes (checked or
#: unchecked). GitHub task-list syntax accepts -, *, and + as bullet
#: markers.
_CHECKBOX_RE: re.Pattern[str] = re.compile(
    r"^\s*[-*+]\s*\[[ x]\]\s+", re.MULTILINE
)

#: Regex matching "blocked by #N" or "depends on #N" (case-insensitive).
_BLOCKER_PROSE_RE: re.Pattern[str] = re.compile(
    r"(blocked\s+by|depends\s+on)\s+#\d+", re.IGNORECASE
)

#: Strings whose presence in body or title indicates a load-bearing path.
_LOAD_BEARING_MARKERS: tuple[str, ...] = (
    "CLAUDE.md",
    "agents/",
    "skills/",
    "hooks/",
    "deploy.py",
)

#: Minimum body length (chars) required for body_appears_drafted check.
_DRAFTED_MIN_LEN: int = 800

#: Regex for fenced code blocks.
_CODE_BLOCK_RE: re.Pattern[str] = re.compile(r"```")

#: Regex for markdown table rows (``| col |`` shape).
_TABLE_RE: re.Pattern[str] = re.compile(r"^\s*\|.+\|", re.MULTILINE)

#: Regex for H3 headers.
_H3_RE: re.Pattern[str] = re.compile(r"^###\s+", re.MULTILINE)


# ---------------------------------------------------------------------------
# Signal computation
# ---------------------------------------------------------------------------


def _touches_load_bearing(issue: dict[str, Any]) -> bool:
    """Return True if the issue title or body references load-bearing paths.

    Load-bearing paths are those that affect every future agent session:
    ``CLAUDE.md``, ``agents/``, ``skills/``, ``hooks/``, ``deploy.py``.

    Args:
        issue: GitHub issue dict with ``title`` and ``body`` keys.

    Returns:
        True when any load-bearing marker appears in the title or body.
    """
    haystack = (
        (issue.get("title") or "") + "\n" + (issue.get("body") or "")
    )
    return any(marker in haystack for marker in _LOAD_BEARING_MARKERS)


def _body_appears_drafted(issue: dict[str, Any]) -> bool:
    """Return True if the issue body looks like it contains the deliverable.

    Heuristic: body length > 800 chars AND at least one of:
    - a fenced code block (```),
    - a markdown table row (``| ... |``),
    - more than 3 ``###`` headers.

    Args:
        issue: GitHub issue dict with a ``body`` key.

    Returns:
        True when the body meets the drafted heuristic criteria.
    """
    body: str = issue.get("body") or ""
    if len(body) <= _DRAFTED_MIN_LEN:
        return False

    has_code_block = bool(_CODE_BLOCK_RE.search(body))
    has_table = bool(_TABLE_RE.search(body))
    has_many_headers = len(_H3_RE.findall(body)) > 3

    return has_code_block or has_table or has_many_headers


def _comment_count(issue: dict[str, Any]) -> int:
    """Return the number of comments on an issue.

    ``gh`` returns ``comments`` in two different shapes depending on the
    subcommand:

    - ``gh issue list --json comments`` → **integer count** (scalar)
    - ``gh issue view --json comments`` → **array of comment objects**

    Args:
        issue: GitHub issue dict with a ``comments`` key (int or list).

    Returns:
        Comment count; 0 if absent or in an unexpected shape.
    """
    comments = issue.get("comments")
    if isinstance(comments, int):
        return comments
    if isinstance(comments, list):
        return len(comments)
    return 0


def _days_since_update(issue: dict[str, Any]) -> int:
    """Return the number of whole days since the issue was last updated.

    Args:
        issue: GitHub issue dict with an ``updatedAt`` ISO 8601 timestamp.

    Returns:
        Non-negative integer days since ``updatedAt``.  Returns 0 on
        parse failure.
    """
    updated_at: str = issue.get("updatedAt") or ""
    try:
        dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        now = datetime.now(tz=timezone.utc)
        return max(0, (now - dt).days)
    except (ValueError, TypeError):
        return 0


def _compute_signals(issue: dict[str, Any]) -> dict[str, Any]:
    """Compute the signals sub-object for a surviving issue.

    Args:
        issue: GitHub issue dict that has passed the exclusion filter.

    Returns:
        Dict with keys ``touches_load_bearing``, ``body_appears_drafted``,
        ``comment_count``, ``days_since_update``, and ``label_set``.
    """
    label_set: list[str] = [
        lbl.get("name", "") for lbl in (issue.get("labels") or [])
    ]
    return {
        "touches_load_bearing": _touches_load_bearing(issue),
        "body_appears_drafted": _body_appears_drafted(issue),
        "comment_count": _comment_count(issue),
        "days_since_update": _days_since_update(issue),
        "label_set": label_set,
    }


# ---------------------------------------------------------------------------
# Exclusion filter
# ---------------------------------------------------------------------------


def apply_exclusion_filter(
    issues: list[dict[str, Any]],
    max_ac: int = _DEFAULT_MAX_AC,
) -> list[dict[str, Any]]:
    """Apply the deterministic exclusion filter to a list of open issues.

    An issue is excluded if ANY of the following are true:

    - It has a label in the excluded label set (``blocked``, ``deferred``,
      ``epic``, ``meta``, ``umbrella``, ``needs-design``,
      ``needs-discussion``, ``wontfix``).
    - It has any assignee.
    - Its body contains more than ``max_ac`` task-list checkboxes
      (checked or unchecked).
    - Its body contains ``blocked by #N`` or ``depends on #N`` prose
      (case-insensitive).

    Surviving issues are returned as new dict objects with a ``signals``
    sub-object attached.

    Args:
        issues: List of issue dicts from ``gh issue list --json``.
        max_ac: Maximum allowed number of AC checkboxes; issues with
            strictly more than this many are excluded. Default: 5.

    Returns:
        Filtered list of issue dicts, each augmented with a ``signals``
        key.
    """
    survivors: list[dict[str, Any]] = []

    for issue in issues:
        # --- Label check ---
        label_names: set[str] = {
            lbl.get("name", "") for lbl in (issue.get("labels") or [])
        }
        if label_names & _EXCLUDED_LABELS:
            continue

        # --- Assignee check ---
        assignees: list[Any] = issue.get("assignees") or []
        if assignees:
            continue

        # --- AC checkbox count check ---
        body: str = issue.get("body") or ""
        checkbox_matches = _CHECKBOX_RE.findall(body)
        if len(checkbox_matches) > max_ac:
            continue

        # --- Blocker prose check ---
        if _BLOCKER_PROSE_RE.search(body):
            continue

        # --- Issue survives: attach signals ---
        augmented = dict(issue)
        augmented["signals"] = _compute_signals(issue)
        survivors.append(augmented)

    return survivors


# ---------------------------------------------------------------------------
# REST field normalization
# ---------------------------------------------------------------------------


def _normalize_rest_issue(d: dict[str, Any]) -> dict[str, Any]:
    """Normalize a REST API issue dict to the camelCase field names used downstream.

    The GitHub REST ``/repos/{owner}/{repo}/issues`` endpoint returns
    snake_case field names (``updated_at``, ``html_url``), but the rest of
    the pipeline was written against the ``gh issue list --json`` field names
    (``updatedAt``, ``url``).  This helper maps the REST names to their
    camelCase equivalents so the pipeline remains field-agnostic after the
    fetch boundary.

    Only the fields the pipeline reads are mapped; all other fields are
    preserved unchanged.

    Args:
        d: Raw issue dict from the GitHub REST API.

    Returns:
        New dict with ``updated_at`` → ``updatedAt`` and
        ``html_url`` → ``url`` remapped; all other keys preserved.
    """
    out = dict(d)
    if "updated_at" in out:
        out.setdefault("updatedAt", out["updated_at"])
    if "html_url" in out:
        out.setdefault("url", out["html_url"])
    return out


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


def fetch_issues(
    repo: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Fetch all open issues from GitHub via the ``gh api`` CLI with pagination.

    Uses ``gh api --paginate --slurp`` to retrieve the full open-issue
    backlog rather than a single page.  The GitHub ``/issues`` endpoint
    returns both issues and pull requests; items with a ``pull_request``
    key are filtered out before returning.

    Args:
        repo: ``owner/name`` string, or ``None`` to auto-detect from cwd.
        limit: Safety ceiling on the total number of issues returned.

    Returns:
        List of issue dicts from the GitHub API (PRs excluded). If more
        than ``limit`` issues were fetched, the list is truncated to
        ``limit`` and a warning is printed to stderr noting the cap.

    Raises:
        RuntimeError: If ``gh`` exits non-zero or returns invalid JSON.
    """
    # Resolve the repo path for the gh api endpoint.
    if repo:
        owner_repo = repo
    else:
        detect = subprocess.run(
            [
                "gh",
                "repo",
                "view",
                "--json",
                "nameWithOwner",
                "-q",
                ".nameWithOwner",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if detect.returncode != 0:
            raise RuntimeError(
                f"gh repo view failed (exit {detect.returncode}): "
                f"{detect.stderr.strip()}"
            )
        owner_repo = detect.stdout.strip()

    cmd = [
        "gh",
        "api",
        "-X",
        "GET",
        f"/repos/{owner_repo}/issues",
        "-F",
        "state=open",
        "-F",
        "per_page=100",
        "--paginate",
        "--slurp",
    ]

    result = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh api failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    try:
        # --slurp wraps all paginated arrays into a single outer array,
        # so the shape is [[page1_item, ...], [page2_item, ...]].
        # Flatten into a single list.
        pages: list[list[dict[str, Any]]] = json.loads(result.stdout)
        all_items: list[dict[str, Any]] = [
            item for page in pages for item in page
        ]
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError(
            f"gh api returned invalid JSON: {exc}"
        ) from exc

    # The /issues endpoint returns PRs alongside issues; filter them out.
    # Also normalize REST snake_case field names to camelCase so the rest
    # of the pipeline can read updatedAt and url regardless of source.
    issues_only = [
        _normalize_rest_issue(item)
        for item in all_items
        if "pull_request" not in item
    ]

    # Apply the safety ceiling, warning on stderr if issues are dropped
    # so the caller knows the result may be truncated (Issue #19).
    if len(issues_only) > limit:
        print(
            f"warning: fetched {len(issues_only)} open issues but "
            f"limit is {limit}; results are truncated and some open "
            "issues will not be shown",
            file=sys.stderr,
        )
    return issues_only[:limit]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the CLI.

    Returns:
        Configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Filter open GitHub issues to low-blast-radius quick-win "
            "candidates.  Emits a JSON array on stdout; each surviving "
            "issue includes a 'signals' object for LLM ranking."
        )
    )
    parser.add_argument(
        "--repo",
        metavar="OWNER/REPO",
        default=None,
        help=(
            "Target repository as owner/name "
            "(default: auto-detected from cwd via gh)."
        ),
    )
    parser.add_argument(
        "--limit",
        metavar="N",
        type=int,
        default=_DEFAULT_LIMIT,
        help=(
            f"Safety ceiling on total issues fetched "
            f"(default: {_DEFAULT_LIMIT})."
        ),
    )
    parser.add_argument(
        "--max-ac",
        metavar="N",
        type=int,
        default=_DEFAULT_MAX_AC,
        help=(
            "Exclude issues with more than N AC checkboxes "
            f"(default: {_DEFAULT_MAX_AC})."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the gh-quick-wins exclusion filter.

    Args:
        argv: Optional argument list for testing; defaults to sys.argv.

    Returns:
        Exit code: 0 on success, 1 on gh failure.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        issues = fetch_issues(repo=args.repo, limit=args.limit)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    survivors = apply_exclusion_filter(issues, max_ac=args.max_ac)

    emit_output(survivors)
    return 0


def emit_output(survivors: list[dict[str, Any]]) -> None:
    """Write the survivors JSON to stdout.

    Reconfigures stdout to UTF-8 when the stream supports it (CPython
    direct run on Windows where the default console codec may be
    cp1252). Falls back to a plain ``print`` when ``sys.stdout``
    lacks a ``reconfigure`` method (e.g., pytest's ``capsys`` wrapper).

    Args:
        survivors: List of filtered+signal-augmented issue dicts to
            serialize as JSON.
    """
    payload = json.dumps(survivors, ensure_ascii=False)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(  # type: ignore[attr-defined]
            encoding="utf-8", errors="replace"
        )
    print(payload)


if __name__ == "__main__":
    sys.exit(main())
