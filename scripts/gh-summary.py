#!/usr/bin/env python3
"""Deterministic roadmap snapshot for a GitHub repository.

Produces a top-down view of Epics → Milestones → Critical/blocked issues,
with an optional Recent releases section. All data comes from the ``gh``
CLI; no LLM tokens are consumed.

Output (to stdout):
  - 1-line state-of-the-union summary
  - Markdown table: Epics and Milestones with completion rates and
    descriptions
  - Markdown table: Critical / blocked issues (omitted when zero matches)
  - Markdown table: Recent releases (top 5, skipped if none)

Usage::

    gh-summary.py [--repo OWNER/REPO] [--critical-labels LABEL,...]

Exit 0 on success, non-zero on ``gh`` failure.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
from typing import Any

import _gh_common

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_UMBRELLA_PREFIX = "[Umbrella]"
_META_LABEL = "meta"

# Default set of labels that qualify an issue as critical / blocked.
_DEFAULT_CRITICAL_LABELS = "blocked,security,bug"

# Maximum chars to render from a milestone description before truncating.
_DESCRIPTION_MAX_LEN = 100


# ---------------------------------------------------------------------------
# Epic detection
# ---------------------------------------------------------------------------


def is_epic(issue: dict[str, Any]) -> bool:
    """Return True if an issue qualifies as an epic.

    An epic must have **both**:
    - Title starting with the literal prefix ``[Umbrella]`` (case-sensitive)
    - A label named ``meta``

    Args:
        issue: GitHub issue dict with at least ``title`` and ``labels``
            keys.

    Returns:
        True if the issue is an epic; False otherwise.
    """
    title: str = issue.get("title", "")
    labels: list[dict[str, Any]] = issue.get("labels", [])
    has_prefix = title.startswith(_UMBRELLA_PREFIX)
    has_meta = any(lbl.get("name") == _META_LABEL for lbl in labels)
    return has_prefix and has_meta


# ---------------------------------------------------------------------------
# Checklist parsing
# ---------------------------------------------------------------------------

# Matches GitHub-flavoured markdown task-list items:
#   - [x] or - [ ] with optional leading whitespace
_CHECKBOX_RE = re.compile(r"^\s*-\s*\[([ x])\]\s+", re.MULTILINE)


def parse_checklist(body: str | None) -> tuple[int, int] | None:
    """Parse a GitHub issue body for task-list checkboxes.

    Args:
        body: Raw markdown body text, or None.

    Returns:
        ``(checked, total)`` tuple when checkbox syntax is found; ``None``
        when the body contains no checkboxes.
    """
    if not body:
        return None
    matches = _CHECKBOX_RE.findall(body)
    if not matches:
        return None
    total = len(matches)
    checked = sum(1 for ch in matches if ch == "x")
    return checked, total


# ---------------------------------------------------------------------------
# Milestone completion formatting
# ---------------------------------------------------------------------------


def format_milestone_completion(milestone: dict[str, Any]) -> str:
    """Format a milestone's closed/total issue ratio as a short string.

    Args:
        milestone: GitHub milestone dict with ``open_issues`` and
            ``closed_issues`` keys.

    Returns:
        String in the form ``"closed/total (pct%)"`` — e.g.
        ``"3/5 (60%)"``. Returns ``"0/0"`` when total is zero to avoid
        division-by-zero.
    """
    closed: int = milestone.get("closed_issues", 0)
    open_count: int = milestone.get("open_issues", 0)
    total = closed + open_count
    if total == 0:
        return "0/0"
    pct = int(round(closed / total * 100))
    return f"{closed}/{total} ({pct}%)"


def _sanitize_description(desc: str) -> str:
    """Sanitize a milestone description for safe embedding in a markdown table.

    Markdown table rows are delimited by ``|`` characters and cannot span
    multiple lines.  Passing a raw description that contains either of
    those characters into ``render_table`` would split the cell and
    corrupt the rendered table.

    Transformations applied (in order):

    1. Replace every CR+LF, CR, or LF sequence with a single space.
    2. Escape every literal ``|`` as ``\\|``.
    3. Collapse runs of whitespace to a single space, then strip.

    Args:
        desc: Non-empty description string (caller must guard against
            ``None`` / empty before calling).

    Returns:
        Sanitized description string safe for use as a table cell.
    """
    # Step 1: normalise line endings → space
    sanitized = re.sub(r"\r\n|\r|\n", " ", desc)
    # Step 2: escape literal pipe characters
    sanitized = sanitized.replace("|", r"\|")
    # Step 3: collapse whitespace runs and trim
    sanitized = re.sub(r"[ \t]+", " ", sanitized).strip()
    return sanitized


def _truncate_description(desc: str | None) -> str:
    """Sanitize and truncate a milestone description to at most 100 characters.

    Sanitization (via :func:`_sanitize_description`) is applied *before*
    truncation so that the 100-character limit is measured against the
    rendered, table-safe text rather than the raw API value.

    Args:
        desc: Raw description string, or None.

    Returns:
        Empty string when ``desc`` is None or empty; the sanitized string
        when its length is ≤ 100; otherwise the first 100 characters of
        the sanitized string followed by ``"…"``.
    """
    if not desc:
        return ""
    sanitized = _sanitize_description(desc)
    if len(sanitized) <= _DESCRIPTION_MAX_LEN:
        return sanitized
    return sanitized[:_DESCRIPTION_MAX_LEN] + "…"


# ---------------------------------------------------------------------------
# Orphan detection
# ---------------------------------------------------------------------------

_CHECKLIST_ISSUE_REF_RE = re.compile(
    r"^\s*-\s*\[[ x]\]\s+.*?#(\d+)", re.MULTILINE
)


def _collect_epic_referenced_numbers(
    epics: list[dict[str, Any]],
) -> set[int]:
    """Collect all issue numbers referenced in any epic's checklist lines.

    Args:
        epics: List of epic issue dicts (must have ``body`` key).

    Returns:
        Set of integer issue numbers mentioned in checklist lines of any
        epic.
    """
    referenced: set[int] = set()
    for epic in epics:
        body: str = epic.get("body") or ""
        for match in _CHECKLIST_ISSUE_REF_RE.finditer(body):
            referenced.add(int(match.group(1)))
    return referenced


def detect_orphans(
    issues: list[dict[str, Any]],
    epics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Identify orphaned open issues.

    An orphan is an open issue that:
    - Has no milestone assigned (``milestone`` is ``None``)
    - Is not an epic itself
    - Is not referenced by a ``#N`` mention in any epic's checklist lines

    Args:
        issues: All open issues to evaluate.
        epics: All detected epic issues (used to build the referenced set).

    Returns:
        List of orphaned issue dicts, preserving input order.
    """
    epic_numbers = {e["number"] for e in epics}
    referenced = _collect_epic_referenced_numbers(epics)

    orphans: list[dict[str, Any]] = []
    for issue in issues:
        if issue.get("milestone") is not None:
            continue
        if issue["number"] in epic_numbers:
            continue
        if issue["number"] in referenced:
            continue
        orphans.append(issue)
    return orphans


