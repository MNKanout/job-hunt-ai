# Job Hunt AI — Claude Code plugin

A team of agents that finds jobs, tailors your CV and cover letter for each one,
fact-checks them against your real CV, builds print-ready files, and fills the
application form for you to send. It runs in your own Claude Code, on your own
Claude plan. Your data (CV, jobs, drafts, learned rules) lives on the hosted
Job Hunt AI server, behind your login.

## What you need

- Claude Code
- Python 3.10+, Google Chrome, Node.js (for the browser tools), and poppler
  (`brew install poppler` on macOS, `apt install poppler-utils` on Debian/Ubuntu)
- A Job Hunt AI account: you create it the first time you sign in

The plugin checks these when a session starts and tells you what is missing.
It keeps its own small Python environment in its data folder; nothing is
installed into your Python.

## Install

```
/plugin marketplace add MNKanout/job-hunt-ai
/plugin install job-hunt-ai@job-hunt-ai
```

Then sign in: run `/mcp`, choose the job-hunt-ai plugin's **jobs** server, and follow the browser. You type
your email, get a code, type the code. That is your account.

Then run `/job-hunt-ai:setup`. It asks for your CV (a PDF, Word or text file), your
contact details, the job titles and countries to search, and optionally a few
of your own cover letters and a photo. Run it again whenever something changes.

## Use

| Command | What it does |
|---|---|
| `/job-hunt-ai:setup` | Your CV, contact details, job titles, countries, examples, photo |
| `/job-hunt-ai:add-source <url or file>` | Add a project or role to your CV from a repo, website or file |
| `/job-hunt-ai:status` | Signed in? Subscription? Setup complete? |
| `/job-hunt-ai:find-and-apply [country]` | One full run: find, score, write, check, build, fill the form |
| `/job-hunt-ai:apply-url <url>` | The same for one posting you found |
| `/job-hunt-ai:pending` | One line: what waits on you |
| `/job-hunt-ai:catch-up` | The newest run in detail |
| `/job-hunt-ai:submit <job>` | Fill a built job's form for you to send |
| `/job-hunt-ai:mark-applied <job>` | You sent it (or applied by hand) |
| `/job-hunt-ai:set-status <status> <job>...` | Mark jobs open, applied, expired (the ad closed) or apply manually |
| `/job-hunt-ai:check-expired` | Check whether the ads waiting on you have closed, then mark them expired |
| `/job-hunt-ai:mark-template <family> <job> [label]` | Make a job's built CV the starting point for that kind of role |
| `/job-hunt-ai:review-feedback` | Approve or reject the lessons the agents learned |
| `/job-hunt-ai:write-cv`, `/job-hunt-ai:write-coverLetter`, `/job-hunt-ai:qa`, `/job-hunt-ai:verify`, `/job-hunt-ai:build-cv-coverletter` | Run one step for one job |
| `/job-hunt-ai:browser-login [url]` | Log in to a job platform once in the plugin's Chrome |
| `/job-hunt-ai:delete-account` | Delete your account and all its data, for good |

**Your data.** It is stored in the EU (Supabase, Ireland). See the privacy policy and terms at
https://mcp.job-hunt-agents.syrwegian.com/privacy and `/terms`.

**It never sends an application.** The submitter fills the form in a visible
Chrome window and stops before the send button. You check it and press send.

Built files go to `job-app/builds/<role>-<date>/` in the folder you run Claude Code from (set `JOBAPP_HOME` to use another folder). Open `cv.html` in a
browser and print it to PDF if you want one.

## How it fits together

- `agents/`, `commands/`, `ORCHESTRATOR.md`: the prompts. The main session
  reads `ORCHESTRATOR.md` and hands each step to an agent.
- `.mcp.json`: the hosted **jobs** server (all your data, through typed tools)
  and a local **playwright** server (the browser).
- `scripts/`: the local scripts the agents run (rendering and print checks need
  Chrome on your machine). They are copies; the source is the repository's
  `scripts/`, synced with `python3 scripts/sync_plugin.py`.
- `hooks/setup-env.sh`: the session-start check and Python environment.

The server, its tools and its security model are described in
[`mcp-server/README.md`](../mcp-server/README.md).
