#!/usr/bin/env python3
"""scripts/log_event.py — an agent reports that it started, or how it ended.

    python3 scripts/log_event.py <run_id> <agent> <status> [--job <id>] [--detail key=value ...]

Writes one row to Supabase `pipeline_events`. Every pipeline agent calls it
twice per task: `started` as its first action, and its result (`completed` or
`failed`) as its last. The Office in the web app draws exactly these rows, so a
task nobody logged is a task nobody sees.

`--job` takes the short job id (`job-03`) or the database uuid; the short one
is what gets stored, the same as every other event.

A jobFinder `completed` with `--job`, and every cvWriter `started`, gets the
posting's title and company added from its `jobs` row, so free text never has
to survive a command line. The cvWriter one is how the Office names the job
when jobFinder brought a batch, or when the job came from the queue.

`--detail` may be repeated. Details are `key=value` rather than one JSON
argument because the Antigravity and Codex command guards refuse any command
containing `{`, `}` or `$`. A value that reads as a JSON number, `true`,
`false` or `null` is stored as that; anything else is stored as text:

    --detail score=82 --detail qualified=true --detail status=pass

Prints one JSON status line. If the row cannot be written it exits non-zero
with `{"status": "failed", "error": "supabase_unreachable"}` — there is no
local fallback log.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = PROJECT_ROOT / ".venv"
VENV_PYTHON = VENV_DIR / "bin" / "python"

sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "supabase"))

AGENTS = ("jobFinder", "jobVerifier", "cvWriter", "appWriter", "builder", "qa",
          "submitter", "submitVerifier", "sumWriter")
# `candidate` is the builder's test print: a result that is not yet a publish.
STATUSES = ("started", "completed", "failed", "candidate")
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def write_event(sb, run_id: str, agent: str, status: str, job: str | None = None,
                details: dict | None = None) -> None:
    """Insert one event row. Raises whatever the Supabase client raises."""
    sb.table("pipeline_events").insert({
        "run_id": run_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "step": agent,
        "job": job,
        "status": status,
        "details": details or {},
    }).execute()


def short_job_id(sb, job: str) -> str | None:
    """The short job id for a database uuid, or None if no such job exists."""
    rows = sb.table("jobs").select("job_id").eq("id", job).limit(1).execute().data
    return rows[0]["job_id"] if rows else None


def posting(sb, run_id: str, job: str) -> dict:
    """Title and company of a posting, for the Office's status line."""
    rows = (sb.table("jobs").select("title,company").eq("run_id", run_id).eq("job_id", job)
            .limit(1).execute().data)
    return {k: v for k, v in (rows[0] if rows else {}).items() if v}


def parse_value(value: str):
    try:
        parsed = json.loads(value)
    except ValueError:
        return value
    return value if isinstance(parsed, (dict, list, str)) else parsed


def refuse(message: str) -> None:
    print(json.dumps({"status": "failed", "error": "bad_arguments", "message": message}))
    sys.exit(2)


def unreachable(message: str) -> None:
    print(message, file=sys.stderr)
    print(json.dumps({"status": "failed", "error": "supabase_unreachable"}))
    sys.exit(1)


def connect():
    try:
        from dotenv import load_dotenv
        from sync_config import ENV_FILE
        load_dotenv(ENV_FILE)
    except ImportError:
        pass
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        unreachable("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set (scripts/supabase/.env)")
    try:
        from sync_config import get_supabase_client
        return get_supabase_client()
    except (SystemExit, Exception) as e:  # noqa: BLE001
        unreachable(f"could not create Supabase client: {e}")


def main(argv: list[str] | None = None) -> None:
    # Agents call this with the system python3, which lacks the Supabase
    # client. Hop into the project environment; the prefix check prevents a loop.
    if argv is None and VENV_PYTHON.is_file() and Path(sys.prefix).resolve() != VENV_DIR.resolve():
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("agent")
    parser.add_argument("status")
    parser.add_argument("--job")
    parser.add_argument("--detail", action="append", default=[], metavar="key=value")
    args = parser.parse_args(argv)

    if not ID_PATTERN.match(args.run_id):
        refuse(f"run_id {args.run_id!r} is not a plain id")
    if args.agent not in AGENTS:
        refuse(f"agent must be one of: {', '.join(AGENTS)}")
    if args.status not in STATUSES:
        refuse(f"status must be one of: {', '.join(STATUSES)}")
    if args.job is not None and not ID_PATTERN.match(args.job):
        refuse(f"job {args.job!r} is not a job id")
    details = {}
    for pair in args.detail:
        key, sep, value = pair.partition("=")
        if not sep or not ID_PATTERN.match(key):
            refuse(f"--detail {pair!r} is not key=value")
        details[key] = parse_value(value)

    sb = connect()
    job = args.job
    try:
        if job and UUID_PATTERN.match(job):
            job = short_job_id(sb, job)
            if job is None:
                refuse(f"no job with id {args.job}")
        if job and (args.agent, args.status) in (("jobFinder", "completed"), ("cvWriter", "started")):
            details = {**posting(sb, args.run_id, job), **details}
        write_event(sb, args.run_id, args.agent, args.status, job, details)
    except Exception as e:  # noqa: BLE001
        unreachable(f"writing the event failed: {e}")
    print(json.dumps({"status": "ok"}))


if __name__ == "__main__":
    main()
