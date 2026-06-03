"""Shared helpers for GitHub workflow scripts.

Provides a thin subprocess wrapper around ``gh api``, a repo-detection
helper, a markdown table renderer, and a recent-releases section
renderer shared by ``gh-summary`` and ``gh-release-status``.

All functions are pure or side-effect-free except ``run_gh_api``,
``get_current_repo``, and ``render_recent_releases``, which shell out
to ``gh``.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_gh_api(
    path: str,
    *,
    paginate: bool = False,
    jq: str | None = None,
) -> Any:
    """Call ``gh api <path>`` and return the parsed result.

    When a ``jq`` filter is supplied, ``gh`` may emit the selected value
    as raw, unquoted text (e.g. ``main\\n`` for a string field, ``42\\n``
    for an integer field).  ``json.loads`` handles valid JSON scalars
    (numbers, booleans, ``null``) transparently, but raises
    ``JSONDecodeError`` for bare strings like ``main``.  In that case the
    raw, stripped stdout is returned as a plain Python string.

    When no ``jq`` filter is supplied the full response must be valid
    JSON; a parse failure is a genuine error and is allowed to propagate.

    Args:
        path: GitHub API path, e.g. ``repos/owner/repo/issues``.
        paginate: When True, pass ``--paginate`` to gh so all pages are
            fetched automatically and merged into a single JSON array.
        jq: Optional jq filter string. Passed as ``--jq <filter>`` to
            gh.  When the filter selects a scalar string field, the
            return value will be a plain Python ``str`` rather than a
            parsed JSON type.

    Returns:
        Parsed JSON value (dict, list, int, bool, None) or, when a
        ``jq`` filter was supplied and the output is a bare unquoted
        string, a plain ``str`` with surrounding whitespace stripped.

    Raises:
        RuntimeError: If gh exits with a non-zero return code.  The
            error message includes gh's stderr output.
        json.JSONDecodeError: If no ``jq`` filter was supplied and
            gh's stdout is not valid JSON (indicates a real API error).
    """
    cmd = ["gh", "api", path]
    if paginate:
        cmd.append("--paginate")
    if jq is not None:
        cmd.extend(["--jq", jq])

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh api {path!r} failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        if jq is not None:
            # gh emitted a raw scalar (unquoted string); return stripped.
            return result.stdout.strip()
        raise


def get_current_repo() -> tuple[str, str]:
    """Detect the current repo's owner and name via ``gh repo view``.

    Returns:
        Tuple of ``(owner, name)``, e.g. ``("acme", "my-repo")``.

    Raises:
        RuntimeError: If ``gh repo view`` fails (not in a configured git
            repo, not authenticated, etc.).
    """
    cmd = [
        "gh",
        "repo",
        "view",
        "--json",
        "nameWithOwner",
        "--jq",
        ".nameWithOwner",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(
            "gh repo view failed — not in a GitHub-configured git repo? "
            f"stderr: {result.stderr.strip()}"
        )
    name_with_owner = result.stdout.strip()
    owner, _, name = name_with_owner.partition("/")
    return owner, name


def render_table(
    headers: list[str],
    rows: list[list[str]],
    *,
    align: list[str] | None = None,
) -> str:
    """Render a GitHub-flavored markdown table.

    Args:
        headers: Column header strings.
        rows: List of rows; each row is a list of cell strings whose
            length must match ``headers``.
        align: Optional per-column alignment list. Each element is one of
            ``'left'``, ``'right'``, or ``'center'``. Defaults to all
            ``'left'`` when absent.

    Returns:
        Multi-line markdown table string, or empty string if ``rows`` is
        empty (never render a header-only table).
    """
    if not rows:
        return ""

    # Build alignment separators.
    effective_align = (
        align if align is not None else ["left"] * len(headers)
    )
    sep_cells: list[str] = []
    for a in effective_align:
        if a == "right":
            sep_cells.append("---:")
        elif a == "center":
            sep_cells.append(":---:")
        else:
            sep_cells.append("---")

    lines: list[str] = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(sep_cells) + " |")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def render_recent_releases(
    limit: int = 5,
    repo: Optional[str] = None,
) -> Optional[str]:
    """Render a markdown section listing recent releases.

    Shells out to ``gh release list --limit N --json ...``. Returns
    ``None`` (skip the section entirely) when the repo has no releases
    or when ``gh`` returns an error.

    Args:
        limit: Maximum number of releases to show. Defaults to 5.
        repo: Optional ``owner/name`` repository slug. When provided,
            passed as ``--repo`` to ``gh``. When omitted, ``gh`` auto-
            detects from the current working directory.

    Returns:
        Multi-line markdown string for the section (``### Recent
        releases`` header + table), or ``None`` if the repo has no
        releases or ``gh`` fails.
    """
    cmd = [
        "gh",
        "release",
        "list",
        "--limit",
        str(limit),
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
        releases: list[dict[str, Any]] = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None

    if not releases:
        return None

    section_lines: list[str] = []
    section_lines.append("### Recent releases")
    section_lines.append("")

    rows: list[list[str]] = []
    for rel in releases:
        tag = rel.get("tagName", "")
        published = rel.get("publishedAt", "")[:10]  # YYYY-MM-DD
        name = rel.get("name", "")
        rows.append([tag, published, name])

    table = render_table(
        ["Tag", "Published", "Title"],
        rows,
    )
    section_lines.append(table)
    return "\n".join(section_lines)
