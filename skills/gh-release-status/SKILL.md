---
name: gh-release-status
description: >
  Release status snapshot for the current GitHub repo — what's unreleased,
  diff against the last release, changes since the last release, release
  status, what shipped since the last release, what's in main but not yet
  tagged. Shows the most recent releases table plus a per-area file-change
  breakdown between the latest release tag and the default branch.
  Trigger this skill whenever the user types /gh-release-status, asks
  "what's unreleased", "what are the changes since the last release",
  "show the release status", "diff against last release", "what shipped
  since the last release", "what's in main that hasn't been released",
  "what's between the last tag and HEAD", or any similar request about
  what has changed since the most recent release.
  Hybrid: deterministic script output + a single LLM prose step.
---

# /gh-release-status

Two-step recipe. The script fetches the recent releases table and the
compare diff between the latest release tag and the default branch via
`gh` and the GitHub compare API — zero LLM tokens. The LLM step then
writes a short prose section grouping the diff by feature area.

## Step 1 — Run the deterministic script

Run the script and surface its stdout verbatim:

```bash
PY="${CLAUDE_PLUGIN_DATA}/venv/Scripts/python.exe"
[ -f "$PY" ] || PY="${CLAUDE_PLUGIN_DATA}/venv/bin/python"
"$PY" "${CLAUDE_PLUGIN_ROOT}/scripts/gh-release-status.py"
```

Paste the script's output directly into the conversation without
modification or interpretation. If the script exits non-zero, surface
its stderr and stop — do not proceed to Step 2.

The script supports these flags:

- `--repo OWNER/REPO` — target a specific repo (default: auto-detected
  from the current directory via `gh repo view`)
- `--limit N` — number of recent releases to show in the table
  (default: 5)

Pass flags through if the user has specified them.

### What the script outputs

1. **Recent releases** table — the last N releases with tag name,
   published date (`YYYY-MM-DD`), and release title.
2. **Changes since `<tag>`** — total commits, files changed,
   insertions/deletions, and a markdown table breaking down the diff
   by top-level directory area (e.g. `scripts`, `skills`, `.github`).

### Edge cases the script handles

- **No releases yet** — emits `_No releases yet — nothing to diff
  against._` and exits 0. Do not proceed to Step 2.
- **Up to date** — emits an explicit "up to date" line when there are
  no commits since the last release. Acknowledge this and skip Step 2.
- **Large diffs** — the GitHub compare API caps `files[]` at 300.
  The script renders top-level totals and a truncation note; do not
  attempt to enumerate every file.

## Step 2 — Write the changes narrative

After pasting the script's output verbatim, write a `### Changes since
<tag>` prose section. This is the **only** step that spends LLM tokens.

Use the per-area stats from the script output and the commit subjects
to write a short (4–6 sentences) grouped narrative covering:

- **Feature/area themes** — which top-level dirs saw the most activity
  and what the work is broadly about (e.g. "Most changes are in
  `scripts/` — new release-status script and extraction of the shared
  `render_recent_releases` helper")
- **Notable changes** — any new skills, scripts, or significant
  refactors visible in the diff
- **Size signal** — call out unusually large or small diffs relative
  to the release cadence if relevant

Do **not** re-render the area table or restate numbers already in the
script output — this section adds narrative on top of the structured
data. Keep it under 150 words.

If there are no unreleased commits, skip Step 2 entirely and note that
the repo is current with the last release.

## Notes

- The script fetches the compare diff via the GitHub REST compare API
  (`/repos/{owner}/{repo}/compare/{tag}...{branch}`) — no local `git`
  clone or `git log` is performed.
- Step 1's output is the structural source of truth. Do not paraphrase
  or re-render it from JSON; paste verbatim.
- The per-area table groups files by their first path segment (e.g.
  `scripts/gh-release-status.py` → area `scripts`). Root-level files
  (e.g. `README.md`) are grouped under their own filename.
