---
description: Check the job-app connection - signed in, subscription, setup, and this computer's tools
---

Do not spawn any agent.

1. `account`. If the job-app tools are missing or answer `not_authenticated`: say "Not signed in. Run /mcp, choose the job-hunt-ai plugin's jobs server, and sign in with your email." and stop.
2. `get_settings`.
3. Answer in a short list:
   - Signed in as `<email>`; the plan (`plan`: Free, Plus, Pro or Ultra) and, for a paid plan, its billing status.
   - Today: `runs.used` of `runs.limit` jobs, reset at `runs.resets_at` (midnight UTC), and `max_agents` agents at once. Example: "Plus: 2 of 5 jobs today, resets at midnight UTC; 3 agents at once". Say "no daily limit" when `runs.limit` is null.
   - Setup: complete, or the `missing` items and `/job-hunt-ai:setup`.
   - Browser tools: whether `mcp__plugin_job-hunt-ai_playwright__*` tools are available.
   - Anything the session-start setup notes reported (Chrome, poppler, Python).
