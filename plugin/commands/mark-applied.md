---
description: Record that you applied for a job yourself (after pressing send, or applying by hand)
argument-hint: "<job-id | url>"
---

`$ARGUMENTS` is a job label (`job-03`) or a posting URL. If it is empty, ask which job.

1. `mark_applied(job: <argument>)`. On `job_not_found`, tell the user and stop; never guess a URL.
2. If it was a job label, tidy up its PDFs: `find_job(job: <label>)`, call `script_token`, then run
   ```bash
   "${CLAUDE_PLUGIN_DATA}/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/scripts/build_html.py" <run_id> <job_id> --discard-pdfs --token <token>
   ```
   (the HTML stays).
3. Answer: `Marked <title> at <company> as applied. It will not be offered again.`
