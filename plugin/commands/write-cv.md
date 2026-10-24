---
description: Run cvWriter for a job (or all that need a CV). Optional feedback is staged as a rule.
argument-hint: "[job-id] [feedback]"
---

First read `${CLAUDE_PLUGIN_ROOT}/ORCHESTRATOR.md`. `$ARGUMENTS` is an optional job label, then optional feedback text.

1. **Feedback given:** stage it with `stage_rule(agent: cvWriter, title, why, how_to_apply, origin: user_correction)`, general and concise, and tell the user it is staged until `/job-hunt-ai:review-feedback`. Also pass the feedback to the writer below, so it applies to this draft.
2. **A cvWriter for that job is still waiting in this session:** send it a follow-up with the feedback. Otherwise:
3. With a label: `find_job(job: <label>)` and spawn `cvWriter` with `run_id`, `job_id` and `feedback`.
   Without one: `list_jobs(filter: needs_cv)` for the newest run, ask which, then spawn for each.
   - **The job already has a CV** (`get_drafts(run_id, job_id, which: cv)` returns one): spawn it in correction mode, with no `template`: "correction mode: your previous draft is the latest version from `get_drafts`; fix only what the feedback asks". Never start the CV over.
   - **First draft** (the job has no CV yet): first do the playbook's template lookup (step 6) and pass its `template`.
4. Report the version saved and any warnings. Remind the user the CV needs a fresh build and QA: `/job-hunt-ai:build-cv-coverletter <job-id>`.
