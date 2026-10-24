---
description: One-line status - anything waiting on you
---

Call `pending`. Do not spawn any agent. Answer in exactly one line:

- Jobs in `ready_to_apply` or `apply_manually`: `<N> job(s) waiting on you: <job-ids>. /job-hunt-ai:catch-up for details.`
- Else jobs in `pending_build`: `<N> draft(s) pending build: <job-ids>. Use /job-hunt-ai:build-cv-coverletter <job-id>.`
- Else, with `newest_run.status` still `started`: `Run <run-id> in progress — nothing waiting on you yet.`
- Else, with a newest run: `Nothing pending. Last run <run-id> is complete.`
- With no run at all: `No runs yet. Use /job-hunt-ai:find-and-apply to start one.`

When `staged_rules` > 0, append to the same line: ` · <M> rule(s) staged — /job-hunt-ai:review-feedback`
