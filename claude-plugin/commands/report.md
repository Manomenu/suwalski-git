---
description: Build a suwgit work report for a client — say the client and the period in plain words, e.g. "lokum for September, with hours"
argument-hint: "<client> [period] [with hours] [as json] [save to <path>] [no summary]"
allowed-tools: Bash(suwgit:*), Bash(jq:*)
---

Build a suwgit work report as the user describes it:

$ARGUMENTS

1. The client is a tenant. If it is not named, or not clearly one of them, run
   `suwgit list` and pick it from the tenants there; if it is still unclear, ask.
2. Turn the request into one `suwgit report --tenant <tenant>` command:
   - a period ("September", "last month", "Q3", "since 1 October", "wrzesień")
     → exact `--from YYYY-MM-DD --to YYYY-MM-DD`, both days included, worked out
     from today's date; no period → leave both out (the last 30 days);
   - "with hours", "z godzinami" → `--time`;
   - "save to …", "to my invoices folder" → `-o <file or folder>`;
   - "as json", "as data" → `--format json` (stdout only; no `-o` with it);
   - "no summary", "quick", "offline" → `--no-summary`.
3. Run it. For a PDF, give the path it wrote. For JSON, or when the user asked
   a question about the work rather than for a file, answer from the JSON
   (`jq` is fine for that) instead of pasting it whole.
