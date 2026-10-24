#!/usr/bin/env python3
"""scripts/ingest_posting.py — turn a scraped job posting into Supabase rows.

Replaces jobFinder's former manual procedure (grep a Playwright snapshot by
hand, hand-build an INSERT ... VALUES SQL string) with one script call:

    python3 scripts/ingest_posting.py --snapshot <path> --meta <path.json> [--dry-run]

--snapshot is a Playwright `browser_snapshot` accessibility-tree YAML-like
file (jobFinder writes it into tmp_dir, never the repo). This script extracts
the posting's visible text from it (heading / paragraph / listitem / text /
strong nodes, scoped to the page's <main> landmark when present) and stores
it verbatim in jobs.body — it does not try to parse structured fields out of
the snapshot.

--meta is a JSON file jobFinder writes into tmp_dir containing the fields
that require judgement (title, company, skills, deadline, etc. — see
jobFinder.md and the `jobs` table columns). This script validates and writes
them; it never invents or guesses a value the agent didn't provide.

Writes: upsert `jobs` on (run_id, job_id) — insert, or on conflict update
only title/body/scraped_at (mirrors jobFinder.md's literal ON CONFLICT
clause); upsert `seen_jobs` on url — insert, or on conflict bump
times_seen/last_evaluated_at only. All writes go through the Supabase client
query builder — no hand-built SQL strings anywhere.

--dry-run prints the extracted body length + first 300 chars and the exact
rows that would be written; it makes no DB calls at all.

--token <script token> is the plugin's mode: instead of the service key, the
posting goes to the hosted server's save_postings tool, which writes both rows
as the logged-in user. There `job_id` may be left out of the meta file; the
server gives the posting the run's next free label and the result names it.

Always prints exactly one JSON status line as the last line of stdout. All
other diagnostics go to stderr.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "supabase"))


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def fail(error: str, message: str = "") -> None:
    print(json.dumps({"status": "failed", "error": error, "message": message}))
    sys.exit(1)


def classify_error(e: Exception) -> tuple[str, str]:
    """Distinguish connectivity failures (retry-worthy, network/DNS/TLS) from
    a genuine Postgres/PostgREST error (FK violation, check constraint, bad
    column, ...) from anything unanticipated, instead of collapsing every
    exception into 'supabase_unreachable'."""
    try:
        import httpx
        if isinstance(e, (httpx.NetworkError, httpx.TimeoutException)):
            return "supabase_unreachable", str(e)
    except ImportError:
        pass
    if isinstance(e, (ConnectionError, OSError)):
        return "supabase_unreachable", str(e)
    try:
        from postgrest.exceptions import APIError
        if isinstance(e, APIError):
            code, message = e.code, (e.message or str(e))
            return "db_error", f"{code}: {message}" if code else message
    except ImportError:
        pass
    return "internal_error", f"{type(e).__name__}: {e}"


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

REQUIRED_META_FIELDS = ("run_id", "job_id", "url", "title", "company")
LIST_META_FIELDS = ("required_skills", "preferred_skills", "languages_required", "languages_preferred")
OPTIONAL_SCALAR_FIELDS = ("country", "how_to_apply", "years_experience", "education")
# run_id/job_id are used as-is in a (run_id, job_id) upsert key and end up in
# other scripts' filesystem paths (build_html.py) and Storage object keys —
# reject anything but a plain token here too, at the point of ingestion.
ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]+$')


def validate_meta(meta: dict, hosted: bool = False) -> dict:
    required = [f for f in REQUIRED_META_FIELDS if not (hosted and f == "job_id")]
    missing = [f for f in required if not meta.get(f)]
    if missing:
        fail("invalid_meta", f"meta.json missing required field(s): {', '.join(missing)}")

    for id_field in ("run_id", "job_id"):
        value = meta.get(id_field)
        if value is None and id_field not in required:
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


def build_jobs_row(meta: dict, body: str, scraped_at: str) -> dict:
    return {
        "run_id": meta["run_id"],
        "job_id": meta.get("job_id"),
        "title": meta["title"],
        "company": meta["company"],
        "url": meta["url"],
        "country": meta.get("country"),
        "deadline": meta.get("deadline"),
        "application_url": meta["application_url"],
        "how_to_apply": meta.get("how_to_apply"),
        "required_skills": meta["required_skills"],
        "preferred_skills": meta["preferred_skills"],
        "years_experience": meta.get("years_experience"),
        "education": meta.get("education"),
        "languages_required": meta["languages_required"],
        "languages_preferred": meta["languages_preferred"],
        "scraped_at": scraped_at,
        "body": body,
    }


def _is_conflict_error(e: Exception) -> bool:
    msg = str(e)
    return "23505" in msg or "duplicate key" in msg.lower()


def write_job(sb, row: dict) -> dict:
    """Insert; on a (run_id, job_id) conflict, update only title/body/
    scraped_at — mirrors jobFinder.md's literal
    `ON CONFLICT (run_id, job_id) DO UPDATE SET title=..., body=..., scraped_at=...`
    (a full-column upsert would also silently overwrite the other structured
    fields, which the original SQL deliberately left alone on conflict)."""
    try:
        res = sb.table("jobs").insert(row).execute()
        return {"action": "inserted", "id": (res.data[0]["id"] if res.data else None)}
    except Exception as e:  # noqa: BLE001
        if _is_conflict_error(e):
            sb.table("jobs").update(
                {"title": row["title"], "body": row["body"], "scraped_at": row["scraped_at"]}
            ).eq("run_id", row["run_id"]).eq("job_id", row["job_id"]).execute()
            return {"action": "updated_on_conflict"}
        raise


def write_seen_job(sb, url: str, title: str, company: str, country, now_iso: str, max_attempts: int = 8) -> dict:
    """Upsert on url. Mirrors jobFinder.md's
    `ON CONFLICT (url) DO UPDATE SET times_seen = seen_jobs.times_seen + 1,
    last_evaluated_at = now()`.

    supabase/migrations/20260725000000_parallel_runs.sql allows several
    pipeline runs concurrently, so two jobFinder instances can race on the
    same URL — a plain read-then-write here could under-count `times_seen`.
    There is no existing RPC for an atomic increment, and the task avoids
    hand-built SQL strings, so this uses optimistic concurrency instead: the
    UPDATE is conditioned on `times_seen` still being what we just read
    (`.eq("times_seen", old)`), entirely through the query-builder (no SQL
    string). If another writer updated the row first, Postgrest returns zero
    matched rows and we re-read and retry, bounded by `max_attempts`. This is
    genuinely race-safe for the update path (compare-and-swap), not just
    'less racy' — the only remaining gap is the INSERT branch (first-ever
    sighting of a URL), where a duplicate-key conflict from a concurrent
    insert is handled by falling through to the same CAS loop.
    """
    for _attempt in range(max_attempts):
        from sync_config import owner_filter
        existing = owner_filter(sb.table("seen_jobs").select("times_seen").eq("url", url), sb).execute().data
        if not existing:
            try:
                sb.table("seen_jobs").insert(
                    {
                        "url": url,
                        "title": title,
                        "company": company,
                        "country": country,
                        "first_seen_at": now_iso,
                        "last_evaluated_at": now_iso,
                        "times_seen": 1,
                    }
                ).execute()
                return {"action": "inserted", "times_seen": 1}
            except Exception as e:  # noqa: BLE001
                if _is_conflict_error(e):
                    continue  # someone else inserted first; retry as an update
                raise
        else:
            old_times_seen = existing[0].get("times_seen") or 1
            new_times_seen = old_times_seen + 1
            res = (
                owner_filter(sb.table("seen_jobs")
                             .update({"last_evaluated_at": now_iso, "times_seen": new_times_seen})
                             .eq("url", url), sb)
                .eq("times_seen", old_times_seen)  # compare-and-swap guard
                .execute()
            )
            if res.data:
                return {"action": "updated", "times_seen": new_times_seen}
            # times_seen changed under us since the read above; retry.
    raise RuntimeError(f"write_seen_job: gave up after {max_attempts} attempts racing a concurrent writer on url={url!r}")


def save_hosted(token: str, meta: dict, body: str) -> None:
    """Send the posting to the hosted server's save_postings tool."""
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from jobapp_api import Api, ApiError

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
    parser.add_argument("--dry-run", action="store_true", help="print what would be written; make no DB calls")
    parser.add_argument("--token", help="a job-app script token: save through the hosted server (the plugin)")
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

    meta = validate_meta(meta, hosted=bool(args.token))
    body = extract_body(snapshot_path.read_text(encoding="utf-8"))
    if not body:
        log("WARNING: extracted body is empty — snapshot may not contain a <main> landmark or the expected node roles")

    now_iso = datetime.now(timezone.utc).isoformat()
    jobs_row = build_jobs_row(meta, body, now_iso)

    if args.dry_run:
        result = {
            "status": "ok",
            "dry_run": True,
            "body_length": len(body),
            "body_preview": body[:300],
            "would_write": {
                "jobs": jobs_row,
                "seen_jobs": {
                    "url": meta["url"],
                    "title": meta["title"],
                    "company": meta["company"],
                    "country": meta.get("country"),
                    "first_seen_at_or_bump": now_iso,
                },
            },
        }
        log(f"[dry-run] extracted body length: {len(body)}")
        log(f"[dry-run] body preview: {body[:300]!r}")
        print(json.dumps(result, default=str))
        return

    if args.token:
        save_hosted(args.token, meta, body)
        return

    try:
        from sync_config import get_supabase_client
        sb = get_supabase_client()
    except SystemExit:
        fail("supabase_unreachable", "could not create Supabase client")
        return

    try:
        job_result = write_job(sb, jobs_row)
        seen_result = write_seen_job(sb, meta["url"], meta["title"], meta["company"], meta.get("country"), now_iso)
    except Exception as e:  # noqa: BLE001
        error, message = classify_error(e)
        fail(error, f"write failed: {message}")
        return

    print(
        json.dumps(
            {
                "status": "ok",
                "run_id": meta["run_id"],
                "job_id": meta.get("job_id"),
                "body_length": len(body),
                "jobs": job_result,
                "seen_jobs": seen_result,
            }
        )
    )


if __name__ == "__main__":
    main()
