---
name: gh-pr-review-address
description: >
  Process open PR review feedback and failing CI checks in the current repo.
  Triages every review comment and status check failure — auto-fixes unambiguous
  items and commits directly, discusses judgment calls with the user, and logs
  out-of-scope feedback as new GitHub issues. Can target a single PR by number
  or process all open PRs authored by the user.

  Trigger this skill whenever the user says things like "check my PR feedback",
  "address my review comments", "what's blocking my PR", "process my PR reviews",
  "handle PR feedback", "anything blocking merge", "fix my CI failures",
  "address PR #42", "check PR 15", or any variation of wanting to act on GitHub
  pull request review comments or failing checks. If the user is in a repo and
  mentions PRs, reviews, or CI status in any action-oriented way, this skill
  should activate.
---

# PR Feedback Processor

## Your role in this skill

You are the **orchestrator**. Your job is to gather information, triage feedback,
and hand off work to the right specialist agent for each task. Do not write code,
edit files, or run commits yourself — delegate those actions.

The guiding principle: **if a reviewer raised it and it carries signal, address
it**. Documentation gaps, missing tests, correctness concerns, and substantive
style issues are part of the workflow — none of those are beneath fixing.

Two categories don't get acted on this PR:

1. **Nit / cosmetic findings** (see § Suppression filter in Step 3) — silently
   skipped to break the unbounded review-fix-review loop. Each fix-up commit
   re-triggers bot review, which finds more nits, ad infinitum. Suppressing
   them at intake is the cheapest stop rule.
2. **Genuinely out-of-scope feedback** — logged as a new GitHub issue rather
   than dropped.

---

## Step 1 — Identify the repo and target PRs

The user may specify a single PR to process — by number (`#42`, `42`), URL, or
branch name. If they do, skip the discovery step and target only that PR.

1. Run `git remote get-url origin` to get the current repo's remote URL. Parse the
   `owner` and `repo` from it (handle both HTTPS and SSH formats).
2. **If the user specified a PR**: confirm it exists and is open directly:
   ```bash
   gh pr view <N> --repo <owner>/<repo> --json number,title,state
   ```
3. **If no PR was specified**: identify your open PRs directly:
   ```bash
   # Get the authenticated username
   gh api user --jq .login
   # List open PRs authored by that user
   gh pr list --repo <owner>/<repo> --state open --author @me --json number,title,headRefName
   ```
4. If there are no matching PRs, tell the user and stop.

---

## Step 2 — Gather feedback for each PR

Fetch the full PR data directly, running these in parallel:

```bash
# 1. PR details + formal review bodies — title, description, changed files, reviews
gh pr view <N> --repo <owner>/<repo> --json title,body,files,reviews,state

# 2. Inline review-thread comments (anchored to file + line)
gh api repos/<owner>/<repo>/pulls/<N>/comments --jq '.[]'

# 3. General PR-conversation comments (SEPARATE source — where bots post)
gh api repos/<owner>/<repo>/issues/<N>/comments --jq '.[]'

# 4. Mergeable state — fetch the literal value; do not interpret
gh pr view <N> --repo <owner>/<repo> --json mergeable,mergeStateStatus
```

For item 4: if `mergeable` is `UNKNOWN`, wait ~5 seconds and re-run that command once. If still `UNKNOWN` after one retry, proceed with `UNKNOWN` noted explicitly — do not interpret it as clean.

These commands return **three distinct finding streams** — keep them as
separate lists through triage, never merge them into one:

- **Review-body findings** — the `reviews[].body` text from #1 (a reviewer's formal
  review summary).
- **Inline findings** — the per-line review-thread comments from #2.
- **Conversation findings** — the general PR-conversation comments from #3.

