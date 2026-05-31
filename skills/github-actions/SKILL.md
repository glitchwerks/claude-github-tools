---
name: github-actions
description: Expert GitHub Actions authoring assistant. Use whenever writing, reviewing, or debugging `.github/workflows/*.yml` files, composing reusable workflows or composite actions, designing job/step structure, configuring permissions, debugging failed workflow runs, or troubleshooting CI/CD pipeline behavior. Trigger for any task involving GitHub Actions workflows, runners, secrets, environments, or release automation.
---

# GitHub Actions Authoring Guidance

> Incident provenance (rule→memory-source map; dogfood/ruleset-gate motivation) lives in [references/incident-history.md](references/incident-history.md) — load only when **revising** this skill.

## 1. Project Conventions (Apply Before Authoring Anything New — In This Order)

**Step 1 — Prefer the shared actions repo first.** Reusable actions live at `glitchwerks/github-actions`. Check there before writing anything new. Reference via `uses: glitchwerks/github-actions/<action-path>@<sha-or-tag>`.

**Step 2 — Search for OSS alternatives.** If the shared repo has nothing, look on the GitHub Marketplace and across well-known orgs (`actions/*`, `azure/*`, etc.) before authoring. Hand-rolled implementations are the last resort, not the first.

**Step 3 — If you must author new logic, ask the project-vs-shared question.** When neither the shared repo nor OSS has what's needed, **always ask the user**: _"Is this project-specific, or is there a chance another project could reuse it? If reusable, it might belong in `glitchwerks/github-actions` instead of in this repo."_ This is a **suggestion only** — YAGNI still applies. Don't preemptively extract or refactor; just surface the question so the user can make an informed call. The reusability question becomes more compelling when similar needs have come up in two or more projects, and less compelling when the logic is genuinely one-off.

**Step 4 — Keep action logic minimal — push business logic into scripts.** Whether the action ends up project-local or in the shared repo, the action YAML should be a thin wrapper: declare inputs/outputs, invoke a script (`scripts/<name>.ps1`, `scripts/<name>.sh`, or `scripts/<name>.py`), surface results. Business logic (file checks, parsing, transformation, conditional branching) belongs in the script. Rationale: scripts are testable in isolation, run locally, and don't trap maintainers in YAML quoting hell. If you find yourself writing a multi-step `run:` block with conditionals, extract it.

## 2. File Layout Convention

- Workflows live in `.github/workflows/<name>.yml`
- Reusable workflows in `.github/workflows/_<name>.yml` (leading underscore) or `reusable-<name>.yml`
- Composite actions in `.github/actions/<name>/action.yml` — and per Section 1, the `action.yml` should call into `scripts/` rather than embed logic
- One workflow per concern — don't bundle CI, release, and scheduled jobs in a single file

## 3. Permissions (Mandatory Least-Privilege)

- The default `GITHUB_TOKEN` has been **read-only since 2023**. Any job that needs to write (create releases, push commits, comment on PRs, create issues) **must** declare a `permissions:` block.
- Common required scopes:
  - `contents: write` — for releases, `softprops/action-gh-release`, `gh release create`
  - `pull-requests: write` — for PR comments
  - `issues: write` — for issue creation/updates
  - `id-token: write` — for OIDC federation (e.g. Azure deployment without long-lived secrets)
- **Always declare permissions at the job level**, not workflow level, when only some jobs need elevated scopes. Workflow-level permissions apply to every job and break least-privilege.

## 4. Triggers

- `push` for branch-event CI; scope with `branches:` to avoid running on every push
- `pull_request` for PR-event CI; prefer over `push` for short-lived branches to avoid duplicate runs
- `workflow_dispatch` for manual runs (with `inputs:` for parameterization)
- `schedule` for cron jobs (UTC; standard cron syntax)
- `workflow_call` for reusable workflows
- **Avoid mixing `push` + `pull_request` without `concurrency:`** — produces duplicate runs on every PR push

## 5. Job Structure

- **Split lint and test into separate jobs**, not sequential steps in one job. Distinct check entries make failure source obvious at a glance, and the jobs parallelize.
- Use `needs:` for job dependencies; use `if:` for conditional execution
- Use `concurrency:` group with `cancel-in-progress: true` for PR workflows to avoid wasted compute on rapid pushes

## 6. Action Pinning (Security)

- Pin third-party actions by **commit SHA**, not tag — tags are mutable.
- Example: `uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11  # v4.1.1`
- First-party `actions/*` actions can use major-version tags (`@v4`) since they're trusted, but SHA-pinning is still recommended for reproducibility.

