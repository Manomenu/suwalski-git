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
   - "no summary", "quick", "offline" → `--no-summary`;
   - "send it to …", "mail it to …", "wyślij do …": see step 4. Never add an
     address on your own.
3. Run it. For a PDF, give the path it wrote.
4. Sending by e-mail is two steps, because suwgit's `[y/N]` question cannot
   be answered from here (no terminal, so it always answers no):
   - First make the report **without** `--mail`, give the user its path, and
     ask them to look at it and confirm sending to the exact address(es) they
     named. The sender is always their own mailbox from `suwgit init`.
   - Only after they say yes in this conversation, run the same command with
     `--mail <address>` (once per address, as written) and `-y`, and report who
     it was sent to.
   - If suwgit says e-mail is not set up, explain that `suwgit init` sets it up
     (the user runs it; it asks for the SMTP password) and that they can send
     the PDF by hand meanwhile. For JSON, or when the user asked
   a question about the work rather than for a file, answer from the JSON
   (`jq` is fine for that) instead of pasting it whole.
