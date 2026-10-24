---
description: Set what happened with one or more jobs - open, applied, expired (the ad closed) or apply manually
argument-hint: "<open | applied | expired | apply_manually> <job-id | url> [<job-id | url> ...]"
---

`$ARGUMENTS` is a status followed by one or more job labels (`job-03`) or posting URLs. If the status or the jobs
are missing, ask. The statuses:

- `open`: not applied yet. It stays open even if its deadline has passed.
- `applied`: you applied (`/job-hunt-ai:mark-applied` does the same for one job).
- `expired`: the ad has closed. It is no longer offered.
- `apply_manually`: the pipeline cannot send it; you apply yourself.

1. **One call for all of them:** `set_application_status(status, jobs: [{job, run_id}, ...])`. A label repeats across
   runs: when the jobs came from a `pending` or `get_run` list shown in this conversation, pass the `run_id` it gave
   each job. Otherwise pass the label alone (the newest run that has it). Pass URLs as they are. On `job_not_found`,
   nothing was changed: name the jobs in `details.unknown` and stop; never guess a URL.
2. **Only for `applied`:** tidy up the PDFs of each returned job that has a `run_id`: call `script_token` once, then
   for each one run
   ```bash
   "${CLAUDE_PLUGIN_DATA}/venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/scripts/build_html.py" <run_id> <job_id> --discard-pdfs --token <token>
   ```
   (the HTML stays). If this fails, say so, but the status is already saved.
3. Answer one line per job: `<title> at <company> → <Open | Applied | Expired | Apply manually>`, using the URL when
   there is no title.
