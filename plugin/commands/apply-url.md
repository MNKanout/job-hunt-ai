---
description: Run the full pipeline for one job posting URL you already found - scrape, score, write CV and letter, QA, build, fill the form
argument-hint: "<url>"
---

First read `${CLAUDE_PLUGIN_ROOT}/ORCHESTRATOR.md` and follow it.

The URL is `$ARGUMENTS`. If it is empty, ask the user for one.

1. `get_settings` (stop with `missing` if `setup_needed`), then `start_run` (on `run_limit_reached`, say the plan's jobs for today are used up, point to `/job-hunt-ai:status`, and stop). Set `tmp_dir` to the literal path `/tmp/playwright-mcp/<run_id>`; do not create it with a shell command (Playwright and the Write tool create it). `log_event(run_id, pipeline, started, details: {flow: apply_url})`. From here on, if you stop before step 8 (an error, or the user says no), first call `end_run(run_id, failed)` for an error or `end_run(run_id, interrupted)` for the user's choice: an open run counts against the user's plan for up to 24 hours.
2. **Applied before?** Call `seen_urls`. If the URL is there as `applied`, say so and ask whether to go on. If it is `apply_manually`, say that too.
3. **Scrape it.** Spawn `jobFinder` with `run_id`, `tmp_dir`, and `url`: the single-URL mode, no search. If it fails, report it and stop.
4. **Score it.** Spawn `jobVerifier` with `run_id` and the `job_id` jobFinder returned. Unlike the normal pipeline, do not drop a job below the threshold: show the user the score, the threshold and the gaps, and ask "This job scored below the qualification threshold. Continue anyway?" On no, spawn sumWriter and stop. On yes, `log_event(run_id, pipeline, ok, job, details: {action: user_override_threshold})` and go on.
5. **Write, build, check, publish** exactly as the playbook's step 6 and 7: the template lookup, cvWriter, appWriter, builder `candidate`, the print check, qa, the correction loop (at most 5 rounds per agent; `discard_cv_template` on the cap), builder `publish`, then `promote_cv_template`.
6. **Fill the form** with `/job-hunt-ai:submit`'s steps 2 onward. The user presses send.
7. **Present** the title and company, QA status and warnings, the local HTML paths, and **Ready to send** or **Apply manually** with the note and link.
8. Count the tokens as the playbook's step 10 says, then spawn `sumWriter` with `run_id`.
