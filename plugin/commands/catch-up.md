---
description: Detailed report on your newest run - what was done, what failed, what waits on you
argument-hint: "[run-id]"
---

Report on a run: `$ARGUMENTS` when given, else your newest. Do not spawn any agent.

1. `get_run(run_id)` (leave `run_id` out for the newest). If `run` is null, say `No runs yet. Use /job-hunt-ai:find-and-apply to start one.` and stop.
2. Output:

```
Last run: <run-id> — <status>

Jobs found: N | Qualified: M | Drafts ready: K | Built: J | Applied: A | Failed: F

WAITING ON YOU:
  • job-NN — <title> at <company> — <ready to send / apply manually: the note, verbatim, with the link>

NEEDS REWRITE (QA failed):
  • job-NN — <title> — <one-line summary of the issue>

IN PROGRESS / INCOMPLETE:
  • job-NN — at <step>

ERRORS:
  • <step>:<job> — <reason from the events>

NEXT ACTIONS:
  - <suggested /job-hunt-ai: commands>
```

Qualified comes from each job's `qualified` (the server applies the threshold). A blocker note is copied word for word.