### SHA-pin to the commit SHA, never the annotated-tag object SHA

When you resolve a tag to a SHA for pinning, **dereference annotated tags to their underlying commit**. `git refs/tags/<tag>` returns a commit SHA for a lightweight tag but a **tag-object SHA** for an annotated tag — and the runner resolves `uses: owner/action@<sha>` as a _commit_ ref. Pinning the tag-object SHA fails **silently at startup** with `"This run likely failed because of a workflow file issue."` — no jobs start, no logs. `actionlint` does NOT catch this (runtime resolution error, not syntax), and YAML parsing passes.

The trap is partial: lightweight-tag pins in the same PR work fine, so it looks like "some workflows fail" rather than a systematic pinning bug. The same failure hits Claude Code `marketplace.json` `plugins[].source.sha` (loader reports `<plugin> not found in the marketplace`).

```bash
# Always check .object.type first; dereference one more level if it's a tag object.
# Use `gh api <path> --jq` directly — gh api takes an API path, not stdin, and its
# built-in --jq avoids the external-jq dependency (not on PATH on this host, see #544).
type=$(gh api "repos/$owner/$repo/git/refs/tags/$tag" --jq .object.type)
sha=$(gh api "repos/$owner/$repo/git/refs/tags/$tag" --jq .object.sha)
if [ "$type" = "tag" ]; then
  commit=$(gh api "repos/$owner/$repo/git/tags/$sha" --jq .object.sha) # deref annotated tag
else
  commit=$sha
fi
```

**Local-clone shortcut:** force the commit deref with `git rev-parse <tag>^{commit}` — bare `git rev-parse <tag>` returns the tag-object SHA on annotated tags. (`azure/login@v3` and `glitchwerks/github-actions` tags are annotated; many `docker/*` tags are lightweight — re-verify per pin.) This extends §6's pinning rule: always verify a pin at write time by checking `object.type == "tag"` and dereferencing if so.

## 7. Secrets and Environments

- Repository secrets: `${{ secrets.NAME }}`
- Environment-scoped secrets: declare `environment:` on the job, then access via `secrets.NAME`
- Variables (non-sensitive): `${{ vars.NAME }}` — distinct namespace from secrets
- **Never `echo` a secret directly** — use `::add-mask::` if you must surface a derived value

## 8. Caching

- Most `setup-*` actions support caching via a `cache:` input — prefer that over manual `actions/cache` when available
- Cache keys should include lockfile hashes, e.g. `${{ hashFiles('**/package-lock.json') }}` — don't cache by branch name alone

## 9. Matrix Strategies

- `strategy.matrix` for cross-version / cross-OS testing
- `fail-fast: false` when you want all matrix jobs to complete regardless of one failing
- `include:` / `exclude:` for surgical matrix shaping

### Matrix Cost Warning (Read Before Adding Cross-OS Matrices)

