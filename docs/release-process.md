# Release Process

This document covers the **buildwithclaude community-marketplace sync** step for `claude-github-tools` releases.

> **Note:** this repo does not yet have a formal tag-triggered release workflow (no `.github/workflows/release.yml`). When one is created, the buildwithclaude sync below belongs in the release runbook as a documented **manual** step — not as CI automation (see the rationale box).

## Sync the buildwithclaude listing (external marketplace)

`claude-github-tools` is listed in the community marketplace [davepoon/buildwithclaude](https://github.com/davepoon/buildwithclaude) as a github-source entry (added in davepoon/buildwithclaude#181). That entry mirrors `version` (plus `description` / `keywords`) from this repo's `.claude-plugin/plugin.json`.

After each release, refresh the entry so the public listing stays accurate.

> **Why this is manual — and stays out of any `release.yml`.** The target is an *external* repo. A CI-driven cross-repo PR would require a long-lived PAT with write access to a buildwithclaude fork, which we do not want to provision or manage. This is a deliberate manual checklist step.
>
> **Scope.** For a github-source entry the listed `version` is display/discovery metadata only — installs resolve this repo's live `plugin.json`, so a stale entry never breaks installs. This step keeps the public listing accurate; it is not an install-correctness gate.

### Steps

1. Sync your `cbeaulieu-gt/buildwithclaude` fork's `main` with upstream:

   ```bash
   git -C <fork> fetch upstream
   git -C <fork> push origin upstream/main:main
   ```

2. Branch, then edit `.claude-plugin/marketplace.json` → the `claude-github-tools` entry → set `version` to `X.Y.Z` (and update `description` / `keywords` if they changed) so it matches this repo's `plugin.json`.

3. Open the PR to upstream:

   ```bash
   gh pr create --repo davepoon/buildwithclaude --base main \
     --head cbeaulieu-gt:sync-claude-github-tools-vX.Y.Z \
     --title "Update claude-github-tools to vX.Y.Z"
   ```

### Checklist

- [ ] Fork `main` synced with upstream
- [ ] `claude-github-tools` entry in `marketplace.json` matches `plugin.json` (`version`, `description`, `keywords`)
- [ ] PR opened to `davepoon/buildwithclaude`