**Why #2 and #3 are both required:** `get_pull_request_comments` returns ONLY inline
review-thread comments (anchored to a file + line). General PR-conversation comments —
where automated review bots like `claude-action-runner`, `coderabbitai`, and `copilot[bot]`
post substantial multi-finding reviews — live in the **issue_comments API**, because GitHub
treats a PR as a special issue sharing the same number (PR # = issue #). Fetching only inline
comments silently misses these. Treat any review by an automated reviewer as a high-priority
signal even when it arrives as a single conversation comment.

**Why the formal review body is not canonical:** bot reviewers (Codex, CodeRabbit,
Copilot) increasingly post their actual findings as **inline comments** (#2) while the
formal review **body** (#1) carries only a generic "Here are some automated review
suggestions" preamble — no severity tags, no findings text. **An empty or preamble-only
review body does NOT mean "no findings"** — the findings are in the inline stream. Never
let the review-body text gate whether you inspect the inline list; the two streams are
independent. (Incident: a P1 inline comment was silently dropped because the review body
was treated as canonical — `glitchwerks/claude-configs` PR #833 / commit `be03d7f`.)

**Determining what's unresolved:** A comment is resolved if the thread is marked
resolved on GitHub, or if a later commit message or reply clearly addresses it.
When in doubt, treat it as unresolved.

### Merge conflicts

The `mergeable` field returned by the Step 2 query above will be `"CONFLICTING"`
if there are conflicts, `"MERGEABLE"` if clean, or `"UNKNOWN"` if GitHub is still
computing (the Step 2 instructions already call for a retry once and returning the
literal `UNKNOWN` rather than interpreting it).

**Sanity check on recently-pushed branches.** GitHub can return a stale or ambiguous
`mergeable` value on a freshly-pushed branch where the merge computation hasn't finished.
A false "clean" here leads you to merge a conflicted PR. So when `mergeable` reads clean
on a recently-pushed branch, verify by running:

```bash
gh pr view <N> --repo <owner>/<repo> --json mergeable,mergeStateStatus
```

If `mergeStateStatus` is anything other than `CLEAN` / `HAS_HOOKS` / `UNSTABLE` — especially
`DIRTY` (conflict), `BLOCKED` (waiting on CI/reviews), or `BEHIND` (out of date with base) —
treat the earlier mergeable result as suspect and re-triage.

If conflicts exist, flag them as a blocking item for triage in Step 3. Merge
conflicts are higher priority than review comments — a conflicted PR can't merge
regardless of review status.

### CI status checks

Also fetch the PR's check suite / status check results. Use
`gh pr checks <N> --json name,state,conclusion` (there is no MCP equivalent for
this). Look for any checks with `conclusion` of `failure`, `action_required`,
or `cancelled`.

For each failing check, capture:

- The check name (e.g. `lint`, `test`, `build`)
- The failure summary or log URL
- Enough of the log output to understand what failed (use
  `gh run view <run-id> --log-failed` to get the relevant log lines)

Failing CI checks are just as blocking as review comments — treat them as
additional items to triage in the next step.

---

## Step 3 — Triage each comment and CI failure

Do this yourself — triage is analysis, not implementation.

### Iterate every finding stream independently

Triage walks all three Step 2 finding streams — review-body, inline, and
conversation — and treats each as its own list:

- **Iterate inline comments one at a time.** Each inline comment is a separate
  finding; do not expect one finding per formal review, and do not roll multiple
  inline comments up into a single review-level verdict. Every inline comment
  ends up as its own row in the triage matrix.
- **An empty or preamble-only review body must NOT short-circuit triage.** If the
  formal review body (#1) is blank or just a generic preamble, that says nothing
  about the inline (#2) and conversation (#3) streams — triage those in full
  regardless. Never use "review body had no findings" as a reason to skip the
  inline list.

Then apply the **suppression filter first**, then evaluate the surviving items on
the two axes that follow.

### Suppression filter (apply first)

Skip findings that match any of these patterns. Suppression is silent — the
items are still counted and listed in the Step 5 summary so the user can
override, but they do not generate fix-up commits.

**Bot-specific patterns:**

| Bot                             | Suppress these                                                               |
| ------------------------------- | ---------------------------------------------------------------------------- |
| `claude-action-runner[bot]`     | `findings.low` (the schema's lowest tier)                                    |
| `coderabbit-ai[bot]`            | Inline comments starting with `Nitpick:` or `Nit:`                           |
| `chatgpt-codex-connector[bot]`  | Findings explicitly tagged `P3` or lower                                     |
| `copilot-pull-request-reviewer` | Suggestions framed as style/formatting preferences with no correctness claim |

**Cross-bot pattern (apply to any review source, including human reviews):**

Suppress findings that are _purely cosmetic_ — i.e. the reviewer would accept
either form as correct, and the change has no impact on behavior, correctness,
performance, or security. Concrete examples that get suppressed:

- Pure formatting preferences not enforced by the project's formatter
- Naming preferences where both forms are idiomatic
- Comment wording tweaks ("could be clearer", "consider rephrasing")
- Re-ordering imports / fields when project has no rule
- Choice of equivalent stdlib functions (`x.append(y)` vs `x += [y]`)

**Always keep (never suppress), regardless of how the bot tagged it:**

- Anything Medium severity or above
- Anything naming a real bug, incorrect behavior, or broken assumption
- Security findings of any severity
- Performance concerns of any severity
- Findings about missing tests, missing error handling, or unhandled edge cases
- Findings the project's formatter/linter would flag (those are correctness in
  this codebase, not style)
- Documentation gaps that affect a user-facing surface (README, public docstring)

**When in doubt:** keep the finding. The cost of one extra fix-up commit is
lower than the cost of shipping a real issue past review because it was
tagged "nit."

### In scope or out of scope?

The PR has a stated purpose from its title and description. Feedback is **out of
scope** if it addresses something genuinely unrelated to that purpose — a different
system, pre-existing code the PR didn't touch, or a separate feature entirely.

Feedback is **in scope** if it relates to any code, documentation, or behavior
introduced or touched by this PR — even if the comment feels minor.

### Auto-fixable or needs discussion?

**Auto-fixable** — the fix is unambiguous and can be delegated with confidence:

- Typos or grammar in comments, docs, or strings
- Missing or incomplete docstrings/comments
- A specific variable rename the reviewer called out
- A null/bounds check the reviewer explicitly requested
- Formatting or style that deviates from the codebase convention
- README or changelog updates
- **CI failures with clear errors** — lint violations, type errors, failing tests
  where the log output points to a specific file and line

**Needs discussion** — requires a judgment call before delegating:

- Architectural trade-offs
- Ambiguous reviewer intent ("this feels off" without a clear direction)
- Changes that would affect other callers or downstream behavior
- Anything where getting it wrong would introduce a bug
- **CI failures with ambiguous causes** — flaky tests, environment issues,
  failures where the root cause isn't obvious from the logs

When uncertain whether something is auto-fixable, default to discussing it rather
than guessing.

---

## Step 4 — Take action via delegation

Process in this order: merge conflicts first (nothing else matters if the PR
can't merge), then CI failures (they often block everything else), then
auto-fixable review comments, then discussion items, then out-of-scope items.

### Merge conflicts → delegate to `debugger`

If the PR has merge conflicts, spawn a **`debugger`** agent with:

- The PR number and branch name
- The base branch (usually `main`)
- Instruction to merge the base branch into the PR branch, resolve conflicts,
  and push the result
- The list of files changed in the PR (so the agent understands intent when
  resolving conflicts)

If the conflicts are complex (touching the same logic in multiple places, or
conflicting with a large refactor on the base branch), present the conflict
summary to the user first and confirm the resolution strategy before delegating.

### CI failures → delegate to `debugger`

For each failing check with a clear error, spawn a **`debugger`** agent with:

- The PR number and branch name
- The check name and its failure output (paste the relevant log lines)
- The list of files changed in the PR (to scope the investigation)
- Instruction to fix the issue and push to the PR branch

For CI failures that need discussion (ambiguous cause, flaky tests, environment
issues), present them to the user the same way you would a review comment that
needs discussion — show the failure, explain what you see, propose an approach,
and wait for confirmation before delegating.

### Auto-fixable review items → delegate to `code-writer`

Spawn a **`code-writer`** agent with a precise brief that includes:

- The PR number and branch name
- Each file to change and exactly what to change (quote the review comment)
- The commit message to use:

  ```
  review: address PR #N feedback

  - fix typo in UserService.validate() comment
  - add null check in parseConfig() per review
  - update README with new env var
  ```

- Instruction to push to the PR branch when done

Report the commit hash back to the user once the agent completes.

If the fix is a pure bug (incorrect behavior, not just a code style issue),
spawn a **`debugger`** agent instead.

### Items needing discussion → present to user, then delegate

For each one, present it clearly:

- Quote the review comment verbatim
- State the file and line it refers to
- Describe the trade-offs or ambiguity
- Propose a specific approach

Wait for the user to confirm or redirect. Once confirmed, delegate to `code-writer`
(or `debugger`) exactly as above with the agreed approach.

### Out-of-scope items → use `gh-create-issue` skill

For each out-of-scope item:

1. Run a duplicate check directly to avoid creating duplicate issues:
   ```bash
   gh issue list --repo <owner>/<repo> --search '<keywords>' --state all --json number,title,state
   ```
2. If a related issue exists, note it and move on.
3. If none exists, invoke the **`gh-create-issue` skill** to create a well-formed
   issue. Include in the brief: what the reviewer said, why it's deferred from this
   PR, and the relevant PR number for context.
4. Tell the user which issue was created or already existed.

---

## PR body / comment body patterns

When pushing a fresh PR body, comment, or release notes via `gh pr create`,
`gh pr edit`, `gh issue create`, or `gh release create`, use a single-quoted
HEREDOC fed through command substitution:

```bash
gh pr create --title "fix: handle stale rollup" --body "$(cat <<'EOF'
## Summary

- One bullet per discrete change
- Use the body for detail, the title stays short (<70 chars)

## Test plan

- [ ] Lint clean
- [ ] Tests green

Closes #123

🤖 *Generated by Claude Code*
EOF
)"
```

Rules:

- **Closing `EOF` and `)"` MUST be at column zero.** Indented closing markers
  are a shell parse error, not a content issue — there is no error message
  pointing at the indent; the body just turns into garbled output.
- **Use single-quoted `<<'EOF'`** (not `<<EOF`). Single quotes suppress
  `$variable` and `$(cmd)` expansion inside the body. With unquoted `EOF`,
  any `$(date)` / `$VAR` mention in the prose runs as a command — silently
  corrupting the body or, worse, executing arbitrary substitutions.
- **For payloads above ~30 KB**, do NOT use the HEREDOC pattern. Write the
  body to `.tmp/<scratch>.md` and pass `--body-file <path>` instead — or for
  raw `gh api` calls, `--input <payload.json>`. The HEREDOC route is for
  human-readable PR/issue bodies, not arbitrary large payloads.
- **PowerShell users**: this is a bash-only pattern. The PowerShell equivalent
  is `gh ... --body-file <path>` after writing the body via `Out-File`. Avoid
  `Set-Content -NoNewline` — it can corrupt multi-line bodies on Windows.

---

## Step 5 — Summary

After all PRs are processed, give the user a concise recap:

- Which PRs were checked
- Whether merge conflicts were resolved (and how)
- What CI failures were fixed (check name + what was wrong)
- What review comments were auto-fixed (commit hash and bullet list of changes),
  reported under **two separate headings** so the user sees what was triaged in
  each category:
  - **Review-body findings** — items drawn from formal review summaries
  - **Inline findings** — items drawn from per-line review-thread comments
    (also note the count, so an inline-only review is visibly accounted for)
- What was discussed and how it was resolved
- What issues were created for deferred items
- **Findings suppressed by the Step 3 nit/cosmetic filter** — one line per suppression with the bot, the file/line ref, and a 6-8 word summary so the user can spot any that should have been kept and ask for them to be re-included
- Anything still pending user input

Keep the summary scannable — the user should be able to confirm everything was
handled at a glance.

---

## Long-Form Artifact Discipline

When the Step 3 triage produces more than ~5 items, or when fetched review-bot output is substantial (CodeRabbit / Copilot reviews routinely exceed 40 lines), save the triage matrix and the raw review-bot output to `<repo>/.tmp/<YYYY-MM-DD>-pr<N>-triage.md` before delegating in Step 4. The chat reply lists item counts by category (auto-fixable / discussion / out-of-scope), names the top blocker, and points to the file. For the Step 5 final summary, the same discipline applies — save the recap if it would exceed 40 lines.

Write long artifacts to `.tmp/<name>.md` under the repo root rather than inlining them in chat. Pass them to `gh` commands via `--body-file <path>`. This keeps the conversation scannable and sidesteps shell-escaping and encoding problems with large strings.
