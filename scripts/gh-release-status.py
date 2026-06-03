#!/usr/bin/env python3
"""Release status snapshot for a GitHub repository.

Shows the N most recent releases and a diff between the latest release
tag and the default branch (``latest-tag...HEAD``). All data comes from
the ``gh`` CLI and the GitHub compare REST API; no LLM tokens are
consumed.

Output (to stdout, markdown):
  - ``### Recent releases`` table (tag, published date, title)
  - ``### Unreleased diff`` section showing files changed, total
    insertions/deletions, and a per-top-level-area breakdown

Usage::

    gh-release-status.py [--repo OWNER/REPO] [--limit N]

Exit 0 on success, non-zero on ``gh`` failure or missing repo.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from typing import Any, Optional

import _gh_common

# Maximum number of files the GitHub compare API returns per request.
# When this cap is hit the response indicates the diff is truncated.
_COMPARE_FILES_CAP = 300


# ---------------------------------------------------------------------------
# Per-area grouping
# ---------------------------------------------------------------------------


def group_files_by_area(
    files: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group diff file entries by their top-level path segment.

    The top-level segment is everything before the first ``/``.  For a
    file with no directory component (e.g. ``README.md``) the whole
    filename is used as the area name.

    Args:
        files: List of file dicts from the GitHub compare API.  Each
            dict must have at least a ``filename`` key.

    Returns:
        Dict mapping area name (str) to list of file dicts in that
        area.  Empty when ``files`` is empty.
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in files:
        filename: str = f.get("filename", "")
        area = filename.split("/")[0] if "/" in filename else filename
        groups[area].append(f)
    return dict(groups)


# ---------------------------------------------------------------------------
# Diff rendering
# ---------------------------------------------------------------------------


def render_unreleased_diff(
    tag: str,
    branch: str,
    compare: dict[str, Any],
) -> str:
    """Render the unreleased-diff section as markdown.

    Args:
        tag: The latest release tag (e.g. ``v1.2.3``).
        branch: The default branch name (e.g. ``main``).
        compare: GitHub compare API payload for
            ``{tag}...{branch}``.  Must contain ``files``,
            ``commits``, ``total_commits``, and ``ahead_by`` keys.

    Returns:
        Multi-line markdown string for the ``### Changes since <tag>``
        section.
    """
    files: list[dict[str, Any]] = compare.get("files", [])
    total_commits: int = compare.get("total_commits", 0)
    files_truncated: bool = compare.get("files_truncated", False)

    lines: list[str] = []
    lines.append(f"### Changes since {tag}")
    lines.append("")

    if total_commits == 0 and not files:
        lines.append(
            f"_No unreleased commits — `{branch}` is up to date "
            f"with `{tag}`._"
        )
        return "\n".join(lines)

    # Totals
    total_add = sum(f.get("additions", 0) for f in files)
    total_del = sum(f.get("deletions", 0) for f in files)
    file_count = len(files)

    lines.append(
        f"{total_commits} commit(s) · "
        f"{file_count} file(s) changed · "
        f"+{total_add} / -{total_del} lines"
    )

    if files_truncated or file_count >= _COMPARE_FILES_CAP:
        lines.append(
            f"> **Note:** diff capped at {_COMPARE_FILES_CAP} files "
            "— per-area breakdown reflects only the listed files."
        )

    lines.append("")

    # Per-area breakdown table
    if files:
        groups = group_files_by_area(files)
        rows: list[list[str]] = []
        for area in sorted(groups.keys()):
            area_files = groups[area]
            n_files = len(area_files)
            area_add = sum(f.get("additions", 0) for f in area_files)
            area_del = sum(f.get("deletions", 0) for f in area_files)
            rows.append(
                [
                    f"`{area}`",
                    str(n_files),
                    f"+{area_add}",
                    f"-{area_del}",
                ]
            )
        table = _gh_common.render_table(
            ["Area", "Files", "Insertions", "Deletions"],
            rows,
            align=["left", "right", "right", "right"],
        )
        lines.append(table)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main rendering function
# ---------------------------------------------------------------------------


def _fetch_latest_tag(repo: Optional[str] = None) -> Optional[str]:
    """Fetch the latest release tag via ``gh release list --limit 1``.

    Args:
        repo: Optional ``owner/name`` slug. When omitted ``gh`` auto-
            detects from the current working directory.

    Returns:
        The latest tag name string, or ``None`` when there are no
        releases or on ``gh`` failure.
    """
    cmd = [
        "gh",
        "release",
        "list",
        "--limit",
        "1",
        "--json",
        "tagName,publishedAt,name",
    ]
    if repo:
        cmd.extend(["--repo", repo])

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        return None
    try:
        data: list[dict[str, Any]] = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    if not data:
        return None
    return data[0].get("tagName")


def render_release_status(
    repo: str,
    limit: int = 5,
) -> str:
    """Fetch data and render the full release-status report.

    Fetches releases and compare diff via the ``gh`` CLI and GitHub
    REST API, then renders a markdown report.

    Args:
        repo: ``owner/name`` string for the target repository.
        limit: Number of recent releases to show in the table.

    Returns:
        Multi-line markdown string ready to print to stdout.

    Raises:
        RuntimeError: On any ``gh`` CLI failure (propagated from
            ``_gh_common.run_gh_api`` or subprocess calls).
    """
    owner, _, name = repo.partition("/")

    # --- Recent releases table ---
    releases_section = _gh_common.render_recent_releases(
        limit=limit, repo=repo
    )

    if releases_section is None:
        # No releases — nothing to diff against
        return "_No releases yet — nothing to diff against._"

    # --- Resolve latest tag via gh release list --limit 1 ---
    latest_tag = _fetch_latest_tag(repo=repo)
    if not latest_tag:
        return "\n".join([
            releases_section,
            "",
            "_No releases yet — nothing to diff against._",
        ])

    # --- Get default branch via repos API ---
    default_branch: str = _gh_common.run_gh_api(
        f"repos/{owner}/{name}",
        jq=".default_branch",
    )

    # --- Compare API: latest tag...default branch ---
    compare: dict[str, Any] = _gh_common.run_gh_api(
        f"repos/{owner}/{name}/compare/"
        f"{latest_tag}...{default_branch}",
        jq=".",
    )

    # --- Assemble output ---
    sections: list[str] = [
        releases_section,
        "",
        render_unreleased_diff(latest_tag, default_branch, compare),
    ]
    return "\n".join(sections)


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
            "Print a release status snapshot of a GitHub repo — "
            "recent releases table and a per-area breakdown of "
            "unreleased commits since the latest release tag. "
            "Reads from the GitHub API via gh; writes markdown to stdout."
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
        "--limit",
        metavar="N",
        type=int,
        default=5,
        help=(
            "Number of recent releases to show in the releases table "
            "(default: 5)."
        ),
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Run the gh-release-status script.

    Args:
        argv: Argument list to parse.  Defaults to ``sys.argv[1:]``
            when ``None``.

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

        report = render_release_status(repo=repo, limit=args.limit)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # Reconfigure stdout to UTF-8 so Unicode in release names/titles
    # does not fail on Windows where the default console codec is
    # cp1252.  Falls back to a plain print when sys.stdout lacks
    # ``reconfigure`` (e.g., pytest's capsys wrapper).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(  # type: ignore[attr-defined]
            encoding="utf-8", errors="replace"
        )
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
