---
description: Delete your job-app account and all its data, for good
---

Do not spawn any agent. This cannot be undone, so go one step at a time and never fill in the confirmation yourself.

1. `account`. If the job-app tools are missing or answer `not_authenticated`: say "Not signed in. Run /mcp, choose the job-hunt-ai plugin's jobs server, and sign in with your email." and stop.
2. Say, in plain words, what will be deleted from the job-app server: the CV and its sections, contact details, settings, example letters, photo, every run, job, CV and letter draft, QA result, application record, personal rule, uploaded file, and the login itself. Global rules are not personal and stay. Files on this computer (the `job-app/builds` folder) are not touched; the user can delete that folder themselves. Backups on the server roll off within 7 days.
3. Ask the user to type their account email (`<email>` from step 1) to confirm. Wait for their answer. If they type anything else, or say no, stop: nothing is deleted.
4. Call `delete_my_data` with `confirm` set to exactly what the user typed.
5. On `ok`: say the account is deleted and how many files were removed, and that the plugin can be removed with `/plugin uninstall job-hunt-ai@job-hunt-ai`. On `confirm_mismatch`: say the email did not match and nothing was deleted. On any other error: show it, and say they can write to the contact address on the privacy page.
