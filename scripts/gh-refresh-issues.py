#!/usr/bin/env python3
"""List open GitHub issues (and optionally PRs) grouped by milestone.

Outputs a deterministic markdown report with one table per milestone,
sorted by milestone number ascending, with a final '## No milestone'
group last. All data fetched via ``gh api`` (raw REST) — no LLM tokens
consumed.

Output format:
  - ``## <Milestone> (N open)`` heading per group
  - Table: ``# | Title | Labels | Assignee | Created`` (issues-only)
  - Table: ``# | Type | Title | Labels | Assignee | Created`` (with --prs)
  - Summary: ``**Total: N open across M milestones (fetched <ts>).**``

Usage::

    gh-refresh-issues.py [label_filter] [--prs]

Exit 0 on success, non-zero on ``gh`` failure.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import Any

import _gh_common

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EM_DASH = "—"
NO_MILESTONE_KEY: tuple[float, str] = (float("inf"), "No milestone")
TITLE_MAX = 60

# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


def fetch_data(
    repo: str,
    include_prs: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch open issues (and optionally open PRs) from the GitHub API.

    The ``/repos/{repo}/issues`` endpoint returns both issues and PRs;
    PR items are identified by the ``pull_request`` key and stripped from
    the issues list. PRs are fetched separately from
    ``/repos/{repo}/pulls`` when ``include_prs`` is True.

    Args:
        repo: ``owner/name`` string, e.g. ``acme/my-repo``.
        include_prs: When True, also fetch open PRs from the pulls
            endpoint.

    Returns:
        Tuple of ``(issues, prs)`` where each element is a list of dicts
        from the GitHub REST API. ``prs`` is always ``[]`` when
        ``include_prs`` is False.

    Raises:
        RuntimeError: Propagated from ``_gh_common.run_gh_api`` when the
            ``gh`` CLI exits non-zero.
    """
    raw: list[dict[str, Any]] = _gh_common.run_gh_api(
        f"repos/{repo}/issues",
        paginate=True,
        jq=".",
    )
    # /issues includes PRs — strip them so they don't double-count.
    issues = [item for item in raw if "pull_request" not in item]

    prs: list[dict[str, Any]] = []
    if include_prs:
        prs = _gh_common.run_gh_api(
            f"repos/{repo}/pulls",
            paginate=True,
            jq=".",
        )

    return issues, prs


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def apply_label_filter(
    items: list[dict[str, Any]],
    label: str | None,
) -> list[dict[str, Any]]:
    """Filter items to those carrying a specific label.

    Args:
        items: List of issue or PR dicts from the GitHub REST API.
        label: Label name to filter by, or None to return all items.

    Returns:
        Filtered list. When ``label`` is None all items pass through.
    """
    if label is None:
        return items
    return [
        item
        for item in items
        if any(lbl["name"] == label for lbl in item.get("labels", []))
    ]


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _milestone_sort_key(item: dict[str, Any]) -> tuple[float, str]:
    """Return a sort key for grouping items by milestone.

    Args:
        item: Issue or PR dict from the GitHub REST API.

    Returns:
        Tuple ``(milestone_number, milestone_title)`` for sorting.
        Items with no milestone assigned use the sentinel
        ``(inf, "No milestone")`` so they sort last.
    """
    ms = item.get("milestone")
    if ms is None:
        return NO_MILESTONE_KEY
    return (float(ms["number"]), ms["title"])


def _render_labels(item: dict[str, Any]) -> str:
    """Render the Labels cell value for a table row.

    Args:
        item: Issue or PR dict from the GitHub REST API.

    Returns:
        Comma-separated backtick-wrapped label names, or em-dash if the
        item carries no labels.
    """
    labels = [lbl["name"] for lbl in item.get("labels", [])]
    if not labels:
        return EM_DASH
    return ", ".join(f"`{lbl}`" for lbl in labels)


def _render_assignee(item: dict[str, Any]) -> str:
    """Render the Assignee cell value for a table row.

    Args:
        item: Issue or PR dict from the GitHub REST API.

    Returns:
        GitHub login of the first assignee, or ``"Unassigned"`` when the
        item has no assignees.
    """
    assignees = item.get("assignees", [])
    if not assignees:
        return "Unassigned"
    return assignees[0]["login"]


def _render_created(item: dict[str, Any]) -> str:
    """Render the Created date cell (YYYY-MM-DD) for a table row.

    The GitHub REST API exposes creation time as ``created_at`` (snake)
    on the /issues and /pulls endpoints. The ``gh`` CLI JSON aliases it
    as ``createdAt`` (camel) in some contexts. This helper checks both.

    Args:
        item: Issue or PR dict from the GitHub REST API.

    Returns:
        Date string in ``YYYY-MM-DD`` format, or empty string if both
        ``createdAt`` and ``created_at`` are absent.
    """
    ts = item.get("createdAt") or item.get("created_at") or ""
    return ts[:10]