Matrix jobs multiply runtime cost by the number of cells, and **macOS billing is 10× Linux** (Windows is 2×, both per GitHub's runner billing multipliers). A 3-OS × 2-version matrix triggered on every PR can burn through the entire monthly free Actions tier (2,000 billed minutes) in **a few days**, especially if you ship multiple PRs per day. Before adding any cross-OS matrix, ask:

1. **Is the signal worth the cost?** macOS rarely catches anything Linux doesn't for byte-equal output, formatter consistency, or generic Python/Node tests. macOS earns its 10× cost only for genuinely platform-specific code (Cocoa/AppKit bindings, file-system case-insensitivity tests, Apple Silicon-specific behavior). For a JSON-byte-equality drift detector, Linux + Windows is sufficient.
2. **Does this need to run on every PR?** Drift detectors and cross-environment stability checks usually don't need per-PR cadence — once-a-week catches it. Move them to `schedule:` + `paths:` filter and use `workflow_dispatch:` for ad-hoc full-matrix runs. Reserve every-PR cadence for fast unit/lint jobs that block bad merges.
3. **Are all matrix cells actually exercised?** Two Python versions when the project pins one. Three OSes when only one ships. These cells produce noise without signal and double or triple your cost.

Default new cross-OS matrices to `ubuntu-latest` only on every-PR cadence; add other OSes only with documented per-OS justification, and prefer scheduling them weekly via a separate workflow file. See `.github/workflows/catalog-stability.yml` in this repo for the canonical pattern (paths-filter + weekly cron + `workflow_dispatch` with a `full-matrix` toggle for ad-hoc broader runs).

## 10. Reusable Workflows vs Composite Actions

- **Reusable workflow**: a full job-level abstraction, called via `uses: ./.github/workflows/_lint.yml`. Inherits secrets via `secrets: inherit`.
- **Composite action**: a step-level abstraction, called via `uses: ./.github/actions/setup-stack`. Cleaner for setup logic.
- Pick reusable workflow when you need full job semantics (matrix, env, permissions); composite for simple step bundles.

## 11. Debugging Failed Runs

- Use `gh run view <id> --log-failed` to fetch the failing step's log without scrolling the full run.
- Fetch CI status directly via `gh run list --repo <owner>/<repo>` and `gh pr checks <N>` — there is no MCP equivalent for these commands.
- Set the `ACTIONS_STEP_DEBUG=true` repository variable for verbose step logging.

## 12. Companion Skills

- PowerShell-based workflow steps → `powershell` skill
- Python-based workflow steps → `python` skill
- GitHub-side metadata (issues, PRs, releases) → your configured GitHub MCP tools if present (the model resolves the exact tool name, which varies by install), otherwise the `gh` CLI

## 13. Common Issues

### Release jobs fail with HTTP 403 even with a valid token

If `softprops/action-gh-release@v2` or `gh release create` fails with `{"message":"Resource not accessible by integration"}` (HTTP 403), the cause is almost always a missing `permissions: contents: write` on the job — not an invalid PAT or wrong secret value. The default `GITHUB_TOKEN` is `contents: read` since 2023.

```yaml
jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      contents: write # required for release creation
    steps: ...
```

Always scope permissions at the **job level**, not workflow level — only the publishing job needs the elevated scope.

### Branch protection blocks pre-merge deploy validation

Before adding a "DO NOT MERGE pending validation" banner to an infra PR, check whether the repo's deployment workflow can actually run against feature branches. Many repos restrict environment workflows (`environments: dev` / `prod`) to `main` or release tags via GitHub Environments protection rules. If feature-branch deploys are blocked, the banner is wrong — the user cannot validate before merging.

**Correct shape for environment-restricted infra PRs:**

1. Remove the DO NOT MERGE banner. Replace with a "Deployment workflow" section explaining the post-merge sequence.
2. Move all post-merge validation items into a dedicated follow-up issue (items inside a closed PR body disappear from view).
3. Document the rollback path explicitly in both the PR body and the follow-up issue.
4. Distinguish clearly: pre-merge gate = CI (`bicep build`, lint, tests); post-merge gate = live-environment validation in the follow-up issue.

For repos where feature deploys ARE allowed, the standard "DO NOT MERGE + pre-merge validation" pattern still applies — these two shapes are distinct.

### "We dogfooded it" — when the dogfood didn't actually exercise the change

Repos that publish reusable workflows or composite actions and consume them from within themselves (e.g. `glitchwerks/github-actions`'s own workflows call `glitchwerks/github-actions/<action>@v2`) have a structural dogfooding limitation: **PRs opened against the repo execute the released tag's code, not the branch's code.** GitHub Actions does not support expressions in `uses:` values, so there is no `@main` vs `@v2` conditional. Even if the PR's diff edits the composite action heavily, the repo's own CI still calls the old `@v2` version of that action.

This produces a recurring failure mode: a green CI rollup is claimed as dogfood validation, but the actual code path under test was the released code, not the branch code. The bug — or the unverified new behavior — ships unnoticed because every check passed.

**Verification discipline (mandatory for any composite-action / reusable-workflow change in a self-referencing repo):**

1. **Identify whether the change can be dogfooded at all.** If the modified action is referenced via `<owner>/<repo>/<action>@<tag>` from within the same repo, dogfood is structurally blocked. Acknowledge this explicitly in the PR body — do not let "all checks pass" stand in for validation.
2. **Look for an observable that proves the new code ran.** Examples:
   - **Identity change** (this PR's case, #250): if the change switches the bot identity that posts review comments, inspect the PR's actual review comment author. `gh api repos/<owner>/<repo>/issues/<n>/comments --jq '.[] | .user.login'` returns the literal API value (`github-actions[bot]` if the old code ran, the App's `[bot]` if the new code ran). The CLI form `gh pr view --json comments` strips the `[bot]` suffix from `author.login` and is misleading — go to the REST API for unambiguous identity.
   - **Behavior change**: the new code emits a log line, output, or status the old code did not. Inspect the run log directly.
   - **Selector change**: a comment / status the old code would have posted is now absent or different. Look for the absence as a positive signal.
3. **If no observable exists, dogfood is impossible.** Validate via floated-tag-on-external-consumer instead — temporarily move `@v2` to the branch's HEAD and trigger a run on a separate consumer repo. Do this _before_ merge, not after.
4. **Never accept "CI green" as dogfood evidence.** A passing rollup proves only that the released code still works with whatever YAML / inputs / secrets the PR added — it proves nothing about the new code path. The check that catches the regression is the check that runs the new code.

This is a verification gate, not informational text: if a PR claims dogfood validation, the PR body or review must cite the specific observable that distinguishes new-code from old-code execution. (Why this is a hard gate — recurrence history — in [references/incident-history.md](references/incident-history.md).)

### `astral-sh/setup-uv` + `uv pip install` fails with "No virtual environment found"

If a CI job using `astral-sh/setup-uv` fails on the first `uv pip install` step with:

```
No virtual environment found for Python <version>; run `uv venv` to create
an environment, or pass `--system` to install into a non-virtual environment
```

…the cause is that `setup-uv` installs `uv` and provisions a managed Python, but does NOT create a `.venv`. `uv pip install` refuses to operate on system Python without an explicit opt-in.

**Tempting wrong answer**: `env: UV_SYSTEM_PYTHON: 1` at workflow scope. This fails twice:

1. On Ubuntu runners, `setup-uv`'s managed Python lives in a temp `UV_PYTHON_INSTALL_DIR`, not in system paths — `UV_SYSTEM_PYTHON=1` makes uv look in `/usr` where the requested version isn't installed.
2. The runner's actual system Python (e.g. Ubuntu 24's `/usr/bin/python3.12`) is flagged "externally managed" per PEP 668 and blocks `uv pip install` even with `--system`.

**Correct fix**: set `activate-environment: "true"` on the `setup-uv` step itself. This tells the action to run `uv venv .venv` using its managed Python and activate the venv for subsequent steps — `uv pip install` and `uv run` then resolve against `.venv` cleanly.

```yaml
- uses: astral-sh/setup-uv@<sha> # vX.Y.Z
  with:
    python-version: "3.12"
    activate-environment: "true"
- run: uv pip install -e ".[dev]"
- run: pytest -v
```

`setup-uv` also obviates a separate `actions/setup-python` step — its `python-version` input handles Python installation. Caught on `glitchwerks/claude-wayfinder#16`.

### `docker/build-push-action` cache export fails with "not supported for the docker driver"

If a build dies immediately with `ERROR: failed to build: Cache export is not supported for the docker driver`, the cause is a missing `docker/setup-buildx-action` step — not a `cache-from` issue, Dockerfile bug, or registry-auth problem (the error names the fix directly). Any `cache-to: type=gha` (or `type=registry` / `type=local` / `type=inline`) on `docker/build-push-action` requires a `docker-container`-driver builder; the runner's bundled `docker` driver does not support cache exporters.

```yaml
- uses: actions/checkout@<sha>
- uses: docker/setup-buildx-action@<sha> # REQUIRED before build-push when caching
- uses: docker/login-action@<sha> # login must run on the new builder
  with: { ... }
- uses: docker/build-push-action@<sha>
  with:
    push: true
    cache-from: type=gha
    cache-to: type=gha,mode=max
```

**Pre-merge gap:** the PR's own CI usually passes, because the buildx-driven build runs only from the image-push workflow (`workflow_run` on `main`), not on the PR. First observation of the failure is therefore _after_ merge — name the post-merge check explicitly in the PR test plan (`gh run list --workflow=build-image.yml --branch main --limit 1 → success`). (mom-bot #79 / PR #80.)

### `pull_request_target` PR body/title are fork-writable — gate before honoring directives

`pull_request_target` runs in the target repo's context with target-repo secrets, but the PR `body` and `title` are owned by the **head-ref (fork) author** and editable by them at any time. Any workflow that reads `github.event.pull_request.body` (or `gh pr view --json body`) to look for a directive — a `gate-override:` line, a skip-CI marker, a prose label-equivalent — and acts on it **without gating is a privilege-escalation vector**: a fork-PR author can write the directive into their own PR and force the downstream action.

**Safe pattern:** before honoring any body/title-derived directive, gate on `author_association ∈ {OWNER, MEMBER, COLLABORATOR}` (or, more conservatively, `github.actor`) via the allowlist → author*association → API-collaborator-fallback check. Namespace + fence the directive (`<!-- pr-review-gate-override: <justification> -->`). PR \_comments* from `[bot]` accounts are safer (bots can't be fork-PR authors); PR body is not. (inquisitor pass on glitchwerks/github-actions#265.)

## 14. Branch Protection / Rulesets

**A new required-on-merge CI check is not done landing until it is wired into a ruleset that gates `main`.** A passing check that is not in the ruleset can be ignored at merge time — the gate is the ruleset, not the check's existence. Every author adding a new workflow must, in the same change set, either add the check to an existing ruleset covering `main` or create a new ruleset if none does. Use **rulesets**, not classic branch protection — the modern mechanism is what current GitHub UIs and APIs surface.

**Scope.** This applies to checks the team would consider blocking — lint, type, unit/integration tests, security scans, schema validation, anything whose failure on a PR should prevent merge. It does **not** apply to optional informational checks, scheduled cron jobs, drift detectors that run weekly, or local-only validations. If you cannot answer "should a red signal here block merge?" with a clear yes, the check is not a required check.

**Recipe.**

```bash
# 1. Find which ruleset(s) gate `main`
gh api "repos/<owner>/<repo>/rulesets" \
  --jq '.[] | select(.target == "branch") | {id, name, enforcement}'

# 2. Inspect the current required_status_checks on a ruleset
gh api "repos/<owner>/<repo>/rulesets/<id>" \
  --jq '.rules[] | select(.type == "required_status_checks")'

# 3. Add a check: PATCH the ruleset with the full updated rules array.
#    GitHub's API requires the entire rules array, not a delta — fetch
#    the current rules, append the new check name (matching by exact job
#    name as it appears in the GitHub Checks UI) to the
#    required_status_checks rule's parameters, then PATCH back.
#    Shape of the spliced rule:
#      {
#        "type": "required_status_checks",
#        "parameters": {
#          "required_status_checks": [
#            {"context": "JS Hook Tests"},
#            {"context": "Python Hook Tests"},
#            {"context": "your new check name here"}
#          ],
#          "strict_required_status_checks_policy": false
#        }
#      }
#    Full schema: https://docs.github.com/en/rest/repos/rules#update-an-organization-repository-ruleset
gh api "repos/<owner>/<repo>/rulesets/<id>" -X PATCH --input ruleset.json
```

If no ruleset covers `main`, create one via `POST /repos/{owner}/{repo}/rulesets` — see the [GitHub docs](https://docs.github.com/en/rest/repos/rules#create-a-repository-ruleset) for the full payload shape (target, conditions, rules array). The ruleset's `target: "branch"` and `conditions.ref_name.include: ["~DEFAULT_BRANCH"]` is the standard pattern for gating `main`.

**Catch-up audit.** When this skill is first applied to a repo with existing CI but no ruleset coverage, the corrective change is one PR per check (or one omnibus PR adding all of them) — not a backfill done quietly later. Half-protected `main` is the default failure mode.

**Paths-filter second-order rule:** when a new required check uses a paths-filter, verify its behavior on PRs that both should and should not trigger it **before** adding the ruleset entry — a check already in the ruleset can block merges on PRs that legitimately did not run it. Historical motivation (#282, #326 — shipped-but-not-required; #331 — paths-filter mismatch): see [references/incident-history.md](references/incident-history.md).

### GitHub Actions cannot be a ruleset bypass actor

When a workflow push to a protected ref (e.g. a `ci-v*` tag under a "Restrict creations" ruleset) fails with `GH013: Repository rule violations found` / `Cannot create ref due to creations being restricted`, the instinctive fix — adding GitHub Actions to the ruleset's `bypass_actors` — **does not work**. A PUT with `{"actor_id": 15368, "actor_type": "Integration"}` returns `422 Validation Failed: "Actor GitHub Actions integration must be part of the ruleset source or owner organization"`. `actor_type: Integration` accepts only apps the org has **installed** (visible via `gh api orgs/<org>/installations`); GitHub Actions is a built-in, not an installable app, so it is never in that list.

**Before any bypass-list mutation**, check `gh api orgs/<org>/installations` — if `actions[bot]` isn't there (it won't be), skip straight to an alternative. Confirmed working, cheapest first:

1. **PAT-based push** — pass an org-scoped PAT via `token:` to `actions/checkout`; the push authenticates as the PAT owner, whose repo role (Admin) is covered by the ruleset's `RepositoryRole` bypass entry. **Zero ruleset mutation.**
2. **Installed GitHub App token** — for production pipelines where attribution matters: install an App, add its `app_id` to `bypass_actors`, mint a token via `actions/create-github-app-token`.
3. **Manual push by a bypass-eligible user** — workflow prints the `git tag && git push` commands for the user to run locally.

`GITHUB_TOKEN` will not work for ref creation under "Restrict creations" rulesets — plan the auth identity from the start. (claude-configs PR #380 fix; PR #377 / issue #378 origin.)
