---
description: Run appWriter for a job (or all that need a letter). Optional feedback is staged as a rule.
argument-hint: "[job-id] [feedback]"
---

First read `${CLAUDE_PLUGIN_ROOT}/ORCHESTRATOR.md`. `$ARGUMENTS` is an optional job label, then optional feedback text.

1. **Feedback given:** stage it with `stage_rule(agent: appWriter, title, why, how_to_apply, origin: user_correction)`, general and concise, and tell the user it is staged until `/job-hunt-ai:review-feedback`. Also pass it to the writer below.
2. **An appWriter for that job is still waiting in this session:** send it a follow-up. Otherwise:
3. With a label: `find_job(job: <label>)` and spawn `appWriter` with `run_id`, `job_id` and `feedback`.
   Without one: `list_jobs(filter: needs_letter)` for the newest run, ask which, then spawn for each.
   - **The job already has a letter** (`get_drafts(run_id, job_id, which: letter)` returns one): spawn it in correction mode: "correction mode: your previous draft is the latest version from `get_drafts`; fix only what the feedback asks". Never start the letter over.
4. Report the version saved and any warnings. The letter needs a fresh build and QA: `/job-hunt-ai:build-cv-coverletter <job-id>`.
