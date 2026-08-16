#!/usr/bin/env python3
"""Consolidated context-gathering call for the ``gh-create-issue`` skill.

Replaces the 4-5 separate ``gh`` invocations described in
``gh-create-issue/SKILL.md`` Phase 2 ("Investigate") — overlap search,
open-issue list, open-PR list, label list, and milestone list — with a
single call that returns all five as one JSON payload.

The actual judgment (is this a duplicate? does this need a milestone?)
stays with the caller; this script only fetches the candidate data.

Usage::

    gh-create-issue-context.py [--repo OWNER/REPO] [--search "keywords"]

Exit 0 on success, non-zero on ``gh`` failure.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from typing import Any

import _gh_common

# ---------------------------------------------------------------------------
# Context gathering
# ---------------------------------------------------------------------------


def gather_context(
    repo: str,
    search_keywords: str | None = None,
) -> dict[str, Any]:
    """Fetch all context data needed for Phase 2 of gh-create-issue.

    Issues four ``gh api`` calls (open issues, open PRs, labels,
    milestones), plus a fifth search call when ``search_keywords`` is
    provided. No exceptions are caught here — a ``RuntimeError`` from
    any underlying ``run_gh_api`` call propagates unmodified to the
    caller rather than yielding a partial result.

    Args:
        repo: ``owner/name`` string for the target repository.
        search_keywords: Free-text keywords to search open issues for
            potential duplicates/overlap. When ``None`` (the default)
            or omitted, the search call is skipped entirely and
            ``overlap_search`` is returned as an empty list.

    Returns:
        Dict with exactly five keys:
          - ``overlap_search``: list of issues matching
            ``search_keywords`` (``[]`` when no keywords given).
          - ``open_issues``: list of open issues, excluding PR-shaped
            items (the GitHub ``/issues`` endpoint also returns PRs).
          - ``open_prs``: list of open pull requests.
          - ``labels``: list of repository labels.
          - ``milestones``: list of repository milestones.

    Raises:
        RuntimeError: Propagated from ``_gh_common.run_gh_api`` on
            ``gh`` failure.
    """
    open_issues: list[dict[str, Any]] = _gh_common.run_gh_api(
        f"repos/{repo}/issues",
        paginate=True,
    )
    # GitHub API includes pull requests in /issues endpoint; exclude them.
    open_issues = [i for i in open_issues if "pull_request" not in i]

    open_prs: list[dict[str, Any]] = _gh_common.run_gh_api(
        f"repos/{repo}/pulls",
        paginate=True,
    )

    labels: list[dict[str, Any]] = _gh_common.run_gh_api(
        f"repos/{repo}/labels",
        paginate=True,
    )

    milestones: list[dict[str, Any]] = _gh_common.run_gh_api(
        f"repos/{repo}/milestones",
        paginate=True,
    )

    overlap_search: list[dict[str, Any]] = []
    if search_keywords:
        query = urllib.parse.quote(
            f"{search_keywords} repo:{repo} is:issue"
        )
        # The real /search/issues REST endpoint returns an envelope
        # ({"total_count", "incomplete_results", "items"}), not a bare
        # array — extract "items" server-side via jq rather than
        # unwrapping it in Python.
        overlap_search = _gh_common.run_gh_api(
            f"search/issues?q={query}",
            jq=".items",
        )

    return {
        "overlap_search": overlap_search,
        "open_issues": open_issues,
        "open_prs": open_prs,
        "labels": labels,
        "milestones": milestones,
    }


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
            "Fetch the context needed for gh-create-issue Phase 2 in a "
            "single call: overlap search, open issues, open PRs, labels, "
            "and milestones. Reads from the GitHub API via gh; writes "
            "JSON to stdout."
        )
    )
    parser.add_argument(
        "--repo",
        metavar="OWNER/REPO",
        default=None,
        help=(
            "Target repository as owner/name "
            "(default: auto-detected from cwd via gh repo view)."
        ),
    )
    parser.add_argument(
        "--search",
        metavar="KEYWORDS",
        default=None,
        help=(
            "Keywords to search open issues for potential duplicates. "
            "When omitted, overlap_search is returned as an empty list."
        ),
    )
    return parser


def main() -> int:
    """Run the gh-create-issue-context script.

    Returns:
        Exit code: 0 on success, 1 on gh failure.
    """
    parser = _build_parser()
    args = parser.parse_args()

    try:
        if args.repo:
            repo = args.repo
        else:
            owner, name = _gh_common.get_current_repo()
            repo = f"{owner}/{name}"

        context = gather_context(repo=repo, search_keywords=args.search)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(context))
    return 0


if __name__ == "__main__":
    sys.exit(main())
