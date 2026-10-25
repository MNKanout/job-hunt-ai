#!/usr/bin/env python3
"""scripts/upload_usage.py — send a run's token use to the job-app server, for
the office's electricity meter.

    python3 scripts/upload_usage.py --run-id <run_id> --token <script token>

The orchestrator runs it at the end of a run, before sumWriter. Claude Code
records every model call's token counts in its session transcripts on this
computer (~/.claude/projects/, or $CLAUDE_CONFIG_DIR/projects/). This reads
the transcripts of the run's sessions and sends one row of numbers per
session through the server's record_usage tool:

- each agent's own transcript (subagents/agent-*.jsonl) whose first prompt
  has the line `run_id: <run_id>`; its name comes from the agent-*.meta.json
  beside it;
- the orchestrator's session that spawned those agents, counting only its
  calls between the first and the last line that mention the run. Runs it
  ran side by side in one session share that time, so each counts it.

Claude Code writes one line per piece of a reply, repeating the usage, so a
call is counted once, by its message id. A call's usage.iterations, when
present, are its sampling rounds and are what is added up. Every sum covers
the whole transcript, so running this again replaces the numbers rather than
adding to them.

Only numbers and ids leave this computer: never a prompt, a reply, a CV or a
file name. A failure here never stops a run.

Always prints exactly one JSON status line as the last line of stdout.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from jobapp_api import Api, ApiError  # beside this file, in scripts/ and plugin/scripts/

# Transcripts older than this cannot hold a run that is ending now.
RECENT_S = 3 * 86400
KINDS = ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens")
USAGE_KEYS = {"input_tokens": "input_tokens", "cache_read_input_tokens": "cache_read_tokens",
              "cache_creation_input_tokens": "cache_write_tokens", "output_tokens": "output_tokens"}
PLAIN = re.compile(r"^[A-Za-z0-9_-]+$")
MODEL = re.compile(r"^[A-Za-z0-9._-]+$")
AGENT = re.compile(r"^[A-Za-z]+$")


def projects_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser() / "projects"


def records(path: Path):
    """The transcript's JSON lines; a line that does not parse is skipped."""
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                yield line, json.loads(line)
            except ValueError:
                continue


def counts_of(usage: dict) -> dict:
    """One call's tokens. With iterations, their sum: they are the call's rounds."""
    rounds = usage.get("iterations")
    parts = rounds if isinstance(rounds, list) and rounds else [usage]
    total = dict.fromkeys(KINDS, 0)
    for part in parts:
        if not isinstance(part, dict):
            continue
        for key, kind in USAGE_KEYS.items():
            value = part.get(key)
            if isinstance(value, int) and value > 0:
                total[kind] += value
    return total


def tally(lines, since: str | None = None, until: str | None = None) -> dict | None:
    """The calls in these transcript records (optionally between two
    timestamps), each counted once by its message id. None if there are none."""
    calls: dict[str, tuple[str, str, dict]] = {}
    for _line, rec in lines:
        msg = rec.get("message")
        if rec.get("type") != "assistant" or not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
            continue
        model, ts, mid = msg.get("model"), rec.get("timestamp") or "", msg.get("id")
        # Claude Code's own placeholder replies are not model calls.
        if not isinstance(model, str) or not MODEL.match(model) or not mid:
            continue
        if (since and ts < since) or (until and ts > until):
            continue
        # The last line of a call carries its final counts.
        calls[mid] = (model, ts, counts_of(msg["usage"]))
    if not calls:
        return None
    total = dict.fromkeys(KINDS, 0)
    per_model: dict[str, int] = {}
    for model, _ts, counts in calls.values():
        for kind in KINDS:
            total[kind] += counts[kind]
        per_model[model] = per_model.get(model, 0) + 1
    stamps = sorted(ts for _m, ts, _c in calls.values() if ts)
    return {
        "model": max(per_model, key=lambda m: (per_model[m], m)), "calls": len(calls), **total,
        "first_call_at": stamps[0] if stamps else None, "last_call_at": stamps[-1] if stamps else None,
    }


def first_prompt(lines) -> str:
    for _line, rec in lines:
        msg = rec.get("message")
        if rec.get("type") == "user" and isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def agent_of(transcript: Path) -> str:
    """The agent's name from Claude Code's meta file: job-hunt-ai:cvWriter → cvWriter."""
    try:
        kind = json.loads(transcript.with_suffix(".meta.json").read_text()).get("agentType", "")
    except (OSError, ValueError):
        kind = ""
    name = str(kind).rsplit(":", 1)[-1]
    return name if AGENT.match(name) else "unknown"


def collect(run_id: str, root: Path, now: float | None = None) -> list[dict]:
    """One row per session of the run: each agent's, and the orchestrator's
    session that spawned them."""
    now = time.time() if now is None else now
    recent = lambda path: now - path.stat().st_mtime <= RECENT_S  # noqa: E731
    marker = f"run_id: {run_id}"
    rows, sessions = [], set()
    for path in sorted(root.glob("*/*/subagents/agent-*.jsonl")):
        if not recent(path):
            continue
        lines = list(records(path))
        if marker not in [line.strip() for line in first_prompt(lines).splitlines()]:
            continue
        totals = tally(lines)
        session = path.parent.parent
        if totals and PLAIN.match(path.stem):
            rows.append({"conversation_id": path.stem, "agent": agent_of(path),
                         "parent_conversation_id": session.name if PLAIN.match(session.name) else None, **totals})
        sessions.add(session.with_suffix(".jsonl"))
    for path in sorted(sessions):
        if not path.is_file() or not PLAIN.match(path.stem):
            continue
        lines = list(records(path))
        mentions = sorted(rec.get("timestamp") for line, rec in lines if run_id in line and rec.get("timestamp"))
        totals = tally(lines, mentions[0], mentions[-1]) if mentions else None
        if totals:
            rows.insert(0, {"conversation_id": path.stem, "parent_conversation_id": None, "agent": "orchestrator",
                            **totals})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--token", required=True, help="a job-app script token, from the script_token tool")
    args = parser.parse_args()
    if not PLAIN.match(args.run_id):
        print(json.dumps({"status": "failed", "error": "invalid_id", "message": "run_id must be a plain id"}))
        sys.exit(1)
    try:
        rows = collect(args.run_id, projects_dir())
        result = Api(args.token).call("record_usage", {"run_id": args.run_id, "rows": rows})
    except ApiError as e:
        print(json.dumps({"status": "failed", "error": e.error, "message": e.message}))
        sys.exit(1)
    except OSError as e:
        print(json.dumps({"status": "failed", "error": "io_error", "message": str(e)}))
        sys.exit(1)
    print(json.dumps({"status": "ok", "run_id": args.run_id, "sessions": len(rows),
                      "recorded": result.get("recorded", 0),
                      "tokens": sum(r[k] for r in rows for k in KINDS)}))


if __name__ == "__main__":
    main()
