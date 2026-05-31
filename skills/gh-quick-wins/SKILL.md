---
name: gh-quick-wins
description: >
  Surface low-blast-radius backlog items in the current GitHub repo, ranked
  by impact. Trigger this skill whenever the user types /gh-quick-wins, says
  "show me quick wins", "what can I knock out", "low effort items", "low
  blast radius backlog", "what's the easiest thing to ship", "what's small
  enough to do quickly", "what's the most actionable issue right now", or
  any similar request for a filtered, ranked view of the backlog that
  emphasises bounded-risk, high-impact work. Hybrid: deterministic script
  exclusion pass + LLM ranking by impact / blast-radius.
---

# /gh-quick-wins

Two-step recipe. The script applies a deterministic exclusion filter to all
open issues and emits the surviving candidates as JSON. The LLM ranking pass
then reads those signals and produces a top-10 markdown table ordered by
`impact / blast_radius`. Step 1 is zero-LLM; step 2 is the only place LLM
tokens are spent.

**Blast radius** is the load-bearing concept — not effort. A one-line
`CLAUDE.md` typo fix is low effort but high blast radius (every future
session is affected). A full Excalidraw reference guide is high effort but
low blast radius (new file, isolated). This skill ranks by blast radius
first, impact second.

## Step 1 — Run the deterministic script

Run the script and capture its stdout. The interpreter path differs between Windows (Git Bash: `Scripts/python.exe`) and POSIX (`bin/python`); pick the one that exists:

```bash
PY="${CLAUDE_PLUGIN_DATA}/venv/Scripts/python.exe"
[ -f "$PY" ] || PY="${CLAUDE_PLUGIN_DATA}/venv/bin/python"
"$PY" "${CLAUDE_PLUGIN_ROOT}/scripts/gh-quick-wins.py"
```

The script exits 0 and prints a JSON array. Each surviving issue object
includes a `signals` sub-object with:

- `touches_load_bearing` (bool) — body or title references `CLAUDE.md`,
  `agents/`, `skills/`, `hooks/`, or `deploy.py`.
- `body_appears_drafted` (bool) — body is long (>800 chars) AND contains a
  fenced code block, markdown table, or >3 `###` headers. True means the
  issue body already contains the deliverable text.
- `comment_count` (int) — length of the comments array.
- `days_since_update` (int) — days since `updatedAt`.
- `label_set` (list[str]) — label names on the issue.

If the script exits non-zero, surface its stderr and stop.

The script supports `--repo owner/repo` and `--max-ac N` flags. Pass them
through if the user specifies a different repo or checkbox threshold.

## Step 2 — LLM ranking pass

Read the JSON from Step 1. Rank the surviving issues using the rubric below.
Render a top-10 markdown table; if zero issues survived, render the zero-
survivor message.

### Scoring rubric

**Blast radius** (lower is better for quick-wins):

- `low`: New file under `docs/`, `.tmp/`, or a single isolated doc file.
  `touches_load_bearing` is False and the change scope is self-contained.
- `med`: `touches_load_bearing` is True — references `CLAUDE.md`, `agents/`,
  `skills/`, `hooks/`, or `deploy.py`. These affect every future session;
  downweight them even if they look small.

There is no `high` blast-radius tier in this output — items with clearly
unbound blast should have been excluded by label (`epic`, `umbrella`) or
prose (`blocked by #N`). If a load-bearing item slips through, label it
`med` and let impact be the tiebreaker.

**Impact** (higher is better):

- `high`: `body_appears_drafted` is True (issue body already contains the
  deliverable — a new file's full content, a design already written out);
  OR `comment_count >= 3` (active external interest); OR `documentation`
  label on a user-facing surface (README, getting-started guide, public
  reference doc).
- `med`: Isolated bug fix, small scoped feature, minor UX improvement.
- `low`: Internal-only cleanup, single-line typo, no user-facing effect.

**Rank** by: `blast=low` before `blast=med`; within same blast tier, rank
`impact=high` before `impact=med` before `impact=low`; within same blast+
impact, rank by `days_since_update` ascending (fresher first).

### Output format

Render as a markdown table with these columns:

| `#` | `Title` | `Labels` | `Blast` | `Impact` | `Why` |

- `#` — issue number as a link: `[#N](url)`.
- `Title` — issue title, truncated to ~60 chars if needed.
- `Labels` — `label_set` joined by `, `. Empty if none.
- `Blast` — `low` or `med` per rubric above.
- `Impact` — `low`, `med`, or `high` per rubric above.
- `Why` — one sentence, max ~12 words, explaining why this is a quick win.

Cap the table at **10 rows**. If more than 10 survived the filter, add a
footer line:

> _Showing top 10 of N filtered candidates._

If zero issues survived the filter, render:

> _No low-blast-radius candidates in the current backlog._

Do not paraphrase or re-render the script's JSON — use its `signals` fields
as the scoring inputs and produce the table directly.

## Notes

- Step 1's JSON is the structural source of truth for issue data. Do not
  fetch issues a second time or call `gh issue list` again in step 2.
- The `touches_load_bearing` signal is a strong downweight, not a hard
  exclude. An issue touching `CLAUDE.md` can still appear as `blast=med` if
  its impact is high — it just ranks below equivalent `blast=low` items.
- `body_appears_drafted=True` is the strongest positive impact signal
  because it means the deliverable is already written. Shipping it is mostly
  a copy-paste from the issue to a new file.
- This skill is read-only. It does not create, label, assign, or close
  issues. The user always acts on the output themselves.

## Validation

Two reference issues calibrate the scoring. Use these to verify the rubric
stays calibrated after any changes to the skill body:

**#789 — Excalidraw MCP server reference guide (PARAGON: HIGH quick-win)**

- `touches_load_bearing`: False (new standalone file, no harness paths in
  body or title).
- `body_appears_drafted`: True (body contained the full reference guide text
  — a fenced code block, multiple headers, table of contents).
- Labels: `documentation`.
- Expected scoring: `Blast=low`, `Impact=high`. This is the archetype the
  skill is optimised for — content already exists in the issue, blast is
  zero, the `documentation` label confirms user-facing value.

**#800 — bash-as-default migration (PARAGON: LOW / filtered)**

- This issue would have been excluded at the deterministic filter stage by
  either the `umbrella` or `epic` label (it was a multi-step migration
  touching many harness files).
- If it somehow survived filtering (e.g. in a repo without those labels):
  `touches_load_bearing`: True (CLAUDE.md, agents/, hooks/ all in scope).
  `body_appears_drafted`: True (detailed implementation plan in body).
  Expected scoring: `Blast=med`, `Impact=high`. Ranks below any equivalent
  `Blast=low` item despite high impact — the harness-wide blast radius
  overrides the drafted-body signal.

If #789 does not score `Blast=low, Impact=high` in your output, the rubric
has drifted. Re-check `touches_load_bearing` and `body_appears_drafted`
against the signals the script emitted.
