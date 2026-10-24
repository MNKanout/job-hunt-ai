---
description: Open the pipeline's own Chrome window so you can log in to a job platform once; the submitter reuses that login
argument-hint: "[url]"
---

The submitter fills forms in a Chrome profile that belongs to this plugin (`~/.auto-apply-ai-agent-fleet/chrome-profile`). It never sees or types a password. Some platforms need you to be logged in first.

Tell the user to run this in their own terminal (it opens a visible Chrome window and waits until they close it), with the platform's login page as `$ARGUMENTS` if they named one:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/playwright_mcp.py" --login <url>
```

Then they log in there, close the window, and run `/job-hunt-ai:submit <job-id>` again. Do not run the command yourself and never ask for a password.
