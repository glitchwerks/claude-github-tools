---
name: gh-create-issue
description: >
  Creates well-formed GitHub Issues through a structured discovery process.
  Trigger this skill whenever the user types /gh-create-issue, says "create a
  GitHub issue for...", "log this as an issue", "make a ticket for...", "add
  this to the backlog", or any similar request to track or file work in GitHub.
  This skill investigates the codebase and checks for overlapping issues/PRs
  before writing anything — always use it proactively so issues are well-scoped
  before they're created.
---

# GitHub Issue Creator

Your job is to create a high-quality, well-scoped GitHub Issue. The goal isn't
just to record a request — it's to understand what's really being asked, surface
any conflicts or concerns, and produce an issue that's ready to act on. Work
should never start unless the user explicitly asks.

## Workflow

1. **Acquire** a base description
2. **Investigate** the codebase, existing issues/PRs, and available labels
3. **Clarify** — ask questions and challenge assumptions
4. **Draft** — present the full issue for approval
5. **Create** — post to GitHub once approved
6. **Offer** — ask if work should start (never assume)

---

## Phase 1: Acquire Description

If the user invoked the skill with no description, ask:

> "What's the issue you'd like to create? A sentence or two is enough to get started."

If they provided a description, proceed directly to investigation.

---

## Phase 2: Investigate

Run all of these in parallel — don't wait for one to complete before starting
the next.

### Codebase

- Read `README.md` — understand purpose, stack, and conventions
- Read `CLAUDE.md` (root, plus any subdirectory relevant to the topic) — pick
  up project standards and constraints
- Identify source files and directories relevant to the issue topic and read the
  most pertinent ones at a medium depth
- Scan for documentation files (`docs/`, `DESIGN.md`, `ADR.md`, etc.) that
  touch the issue area — note anything that may need updating if this work ships

### Existing Issues and PRs

Run these directly in parallel using `gh`:

```bash
# Search for overlap
gh issue list --repo <owner>/<repo> --search '<keywords from description>' --json number,title,state

# List all open issues
gh issue list --repo <owner>/<repo> --state open --json number,title,labels

# List all open PRs
gh pr list --repo <owner>/<repo> --state open --json number,title,headRefName

# Fetch available labels
gh label list --repo <owner>/<repo> --json name,description
```

If you find significant overlap, flag it before proceeding:

> "I found an existing issue/PR that covers similar ground: #N — [title].
> Would you like to proceed with a new issue, link to that one, or update it
> instead?"

### Labels and Milestones

The queries above fetch labels. For milestones, run:

```bash
gh api repos/<owner>/<repo>/milestones --jq '.[] | {number, title, state}'
```

---

## Phase 3: Clarify

Based on your investigation, ask the questions you need to understand the issue
well. Group related questions together — don't interrogate one point at a time.
Be direct if something seems off.

Think about:

- Is the problem statement clear? Is the proposed approach the right one, or is
  there a simpler path?
- What's the scope? What's explicitly out of scope?
- Who is affected — user-facing, developer-facing, internal?
- Does this touch files that have broader project implications (config,
  README, shared modules)?
- Are there dependencies on other issues or PRs?
- Does the description assume something that may not be true?

After the user responds, iterate if you need to — but work toward alignment,
not an interrogation. Once scope is clear, move to drafting.

---

## Cross-Repo Contract Issues

When the user's description reveals a cross-repo or cross-team contract — "we need repo A to provide X for repo B to consume" — apply consumer-perspective framing rather than implementation prescription. The issue owner may be a separate team or a separate agent in the producer repo; overspecifying implementation removes their latitude to design a better solution.

**Detection:** The request sounds like "document what we need from [other repo]," "lay out the contract between X and Y," or "file an issue against [producer] describing what [consumer] requires."

**How to frame the issue:**

