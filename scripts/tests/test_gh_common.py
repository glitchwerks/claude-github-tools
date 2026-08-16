"""Tests for scripts/_gh_common.py — run_gh_api behaviour.

Covers the scalar-jq regression introduced in issue #7:
  - ``run_gh_api`` with a ``jq`` filter whose stdout is a bare scalar
    (e.g. ``main\\n``) must return the stripped string, not raise
    ``JSONDecodeError``.
  - Without a ``jq`` filter, a non-JSON stdout is a real error and must
    still raise ``RuntimeError``.
  - Object-returning ``jq`` calls continue to return parsed dicts/lists.
  - Non-zero exit code still raises ``RuntimeError`` regardless of whether
    a ``jq`` filter was supplied.

Covers the multi-page pagination regression from issue #41:
  - ``gh api --paginate`` (without ``--slurp``) emits one JSON document
    per page, concatenated back-to-back with no separator. A response
    spanning more than one page must not raise ``JSONDecodeError`` and
    must return the combined, flattened list of items across all pages.
  - A single-page paginated response continues to return that page's
    list unchanged.
  - ``paginate=True`` combined with an explicit ``jq`` filter must
    combine the per-page filtered results into one Python list, and
    must never result in ``gh`` being invoked with both ``--slurp``
    and ``--jq`` at once (``gh api`` rejects that combination).

All subprocess calls are mocked via ``unittest.mock.patch`` on
``subprocess.run``, matching the pattern used in sibling test files
(``test_gh_summary.py``, ``test_gh_release_status.py``).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module loading
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).parent.parent
COMMON_SCRIPT = SCRIPTS_DIR / "_gh_common.py"


def _load_common() -> ModuleType:
    """Import _gh_common as a fresh module instance.

    Returns:
        The loaded _gh_common module object.
    """
    spec = importlib.util.spec_from_file_location(
        "_gh_common", COMMON_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Fixture helper
# ---------------------------------------------------------------------------


def _make_cp(
    stdout: str, returncode: int = 0, stderr: str = ""
) -> MagicMock:
    """Build a fake subprocess.CompletedProcess.

    Args:
        stdout: Raw stdout string (not JSON-encoded — set exactly what
            ``gh`` would emit).
        returncode: Process exit code (default 0).
        stderr: Stderr string (default empty).

    Returns:
        MagicMock simulating subprocess.CompletedProcess.
    """
    cp = MagicMock()
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


# ---------------------------------------------------------------------------
# TestRunGhApiScalarJq — scalar-returning --jq filter (issue #7 regression)
# ---------------------------------------------------------------------------


class TestRunGhApiScalarJq:
    """run_gh_api with a jq filter tolerates bare-scalar stdout."""

    def test_bare_string_scalar_with_jq_returns_stripped_string(
        self,
    ) -> None:
        """jq='.default_branch' returning 'main\\n' yields 'main'."""
        mod = _load_common()
        # gh api ... --jq .default_branch emits the value unquoted + newline
        cp = _make_cp(stdout="main\n")
        with patch("subprocess.run", return_value=cp):
            result = mod.run_gh_api(
                "repos/owner/repo", jq=".default_branch"
            )
        assert result == "main"

    def test_bare_string_scalar_with_jq_no_exception_raised(
        self,
    ) -> None:
        """run_gh_api must not raise JSONDecodeError for scalar jq output."""
        mod = _load_common()
        cp = _make_cp(stdout="develop\n")
        with patch("subprocess.run", return_value=cp):
            # Regression guard: this must not raise
            try:
                mod.run_gh_api("repos/owner/repo", jq=".default_branch")
            except Exception as exc:  # noqa: BLE001
                pytest.fail(
                    f"run_gh_api raised {type(exc).__name__} for scalar "
                    f"jq output: {exc}"
                )

    def test_bare_integer_scalar_with_jq_returns_int(self) -> None:
        """jq filter returning a bare integer (e.g. '42\\n') yields int 42."""
        mod = _load_common()
        # gh --jq .open_issues_count emits '42\n' for integer fields
        cp = _make_cp(stdout="42\n")
        with patch("subprocess.run", return_value=cp):
            result = mod.run_gh_api(
                "repos/owner/repo", jq=".open_issues_count"
            )
        # json.loads("42") succeeds, so the int path is fine already;
        # this test confirms the fix doesn't regress integer scalars
        assert result == 42

    def test_bare_true_scalar_with_jq_returns_bool(self) -> None:
        """jq filter returning 'true\\n' yields Python True."""
        mod = _load_common()
        cp = _make_cp(stdout="true\n")
        with patch("subprocess.run", return_value=cp):
            result = mod.run_gh_api("repos/owner/repo", jq=".private")
        assert result is True

    def test_jq_object_result_still_parsed_as_dict(self) -> None:
        """Object-returning jq filter continues to return a parsed dict."""
        mod = _load_common()
        payload = {"name": "my-repo", "default_branch": "main"}
        cp = _make_cp(stdout=json.dumps(payload) + "\n")
        with patch("subprocess.run", return_value=cp):
            result = mod.run_gh_api("repos/owner/repo", jq=".")
        assert result == payload

    def test_jq_array_result_still_parsed_as_list(self) -> None:
        """Array-returning jq filter continues to return a parsed list."""
        mod = _load_common()
        payload = ["v1.0.0", "v1.1.0"]
        cp = _make_cp(stdout=json.dumps(payload) + "\n")
        with patch("subprocess.run", return_value=cp):
            result = mod.run_gh_api(
                "repos/owner/repo/tags", jq=".[].name"
            )
        assert result == payload

    def test_scalar_with_whitespace_is_stripped(self) -> None:
        """Trailing and leading whitespace is stripped from scalar result."""
        mod = _load_common()
        # Some gh versions may emit extra whitespace
        cp = _make_cp(stdout="  main  \n")
        with patch("subprocess.run", return_value=cp):
            result = mod.run_gh_api(
                "repos/owner/repo", jq=".default_branch"
            )
        assert result == "main"


# ---------------------------------------------------------------------------
# TestRunGhApiNoJq — without jq, bad JSON is still a real error
# ---------------------------------------------------------------------------


class TestRunGhApiNoJq:
    """Without a jq filter, non-JSON stdout is a genuine error."""

    def test_no_jq_valid_json_returns_parsed(self) -> None:
        """Without jq, valid JSON stdout is returned as parsed object."""
        mod = _load_common()
        payload = {"id": 1, "name": "repo"}
        cp = _make_cp(stdout=json.dumps(payload))
        with patch("subprocess.run", return_value=cp):
            result = mod.run_gh_api("repos/owner/repo")
        assert result == payload

    def test_no_jq_invalid_json_raises_json_decode_error(
        self,
    ) -> None:
        """Without jq, non-JSON stdout raises JSONDecodeError (real error)."""
        mod = _load_common()
        cp = _make_cp(stdout="not-json")
        with patch("subprocess.run", return_value=cp):
            with pytest.raises(Exception):  # noqa: B017
                mod.run_gh_api("repos/owner/repo")

    def test_nonzero_exit_raises_runtime_error_no_jq(self) -> None:
        """Non-zero exit code raises RuntimeError regardless of jq."""
        mod = _load_common()
        cp = _make_cp(stdout="", returncode=1, stderr="not authenticated")
        with patch("subprocess.run", return_value=cp):
            with pytest.raises(RuntimeError, match="not authenticated"):
                mod.run_gh_api("repos/owner/repo")

    def test_nonzero_exit_raises_runtime_error_with_jq(self) -> None:
        """Non-zero exit code raises RuntimeError even when jq is supplied."""
        mod = _load_common()
        cp = _make_cp(stdout="", returncode=1, stderr="API rate limit")
        with patch("subprocess.run", return_value=cp):
            with pytest.raises(RuntimeError, match="API rate limit"):
                mod.run_gh_api(
                    "repos/owner/repo", jq=".default_branch"
                )


# ---------------------------------------------------------------------------
# TestRunGhApiPaginate — multi-page concatenated JSON (issue #41 regression)
# ---------------------------------------------------------------------------


def _make_paginate_side_effect(pages: list[list[dict]]):
    """Build a subprocess.run side_effect that simulates `gh api`.

    Simulates whichever of the two valid pagination strategies the
    command under test actually asked for, keeping these tests
    agnostic to which fix strategy the implementation picks:

    - No ``--slurp``: ``gh api --paginate`` writes one JSON document
      per page directly to stdout, concatenated back-to-back with no
      separator (e.g. ``[{"id": 1}][{"id": 2}]``).
    - With ``--slurp``: ``gh api --paginate --slurp`` wraps all pages
      in one outer JSON array of pages (e.g.
      ``[[{"id": 1}], [{"id": 2}]]``).

    Args:
        pages: The pages to emit, each page a list of JSON-able
            items — mirrors what a `gh api <list-endpoint>` call
            returns per page.

    Returns:
        A callable usable as ``subprocess.run``'s ``side_effect``,
        inspecting the command it's called with to decide which of
        the two stdout shapes above to emit.
    """

    def _side_effect(cmd, *args, **kwargs):
        if "--slurp" in cmd:
            return _make_cp(stdout=json.dumps(pages))
        return _make_cp(stdout="".join(json.dumps(p) for p in pages))

    return _side_effect


class TestRunGhApiPaginate:
    """run_gh_api with paginate=True handles multi-page gh output.

    Regression coverage for issue #41: a single
    ``json.loads(result.stdout)`` call cannot parse the concatenated,
    multi-document stdout that ``gh api --paginate`` (without
    ``--slurp``) produces for any response spanning more than one
    page — it raises ``json.JSONDecodeError: Extra data``.
    """

    def test_paginate_multiple_pages_returns_flattened_list(
        self,
    ) -> None:
        """Two paginated array pages combine into one flat list.

        Regression guard for issue #41: this must not raise
        JSONDecodeError, and must return every item across both pages
        flattened into a single list.
        """
        mod = _load_common()
        pages = [[{"id": 1}], [{"id": 2}]]
        with patch(
            "subprocess.run", side_effect=_make_paginate_side_effect(pages)
        ):
            result = mod.run_gh_api(
                "repos/owner/repo/issues", paginate=True
            )
        assert result == [{"id": 1}, {"id": 2}]

    def test_paginate_multiple_pages_no_exception_raised(self) -> None:
        """run_gh_api must not raise JSONDecodeError for multi-page stdout."""
        mod = _load_common()
        pages = [[{"id": 1}], [{"id": 2}]]
        with patch(
            "subprocess.run", side_effect=_make_paginate_side_effect(pages)
        ):
            try:
                mod.run_gh_api("repos/owner/repo/issues", paginate=True)
            except Exception as exc:  # noqa: BLE001
                pytest.fail(
                    f"run_gh_api raised {type(exc).__name__} for "
                    f"multi-page stdout: {exc}"
                )

    def test_paginate_single_page_returns_that_page(self) -> None:
        """A single-page paginated response returns that page's list.

        Characterization test: this is the pre-existing single-page
        case that already worked, pinned so a pagination fix cannot
        regress it.
        """
        mod = _load_common()
        payload = [{"id": 1}, {"id": 2}]
        pages = [payload]
        with patch(
            "subprocess.run", side_effect=_make_paginate_side_effect(pages)
        ):
            result = mod.run_gh_api(
                "repos/owner/repo/issues", paginate=True
            )
        assert result == payload

    def test_paginate_with_jq_combines_filtered_pages(self) -> None:
        """paginate=True + jq="." must still return the flattened list.

        Mirrors the pattern already used in production by
        ``gh-summary.py`` and ``gh-refresh-issues.py``, which both
        call ``run_gh_api(..., paginate=True, jq=".")`` and rely on
        getting back a flat ``list[dict]`` across all pages — the
        identity jq filter selects each page's array unchanged, so
        the combined result must match the plain paginate=True case.

        This also pins the documented incompatibility between gh's
        ``--slurp`` and ``--jq`` flags: whatever command run_gh_api
        builds for paginate+jq, it must never pass both flags to
        `gh` at once, since `gh api` rejects that combination
        outright.
        """
        mod = _load_common()
        pages = [[{"id": 1}], [{"id": 2}]]
        side_effect = _make_paginate_side_effect(pages)
        with patch("subprocess.run", side_effect=side_effect) as mock_run:
            result = mod.run_gh_api(
                "repos/owner/repo/issues", paginate=True, jq="."
            )
        assert result == [{"id": 1}, {"id": 2}]

        # Whatever flags were passed to `gh`, --slurp and --jq must
        # never appear together (gh api rejects that combination).
        call_args = mock_run.call_args[0][0]
        assert not ("--slurp" in call_args and "--jq" in call_args), (
            "gh api rejects --slurp combined with --jq; run_gh_api "
            "must not pass both flags at once"
        )
