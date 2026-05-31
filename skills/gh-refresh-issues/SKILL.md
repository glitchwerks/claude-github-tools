---
name: gh-refresh-issues
description: >
  Fetch open issues from the active GitHub repo and display them grouped by
  milestone, with a Labels column. Trigger this skill whenever the user types
  /gh-refresh-issues, says "show me open issues", "what's open", "refresh
  issues", "list issues", "what issues are there", "show me issues and PRs",
  "what's open including PRs", or any similar request to see the current
  open-issue backlog. Optionally accepts a label filter as an argument
  (e.g. /gh-refresh-issues bug filters to only bug-labeled issues). Pass
  `--prs` to combine open PRs with issues in the same milestone-grouped view.
---

# /gh-refresh-issues

Display open GitHub issues from the active repo grouped by milestone.
Runs `scripts/gh-refresh-issues.py` and surfaces stdout directly —
zero LLM tokens consumed.

```bash
# Resolve the plugin's bundled Python interpreter (Windows Git Bash vs POSIX)
PY="${CLAUDE_PLUGIN_DATA}/venv/Scripts/python.exe"
[ -f "$PY" ] || PY="${CLAUDE_PLUGIN_DATA}/venv/bin/python"
SCRIPT="${CLAUDE_PLUGIN_ROOT}/scripts/gh-refresh-issues.py"

"$PY" "$SCRIPT"
"$PY" "$SCRIPT" --prs
"$PY" "$SCRIPT" meta
"$PY" "$SCRIPT" meta --prs
```

Pass the positional `label_filter` arg to restrict to issues carrying that
label. Add `--prs` to include open PRs in the same milestone-grouped view
(adds a Type column). Output is markdown; surface it as-is.
