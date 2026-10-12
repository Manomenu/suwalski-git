---
description: Show suwgit's setup, the repositories it watches and the clients (tenants) it knows
argument-hint: "[optional question, e.g. which repos are dirty?]"
allowed-tools: Bash(suwgit:*)
---

Run `suwgit list` and answer from its output:

$ARGUMENTS

With no question, summarise it briefly: the LLM server and model, sweep
interval and whether the service runs, whether it pushes, each registered
repository with its state (dirty, clean, missing) and settings, and each tenant
as client → repositories (projects). Under `tenants`, each line reads
`<tenant>, <repository folder> (<project>)`: the tenant is the client's working
name, then the folder of a repository holding work for it, then — when known —
the project name the client sees in reports.

If `suwgit` is not found or says there is no config, it is not set up yet:
explain the setup from the suwgit skill, and give the user the `!` lines to run,
since `init` asks questions only they can answer.
