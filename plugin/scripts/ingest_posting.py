#!/usr/bin/env python3
"""scripts/ingest_posting.py — save one scraped job posting through the
hosted job-app server.

    python3 scripts/ingest_posting.py --token <script token> --snapshot <path> --meta <path.json>

Run by the plugin's jobFinder agent, with a token from the `script_token` tool.

--snapshot is a Playwright `browser_snapshot` accessibility-tree YAML-like
file (jobFinder writes it into its temporary folder). This script extracts
the posting's visible text from it (heading / paragraph / listitem / text /
strong nodes, scoped to the page's <main> landmark when present) and stores
it verbatim as the job's body — it does not try to parse structured fields
out of the snapshot.

--meta is a JSON file jobFinder writes beside it, with the fields that need
judgement (title, company, skills, deadline, etc. — see jobFinder.md). This
script validates them; it never invents or guesses a value the agent didn't
provide. `job_id` may be left out: the server gives the posting the run's
next free label and the result names it.

The posting goes to the server's save_postings tool, which saves the job and
records its URL as seen, as the logged-in user.

Always prints exactly one JSON status line as the last line of stdout. All
other diagnostics go to stderr.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from jobapp_api import Api, ApiError  # beside this file, in scripts/ and plugin/scripts/


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def fail(error: str, message: str = "") -> None:
    print(json.dumps({"status": "failed", "error": error, "message": message}))
    sys.exit(1)


# ---------------------------------------------------------------------------
# Playwright accessibility-tree snapshot -> plain text body
# ---------------------------------------------------------------------------
# Each line looks like (indentation = 2 spaces per nesting level):
#   - heading "Graduate 2027" [level=2] [ref=f160e88]
#   - paragraph [ref=f160e109]: Vi søker ambisiøse nyutdannede ...
#   - listitem [ref=f160e113]: Sterke analytiske evner ...
#   - strong [ref=f160e150]: "Industrier:"
#   - text: Vis mindre
# Roles like button/link/img/checkbox/combobox/status/navigation and the
# "/url: ..." lines are ignored (not in KEEP_ROLES / don't match NODE_RE).

LINE_RE = re.compile(r'^(?P<indent>\s*)- (?P<content>.*)$')
NODE_RE = re.compile(
    r'^(?P<role>[A-Za-z_]+)'
    r'(?:\s+"(?P<name>(?:[^"\\]|\\.)*)")?'
    r'(?:\s*\[[^\]]*\])*'
    r'\s*(?::\s*(?P<rest>.*))?$'
)
KEEP_ROLES = {"heading", "paragraph", "listitem", "text", "strong"}


def unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return s


def extract_body(snapshot_text: str) -> str:
    parsed = []  # (indent_len, role, name, rest)
    for raw_line in snapshot_text.split("\n"):
        m = LINE_RE.match(raw_line)
        if not m:
            continue
        node = NODE_RE.match(m.group("content"))
        if not node:
            continue
        parsed.append((len(m.group("indent")), node.group("role"), node.group("name"), node.group("rest")))

    # Scope to the <main> landmark when the snapshot has one, so nav/header/
    # filter-sidebar text doesn't leak into the posting body.
    main_idx = main_indent = None
    for idx, (indent_len, role, _name, _rest) in enumerate(parsed):
        if role == "main":
            main_idx, main_indent = idx, indent_len
            break

    if main_idx is not None:
        scoped = []
        for indent_len, role, name, rest in parsed[main_idx + 1:]:
            if indent_len <= main_indent:
                break
            scoped.append((role, name, rest))
    else:
        scoped = [(role, name, rest) for _, role, name, rest in parsed]

    lines_out: list[str] = []
    for role, name, rest in scoped:
        if role not in KEEP_ROLES:
            continue
        text = unquote(rest) if (rest is not None and rest.strip() != "") else (name or None)
        if not text:
            continue
        if role == "heading":
            lines_out.append("")
            lines_out.append(text)
        elif role == "listitem":
            lines_out.append(f"- {text}")
        else:  # paragraph, text, strong
            lines_out.append(text)

    body = "\n".join(lines_out).strip()
    return re.sub(r"\n{3,}", "\n\n", body)


# ---------------------------------------------------------------------------
# Meta validation
# ---------------------------------------------------------------------------

REQUIRED_META_FIELDS = ("run_id", "url", "title", "company")
LIST_META_FIELDS = ("required_skills", "preferred_skills", "languages_required", "languages_preferred")
OPTIONAL_SCALAR_FIELDS = ("country", "how_to_apply", "years_experience", "education")
# run_id/job_id end up in other scripts' filesystem paths (build_html.py) and
# Storage object keys — reject anything but a plain token here too, at the
# point of ingestion.
ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]+$')


def validate_meta(meta: dict) -> dict:
    missing = [f for f in REQUIRED_META_FIELDS if not meta.get(f)]
    if missing:
        fail("invalid_meta", f"meta.json missing required field(s): {', '.join(missing)}")

    for id_field in ("run_id", "job_id"):
        value = meta.get(id_field)
        if value is None and id_field not in REQUIRED_META_FIELDS:
            continue
        if not ID_PATTERN.match(str(value)):
            fail(
                "invalid_meta",
                f"meta.json '{id_field}'={value!r} must match ^[A-Za-z0-9_-]+$ "
                "(no slashes, dots, or other path characters)",
            )

    for field in LIST_META_FIELDS:
        value = meta.get(field)
        if value is None:
            meta[field] = []
        elif not isinstance(value, list):
            fail("invalid_meta", f"meta.json field '{field}' must be a JSON array, got {type(value).__name__}")

    deadline = meta.get("deadline")
    if deadline is not None:
        try:
            date.fromisoformat(str(deadline))
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(deadline)):
                raise ValueError("not YYYY-MM-DD")
        except ValueError:
            fail(
                "invalid_meta",
                f"meta.json 'deadline' must be YYYY-MM-DD or null, got {deadline!r} — "
                "never guess a date (jobFinder.md rule); resolve it or pass null",
            )

    if not meta.get("application_url"):
        meta["application_url"] = meta["url"]

    for field in OPTIONAL_SCALAR_FIELDS:
        meta.setdefault(field, None)
        if meta[field] is not None and not isinstance(meta[field], str):
            meta[field] = str(meta[field])
    for field in LIST_META_FIELDS:
        meta[field] = [str(item) for item in meta[field]]

    return meta


def save(token: str, meta: dict, body: str) -> None:
    """Send the posting to the hosted server's save_postings tool."""
    posting = {k: meta.get(k) for k in (
        "url", "title", "company", "country", "deadline", "application_url", "how_to_apply",
        "required_skills", "preferred_skills", "years_experience", "education",
        "languages_required", "languages_preferred")}
    if meta.get("job_id"):
        posting["job_id"] = meta["job_id"]
    posting["body"] = body
    try:
        result = Api(token).call("save_postings", {"run_id": meta["run_id"], "postings": [posting]})
    except ApiError as e:
        fail(e.error, e.message)
        return
    saved = result["saved"][0]
    print(json.dumps({
        "status": "ok", "run_id": meta["run_id"], "job_id": saved["job_id"], "body_length": len(body),
        "jobs": {"action": saved["jobs"]}, "seen_jobs": saved["seen_jobs"],
    }))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, help="path to a Playwright browser_snapshot YAML file")
    parser.add_argument("--meta", required=True, help="path to a JSON file with the judgement fields (see jobFinder.md)")
    parser.add_argument("--token", required=True, help="a job-app script token, from the script_token tool")
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot)
    meta_path = Path(args.meta)

    if not snapshot_path.is_file():
        fail("snapshot_not_found", f"no such file: {snapshot_path}")
        return
    if not meta_path.is_file():
        fail("meta_not_found", f"no such file: {meta_path}")
        return

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        fail("invalid_meta", f"meta.json is not valid JSON: {e}")
        return

    meta = validate_meta(meta)
    body = extract_body(snapshot_path.read_text(encoding="utf-8"))
    if not body:
        log("WARNING: extracted body is empty — snapshot may not contain a <main> landmark or the expected node roles")
    save(args.token, meta, body)


if __name__ == "__main__":
    main()
