---
description: Check whether the ads of the jobs waiting on you have closed, and mark the closed ones expired once you say yes
---

Rolling ads ("Snarest") have no deadline, so a closed one stays "ready to apply" until someone looks. This command
looks. It only reads until the user says yes: never mark a job expired without their OK.

1. **The jobs:** call `pending`. Take the jobs in `ready_to_apply` and `apply_manually` that have a `url`. With none,
   say `No open jobs to check.` and stop.
2. **Check them all in one go:**
   ```bash
   "${CLAUDE_PLUGIN_DATA}/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/scripts/check_postings.py" <url> <url> ...
   ```
   It prints one JSON line with a `state` for each URL (`closed`, `open` or `unclear`) and its `evidence`. On
   `status: failed`, report the error and stop.
3. **A second look at the `unclear` ones** (at most 10), when the Playwright tools
   (`mcp__plugin_job-hunt-ai_playwright__*`) are available. The plugin's browser may be logged in where a plain request
   was not. For each: `browser_navigate` to the URL, `browser_snapshot` with the filename
   `/tmp/playwright-mcp/check-expired/<job_id>.yml`, then
   ```bash
   "${CLAUDE_PLUGIN_DATA}/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/scripts/check_postings.py" --snapshot /tmp/playwright-mcp/check-expired/<job_id>.yml --url <url>
   ```
   and use its state. Do not judge the page yourself, and never click anything on it (no apply, no sign-in). Without
   the browser, leave them unclear.
4. **Show the result:**
   - **Closed:** one line each, `<job_id> <title> at <company> — <evidence>`.
   - **Could not tell:** one line each with the link, so the user can look.
   - How many are still open.
   With nothing closed, say `All <N> ads are still open.` (plus the could-not-tell list) and stop.
5. **Ask:** `Mark these <N> as expired?` Wait for a clear yes. The user may leave some out.
6. **On yes:** one call, `set_application_status(status: expired, jobs: [{job: <job_id>, run_id: <run_id>}, ...])`,
   with the `run_id` `pending` gave each job. Answer one line per job: `<title> at <company> → Expired`.
