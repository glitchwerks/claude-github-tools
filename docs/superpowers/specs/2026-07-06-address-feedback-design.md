# Design: `address-feedback` skill

**Status:** Implemented — hardened per CodeRabbit PR #28 review
**Issue:** [glitchwerks/claude-github-tools#27](https://github.com/glitchwerks/claude-github-tools/issues/27)
**Date:** 2026-07-06
**Author:** Claude Code (on behalf of @cbeaulieu-gt)

---

## 1. Problem

Addressing PR review feedback from CodeRabbit (and other review bots) is an
iterative, multi-round chore: read the review, fix what carries signal, push,
then **wait minutes** for the bot to re-review the new HEAD, and repeat until
nothing non-trivial remains. Today this is fully manual — the existing
`gh-pr-review-address` skill handles exactly **one** pass and then stops.

We want a **goal-like loop**: point it at a PR and walk away; it drives the PR
to green-and-merged by cycling address → push → wait-for-re-review → repeat
until quiescent, then auto-merges.

### Core design tension

CodeRabbit re-reviews **asynchronously** — its next round of comments lands
minutes after a push, not immediately. Any loop that evaluates "is there more
feedback?" the instant a turn ends will see "nothing new yet" and **falsely
declare done right after a push**. The loop must therefore *wait for external
state the harness cannot notify it about*, and must only declare done once the
bot has demonstrably reviewed the current HEAD.

---

## 2. Chosen approach (Approach A)

Run the skill under **`/loop` dynamic mode** (`/loop /address-feedback [target]`,
no interval ⇒ model self-paces). Each loop firing executes **one tick** of the
skill body; cross-tick continuation and the adaptive wait are supplied by
**`ScheduleWakeup`**:

- After an ACT pass (fix + push) or while awaiting re-review, the tick calls
  `ScheduleWakeup` with a ~270s delay (inside the 5-minute prompt-cache window,
  matched to CodeRabbit's typical re-review latency).
- On quiescence, the tick calls `ScheduleWakeup({ stop: true })` to end the loop.

### Rejected alternatives

| Approach | Why rejected |
| --- | --- |
| **`/goal <condition>`** (built-in) | The `/goal` evaluator judges the transcript only — it never calls tools and fires the next turn *immediately* after the previous one finishes. It cannot wait for CodeRabbit's async round, so it would see "no new feedback" and declare the goal met right after a push. Wrong tool for external-async-state. |
| **`/loop <fixed-interval>`** | A wall-clock interval fights CodeRabbit's variable latency — polls too early (wasted ticks) or too late (idle time), and forces the user to pick and type an interval. |
| **Stop hook** | A `Stop` hook fires synchronously at turn end and cannot block for the minutes needed to wait on the bot; polling GitHub from inside a hook that stalls the session is a non-starter. |

---

## 3. Control flow

Entry: `/loop /address-feedback [<PR# | branch>]`

### Run-state persistence (read first, every tick)

`/loop` dynamic mode hands the skill no tick counter, and the PR's commit
history is not a reliable run boundary — every ACT round pushes the same
commit message (`review: address PR #N feedback`), so the loop cannot count
commits to distinguish this run's pushes from a previous run's, and commit
history carries no fingerprint of prior findings. Inferring loop-guard state
from the live PR alone would let the tick/round caps and the no-progress guard
silently reset across wakeups.

The tick therefore persists `tick_count`, `act_round` count, a fingerprint of
the prior round's findings, a stable `run_id`, and the list of parked-issue
numbers to `<repo>/.tmp/address-feedback-pr<N>.json`, and loads that file at
the top of every tick before evaluating anything else. If the state file is
unreadable, malformed, or inconsistent with the live PR (e.g. a mismatched
target), the tick **fails closed**: stop and report rather than proceed with
reset guards.

Each **tick** (one execution of the skill body):

1. **Resolve target PR (fail closed on ambiguity).** Resolve the repository
   from the target first, then require **exactly one** open PR:
   - Explicit `#N` / `N` / URL → resolve `owner/repo`, then confirm the PR
     exists and is open.
   - Branch name (or current-branch default) → list open PRs for that head
     branch (`gh pr list --head <branch> --state open --json number`) rather
     than `gh pr view <branch>`, which silently picks one PR when a branch
     maps to more than one. Zero or more than one match → refuse the tick and
     report the ambiguity; never guess which PR was meant.
   - **First tick only:** capture the PR head repository and verify the user
     **owns** it. If not owned, refuse the entire run (never loop-merge a repo
     the user does not own — see §6).
2. **Read state** (parallel):
   - current HEAD sha of the PR branch
   - **each configured review bot's** latest review and the commit sha it
     reviewed (one entry per bot, not a single "latest review")
   - CI check results (`gh pr checks <N> --json name,state,conclusion`)
   - `mergeable` / `mergeStateStatus`
3. **Branch on state** (see §4 quiescence table):
   - **(a) Any configured bot behind HEAD** — that bot's latest review commit
     ≠ current HEAD sha, or it has no review yet → **WAIT**:
     `ScheduleWakeup(~270s)`, reason `"awaiting <bot(s)> re-review of <sha>"`.
     A single bot being current does not clear this branch while another
     configured bot is still behind.
   - **(b) Non-trivial in-scope items exist** → **ACT** (one pass, §5): delegate
     fixes, then, **immediately before pushing, re-read the branch HEAD and
     compare it to the sha captured in step 2.** If it changed, someone else
     pushed during this tick — discard the plan and restart the tick rather
     than committing against unknown branch state. Only push when the sha
     still matches, then `ScheduleWakeup(~270s)`.
   - **(c) Quiescent** → **EXIT** (§6): merge-if-green, then
     `ScheduleWakeup({ stop: true })`.
4. **Loop-cap guard** (§6): hard-stop after the tick/round caps, enforced
   against the **persisted** run-state counters, not inferred commit history.

The skill body is *one tick*. `/loop` dynamic mode + `ScheduleWakeup` provide
the loop and the wait.

---

## 4. Quiescence definition ("done")

Done is **not** "no comments in this fetch." All of the following must hold:

| Check | Rationale |
| --- | --- |
| **Every** configured review bot's latest review commit `==` current HEAD sha | Proves the full reviewer set — not just one bot — has actually reviewed the last push. A singular "latest CodeRabbit review" check would exit prematurely when CodeRabbit is current but another configured bot has not yet reviewed HEAD. |
| Zero **non-suppressed, in-scope** items across the **aggregate** of all bots' reviews | Trivial nits (per `gh-pr-review-address` §3 suppression filter) do not count — suppressing them at intake is what breaks the infinite review→fix→review nit-loop. |
| No judgment-call item is left unhandled *in this tick* | Judgment-call items are parked as issues, not blockers (§5), so they never keep the loop alive — but only once parking verifiably resolves to an issue number (see §5). |

**Fresh PR with no review yet from a configured bot, or any bot behind HEAD**
⇒ treat as "bot behind HEAD" → WAIT, never declare done.

---

## 5. Autonomy & composition with `gh-pr-review-address`

**Compose, do not duplicate.** The ACT pass reuses `gh-pr-review-address`'s
gather (Step 2), suppression-filter, and triage (Step 3) logic rather than
re-implementing the ruleset. `address-feedback` layers a loop wrapper plus an
**autonomy override** that removes the interactive branch so the loop never
blocks:

**Why this composition is safe.** `gh-pr-review-address` and `address-feedback`
ship together in this one plugin — the sibling is **trusted first-party
content**, not third-party or user-supplied. Reading and driving its
documented Steps 2 / 2.5 / 3 / 4 carries no more trust risk than this skill's
own body. This does **not** generalize to reading arbitrary or third-party
skills' `SKILL.md` files at runtime: doing so would expose the loop to
untrusted instructions and is a prompt-injection surface. The composition is
scoped to this plugin's own co-bundled sibling and nothing else.

| `gh-pr-review-address` default | `address-feedback` override |
| --- | --- |
| Auto-fix clear items → delegate `code-writer` / `debugger`, push | same |
| Judgment-call items → **stop and ask the user** | **park**: dup-check then log a GitHub issue (its Step 4 out-of-scope procedure), skip the item, continue the loop |
| Out-of-scope items → log a GitHub issue | same |
| Nit / cosmetic items → suppress (still counted) | same |

Each ACT push uses a single `review: address PR #N feedback` commit (the
sibling's commit-body pattern). Parked-issue references accumulate for the
final report.

**Parking must fail closed.** A judgment-call item counts as "handled" only
when parking **verifiably resolves to a GitHub issue number** — either a
confirmed existing duplicate or a newly created issue whose number the create
call returned. If duplicate detection or issue creation fails, races, or lacks
permission, the item is **not** handled: the loop must not reach quiescence,
must not merge, and the tick stops and reports the un-parkable item for a
human. This closes the gap where a silently-failed park would let the loop
treat an unaddressed judgment call as resolved.

**Net effect:** the only behavioral change from the sibling skill is that the
"discuss with user" path becomes "park as issue" — everything else (what counts
as signal, what gets suppressed, how fixes are delegated) is inherited
unchanged.

---

## 6. Safety guards

### Merge gates (all required before auto-merge)

Sourced from the user's `CLAUDE.md § Pull Requests`. Auto-merge yields to every
one of these:

- Live PR re-fetch immediately before merge — still open, not already merged.
- **Ownership re-validated at the merge boundary**, not just tick 1. The
  tick-1 ownership check (comparing the head-repo owner against the
  authenticated user, `gh api user --jq .login`) is authorization *at that
  moment only* — it is stale by the time the loop reaches EXIT rounds later.
  The pre-merge gate re-runs the same authenticated ownership + merge-permission
  check against the *current* head repo owner and fails closed if it no longer
  matches (a repo can be transferred or access revoked mid-loop).
- **CI `success` on the actual merge result, not just the PR head.** `gh pr
  checks <N>` is tied to the head commit and can miss failures that only
  surface on the merged result. The gate fetches the test-merge / merge-queue
  sha (`potentialMergeCommit`) and confirms checks are green **on that
  commit**, not the head commit, before merging.
- No `CHANGES_REQUESTED` review; no unresolved human review threads.
- `mergeStateStatus` ∈ {`CLEAN`, `HAS_HOOKS`, `UNSTABLE`} — never `DIRTY`,
  `BLOCKED`, or `BEHIND`.
- No `DO NOT MERGE` banner in the PR body.
- **Any gate fails ⇒ downgrade the exit to "stop & report"**: surface which
  gate failed and leave the PR open. Never merge on a failed gate.

### Loop guards (runaway protection)

- **Run-state persistence backs every guard below.** `tick_count`, `act_round`
  count, the prior round's findings fingerprint, a stable `run_id`, and parked
  issue numbers are persisted to `<repo>/.tmp/address-feedback-pr<N>.json` and
  loaded at the top of every tick (§3) — the caps and the no-progress guard are
  evaluated against this persisted state, never against inferred commit
  history. If the state file is unreadable, malformed, or inconsistent with
  the live PR, the tick fails closed: stop and report.
- **Hard cap:** max **8 ticks** or max **5 ACT rounds**, whichever first →
  force stop + report.
- **No-progress guard:** if two consecutive ACT rounds produce the same
  **persisted findings fingerprint** (a fix that did not satisfy the bots),
  stop and flag `"stuck on: <items>"` for a human.
- Every `ScheduleWakeup` carries a **long fallback delay** so a hung or silent
  review bot can never wedge the loop indefinitely.

---

## 7. Packaging

- **Location:** `skills/address-feedback/SKILL.md` in this repo
  (`claude-github-tools` plugin), sibling to `gh-pr-review-address`.
- **Entry contract:** invoked under `/loop /address-feedback [<PR#|branch>]`.
  The SKILL.md documents that dynamic-`/loop` is required — it supplies the
  cross-tick engine. A bare `/address-feedback` invocation runs a **single
  tick** and then tells the user to wrap it in `/loop` for the full goal.
- **Frontmatter:**
  - `name: address-feedback`
  - `description:` with trigger phrases — "address feedback until done", "loop
    on PR feedback", "keep addressing my PR", "auto-merge when clean", plus the
    single-PR framing.
  - `context-switch: true` (matches the sibling skill).
- **Harness routing note:** a `skills/**/SKILL.md` edit is a harness carve-out —
  the router self-handles authoring via the `agent-authoring` skill; it is not
  delegated to `code-writer` / `doc-writer`.

---

## 8. Defaults (confirmed)

- **Reviewer sources:** the full sibling set — all review bots + CI failures —
  not CodeRabbit-only. Strictly more useful, same machinery.
- **Wait delay:** 270s default (inside the prompt-cache 5-minute window),
  exposed as a tunable constant in the SKILL.md.

---

## 9. Open questions

None outstanding. Both §8 defaults were confirmed during brainstorming.

---

## 10. Next steps

1. Spec review by the user (this document).
2. Implementation plan via the `writing-plans` skill.
3. Build `skills/address-feedback/SKILL.md`.

---

## 11. Post-review hardening (CodeRabbit, PR #28)

A CodeRabbit review on [PR #28](https://github.com/glitchwerks/claude-github-tools/pull/28)
raised the following design-hardening points, all folded into
`skills/address-feedback/SKILL.md` before the v0.5.0 release:

- **Run-state persistence across wakeups** — `tick_count`, `act_round` count,
  the prior-findings fingerprint, `run_id`, and parked-issue numbers persist to
  `<repo>/.tmp/address-feedback-pr<N>.json`, loaded at the top of every tick;
  fail closed if the state file is unreadable, malformed, or inconsistent.
- **Ambiguous-PR fail-closed selection** — resolve the repo from the target
  first, then require exactly one open PR (`gh pr list --head <branch>
  --state open`, not `gh pr view <branch>`); zero or more than one match
  refuses the tick.
- **Concurrent-update HEAD recheck before push** — re-read HEAD immediately
  before pushing an ACT round and compare to the sha captured at read time;
  discard and restart the tick on mismatch.
- **Multi-bot freshness in quiescence** — WAIT/quiescence requires *every*
  configured review bot's latest review commit to equal current HEAD, and
  aggregates actionable findings across all bots.
- **Fail-closed judgment-call parking** — a parked item counts as "handled"
  only when it verifiably resolves to a GitHub issue number; a failed,
  raced, or unauthorized park stops the loop rather than reaching quiescence.
- **Ownership revalidation at merge boundary** — the tick-1 ownership check is
  stale authorization by EXIT; the pre-merge gate re-runs the authenticated
  ownership + merge-permission check against the current head repo owner.
- **Test-merge SHA for CI gate** — CI success is verified on the test-merge /
  merge-queue sha (`potentialMergeCommit`), not just the PR head, since `gh pr
  checks` can miss failures on the merged result.
- **First-party sibling composition scoping** — composing `gh-pr-review-address`
  is safe because it is a trusted first-party sibling co-bundled in this
  plugin, not third-party content; this must not generalize to reading
  arbitrary third-party skills' `SKILL.md` files.