1. **Open with an explicit framing statement.** The first sentence should name this as a consumer-perspective requirements spec, not an implementation plan — so the producer agent does not read past it and start designing to the wrong spec.
2. **Number the requirements (R1, R2, ...).** Each requirement is a _what_, never a _how_. "The consumer needs to be able to read one place to know which paths are imported" — not "create `docs/CI_CONSUMPTION.md` with section 2 listing the paths."
3. **Include a "non-requirements" section.** Explicitly list what the consumer does not care about: file paths, doc format, internal tooling, branch strategy. This liberates the producer agent from second-guessing implementation choices.
4. **Write acceptance criteria as observable behaviors, not artifacts.** "A maintainer can read a single doc and answer X" — not "the file `docs/X.md` exists." The producer can satisfy the behavioral requirement however they choose.
5. **Coordinate the downstream sequence.** Note what the consumer will do once the contract is satisfied (verify, pin, tag, etc.) so the producer understands the full picture without having to ask.
6. **Provide an escape hatch.** Include a line like: "If during implementation you discover a constraint the consumer hasn't anticipated, file a follow-up against [consumer-side epic]." This invites pushback rather than forcing the producer to silently work around an over-specified requirement.

**Counter-indication:** Do not use this framing when the user wants prescriptive control ("make the doc look exactly like this template") or when the producer and consumer are the same person or agent. The pattern's value is in respecting cross-boundary autonomy.

---

## Phase 4: Draft the Issue

Use this structure. Omit sections that don't apply to this issue.

```
## Summary
One or two sentences: what this is and why it matters.

## Context
Background, motivation, or related work. Why now? Who's affected?

## Acceptance Criteria
- [ ] Specific, testable criteria for "done"
- [ ] Include doc/README updates if the change affects setup, usage, or the
      project spec

## Technical Notes
Relevant files, suggested approach, constraints, edge cases.

## Out of Scope
What this issue explicitly does NOT cover.
```

### Labels

Select from the repo's existing labels. Aim for 1–3 that are precise and
accurate — don't over-tag.

### Milestone

If this issue is clearly part of a broader initiative that will span multiple
issues, suggest a milestone:

> "This feels like it's part of a larger [X] effort. Want to assign it to an
> existing milestone, or create a new one?"

If it's a standalone issue, no milestone is needed — don't suggest one just to
suggest one.

### Documentation flags

If the issue's work would affect any project spec files — README, technical
docs, ADRs — include that explicitly in the Acceptance Criteria. Don't let
those updates get treated as optional.

Present the full draft (body + labels + milestone recommendation) to the user
and ask for approval before creating anything.

---

## Phase 5: Create the Issue

Once the user approves, create the issue using your configured GitHub MCP write tool if one is present (the model resolves the exact tool name, which varies by install), otherwise fall back to the `gh` CLI:

```bash
gh issue create --repo <owner>/<repo> --title "<title>" --body-file <file> --label <l1> --label <l2>
```

Write the body to a temp file rather than passing it inline, to avoid shell-escaping and encoding pitfalls. Apply the agreed labels and milestone.

Confirm with the issue number and URL.

---

## Phase 6: Offer to Start Work

After confirming creation, ask once:

> "Would you like me to start working on this now?"

If yes, proceed. If no, stop. Never start working without a clear yes.

---

## Long-Form Artifact Discipline

When the draft issue body would render as more than 40 lines of markdown — common for `feat:` and `chore(standards):` issues with full Acceptance Criteria, Technical Notes, and Out-of-Scope sections — save the draft to `<git-toplevel>/.tmp/<YYYY-MM-DD>-issue-<slug>.md` before Phase 4 (Draft the Issue) and present a short chat preview: title, labels, milestone recommendation, one-paragraph summary, and the file path. The user reads the file to approve the full body. After approval, the body is posted via `gh issue create --body-file <path>` (or the configured MCP tool's structured body field); the GitHub issue body becomes the durable record.

When an issue body is long, always write it to a `.tmp/` file under the repo root and pass it via `--body-file <path>` instead of embedding it inline in chat — this keeps the conversation readable and sidesteps shell-escaping and encoding problems. For large payloads above ~30 KB, this pattern is mandatory: never inline a body of that size in a shell command.