# ---------------------------------------------------------------------------
# Report rendering — critical / blocked issues
# ---------------------------------------------------------------------------


def _render_critical_issues(
    issues: list[dict[str, Any]],
    critical_labels: set[str],
) -> str | None:
    """Render a markdown section listing critical / blocked open issues.

    Only issues whose label set intersects ``critical_labels`` are listed.
    The section is omitted entirely (returns ``None``) when no issues
    match.

    Args:
        issues: List of open GitHub issue dicts, each with ``number``,
            ``title``, ``html_url``, and ``labels`` keys.
        critical_labels: Set of label names that qualify an issue as
            critical.

    Returns:
        Multi-line markdown string for the section, or ``None`` when
        there are no matching issues.
    """
    matching: list[tuple[dict[str, Any], list[str]]] = []
    for issue in issues:
        issue_label_names = {
            lbl.get("name", "") for lbl in issue.get("labels", [])
        }
        matched = sorted(issue_label_names & critical_labels)
        if matched:
            matching.append((issue, matched))

    if not matching:
        return None

    lines: list[str] = []
    lines.append("### Critical / blocked issues")
    lines.append("")

    rows: list[list[str]] = []
    for issue, matched_labels in matching:
        num = issue["number"]
        url = issue.get("html_url", f"https://github.com/issues/{num}")
        title = issue.get("title", "")
        labels_str = ", ".join(matched_labels)
        rows.append([f"[#{num}]({url})", title, labels_str])

    table = _gh_common.render_table(
        ["Issue", "Title", "Labels"],
        rows,
        align=["left", "left", "left"],
    )
    lines.append(table)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Report rendering — recent releases
# ---------------------------------------------------------------------------


