---
name: mr-chain
description: Work with a chain of stacked merge requests (MR chain, stacked MRs, stacked branches, base <- b1 <- b2 <- b3) with git-town and suwgit. Use when the user has branches whose merge requests target the previous branch, when a change touches code an earlier MR of the chain already changed, when changes must be merged up the chain, or when a merge request points at the wrong branch.
---

# MR chain

A chain is a row of branches, each one's merge request targeting the previous branch
(`base <- b1 <- b2 <- b3`). Changes flow upward by **merges**, never by rebase.

## The rule

**A change that touches what an earlier branch of the chain already changed is not piled on
top in a later branch.** Make it in the *earliest* branch of the chain that owns that code,
commit it there, and merge it up the chain, branch by branch. Otherwise the earlier MR
stays wrong and the later one carries a fix for something it did not write.

## Workflow

The order of the chain is git-town's (`git-town-branch.<branch>.parent` in git config);
suwgit reads it and keeps no order of its own. Needs `git town` and, for merge requests,
`glab` (GitLab) or `gh` (GitHub), logged in.

- Start the next branch on top of the current one: `git town append <name>`. A branch that
  already exists: `git town set-parent` on it.
- See where things stand: `suwgit chain status` (pushed? parent has news to merge up? where
  does each MR point?).
- Fix in the owning branch: `git town switch` (or `git switch <branch>`), change, then
  `suwgit commit` / `suwgit push` (see the suwgit skill; it runs the commits).
- Merge it up the chain: `git town sync --stack`, with `git config git-town.sync-feature-strategy merge`
  set once. On a conflict, fix it in that branch, commit with suwgit, then `git town continue`;
  one branch at a time.
- Open the MRs: `git town propose` for each branch, then `suwgit chain fix-targets` to point
  every MR at the previous branch (it shows the changes and asks first; `--yes` skips the
  question once the user agreed). It never creates or closes an MR.

## Never

- Rebase, amend, reset or force-push — merging up is the whole method.
- Fix in a later branch code an earlier branch owns.
- Run `git town sync` in a way that rebases: check `git config git-town.sync-feature-strategy` says `merge`.
- Retarget an MR by hand when `suwgit chain fix-targets` can show it first.

If `suwgit chain status` says the branch has no git-town parent, tell the user the
`git town set-parent` line; do not invent another setting.

Details: [docs/how-it-works.md](../../../docs/how-it-works.md#merge-request-chains).
