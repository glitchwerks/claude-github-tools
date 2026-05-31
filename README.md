# claude-github-tools

GitHub workflow skills for Claude Code, shipped as a plugin via the glitchwerks marketplace.

## What this is

`claude-github-tools` bundles seven GitHub workflow skills into a single Claude Code plugin. Three skills are pure LLM-based and work immediately after install; four are script-backed and use a plugin-owned Python virtualenv that a bundled `SessionStart` hook materializes automatically on first session.

| Skill | What it does |
|---|---|
| `gh-create-issue` | Runs a structured discovery workflow — codebase read, overlap check, clarification pass — before drafting and creating a well-scoped GitHub Issue |
| `gh-pr-review-address` | Triages every open review comment and failing CI check on your PRs: auto-fixes unambiguous items, surfaces judgment calls for discussion, and logs deferred feedback as new issues |
| `gh-quick-wins` | Filters and ranks the open backlog by blast radius and impact, producing a top-10 table of actionable items |
| `gh-refresh-issues` | Fetches open issues (optionally including PRs) grouped by milestone, with a Labels column — zero LLM tokens consumed |
| `gh-release-status` | Shows the most recent releases table and a per-area diff between the latest release tag and the default branch — what's unreleased and where the changes landed |
| `gh-summary` | Produces a roadmap snapshot covering epics, milestone completion percentages, critical/blocked issues, recent releases, and a short prose summary of recent activity |
| `github-actions` | Expert authoring, review, and debugging assistant for `.github/workflows/*.yml` files, reusable workflows, composite actions, and CI/CD pipelines |

## Prerequisites

**GitHub CLI (required).** All six skills shell out to `gh` for at least some operations. You must be authenticated before using any skill:

```bash
gh auth login
```

**GitHub MCP server (optional).** Skills that create GitHub Issues will use a configured GitHub MCP write tool when one is present (any namespace or implementation) and fall back to the `gh` CLI otherwise. No configuration of a specific tool name is required — the plugin works with or without MCP.

**Python venv (automatic, script-backed skills only).** `gh-quick-wins`, `gh-refresh-issues`, and `gh-summary` run bundled Python scripts. A `SessionStart` hook materializes the plugin's Python virtualenv into the plugin data directory on the first session after install. No manual setup step is needed.

## Install

Inside Claude Code, run these two commands:

```
/plugin marketplace add glitchwerks/claude-plugins
/plugin install claude-github-tools@glitchwerks
```

Open a new session after installation to activate the `SessionStart` hook and materialize the venv for the three script-backed skills.

## Local development

To develop or test against a local checkout, point Claude Code at the plugin directory directly:

```bash
claude --plugin-dir ./claude-github-tools
```

To reload plugins after making changes within a session:

```
/reload-plugins
```

To validate the plugin manifest:

```bash
claude plugin validate .claude-plugin/plugin.json
```

## License

MIT — see [`LICENSE`](./LICENSE).