def _truncate(text: str, max_len: int = TITLE_MAX) -> str:
    """Truncate text to at most max_len characters with a trailing ellipsis.

    Args:
        text: Input string.
        max_len: Maximum allowed length including the ellipsis character.

    Returns:
        Original string when it fits; truncated string ending with
        ``"…"`` (horizontal ellipsis) when it does not.
    """
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _item_type(item: dict[str, Any], is_pr: bool) -> str:
    """Return the Type cell value for an item in the combined PRs view.

    Args:
        item: Issue or PR dict from the GitHub REST API.
        is_pr: True when the item came from the /pulls endpoint.

    Returns:
        ``"Issue"``, ``"PR"``, or ``"PR (draft)"``.
    """
    if not is_pr:
        return "Issue"
    if item.get("draft", False):
        return "PR (draft)"
    return "PR"


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def render_report(
    issues: list[dict[str, Any]],
    prs: list[dict[str, Any]],
    include_prs: bool,
) -> str:
    """Render the full milestone-grouped markdown report.

    Issues and (optionally) PRs are merged, grouped by milestone in
    ascending milestone-number order, and formatted as GitHub-flavoured
    markdown tables. The no-milestone group always renders last.

    Args:
        issues: Open issue dicts from the GitHub REST API.
        prs: Open PR dicts (may be empty when ``include_prs`` is False).
        include_prs: When True, merge PRs into the milestone tables and
            add a Type column; when False, issues-only layout is used.

    Returns:
        Multi-line markdown string ready to print to stdout.
    """
    if not issues and not prs:
        fetched_at = datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
        return (
            f"No open issues found (fetched {fetched_at}).\n\n"
            f"**Total: 0 open across 0 milestones"
            f" (fetched {fetched_at}).**"
        )

    # Tag items with their kind before grouping.
    tagged: list[tuple[dict[str, Any], bool]] = [
        (issue, False) for issue in issues
    ]
    if include_prs:
        tagged.extend((pr, True) for pr in prs)

    # Partition into milestone-keyed buckets.
    groups: dict[
        tuple[float, str], list[tuple[dict[str, Any], bool]]
    ] = {}
    for item, is_pr in tagged:
        key = _milestone_sort_key(item)
        groups.setdefault(key, []).append((item, is_pr))

    # Sort groups: milestone number ascending; no-milestone sentinel last.
    sorted_keys = sorted(groups.keys(), key=lambda k: k)

    lines: list[str] = []
    total_count = 0
    milestone_count = 0

    for key in sorted_keys:
        group = groups[key]
        # Within-group: oldest createdAt first.
        group.sort(
            key=lambda pair: (
                pair[0].get("createdAt") or pair[0].get("created_at") or ""
            )
        )

        _, ms_title = key
        heading_title = (
            "No milestone" if ms_title == "No milestone" else ms_title
        )
        count = len(group)
        total_count += count
        milestone_count += 1

        lines.append(f"## {heading_title} ({count} open)")
        lines.append("")

        if include_prs:
            headers = [
                "#", "Type", "Title", "Labels", "Assignee", "Created",
            ]
            rows: list[list[str]] = []
            for item, is_pr in group:
                num = item["number"]
                url = item.get("html_url") or item.get("url", "")
                rows.append(
                    [
                        f"[#{num}]({url})",
                        _item_type(item, is_pr),
                        _truncate(item.get("title", "")),
                        _render_labels(item),
                        _render_assignee(item),
                        _render_created(item),
                    ]
                )
        else:
            headers = ["#", "Title", "Labels", "Assignee", "Created"]
            rows = []
            for item, _ in group:
                num = item["number"]
                url = item.get("html_url") or item.get("url", "")
                rows.append(
                    [
                        f"[#{num}]({url})",
                        _truncate(item.get("title", "")),
                        _render_labels(item),
                        _render_assignee(item),
                        _render_created(item),
                    ]
                )

        lines.append(_gh_common.render_table(headers, rows))
        lines.append("")

    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    plural = "s" if milestone_count != 1 else ""
    lines.append(
        f"**Total: {total_count} open across"
        f" {milestone_count} milestone{plural}"
        f" (fetched {fetched_at}).**"
    )

    return "\n".join(lines)


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
            "List open GitHub issues (and optionally PRs) grouped by "
            "milestone. Reads from the GitHub API via gh; writes markdown "
            "to stdout. Zero LLM tokens consumed."
        )
    )
    parser.add_argument(
        "label_filter",
        nargs="?",
        default=None,
        metavar="LABEL",
        help=(
            "Optional label name. When provided, only issues/PRs carrying "
            "this label are included."
        ),
    )
    parser.add_argument(
        "--prs",
        action="store_true",
        default=False,
        help=(
            "Include open PRs alongside issues in the same "
            "milestone-grouped view. Adds a Type column to each table."
        ),
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
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the gh-refresh-issues script.

    Args:
        argv: Argument list; defaults to sys.argv[1:] when None.

    Returns:
        Exit code: 0 on success, 1 on ``gh`` failure.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.repo:
            repo = args.repo
        else:
            owner, name = _gh_common.get_current_repo()
            repo = f"{owner}/{name}"

        issues, prs = fetch_data(repo=repo, include_prs=args.prs)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.label_filter:
        issues = apply_label_filter(issues, args.label_filter)
        prs = apply_label_filter(prs, args.label_filter)

    report = render_report(issues=issues, prs=prs, include_prs=args.prs)

    # Force UTF-8 output so Unicode in issue titles/labels doesn't fail
    # on Windows where the default console encoding may be cp1252.
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.buffer.write(
            (report + "\n").encode("utf-8", errors="replace")
        )
        sys.stdout.buffer.flush()
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
