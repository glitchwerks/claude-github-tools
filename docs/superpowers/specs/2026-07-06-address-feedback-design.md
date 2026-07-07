# Design: `address-feedback` skill

**Status:** Approved design (brainstorming complete) — pending spec review
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

Each **tick** (one execution of the skill body):

1. **Resolve target PR.** Use the argument (`#N`, `N`, URL, or branch name);
   otherwise the current branch's open PR. If none, report and `stop`.
   - **First tick only:** capture the PR head repository and verify the user
     **owns** it. If not owned, refuse the entire run (never loop-merge a repo
     the user does not own — see §6).
2. **Read state** (parallel):
   - current HEAD sha of the PR branch
   - latest CodeRabbit review and the commit sha it reviewed
   - CI check results (`gh pr checks <N> --json name,state,conclusion`)
   - `mergeable` / `mergeStateStatus`
3. **Branch on state** (see §4 quiescence table):
   - **(a) CR behind HEAD** — latest CR review commit ≠ current HEAD sha, or no
     CR review exists yet → **WAIT**: `ScheduleWakeup(~270s)`,
     reason `"awaiting CR re-review of <sha>"`.
   - **(b) Non-trivial in-scope items exist** → **ACT** (one pass, §5): delegate
     fixes, push, then `ScheduleWakeup(~270s)`.
   - **(c) Quiescent** → **EXIT** (§6): merge-if-green, then
     `ScheduleWakeup({ stop: true })`.
4. **Loop-cap guard** (§6): hard-stop after the tick/round caps.

The skill body is *one tick*. `/loop` dynamic mode + `ScheduleWakeup` provide
the loop and the wait.

---

## 4. Quiescence definition ("done")

Done is **not** "no comments in this fetch." All of the following must hold:

| Check | Rationale |
| --- | --- |
| Latest CR review's commit `==` current HEAD sha | Proves CodeRabbit has actually reviewed the last push — prevents stopping mid-flight right after pushing. |
| Zero **non-suppressed, in-scope** items in that review | Trivial nits (per `gh-pr-review-address` §3 suppression filter) do not count — suppressing them at intake is what breaks the infinite review→fix→review nit-loop. |
| No judgment-call item is left unhandled *in this tick* | Judgment-call items are parked as issues, not blockers (§5), so they never keep the loop alive. |

**Fresh PR with no CR review yet** ⇒ treat as "CR behind HEAD" → WAIT, never
declare done.

---

## 5. Autonomy & composition with `gh-pr-review-address`

**Compose, do not duplicate.** The ACT pass reuses `gh-pr-review-address`'s
gather (Step 2), suppression-filter, and triage (Step 3) logic rather than
re-implementing the ruleset. `address-feedback` layers a loop wrapper plus an
**autonomy override** that removes the interactive branch so the loop never
blocks:

| `gh-pr-review-address` default | `address-feedback` override |
| --- | --- |
| Auto-fix clear items → delegate `code-writer` / `debugger`, push | same |
| Judgment-call items → **stop and ask the user** | **park**: dup-check then log a GitHub issue (its Step 4 out-of-scope procedure), skip the item, continue the loop |
| Out-of-scope items → log a GitHub issue | same |
| Nit / cosmetic items → suppress (still counted) | same |

Each ACT push uses a single `review: address PR #N feedback` commit (the
sibling's commit-body pattern). Parked-issue references accumulate for the
final report.

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
- CI `success` on the **actual merge commit** (`gh pr checks <N>`).
- No `CHANGES_REQUESTED` review; no unresolved human review threads.
- `mergeStateStatus` ∈ {`CLEAN`, `HAS_HOOKS`, `UNSTABLE`} — never `DIRTY`,
  `BLOCKED`, or `BEHIND`.
- PR head repository is **owned by the user** (checked on tick 1) — otherwise
  the run is refused before it starts.
- No `DO NOT MERGE` banner in the PR body.
- **Any gate fails ⇒ downgrade the exit to "stop & report"**: surface which
  gate failed and leave the PR open. Never merge on a failed gate.

### Loop guards (runaway protection)

- **Hard cap:** max **8 ticks** or max **5 ACT rounds**, whichever first →
  force stop + report.
- **No-progress guard:** if two consecutive ACT rounds produce identical CR
  findings (a fix that did not satisfy the bot), stop and flag
  `"stuck on: <items>"` for a human.
- Every `ScheduleWakeup` carries a **long fallback delay** so a hung or silent
  CodeRabbit can never wedge the loop indefinitely.

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
