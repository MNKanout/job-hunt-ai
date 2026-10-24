---
description: Fill the application form for a published job - submitter fills it in a visible browser, submitVerifier checks it, you press send
argument-hint: "[job-id]"
---

Auto-submit is off: the agents fill and check the form, and **you** press send.

`$ARGUMENTS` is a job label. Without one, use the newest built job (`list_jobs(filter: built)`).

1. **Resolve the job:** `find_job(job: <label>)`. If it is not built, tell the user to run `/job-hunt-ai:build-cv-coverletter <job-id>` first and stop. If `get_run(run_id)` shows its application as `applied`, say so and stop.
2. **Fill.** Spawn `submitter` with `run_id` and `job_id`.
   - `prepared` → step 3.
   - `apply_manually` → step 5.
   - `failed` with `not_built`, or a build error (`qa_evidence_mismatch`, `print_tool_failure`, ...) → the build is no longer what QA approved. Fix it through `/job-hunt-ai:build-cv-coverletter`; never retry this directly.
3. **Check.** Spawn `submitVerifier` with `run_id`, `job_id` and the `attempt_id` submitter returned.
   - `pass` → step 4.
   - `fail` → `get_attempt(run_id, job_id)` and send its `verifier_issues` back to step 2 as the submitter's `issues`. You do not count rounds: submitVerifier does, and returns `apply_manually` on its third fail.
   - `apply_manually` → step 5.
   - Do not surface verifier fails to the user mid-loop.
4. **Hand over.** Tell the user: the form for `<title>` at `<company>` is filled and checked in the open Chrome window (and the review screenshot is at `<builds_dir>/submission/review.png`). Check it, press send yourself, then run `/job-hunt-ai:mark-applied <job-id>`.
5. **Apply manually.** Relay the agent's `note` word for word, with the application link. Once the user has applied by hand, they run `/job-hunt-ai:mark-applied <job-id>`.

Rules:
- Never fill a form, answer a form question, or press send yourself.
- Never save an attempt status yourself; the two agents do.
- Do not log the spawns yourself: each agent logs its own start and result.
