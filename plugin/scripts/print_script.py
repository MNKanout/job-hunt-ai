"""The print script the plugin's build asks the browser to run.

In a sandboxed Claude Code session the build script cannot start Chrome, so
it writes this script and the agent runs it with the Playwright MCP server's
run-code tool, which prints outside the sandbox. build_html.py writes it with
render(); the plugin's hook (plugin/hooks/allow_print.py) lets the tool run
without asking only for a file that parse() reads back to exactly what
render() writes, so nothing else can pass as a print.
"""
from __future__ import annotations

import json
import re

PREFIX = "async (page) => {\n  const prints = "
SUFFIX = """;
  const tab = await page.context().newPage();
  try {
    for (const job of prints) {
      await tab.goto(job.html, { waitUntil: "load" });
      await tab.evaluate(() => document.fonts.ready);
      await tab.pdf({ path: job.pdf, preferCSSPageSize: true, printBackground: true });
    }
  } finally {
    await tab.close();
  }
  return prints.map((job) => job.pdf);
}
"""
# The only names a print script may have.
NAME_RE = re.compile(r"print-[0-9a-f]{32}\.js")


def render(prints: list[dict]) -> str:
    """The script that prints each {"html": file URI, "pdf": path}."""
    return PREFIX + json.dumps(prints, indent=2) + SUFFIX


def parse(text: str) -> list[dict] | None:
    """The prints in a script render() wrote, or None for anything else."""
    if not (text.startswith(PREFIX) and text.endswith(SUFFIX)):
        return None
    try:
        prints = json.loads(text[len(PREFIX):len(text) - len(SUFFIX)])
    except ValueError:
        return None
    if not (isinstance(prints, list) and prints and all(
            isinstance(p, dict) and set(p) == {"html", "pdf"}
            and isinstance(p["html"], str) and isinstance(p["pdf"], str) for p in prints)):
        return None
    return prints if render(prints) == text else None
