# GitHub Actions — incident history & provenance (load only when revising this skill)

This file holds the **origin stories** behind rules in `SKILL.md`: which
memory file a rule was distilled from, and the narrative incidents that
motivated the verification gates. None of it is needed to **author or debug** a
workflow — the actionable rules live inline in `SKILL.md`. This provenance is
needed only to **revise** the skill without re-deriving why a rule exists.

`SKILL.md` is a high-frequency skill (fires on any GitHub Actions / CI work),
so this archaeology is kept out of the always-loaded body deliberately.

---

## Rule → source-memory map

These rules in `SKILL.md` were promoted from incident experience. The
rule is authoritative inline; the source is provenance for a future reviser.

| `SKILL.md` rule                                            | Origin                                                                     |
| ---------------------------------------------------------- | -------------------------------------------------------------------------- |
| § 3 — release jobs need `permissions: contents: write`     | Incident: `gh release create` failed 403 due to missing contents:write     |
| § 5 — split lint and test into separate jobs               | Incident: merged job masked which check actually failed                    |
| § 14 — use rulesets, not classic branch protection         | Incident: classic branch protection missed new checks; PRs merged unguarded |
| § 6 — verify SHA pins at write time (deref annotated tags) | Incident: annotated-tag SHA pin failed silently at workflow startup         |

---

## § 13 dogfood gate — why "CI green ≠ dogfood validation" is a hard gate, not a note

> **Caught repeatedly.** The `glitchwerks/github-actions` repo's own CLAUDE.md
> documents the self-referencing-dogfood limitation, but agents (router,
> code-writer, code-reviewer) keep treating green CI as proof. That recurrence
> is why the four-step verification discipline in `SKILL.md § 13` is framed as
> a mandatory gate rather than informational text: if a PR claims dogfood
> validation, the PR body or review must cite the specific observable that
> distinguishes new-code from old-code execution.

---

## § 14 ruleset gate — historical motivation in `glitchwerks/claude-configs`

The "a required check isn't done landing until it's wired into a ruleset"
rule has direct precedent in this repo:

- **#282** — `claude-pr-review/quality-gate` was running but not required.
- **#326** — `Test Hooks` and `Validate Export Manifest` ran on every PR but
  did not block.

Both are the same failure: the workflow shipped, the ruleset entry did not.

**#331** is a related but distinct failure — a check already in the ruleset
blocked merges on PRs that legitimately did not run it (paths-filter mismatch).
The second-order rule distilled from #331 (verify paths-filter behavior on
triggering and non-triggering PRs before adding the ruleset entry) is retained
inline in `SKILL.md § 14`.
