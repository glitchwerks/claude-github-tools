---
name: address-feedback
description: >
  Unattended goal-loop that drives a single PR to green-and-merged by
  repeatedly addressing reviewer feedback (CodeRabbit and other review bots)
  and failing CI checks, waiting for asynchronous re-review between rounds,
  then auto-merging once the PR is quiescent. Composes the one-pass
  gh-pr-review-address skill and wraps it in a self-pacing loop.

  Invoked as `/loop /address-feedback [<PR#|branch>]` — dynamic /loop mode
  supplies the cross-tick engine; each firing runs ONE tick. A bare
  /address-feedback runs a single tick and then tells you to wrap it in /loop.

  Trigger this skill whenever the user says "address feedback until done",
  "loop on PR feedback", "keep addressing my PR", "auto-merge when clean",
  "drive this PR to merge", "handle PR feedback until it's mergeable", or any
  variation of wanting a PR taken all the way to merged without babysitting
  each review round.
---

# Address Feedback — unattended PR goal-loop

## Your role in this skill

You are the **loop driver**. Each invocation of this skill body is **one tick**.
You gather PR state, decide WAIT / ACT / EXIT, take at most one action, then
schedule the next tick (or stop). You never write code or run commits yourself —
delegate exactly as `gh-pr-review-address` does.

**This skill composes, it does not duplicate.** The gather (Step 2), resolution
axes (Step 2.5), suppression filter and triage (Step 3), and delegation shapes
(Step 4) all come from `gh-pr-review-address` **unchanged**. This skill adds
three things and nothing else:

1. A **loop wrapper** (`/loop` dynamic mode + `ScheduleWakeup`) so review rounds
   run unattended.
2. An **autonomy override**: the sibling's "stop and ask the user" branch for
   judgment-call items becomes "park as a GitHub issue and continue".
3. A **quiescence + auto-merge** exit that respects every `CLAUDE.md § Pull
   Requests` merge gate.

If you have not already, read `skills/gh-pr-review-address/SKILL.md` — its Steps
2, 2.5, 3, and 4 are the machinery this skill drives.

---

## Entry contract

```
/loop /address-feedback [<PR# | branch>]
```

- **`/loop` dynamic mode is required for the full goal.** No interval — the model
  self-paces. Each firing executes one tick of this body; `ScheduleWakeup`
  supplies the cross-tick wait matched to the review bot's async latency.
- **A bare `/address-feedback`** (no `/loop`) runs exactly **one tick**, reports
  its WAIT / ACT / EXIT decision, and then tells the user to wrap it in
  `/loop /address-feedback` for the unattended goal. Do not fake the loop from a
  single tick.
- **Target argument** is optional: `#N`, `N`, a PR URL, or a branch name. If
  omitted, resolve the current branch's open PR (sibling Step 1).

### Wait delay (tunable)

```
WAIT_DELAY_SECONDS = 270   # inside the 5-minute prompt-cache window; matched to
                           # CodeRabbit's typical re-review latency
FALLBACK_DELAY_SECONDS = 1800  # long heartbeat so a silent bot can never wedge the loop
```

`270s` keeps the prompt cache warm between ticks. Every `ScheduleWakeup` also
carries the longer fallback as a safety heartbeat.

---

## One tick

### 1. Resolve the target PR

Resolve per `gh-pr-review-address` Step 1 (argument → current-branch PR). If none
exists, report and `ScheduleWakeup({ stop: true })`.

**First tick only — ownership gate.** Capture the PR head repository owner and
verify the user **owns** it:

```bash
gh pr view <N> --repo <owner>/<repo> --json headRepositoryOwner --jq .headRepositoryOwner.login
```

If the head repo is **not** owned by the user, **refuse the entire run** — do not
tick, do not merge. Surface: "address-feedback only drives PRs on repos you own;
`<owner>/<repo>` is not yours." Then `stop`. This gate exists because the loop can
auto-merge; never loop-merge a repo the user does not own (mirrors
`CLAUDE.md § Confirmation gates`).

### 2. Read state (parallel)

Fetch, in parallel:

- Current HEAD sha of the PR branch (`gh pr view <N> --json headRefOid --jq .headRefOid`)
- Latest review from each review bot **and the commit sha it reviewed**
  (`gh api repos/<owner>/<repo>/pulls/<N>/reviews --jq '.[] | {user:.user.login, state:.state, commit:.commit_id, at:.submitted_at}'`)
- CI check results (`gh pr checks <N> --json name,state,conclusion`)
- `mergeable` / `mergeStateStatus` (`gh pr view <N> --json mergeable,mergeStateStatus`)

This is the sibling's Step 2 + Step 2.5 gather, scoped to what the branch
decision needs. For the ACT triage you run the full Step 2 / 2.5 / 3.

### 3. Branch on state → WAIT / ACT / EXIT

Evaluate in this order (see § Quiescence for the "done" definition):

**(a) Review bot is behind HEAD → WAIT.**
Latest review-bot review commit ≠ current HEAD sha, **or** no bot review exists
yet (fresh PR / just pushed). The bot has not yet reviewed the current code.

```
ScheduleWakeup({
  delaySeconds: WAIT_DELAY_SECONDS,
  prompt: "/loop /address-feedback <target>",
  reason: "awaiting review re-review of <HEAD_SHA>",
})
```

Never declare done here. "No new comments this fetch" right after a push is the
false-positive this skill exists to avoid.

**(b) Non-trivial, in-scope items exist → ACT (one pass).**
Run the sibling's full Step 2 → 2.5 → 3 triage against the current HEAD, then
Step 4 delegation for **this one round only**:

- Merge conflicts → `debugger` (sibling Step 4).
- CI failures with clear errors → `debugger`.
- Auto-fixable review items → `code-writer` (or `debugger` for pure bugs), single
  commit `review: address PR #N feedback`.
- **Judgment-call items → PARK, do not ask** (see § Autonomy override).
- Out-of-scope items → park as issue (sibling Step 4 out-of-scope procedure).

After the fixes land and are pushed, run the sibling's **Step 4.5** thread
resolution, then schedule the next tick:

```
ScheduleWakeup({
  delaySeconds: WAIT_DELAY_SECONDS,
  prompt: "/loop /address-feedback <target>",
  reason: "pushed round <k>; awaiting re-review",
})
```

**(c) Quiescent → EXIT (merge-if-green).**
All quiescence conditions hold (§ Quiescence). Run the merge-gate checklist
(§ Safety guards). If every gate passes, merge; then
`ScheduleWakeup({ stop: true })`. If any gate fails, **downgrade to stop-and-
report** — leave the PR open, name the failed gate, and `stop`.

### 4. Loop-cap guard

Enforce the runaway caps (§ Safety guards) on every tick. On any cap breach,
force-stop with a report and `ScheduleWakeup({ stop: true })`.

---

## Quiescence ("done")

Done is **not** "no comments in this fetch." **All** of the following must hold:

| Check | Rationale |
| --- | --- |
| Latest review-bot review commit `==` current HEAD sha | Proves the bot actually reviewed the last push — prevents stopping mid-flight right after pushing. |
| Zero **non-suppressed, in-scope** items in that review | Trivial nits (sibling Step 3 suppression filter) do not count — suppressing them at intake is what breaks the infinite review→fix→review nit-loop. |
| No judgment-call item left unhandled **this tick** | Judgment-call items are parked as issues (§ Autonomy override), so they never keep the loop alive. |

A **fresh PR with no bot review yet** is treated as "bot behind HEAD" → WAIT.
Never declare done on an unreviewed HEAD.

---

## Autonomy override

The **only** behavioral change from `gh-pr-review-address`:

| `gh-pr-review-address` default | `address-feedback` override |
| --- | --- |
| Auto-fix clear items → delegate `code-writer` / `debugger`, push | same |
| Judgment-call items → **stop and ask the user** | **park**: dup-check, then log a GitHub issue (sibling Step 4 out-of-scope procedure), skip the item, continue the loop |
| Out-of-scope items → log a GitHub issue | same |
| Nit / cosmetic items → suppress (still counted) | same |

Everything else — what counts as signal, what is suppressed, how fixes are
delegated, how threads are resolved — is inherited **unchanged**. Parked-issue
references accumulate for the final report. A parked item **never** blocks
quiescence: it left the PR as an issue, so the loop may exit clean with parked
issues outstanding.

---

## Safety guards

### Merge gates (all required before auto-merge)

Sourced from `CLAUDE.md § Pull Requests`. Auto-merge yields to **every** one:

- **Live re-fetch immediately before merge** — PR still open, not already merged.
- **CI `success` on the actual merge commit** (`gh pr checks <N>`).
- **No `CHANGES_REQUESTED` review** (sibling Axis C) and no unresolved human review
  threads.
- **`mergeStateStatus` ∈ {`CLEAN`, `HAS_HOOKS`, `UNSTABLE`}** — never `DIRTY`,
  `BLOCKED`, or `BEHIND`.
- **PR head repo owned by the user** (checked on tick 1).
- **No `DO NOT MERGE` banner** in the PR body.

**Any gate fails ⇒ downgrade the exit to stop-and-report.** Surface which gate
failed, leave the PR open, and `stop`. Never merge on a failed gate — even to
"finish the loop".

### Loop guards (runaway protection)

- **Hard cap:** max **8 ticks** or max **5 ACT rounds**, whichever comes first →
  force-stop + report.
- **No-progress guard:** if two consecutive ACT rounds produce **identical** bot
  findings (a fix that did not satisfy the bot), stop and flag
  `"stuck on: <items>"` for a human. Do not keep pushing the same non-fix.
- **Fallback heartbeat:** every `ScheduleWakeup` carries
  `FALLBACK_DELAY_SECONDS` as a long backstop so a hung or silent review bot can
  never wedge the loop indefinitely.

### Tick-count bookkeeping

`/loop` dynamic mode does not hand you a tick counter. Track ticks and ACT rounds
by reading the PR's own history each tick — count `review: address PR #N feedback`
commits authored during this run for ACT rounds, and treat each firing as a tick.
When either cap is reached, force-stop.

---

## Final report (on EXIT or forced stop)

One scannable recap:

- Terminal state: **merged** / **stopped (gate: `<name>`)** / **stopped (cap)** /
  **stuck: `<items>`** / **refused (not owner)**.
- ACT rounds run and the commit sha of each `review: address PR #N feedback` push.
- Resolution-state recap (sibling Step 5 line): `N threads: X resolved, Y
  candidate-addressed, Z open`.
- Issues parked (judgment-call + out-of-scope), with numbers.
- Suppressed nits (sibling Step 5 line) so the user can override.
- If merged: the merge commit sha. If stopped: exactly which gate or cap fired and
  what a human needs to do next.

---

## Long-Form Artifact Discipline

Per-tick triage output follows `gh-pr-review-address`'s discipline: when a round's
triage exceeds ~5 items or bot output exceeds ~40 lines, write the matrix to
`<repo>/.tmp/<YYYY-MM-DD>-pr<N>-round<k>-triage.md` and keep the chat reply to
counts + the file path. The final report, if it would exceed 40 lines, is written
to `<repo>/.tmp/<YYYY-MM-DD>-pr<N>-address-feedback-report.md` and summarized in
chat.
