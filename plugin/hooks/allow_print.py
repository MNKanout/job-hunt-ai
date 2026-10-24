#!/usr/bin/env python3
"""PreToolUse hook for the Playwright server's run-code tool.

The build script asks the browser to print the CV and letter (Claude Code's
sandbox can refuse Chrome started from a script), by writing a print script
and having the agent run it with browser_run_code_unsafe. That tool runs any
JavaScript outside the sandbox, so this hook decides every call to it:

- allow, without asking, a print script exactly as build_html.py writes it:
  in job-app/.print/, named print-<32 hex>.js, read back by print_script.parse
  to the very text render() writes, printing only build pages to .print PDFs;
- deny everything else, so a page the agents read can never get code run.

If this hook itself fails, Claude Code falls back to asking the user.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import print_script  # noqa: E402


def decide(event: dict) -> tuple[str, str]:
    tool_input = event.get("tool_input") or {}
    if tool_input.get("code"):
        return "deny", "Only the build script's print files may run here, never code passed in the call."
    name = tool_input.get("filename")
    if not isinstance(name, str) or not name:
        return "deny", "Only the build script's print files may run here."
    cwd = Path(event.get("cwd") or os.getcwd())
    # As jobapp_home() in build_html.py: job-app/ in the folder Claude Code runs in.
    home = Path(os.environ.get("JOBAPP_HOME") or cwd / "job-app").expanduser().resolve()
    cache, builds = home / ".print", home / "builds"
    path = (cwd / name).resolve()
    if path.parent != cache or not print_script.NAME_RE.fullmatch(path.name):
        return "deny", f"{name} is not a print file the build script wrote (in {cache})."
    try:
        prints = print_script.parse(path.read_text(encoding="utf-8"))
    except OSError as e:
        return "deny", f"could not read {path}: {e}"
    if prints is None:
        return "deny", f"{path} is not exactly a print script the build writes."
    for job in prints:
        url = urlsplit(job["html"])
        page = Path(unquote(url.path)).resolve()
        pdf = Path(job["pdf"]).resolve()
        if url.scheme != "file" or url.netloc or page.suffix != ".html" or builds not in page.parents:
            return "deny", f"{job['html']} is not a page in {builds}."
        if pdf.parent != cache or pdf.suffix != ".pdf" or str(pdf) != job["pdf"]:
            return "deny", f"{job['pdf']} is not a PDF in {cache}."
    return "allow", "A print-check of this job's CV and cover letter, written by the build script."


def main() -> None:
    decision, reason = decide(json.load(sys.stdin))
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": decision, "permissionDecisionReason": reason}}))


if __name__ == "__main__":
    main()
