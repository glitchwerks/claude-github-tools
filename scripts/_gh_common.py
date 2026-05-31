"""Shared helpers for gh-summary and gh-refresh-issues scripts.

Provides a thin subprocess wrapper around ``gh api``, a repo-detection
helper, and a markdown table renderer. All functions are pure or
side-effect-free except ``run_gh_api`` and ``get_current_repo``, which
shell out to ``gh``.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_gh_api(
    path: str,
    *,
    paginate: bool = False,
    jq: str | None = None,
) -> Any:
    """Call ``gh api <path>`` and return parsed JSON.

    Args:
        path: GitHub API path, e.g. ``repos/owner/repo/issues``.
        paginate: When True, pass ``--paginate`` to gh so all pages are
            fetched automatically and merged into a single JSON array.
        jq: Optional jq filter string. Passed as ``--jq <filter>`` to gh.

    Returns:
        Parsed JSON value (dict, list, str, int, etc.) from gh stdout.

    Raises:
        RuntimeError: If gh exits with a non-zero return code. The error
            message includes gh's stderr output.
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
    return json.loads(result.stdout)


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
