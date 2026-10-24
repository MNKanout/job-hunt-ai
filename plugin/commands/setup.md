---
description: Set up your job hunt - your CV, example letters, photo, contact details, job titles and countries. Run it again any time to change them.
argument-hint: "[path to your CV]"
---

Walk the user through setup one step at a time, in plain words. Keep each message short. Everything is saved on the job-app server under their account; nothing is written to disk. Never invent or "improve" a fact: the CV saved here is the only source of truth every CV and letter is written from.

## 0. Signed in?

Call `account`. If the job-app tools are missing or answer `not_authenticated`, say: "First sign in: run `/mcp`, choose the job-hunt-ai plugin's **jobs** server, and sign in with your email. Then run `/job-hunt-ai:setup` again." and stop.

Then call `get_settings` and `get_source_cv(list_only: true)` to see what already exists. If setup is complete, show a one-screen summary and ask which part to change; go only through those parts.

## 1. Your CV (required)

Ask for the CV: a file path (`$ARGUMENTS` when given; PDF, Word, Markdown or text) or pasted text.

1. Read it with the Read tool. If it cannot be read, ask for another format.
2. Split it into sections, one markdown file each, using the CV's own headings: usually `profile.md` (summary and contact details), `experience.md`, `education.md`, `skills.md`, `projects.md`, `languages.md`, `certifications.md`. Only create a section the CV has.
3. **Keep every fact exactly as written**: employers, titles, dates, places, degrees, grades, numbers, tools, language levels. You may only turn the layout into markdown (`## Role, Company — City · 2021–2024`, bullet lists). Do not add, merge, reword or drop facts. Put anything you cannot place in `other.md` rather than leave it out.
4. Show the user the section list with a two-line preview of each, and anything unclear (an undated role, a skill with no context). Ask: "Save these sections? (yes / edit)".
5. On yes, `save_source_section(filename, section_name, content)` for each. When sections already existed, ask first whether to replace all of them, or only add the new ones; delete replaced sections that no longer exist with `delete_source_section`.

## 2. Contact details (required: name)

From the CV's header, propose `full_name`, `email`, `phone`, `city`, `linkedin`, `github`, `website`. Ask the user to confirm or correct them (they appear at the top of every CV and under every letter). Save with `save_settings` (only the fields they confirm; URLs in full, with https://).

## 3. What to search for (required)

1. **Job titles**, most wanted first (e.g. "Data Analyst", "BI Developer"). Suggest a few from the CV and let the user choose and order them.
2. **Countries** to search (e.g. Norway). The first is the default for `/job-hunt-ai:find-and-apply`.
3. **Job boards** per country: show `get_settings().platforms` for each country (for example Norway: LinkedIn and a national job board); a country not listed uses `default_platforms`. Ask if they want different ones.

Save with `save_settings(titles, countries, platforms?)`. Leave `batch_size` and `verifier_threshold` alone unless the user asks: the defaults suit most people.

## 4. Example letters (optional)

Ask whether they have one to three cover letters they wrote and liked. They are used for tone and style only, never for facts. For each path: read it, show the first lines, and `save_example_letter(name, content)` with a short name like "Acme analyst 2025".

## 5. Photo (optional)

Ask whether they want a photo on the CV. If yes, take the path of a JPEG or PNG (at most 2 MB), call `script_token`, and run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/upload_photo.py" --token <token> "<path>"
```

The image goes straight to the server, not through this conversation. `--remove` instead of a path deletes a saved photo.

## 6. Job platform logins (optional)

Some boards need the user logged in before an application form opens. Explain `/job-hunt-ai:browser-login [url]`: a Chrome window of the plugin's own, where they log in once by hand; the submitter reuses that login and never sees a password.

## 7. Done

Call `get_settings`. If `setup_needed` is still true, say which `missing` item is left and go back to it. Otherwise call `account` and say:

- what was saved (sections, titles, countries, examples, photo),
- their access: active, trial until a date, or none yet (and how to start one, if the `account` result gives a link),
- their runs: how many of `runs.limit` are left, and when the trial ends or the count resets,
- next: "Run `/job-hunt-ai:find-and-apply` to start your first run."
