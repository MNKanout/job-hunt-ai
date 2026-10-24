---
description: Build, check and publish the CV and cover letter for one job. Does not apply.
argument-hint: "<job-id>"
---

First read `${CLAUDE_PLUGIN_ROOT}/ORCHESTRATOR.md`. The job label is `$ARGUMENTS` and is required: never bulk-build. The order is always builder `candidate` → print check → qa → (pass, or warn with no fail) → builder `publish`. Nothing publishes without a QA pass on the exact candidate.

1. `find_job(job: <label>)` for its `run_id`, then `get_run(run_id)` for its status and `get_drafts(run_id, job_id)`.
   - It needs a CV and a letter; otherwise say which writer to run and stop.
   - Application `applied`: say so and ask before building again. `expired`: say so and ask before building.
   - Already built: ask "This job was already built. Rebuild?"
2. Show the title, company and latest QA status.
3. Spawn `builder` with `mode: candidate`.
4. **Print check, then qa, with the playbook's correction loop** (step 6): a print violation or a QA fail goes back to the writer that owns it, which fixes only what was flagged, then a fresh candidate, a print check and a re-review by the same qa. Send writers and qa that are waiting in this session a follow-up; otherwise spawn them in correction mode on their latest draft. Keep the agent ids as the playbook says, and stop at its cap.
   - `pass`, or `warn` with no fail → step 5.
   - Stopped at the cap → show the remaining issues with their `responsible_agent`. Do not publish.
5. Spawn `builder` with `mode: publish`. Report the local paths, or the refusal verbatim. After a publish, call `promote_cv_template(run_id, job_id)` and report the template update if it returns `promoted: true`.

The pipeline stops here. To apply: `/job-hunt-ai:submit <job-id>`, or open `cv.html` and print it, then `/job-hunt-ai:mark-applied <job-id>`.
