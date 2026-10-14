---
description: Commit and push this repository with suwgit — say in plain words if it is client work, for which client and project, or with which ticket id
argument-hint: "[path] [for <client>] [project <name>] [ticket <ID>] — e.g. for lokum, ticket EH-123"
allowed-tools: Bash(suwgit:*)
---

Commit and push with suwgit, as the user describes it:

$ARGUMENTS

1. Run `suwgit list` to see the repositories, tenants and projects that exist.
2. Turn the request into one `suwgit push` command:
   - the path first, if one is named (default: the current repository), then flags;
   - client work ("for lokum", "dla klienta lokum", "b2b") → `--b2b <tenant>`, and
     `--project "<name>"` taken from `suwgit list` or from the request. Never leave
     tenant or project to suwgit's `[Y/n]` question: there is no terminal to answer
     it. Add `-y` only if the user named or confirmed these exact values. If no
     tenant is named and `suwgit list` shows several or none for this repository,
     ask which client instead of guessing;
   - a ticket or task id (YouTrack, Jira, e.g. `EH-3311111`) → `-p <ID>`;
   - wishes about the message itself ("mention it is half done", "po polsku") →
     one quoted sentence right after the path, e.g. `suwgit push . "mention it is half done"`;
   - nothing said → a plain `suwgit push`.
3. Run it and report the commit hash, the message, what was pushed and the
   merge request link, if suwgit printed one.
   - A possible secret: show suwgit's reason and stop; do not work around it.
   - A rejected push: the commit is safe locally. Explain that the remote has
     commits this clone lacks; never force-push or pull on your own.
