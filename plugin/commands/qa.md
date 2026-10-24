---
description: Run qa for a job (or all that need it) to check the CV and cover letter for accuracy and quality
argument-hint: "[job-id]"
---

First read `${CLAUDE_PLUGIN_ROOT}/ORCHESTRATOR.md`. This command never publishes: it makes a fresh candidate when needed and gets a QA verdict. `/job-hunt-ai:build-cv-coverletter` publishes.

1. **One job** (`$ARGUMENTS` is a label): `find_job(job: <label>)` for its `run_id`, then `get_drafts(run_id, job_id)` and `get_build_evidence(run_id, job_id)`.
   - No CV or no letter: tell the user which writer to run first and stop.
   - No candidate, or the candidate's `created_at` is older than either draft's: spawn `builder` with `mode: candidate` first. QA needs fresh print evidence.
   - A `qa` for that job is still waiting in this session: send it a re-review follow-up ("re-review: a new candidate is ready"). Otherwise spawn `qa` with `run_id` and `job_id`. On `fail`, show the issues grouped by `responsible_agent`, with concrete suggestions.
2. **No argument:** `list_jobs(filter: needs_qa)` for the newest run. Show the list and ask which to run (or all). For each: the same candidate-first check, then `qa`. At most 20 agents at once.
