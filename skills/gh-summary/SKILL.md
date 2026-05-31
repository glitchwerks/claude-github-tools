---
name: gh-summary
description: >
  Roadmap snapshot of the current GitHub repo — epics, milestones with
  descriptions and completion percentages, critical/blocked issues, recent
  releases, plus a short LLM-written summary of recently updated issues.
  Trigger this skill whenever the user types /gh-summary, says
  "show me the roadmap", "roadmap summary", "what's the state of the repo",
  "completion rates", "critical issues", "blocked work", "recent releases",
  "summarize this repo", "high-level overview", "what's making progress",
  or any similar request for a high-altitude view of the repo's open work.
  Hybrid: deterministic script output + a one-paragraph LLM summary of
  recent issue activity.
---

# /gh-summary

Two-step recipe. The script emits the deterministic roadmap snapshot —
Epics, Milestones (with descriptions), Critical / blocked issues, and
Recent releases — from `gh api` and `gh release list`. The LLM-summary
step then reads the N most recently updated open issues and writes a
short prose paragraph about themes, momentum, and anything that looks
load-bearing. Step 1 is zero-LLM; step 2 is the only place LLM tokens
are spent.

## Step 1 — Run the deterministic script

Run the script and surface its stdout verbatim:

```bash
PY="${CLAUDE_PLUGIN_DATA}/venv/Scripts/python.exe"
[ -f "$PY" ] || PY="${CLAUDE_PLUGIN_DATA}/venv/bin/python"
"$PY" "${CLAUDE_PLUGIN_ROOT}/scripts/gh-summary.py"
```

Paste the script's output directly into the conversation without
modification or interpretation. If the script exits non-zero, surface
its stderr and stop.

The script supports `--critical-labels blocked,security,bug` (default
shown) to override which labels qualify an issue as critical. Pass it
through if the user has asked for a different critical set.

## Step 2 — Summarize recent issue activity

Fetch the 10 most recently updated open issues and write a one-paragraph
summary. Use `gh issue list` (NOT the `/issues` API endpoint — that
includes PRs):

```bash
gh issue list --state open --limit 10 \
  --search "sort:updated-desc" \
  --json number,title,updatedAt,labels,body
```

The `--search "sort:updated-desc"` is load-bearing: without it, `gh issue list` orders by `CREATED_AT` (its default), and the result will be the 10 newest-created open issues rather than the 10 most recently updated — a different set, and not the one the prose summary contract promises.

Then write a short prose paragraph (3–5 sentences) covering:

- **Themes** — what areas of the repo are seeing activity (auth, CI, harness, scripts, etc.)
- **Momentum** — which issues look fresh / actively-being-worked vs. dormant updates
- **Load-bearing items** — anything in the recent set that looks like it would block other work, has a `blocked` label, or names a critical contract

Render as a section titled `### Recent issue activity` below the
script's output. Keep it under 6 sentences — the script section is
where the structural detail lives; this section adds narrative on top.

If `gh issue list` returns an empty array, render the section as a
single line: `_No open issues with recent activity._`

## Notes

- Step 1's output is the structural source of truth; do not paraphrase
  or re-render it from the JSON. Pasting verbatim is the whole point of
  having a deterministic script.
- Step 2 must not double-count items already surfaced by the script's
  "Critical / blocked issues" subsection. If an item appears in both,
  reference it by `#N` only in step 2 — do not restate the title /
  labels that step 1 already rendered.