def _render_recent_releases() -> str | None:
    """Render a markdown section: the 5 most recent releases.

    Shells out to ``gh release list --limit 5 --json ...``. Returns
    ``None`` (skip the section entirely) when the repo has no releases or
    when ``gh`` returns an error.

    Returns:
        Multi-line markdown string for the section, or ``None`` if the
        repo has no releases.
    """
    cmd = [
        "gh",
        "release",
        "list",
        "--limit",
        "5",
        "--json",
        "tagName,publishedAt,name",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        return None

    try:
        releases: list[dict[str, Any]] = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None

    if not releases:
        return None

    lines: list[str] = []
    lines.append("### Recent releases")
    lines.append("")

    rows: list[list[str]] = []
    for rel in releases:
        tag = rel.get("tagName", "")
        published = rel.get("publishedAt", "")[:10]  # YYYY-MM-DD
        name = rel.get("name", "")
        rows.append([tag, published, name])

    table = _gh_common.render_table(
        ["Tag", "Published", "Title"],
        rows,
    )
    lines.append(table)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Report rendering — full report
# ---------------------------------------------------------------------------


def render_report(
    epics: list[dict[str, Any]],
    milestones: list[dict[str, Any]],
    open_issues: list[dict[str, Any]],
    critical_labels: set[str] | None = None,
) -> str:
    """Render the full roadmap report as a markdown string.

    Args:
        epics: List of epic issue dicts with body checklists parsed.
        milestones: List of open milestone dicts; each may include a
            ``description`` field rendered in the table.
        open_issues: All open non-PR issues, used for critical-issue
            filtering.
        critical_labels: Set of label names that qualify an issue as
            critical / blocked. Defaults to ``{"blocked", "security",
            "bug"}`` when ``None``.

    Returns:
        Multi-line markdown string ready to print to stdout.
    """
    if critical_labels is None:
        critical_labels = {"blocked", "security", "bug"}

    lines: list[str] = []

    # 1-line state-of-the-union
    n_epics = len(epics)
    n_milestones = len(milestones)
    n_issues = len(open_issues)
    lines.append(
        f"{n_epics} active epic{'s' if n_epics != 1 else ''} · "
        f"{n_milestones} open milestone{'s' if n_milestones != 1 else ''} · "
        f"{n_issues} open issue{'s' if n_issues != 1 else ''}."
    )
    lines.append("")

    # --- Epics / Milestones table ---
    lines.append("### Epics / Milestones")
    lines.append("")

    em_rows: list[list[str]] = []

    for epic in epics:
        num = epic["number"]
        url = epic.get("html_url", f"https://github.com/issues/{num}")
        title = epic.get("title", "")
        checklist = parse_checklist(epic.get("body"))
        if checklist is None:
            completion = "checklist absent"
        else:
            checked, total = checklist
            pct = int(round(checked / total * 100)) if total else 0
            completion = f"{checked}/{total} ({pct}%)"
        em_rows.append(
            [f"[#{num}]({url}) {title}", completion, "", "epic"]
        )

    for ms in milestones:
        title = ms.get("title", "")
        completion = format_milestone_completion(ms)
        description = _truncate_description(ms.get("description"))
        em_rows.append([title, completion, description, "milestone"])

    em_table = _gh_common.render_table(
        ["Epic / Milestone", "Completion", "Description", "Type"],
        em_rows,
        align=["left", "right", "left", "left"],
    )
    if em_table:
        lines.append(em_table)
    else:
        lines.append("_No epics or milestones found._")
    lines.append("")

    # --- Critical / blocked issues (omitted when zero matches) ---
    critical_section = _render_critical_issues(open_issues, critical_labels)
    if critical_section is not None:
        lines.append(critical_section)
        lines.append("")

    # --- Recent releases (skipped if none) ---
    releases_section = _render_recent_releases()
    if releases_section is not None:
        lines.append(releases_section)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


def build_report_data(
    repo: str,
) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]
]:
    """Fetch and assemble all data needed for the report.

    Args:
        repo: ``owner/name`` string for the target repository.

    Returns:
        Tuple of ``(epics, milestones, open_issues)`` where each element
        is a list of dicts ready to pass to ``render_report``.

    Raises:
        RuntimeError: Propagated from ``_gh_common.run_gh_api`` on
            gh failure.
    """
    # Fetch all open issues (paginated)
    issues: list[dict[str, Any]] = _gh_common.run_gh_api(
        f"repos/{repo}/issues",
        paginate=True,
        jq=".",
    )
    # GitHub API includes pull requests in /issues endpoint; exclude them.
    issues = [i for i in issues if "pull_request" not in i]

    # Detect epics from the flat issue list
    epics = [i for i in issues if is_epic(i)]

    # Fetch open milestones
    milestones: list[dict[str, Any]] = _gh_common.run_gh_api(
        f"repos/{repo}/milestones",
        paginate=True,
        jq=".",
    )

    return epics, milestones, issues


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
            "Print a deterministic roadmap snapshot of the current GitHub "
            "repo — Epics, Milestones, critical/blocked issues, and recent "
            "releases. Reads from the GitHub API via gh; writes markdown "
            "to stdout."
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
        "--critical-labels",
        metavar="LABEL,...",
        default=_DEFAULT_CRITICAL_LABELS,
        help=(
            "Comma-separated list of labels that qualify an issue as "
            "critical / blocked. Defaults to 'blocked,security,bug'."
        ),
    )
    return parser


def main() -> int:
    """Run the gh-summary script.

    Returns:
        Exit code: 0 on success, 1 on gh failure.
    """
    parser = _build_parser()
    args = parser.parse_args()

    critical_labels: set[str] = {
        lbl.strip()
        for lbl in args.critical_labels.split(",")
        if lbl.strip()
    }

    try:
        if args.repo:
            repo = args.repo
        else:
            owner, name = _gh_common.get_current_repo()
            repo = f"{owner}/{name}"

        epics, milestones, open_issues = build_report_data(repo)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # Force UTF-8 stdout so Unicode in issue bodies / titles doesn't fail
    # on Windows where the default console encoding may be cp1252.
    out = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )
    print(
        render_report(
            epics=epics,
            milestones=milestones,
            open_issues=open_issues,
            critical_labels=critical_labels,
        ),
        file=out,
    )
    out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
