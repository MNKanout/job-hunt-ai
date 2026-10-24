---
description: Start a new job-application run - find jobs, tailor a CV and letter for the best match, fill its application for you to send
argument-hint: "[location]"
---

First read `${CLAUDE_PLUGIN_ROOT}/ORCHESTRATOR.md`, the playbook for every step below, and follow it exactly.

Run the full pipeline (its section "Pipeline") for one job:

1. `get_settings`. Stop with the `missing` items if `setup_needed`. The Location is `$ARGUMENTS` when given, else the first of `countries`.
2. `start_run`, then the queue, then jobFinder and jobVerifier batches until one job qualifies (at most `max_postings_per_run` postings scanned).
3. For that job: the template lookup, cvWriter, appWriter, builder `candidate`, the print check, qa, the correction loop (`discard_cv_template` on the cap), builder `publish`, then `promote_cv_template`.
4. Fill the application with `/job-hunt-ai:submit`'s steps 2 onward. The user presses send.
5. Present the outcome: **Ready to send** (where the filled form is open, and to run `/job-hunt-ai:mark-applied <job_id>` after sending) or **Apply manually** with the note and link, plus the local HTML paths.
6. Count the tokens (the playbook's step 10), then sumWriter, at the end or on interruption.

Enforce every budget guard. If a job-app tool answers `not_authenticated`, tell the user to run `/mcp`, pick the job-hunt-ai plugin's `jobs` server, and sign in; on `subscription_required`, point them to `/job-hunt-ai:setup`; on `run_limit_reached`, say the plan's jobs for today are used up and point them to `/job-hunt-ai:status`.
