---
description: Check the job-app connection - signed in, subscription, setup, and this computer's tools
---

Do not spawn any agent.

1. `account`. If the job-app tools are missing or answer `not_authenticated`: say "Not signed in. Run /mcp, choose the job-hunt-ai plugin's jobs server, and sign in with your email." and stop.
2. `get_settings`.
3. Answer in a short list:
   - Signed in as `<email>`; access: yes/no and the subscription status (with the trial end date if trialing).
   - Runs: `runs.used` of `runs.limit`, and when they reset (`runs.resets_at`; the trial does not reset). Example: "Trial: 1 of 3 runs used, ends 23 Oct" or "12 of 30 runs this month, resets 1 Nov". Skip the limit when `runs.limit` is null.
   - Setup: complete, or the `missing` items and `/job-hunt-ai:setup`.
   - Browser tools: whether `mcp__plugin_job-hunt-ai_playwright__*` tools are available.
   - Anything the session-start setup notes reported (Chrome, poppler, Python).
