---
description: Score one or more saved job postings against your CV
argument-hint: "[job-id | --all]"
---

1. Newest run: `list_jobs` (no `run_id`).
2. With a label: check it is in the run, then spawn `jobVerifier` with `run_id` and that `job_id`.
   With `--all` or nothing: `list_jobs(filter: unscored)` and spawn one `jobVerifier` with every `job_id` in it. If none, say so.
3. Report each score and whether it qualified, with the threshold jobVerifier returned.
