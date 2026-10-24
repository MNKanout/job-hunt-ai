---
description: Make a job's built CV the template new CVs for a role family start from
argument-hint: "<family> <job-id> [label]"
---

A template is the CV you approved as the starting point for a kind of role (a role family, e.g. `ai-ml-ds` for AI engineering, data science and machine learning). cvWriter starts each new CV for that family from it instead of writing from scratch, and adds facts that were added to your source CV since.

`$ARGUMENTS` is a family id (letters, digits, `-` and `_`), a job label, and an optional label for the family. If the family or the job is missing, ask.

1. `find_job(job: <label>)` for its `run_id` and `job_id`. On `job_not_found`, tell the user and stop.
2. **Pick the label.** Use the user's label. If they gave none, call `list_cv_templates`: a family that already has a template keeps its label, so pass none. For a new family, ask the user what kinds of role it covers. The label decides which postings use the template, so never guess it.
3. `mark_cv_template(family, run_id, job_id, label?)`. It takes the job's CV as it was built, and snapshots the source CV as it is now, so only facts added after this point count as new. A family's previous template is kept as superseded.
   - `cv_not_built`: the job has no built CV. Say so, and point to `/job-hunt-ai:build-cv-coverletter <job-id>`.
   - `label_required`: ask the user for the label, then call it again.
4. Answer: the family, its label, the version, and which job's CV it is.
