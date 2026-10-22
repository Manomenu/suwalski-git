---
description: Show where an MR chain stands — pushed or not, what needs merging up, where each merge request points
argument-hint: "[path] [fix targets]"
allowed-tools: Bash(suwgit chain:*)
---

Show the state of the branch chain, as the user describes it:

$ARGUMENTS

1. Run `suwgit chain status` (add the path if one is named) and summarise the table: which
   branches are not pushed, which have news in their parent that needs merging up, which
   merge request targets the wrong branch.
2. If the user asked to fix the targets: there is no terminal for suwgit's question, so show
   what `status` reports, and once the user agrees run `suwgit chain fix-targets --yes`.
3. Merging up and conflicts are not done here: follow the mr-chain skill (`git town sync --stack`).

If suwgit says the branch has no git-town parent, or `glab` / `gh` is missing, relay the
message and the line it gives.
