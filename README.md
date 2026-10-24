# Job Hunt AI

**A team of AI agents that hunts jobs for you.** They find jobs, tailor your CV and cover letter for each one, fact-check them against your real CV, build print-ready files, and fill in the application form. You check it and press send.

It's a [Claude Code](https://claude.com/claude-code) plugin. It runs in your own Claude Code, on your own Claude plan.

**Website:** [job-hunt-ai.syrwegian.com](https://job-hunt-ai.syrwegian.com), where you can watch the agents at work and see the plans.

## Get started

1. Install the plugin in Claude Code:

   ```
   /plugin marketplace add MNKanout/job-hunt-ai
   /plugin install job-hunt-ai@job-hunt-ai
   ```

2. Sign in: run `/mcp`, choose the plugin's **jobs** server, and follow the browser.
3. Set up your job hunt with `/job-hunt-ai:setup`: your CV, contact details, job titles and countries.
4. Start a run with `/job-hunt-ai:find-and-apply`.

**What you need:** Claude Code on a paid Claude plan, Google Chrome, Python 3.10+, Node.js and poppler. The plugin checks these when a session starts and tells you what's missing.

All the commands are in [plugin/README.md](plugin/README.md).

## Your data

Your data is stored in the EU, behind your login. The plugin never sends an application for you. See the [privacy policy](https://mcp.job-hunt-agents.syrwegian.com/privacy) and [terms](https://mcp.job-hunt-agents.syrwegian.com/terms).

## License

[PolyForm Noncommercial 1.0.0](LICENSE)
