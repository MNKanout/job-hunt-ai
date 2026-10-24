#!/usr/bin/env python3
"""scripts/build_html.py — render CV + cover letter HTML for one job.

Two modes, always explicit — there is no default, so nothing can accidentally
publish an unapproved document:

    python3 scripts/build_html.py <run_id> <job_id> --candidate [--dry-run]
    python3 scripts/build_html.py <run_id> <job_id> --publish
    python3 scripts/build_html.py <run_id> <job_id> --pdf
    python3 scripts/build_html.py <run_id> <job_id> --discard-pdfs

Every mode also takes `--token <script token>`: the Claude Code plugin's way
in. The data then comes from, and the events and the publish go to, the
hosted job-app server as the logged-in user (see scripts/jobapp_api.py), the
build directory is job-app/builds in the working folder (JOBAPP_HOME overrides
job-app), and the
name and photo are the user's own. The server repeats the publish gate in
the database before anything is uploaded.

--candidate renders the CV + cover letter from the latest Supabase content,
saves the photo, prints both exact saved documents through headless Chrome, and
returns structured evidence (content hashes + print measurements). It never
uploads, never sets `jobs.cv_built`/`cover_letter_built`, and never logs a
`completed` pipeline_events row — it logs a `candidate` row instead, with the
evidence in `details`, so QA can read the real print measurements from the
database rather than trusting a hand-typed summary. `--dry-run` (temp dir
outside the repo, no DB writes) only makes sense together with `--candidate`.

--candidate and --publish are the builder's two tasks, and each reports itself
to `pipeline_events` through scripts/log_event.py: `started` first, then one
result — `candidate`, `completed`, or `failed`. Once `started` is written,
every refusal and handled error writes `failed` before exiting, so the Office
never shows a builder that is still working after it gave up. A candidate
whose printing failed logs both: the `candidate` row keeps the evidence for
QA, and the `failed` row after it is the outcome. --pdf and --discard-pdfs
are errands for the submitter and log nothing.

--publish requires the latest `qa_results` row for this job to be `pass` (or
`warn` with no `severity: fail` issue) AND carry non-null `build_evidence`.
It re-renders the exact same content from Supabase, hashes it, and requires
an EXACT match against `build_evidence`'s hashes before touching anything —
a stale QA row, or any CV/letter/photo change since QA ran, refuses to
publish. It then re-runs the print gate on what it's about to publish and
requires it to pass with the same measurements QA saw (not just "still
under the limit" — any drift is treated as a change). Only then does it
upload to Storage (a failed upload is fatal — it does not mark the job
built) and flip `jobs.cv_built`/`cover_letter_built`.

--pdf prints an already-published build to `<Name> - <Role> - <Company> - CV.pdf`
and `... - Cover Letter.pdf`
beside the local HTML, for the moment an application is actually sent. PDFs
are never uploaded to Storage: the HTML is the stored artifact and a PDF can
always be printed from it again. --pdf runs the same checks as --publish
(QA approval, exact hashes, print gate, same measurements) but uploads
nothing and changes no database row. --discard-pdfs removes the PDFs again
once the application is sent. A candidate, a publish, or a refused --pdf
also leaves no PDF behind, so a PDF on disk always matches approved HTML.

Hash-determinism note: the Jinja templates under scripts/templates/ render no
timestamps or other run-varying data — output HTML is a pure function of
(tailored_cvs.content, cover_letters.content, jobs.title, jobs.company, the
template files, sources/cv/profile-photo.jpg). That's what makes "re-render
from Supabase and compare hashes" a correct freshness check instead of a
best-effort one: the SAME database content always renders to byte-identical
HTML, so a hash match proves the content is unchanged and a mismatch proves
it isn't. --publish relies on this rather than trusting on-disk candidate
files, which may have been overwritten or deleted between candidate and
publish.

Both modes write builds/<slug>-<date>/{cv.html,cover_letter.html,
profile-photo.jpg,APPROVAL.json}. APPROVAL.json's `state`/`approved` fields
distinguish an unapproved candidate from a QA-approved, published build —
candidate and publish for the SAME job share this directory (see
resolve_build_dir()), so publishing simply flips the label on the exact
bytes QA reviewed.

Always prints exactly one JSON status line as the last line of stdout,
regardless of outcome. All other diagnostics go to stderr.
"""
from __future__ import annotations

import argparse
import html
import hashlib
import math
import json
import os
import re
import shutil
import sys
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import print_script  # beside this file, in scripts/ and plugin/scripts/

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = PROJECT_ROOT / ".venv"
VENV_PYTHON = VENV_DIR / "bin" / "python"
TEMPLATES_DIR = PROJECT_ROOT / "scripts" / "templates"
BUILDS_DIR = PROJECT_ROOT / "builds"
PROFILE_PHOTO = PROJECT_ROOT / "sources" / "cv" / "profile-photo.jpg"
# The owner's name and contact line for the local pipeline, from
# config/owner.json (gitignored; config/owner.example.json shows the shape).
# Hosted users' builds take theirs from their profile (/job-hunt-ai:setup); see
# contact_rows() and the --token path in main().
OWNER_FILE = PROJECT_ROOT / "config" / "owner.json"


def _load_owner() -> tuple[str, dict]:
    try:
        data = json.loads(OWNER_FILE.read_text())
    except (OSError, ValueError):
        return "", {}
    return str(data.get("name") or ""), dict(data.get("contact") or {})


NAME, CONTACT = _load_owner()
STORAGE_BUCKET = "pipeline-artifacts"

sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "supabase"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from log_event import write_event as _write_db_event  # noqa: E402


class Hosted:
    """The plugin's data source: the hosted job-app server, acting as the
    user whose script token it holds. Passed where the Supabase client goes."""

    def __init__(self, api):
        self.api = api


def write_event(sb, run_id: str, agent: str, status: str, job: str | None = None,
                details: dict | None = None) -> None:
    """One pipeline_events row, through the server or straight to Supabase."""
    if isinstance(sb, Hosted):
        payload = {"run_id": run_id, "agent": agent, "status": status, "details": details or {}}
        if job:
            payload["job"] = job
        sb.api.call("log_event", payload)
        return
    _write_db_event(sb, run_id, agent, status, job, details)

# run_id/job_id become directory names (builds/<slug>-<date>[-<job_id>]/) and
# Storage object-key segments (<run_id>/<job_id>/cv.html). Neither may contain
# path separators or traversal sequences — validated before either is used
# for anything filesystem- or Storage-related.
ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]+$')
# A4 is 297 mm tall. At most 45 mm below the last printed word keeps each
# CV page and the single cover-letter page reasonably full without filler.
MAX_BOTTOM_BLANK_MM = 45.0
MM_PER_PT = 25.4 / 72
# Word bottoms closer than this (pt) are on the same printed line.
LINE_MERGE_PT = 2.0


def _chrome_path() -> str | None:
    for candidate in (shutil.which("google-chrome"), shutil.which("chrome"), shutil.which("chromium"),
                      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                      "/Applications/Chromium.app/Contents/MacOS/Chromium"):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def _page_lines(pdf_path: Path, expected_pages: int) -> list[tuple[float, list[float]]]:
    """Each printed page's height and the bottom of each of its text lines, in pt."""
    bbox = subprocess.run(["pdftotext", "-bbox", str(pdf_path), "-"],
                          capture_output=True, text=True, timeout=20)
    if bbox.returncode != 0:
        raise RuntimeError(f"pdftotext -bbox failed: {bbox.stderr.strip()[-1200:]}")
    try:
        root = ET.fromstring(bbox.stdout)
        pages = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "page"]
        if len(pages) != expected_pages:
            raise ValueError(f"bbox found {len(pages)} pages, pdfinfo found {expected_pages}")
        result = []
        for page in pages:
            height = float(page.attrib["height"])
            bottoms = sorted(float(node.attrib["yMax"]) for node in page.iter()
                             if node.tag.rsplit("}", 1)[-1] == "word")
            if height <= 0 or not 0 <= (bottoms[-1] if bottoms else 0.0) <= height + 0.5:
                raise ValueError("invalid page or word bounds in bbox output")
            # Words on one printed line share a bottom edge to within a
            # point or so (bold and regular glyphs differ slightly); a new
            # line is a whole line pitch (~14 pt for CV body text) lower.
            lines: list[float] = []
            for b in bottoms:
                if lines and b - lines[-1] <= LINE_MERGE_PT:
                    lines[-1] = b
                else:
                    lines.append(b)
            result.append((height, lines))
        return result
    except (ET.ParseError, KeyError, ValueError) as e:
        raise RuntimeError(f"could not measure printed page whitespace: {e}") from e


def _page_bottom_blank_mm(page_lines: list[tuple[float, list[float]]]) -> list[float]:
    """Measure the distance from each page's last printed word to its edge."""
    return [round(max(0.0, height - (lines[-1] if lines else 0.0)) * 25.4 / 72, 1)
            for height, lines in page_lines]


def fit_window(page_lines: list[tuple[float, list[float]]], blanks_mm: list[float],
               max_pages: int) -> dict | None:
    """Turn a page-count or bottom-whitespace failure into printed lines.

    Words do not map to page height: text only grows when it wraps onto a
    new line, so a writer told "add 20 words" may add zero lines or three.
    This reports, from the measured PDF, how many whole lines must be added
    or removed to pass and roughly how many can be before the change breaks
    the other way. It describes the measured candidate only; lines move
    between pages unevenly (a bullet is never split across pages), so the
    next candidate must still be rebuilt and measured.

    Returns None when the document passes or its line pitch can't be measured.
    """
    gaps = sorted(b - a for _, lines in page_lines for a, b in zip(lines, lines[1:]))
    pages = len(page_lines)
    over_limit = pages > max_pages
    sparse = [(n, blank) for n, blank in enumerate(blanks_mm, start=1) if blank > MAX_BOTTOM_BLANK_MM]
    if not gaps or not (over_limit or sparse):
        return None
    pitch_mm = gaps[len(gaps) // 2] * MM_PER_PT
    changes = []
    if over_limit:
        overflow = max(1, sum(len(lines) for _, lines in page_lines[max_pages:]))
        room = max(0.0, MAX_BOTTOM_BLANK_MM - blanks_mm[max_pages - 1])
        most = overflow + math.floor(room / pitch_mm)
        changes.append({"action": "remove", "page": None, "min_lines": overflow, "max_lines": most,
                        "summary": f"remove at least {overflow} and at most about {most} printed lines "
                                   f"so the document fits on {max_pages} page{'s' if max_pages > 1 else ''}"})
    else:
        for page, blank in sparse:
            fewest = math.ceil((blank - MAX_BOTTOM_BLANK_MM) / pitch_mm)
            most = max(fewest, math.floor(blank / pitch_mm) - 1)
            changes.append({"action": "add", "page": page, "min_lines": fewest, "max_lines": most,
                            "summary": f"page {page} needs at least {fewest} and at most about {most} "
                                       f"more printed lines"})
    return {"line_pitch_mm": round(pitch_mm, 1), "changes": changes}


def print_preflight(html_path: Path, pdf_path: Path, max_pages: int = 2) -> dict:
    """Print the exact saved HTML through headless Chrome and measure it.

    Returns visible_words (informational), printed_pages, page_size,
    is_a4_portrait, per-page bottom whitespace in mm, and rule_violations
    (a list of human-readable problem strings, empty when the page is clean).
    A rule violation does NOT raise
    — it's a real measurement of a document that fails the gate, and
    candidate mode needs that measurement (not an exception) so QA can see
    the actual numbers. Only a failure to produce a *trustworthy*
    measurement raises RuntimeError: Chrome/pdfinfo/pdftotext failing, or
    the saved file mutating mid-check.
    """
    checked_bytes = html_path.read_bytes()
    chrome_print(html_path, pdf_path)
    return measure_pdf(html_path, pdf_path, checked_bytes, max_pages)


def chrome_print(html_path: Path, pdf_path: Path) -> None:
    """Print the saved HTML to `pdf_path` with headless Chrome."""
    chrome = _chrome_path()
    if not chrome:
        raise RuntimeError("Chrome is required for the print preflight")
    run = subprocess.run([chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                          f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()],
                         capture_output=True, text=True, timeout=90)
    if run.returncode != 0 or not pdf_path.is_file():
        raise RuntimeError(f"Chrome print failed (exit {run.returncode}): {(run.stderr or run.stdout).strip()[-1200:]}")


def measure_pdf(html_path: Path, pdf_path: Path, checked_bytes: bytes, max_pages: int = 2) -> dict:
    """Measure a PDF printed from `html_path`, whose bytes were
    `checked_bytes` when the print started. See print_preflight."""
    info = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True, text=True, timeout=20)
    if info.returncode != 0:
        raise RuntimeError(f"pdfinfo failed: {info.stderr.strip()[-1200:]}")
    fields = dict(re.findall(r"^(Pages|Page size):\s*(.+)$", info.stdout, re.M))
    pages, page_size = int(fields.get("Pages", "0")), fields.get("Page size", "")
    extracted = subprocess.run(["pdftotext", "-layout", str(pdf_path), "-"], capture_output=True, text=True, timeout=20)
    if extracted.returncode != 0:
        raise RuntimeError(f"pdftotext failed: {extracted.stderr.strip()[-1200:]}")
    # Count whitespace-delimited tokens from the actual printed page. This
    # remains informational; page fullness is measured from PDF geometry.
    words = len(extracted.stdout.split())
    page_lines = _page_lines(pdf_path, pages)
    page_bottom_blank_mm = _page_bottom_blank_mm(page_lines)
    dimensions = re.search(r"([\d.]+) x ([\d.]+) pts", page_size)
    # ISO A4 portrait is 595.28 x 841.89 pt (width < height). Chrome/pdfinfo
    # may round a fraction of a point, but a nearby custom size, or the same
    # A4 dimensions swapped into landscape, must not pass as A4 portrait.
    is_a4_portrait = bool(
        dimensions
        and abs(float(dimensions.group(1)) - 595.28) <= 0.5
        and abs(float(dimensions.group(2)) - 841.89) <= 0.5
        and float(dimensions.group(1)) < float(dimensions.group(2))
    )
    if html_path.read_bytes() != checked_bytes:
        raise RuntimeError("saved HTML changed during print preflight")
    problems = []
    if not 1 <= pages <= max_pages:
        limit = "1 page" if max_pages == 1 else f"1 to {max_pages} pages"
        problems.append(f"printed_pages={pages}, must be {limit}")
    for page_number, blank_mm in enumerate(page_bottom_blank_mm, start=1):
        if blank_mm > MAX_BOTTOM_BLANK_MM:
            problems.append(f"page {page_number} bottom_blank_mm={blank_mm:.1f}, maximum {MAX_BOTTOM_BLANK_MM:.1f} mm")
    if not is_a4_portrait:
        problems.append(f"page_size={page_size!r}, A4 portrait required")
    evidence = {
        "visible_words": words,
        "page_bottom_blank_mm": page_bottom_blank_mm,
        "max_bottom_blank_mm": MAX_BOTTOM_BLANK_MM,
        "printed_pages": pages,
        "page_size": page_size,
        "is_a4_portrait": is_a4_portrait,
        "rule_violations": problems,
    }
    # Only a failing print carries `fit`, so passing evidence keeps the exact
    # shape QA approved before this field existed (publish compares it).
    fit = fit_window(page_lines, page_bottom_blank_mm, max_pages)
    if fit is not None:
        evidence["fit"] = fit
    return evidence


def measure_print(html_path: Path, max_pages: int = 2, keep_pdf: Path | None = None) -> dict:
    """Run print_preflight without ever raising: a tool failure becomes
    `tool_errors` in the same evidence shape a clean measurement has (with
    the measured fields null and `rule_violations` empty), so callers can
    log or compare evidence uniformly regardless of what happened.

    `keep_pdf` (--pdf only) moves the measured PDF there instead of deleting
    it, so the file sent with an application is the very one the gate
    measured. The caller owns removing it if a later check refuses."""
    if PRINTER == "playwright":
        return _measure_cached_print(html_path, max_pages, keep_pdf)
    try:
        with tempfile.NamedTemporaryFile(prefix="job_app_print_preflight_", suffix=".pdf", delete=False) as tmp_pdf:
            pdf_path = Path(tmp_pdf.name)
    except OSError as e:
        return {"visible_words": None, "page_bottom_blank_mm": None, "max_bottom_blank_mm": MAX_BOTTOM_BLANK_MM, "printed_pages": None,
                "page_size": None, "is_a4_portrait": None, "rule_violations": [], "tool_errors": [str(e)]}
    try:
        evidence = print_preflight(html_path, pdf_path, max_pages=max_pages)
        if keep_pdf is not None:
            shutil.move(str(pdf_path), str(keep_pdf))
        evidence["tool_errors"] = []
        return evidence
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as e:
        return {"visible_words": None, "page_bottom_blank_mm": None, "max_bottom_blank_mm": MAX_BOTTOM_BLANK_MM, "printed_pages": None,
                "page_size": None, "is_a4_portrait": None, "rule_violations": [], "tool_errors": [str(e)]}
    finally:
        try:
            pdf_path.unlink(missing_ok=True)
        except OSError as e:
            log(f"WARNING: could not remove temporary print file {pdf_path}: {e}")


# How this process prints. "chrome" runs headless Chrome itself (the local
# pipeline). "playwright" is the plugin's: Claude Code's sandbox can refuse
# Chrome started from a script, so the agent prints through the Playwright
# MCP server instead, which runs outside the sandbox. The script names the
# files to print and stops with `needs_print`; the agent runs the print
# script it wrote, then runs the same command again, which finds the PDFs.
PRINTER = "chrome"
PRINT_CACHE: Path | None = None
# Part of every cache key, so a change to how PDFs are printed reprints
# instead of reusing PDFs made the old way.
PRINT_SETTINGS = (print_script.PREFIX + print_script.SUFFIX).encode()
_pending_prints: list[dict] = []


def _no_measurement(errors: list[str]) -> dict:
    return {"visible_words": None, "page_bottom_blank_mm": None, "max_bottom_blank_mm": MAX_BOTTOM_BLANK_MM,
            "printed_pages": None, "page_size": None, "is_a4_portrait": None, "rule_violations": [],
            "tool_errors": errors}


def cached_pdf_path(html_path: Path, html_bytes: bytes) -> Path:
    """Where the PDF of exactly these bytes is kept. The key covers all the
    page loads from disk: the HTML and the photo beside it (the fonts come
    from the web for both printers)."""
    digest = hashlib.sha256(PRINT_SETTINGS)
    digest.update(hashlib.sha256(html_bytes).digest())
    photo = html_path.parent / "profile-photo.jpg"
    digest.update(hashlib.sha256(photo.read_bytes()).digest() if photo.is_file() else b"no photo")
    return PRINT_CACHE / f"{digest.hexdigest()}.pdf"


def _measure_cached_print(html_path: Path, max_pages: int, keep_pdf: Path | None) -> dict:
    """measure_print for the plugin: measure the PDF the agent printed from
    these exact bytes, or note that it must be printed first."""
    try:
        checked_bytes = html_path.read_bytes()
        cached = cached_pdf_path(html_path, checked_bytes)
        if not cached.is_file():
            _pending_prints.append({"html": html_path.resolve().as_uri(), "pdf": str(cached)})
            return _no_measurement([])
        try:
            evidence = measure_pdf(html_path, cached, checked_bytes, max_pages)
        except RuntimeError:
            # A broken print (cut short, say) must be printed again, not
            # measured again.
            cached.unlink(missing_ok=True)
            raise
        if keep_pdf is not None:
            shutil.copyfile(cached, keep_pdf)
        evidence["tool_errors"] = []
        return evidence
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as e:
        return _no_measurement([str(e)])


def request_pending_prints() -> None:
    """If a measurement is waiting for a print, stop here and hand the agent
    the print script. Called before anything is written or logged as a
    result, so the same command can simply run again afterwards."""
    if not _pending_prints:
        return
    code_file = PRINT_CACHE / f"print-{uuid.uuid4().hex}.js"
    partial = code_file.with_suffix(".part")
    try:
        partial.write_text(print_script.render(_pending_prints), encoding="utf-8")
        partial.replace(code_file)
    except OSError as e:
        fail("io_error", f"could not write the print script {code_file}: {e}")
        return
    # Not a failure: the build carries on once the agent has printed.
    end_task()
    print(json.dumps({"status": "needs_print", "print_code_file": str(code_file), "prints": _pending_prints,
                      "message": "Run print_code_file with the Playwright run-code tool, then run this command again."}))
    sys.exit(0)


def discard_cached_prints(build_dir: Path) -> None:
    """Remove the cached PDFs of a build's current HTML: they carry the CV."""
    if PRINT_CACHE is None:
        return
    for name in ("cv.html", "cover_letter.html"):
        html_path = build_dir / name
        try:
            if html_path.is_file():
                cached_pdf_path(html_path, html_path.read_bytes()).unlink(missing_ok=True)
        except OSError as e:
            log(f"WARNING: could not remove the cached print of {html_path}: {e}")


def prune_print_cache() -> None:
    """Old prints and spent print scripts go: a PDF after two weeks, a
    script after a day."""
    now = datetime.now(timezone.utc).timestamp()
    for pattern, max_age in (("*.pdf", 14 * 86400), ("print-*.js", 86400)):
        for path in PRINT_CACHE.glob(pattern):
            try:
                if now - path.stat().st_mtime > max_age:
                    path.unlink()
            except OSError:
                pass


def hash_evidence(cv_bytes: bytes, letter_bytes: bytes, photo_bytes: bytes | None) -> dict:
    """The one shared hash computation candidate and publish both use, so a
    hash mismatch can never come from two different implementations
    disagreeing about how to hash the same bytes."""
    return {
        "cv_sha256": hashlib.sha256(cv_bytes).hexdigest(),
        "letter_sha256": hashlib.sha256(letter_bytes).hexdigest(),
        "photo_sha256": hashlib.sha256(photo_bytes).hexdigest() if photo_bytes is not None else "absent",
    }


def validate_id(value: str, label: str) -> None:
    if not ID_PATTERN.match(value):
        fail("invalid_id", f"{label}={value!r} must match ^[A-Za-z0-9_-]+$ (no slashes, dots, or other path characters)")


def _ensure_within(path: Path, root: Path, label: str) -> Path:
    """Resolve `path` and refuse to proceed if it escapes `root` (defense in
    depth alongside validate_id — belt and suspenders, not either/or)."""
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        fail("path_escape", f"{label} {resolved_path} is outside {resolved_root}")
    return resolved_path


def resolve_build_dir(job_id: str, job_uuid: str, slug: str, date_str: str) -> Path:
    """Pick the on-disk build directory for a (non-dry-run) build.

    Candidate and publish for the SAME job must land in the SAME directory:
    publish overwrites the candidate's unapproved cv.html/cover_letter.html
    with the byte-identical content it just hash-verified, then flips
    APPROVAL.json from candidate to approved in place, rather than creating
    a sibling directory. A directory is reused only when its own
    APPROVAL.json names this exact job by `job_uuid` (the `jobs.id` FK —
    stable even if the run-scoped text label `job_id`, e.g. "job-04", is
    reused for an unrelated job in a later run). A same-day build for a
    genuinely different job with the same title still falls back to the
    original job_id-suffixed directory, same as before this feature existed.
    """
    base_dir = BUILDS_DIR / f"{slug}-{date_str}"
    if base_dir.exists():
        owner = None
        approval_path = base_dir / "APPROVAL.json"
        if approval_path.is_file():
            try:
                owner = json.loads(approval_path.read_text(encoding="utf-8")).get("job_uuid")
            except (OSError, ValueError):
                owner = None
        if owner != job_uuid:
            base_dir = BUILDS_DIR / f"{slug}-{date_str}-{job_id}"
    return base_dir


def find_build_dir(job_uuid: str) -> Path | None:
    """The newest existing build directory whose APPROVAL.json names this
    job. --pdf may run days after the publish, so it can't rebuild the
    dated directory name the way resolve_build_dir() does."""
    owned = []
    for approval_path in BUILDS_DIR.glob("*/APPROVAL.json"):
        try:
            if json.loads(approval_path.read_text(encoding="utf-8")).get("job_uuid") == job_uuid:
                owned.append(approval_path)
        except (OSError, ValueError):
            continue
    return max(owned, key=lambda path: path.stat().st_mtime).parent if owned else None


def write_approval_json(out_dir: Path, *, approved: bool, evidence: dict, run_id: str, job_id: str,
                         job_uuid: str, qa_version: int | None = None) -> None:
    payload = {
        "state": "approved" if approved else "candidate",
        "approved": approved,
        "note": ("Approved by QA — matches the exact candidate build QA reviewed."
                 if approved else
                 "NOT approved — do not use to apply. Run qa on this candidate, then "
                 "build_html.py --publish once it passes."),
        "run_id": run_id,
        "job_id": job_id,
        "job_uuid": job_uuid,
        "evidence": evidence,
    }
    if qa_version is not None:
        payload["qa_version"] = qa_version
    (out_dir / "APPROVAL.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


# Characters a file name may not hold on macOS, Windows or Linux.
UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def document_name(title: str, company: str, kind: str) -> str:
    """What an application document is called wherever someone sees it: the
    PDF file an employer receives and the page title a browser suggests when
    printing. "Kari Nordmann - Deployment Strategist - Acme - CV": a
    reader's download folder of "cv.pdf" files says nothing about whose CV
    it is or which job it was for."""
    parts = [NAME, plain_role_title(title or ""), company or "", kind]
    cleaned = [" ".join(UNSAFE_FILENAME_RE.sub(" ", p).split()) for p in parts]
    return " - ".join(p for p in cleaned if p)


def pdf_names(title: str, company: str) -> tuple[str, str]:
    """The CV's and the cover letter's PDF file names, in that order."""
    return (f"{document_name(title, company, 'CV')}.pdf",
            f"{document_name(title, company, 'Cover Letter')}.pdf")


def discard_pdfs(out_dir: Path) -> None:
    """Remove any printed PDFs from a build directory. A new candidate, a
    publish, or a refused --pdf must not leave an older or unapproved PDF
    beside HTML it no longer matches. Every PDF goes, whatever it is named:
    a renamed job would otherwise leave its old-named PDFs behind."""
    for path in out_dir.glob("*.pdf"):
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            log(f"WARNING: could not remove {path}: {e}")


def copy_photo(out_dir: Path) -> bool:
    if PROFILE_PHOTO.exists():
        shutil.copyfile(PROFILE_PHOTO, out_dir / "profile-photo.jpg")
        return True
    log(f"WARNING: {PROFILE_PHOTO} not found; cv.html will show a broken photo")
    return False


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


# The builder task this process has logged as `started`, if any: (sb, run_id,
# job_id, mode). fail() uses it to log the matching `failed` row.
_task: tuple | None = None


def begin_task(sb, run_id: str, job_id: str, mode: str) -> None:
    """Log `started` for a --candidate or --publish call."""
    global _task
    try:
        write_event(sb, run_id, "builder", "started", job_id, {"mode": mode})
    except Exception as e:  # noqa: BLE001
        # A hosted refusal keeps its own code, e.g. token_expired.
        fail(getattr(e, "error", "supabase_unreachable"), f"failed to log the started pipeline_events row: {e}")
        return
    _task = (sb, run_id, job_id, mode)


def end_task() -> None:
    """The task has its result (or its result could not be logged): fail()
    must not add a `failed` row after this."""
    global _task
    _task = None


def fail(error: str, message: str = "", details: dict | None = None) -> None:
    global _task
    if _task:
        sb, run_id, job_id, mode = _task
        _task = None
        try:
            write_event(sb, run_id, "builder", "failed", job_id, {"mode": mode, "error": error})
        except Exception as e:  # noqa: BLE001
            # The error below still reaches the orchestrator, which records
            # the failure itself when an agent cannot.
            log(f"WARNING: could not log the failed pipeline_events row: {e}")
    payload = {"status": "failed", "error": error, "message": message}
    if details:
        payload.update(details)
    print(json.dumps(payload))
    sys.exit(1)


# ---------------------------------------------------------------------------
# Markdown -> HTML conversion (builder.md steps 3/4 + approved feedback rules)
# ---------------------------------------------------------------------------

HR_RE = re.compile(r'^(-{3,}|\*{3,}|_{3,})$')
BOLD_RE = re.compile(r'\*\*(.+?)\*\*')
TOOLS_RE = re.compile(r'^\*{0,2}(verkt[oø]y|tools|stack)\*{0,2}\s*:', re.I)
PERIOD_HINT_RE = re.compile(r'\d|\b(n[aå]|present|current|now)\b', re.I)


def escape(text: str) -> str:
    return html.escape(text, quote=False)


def inline_format(text: str) -> str:
    """Escape HTML, then convert markdown **bold** spans to <strong>."""
    return BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", escape(text))


def _split_period_location(rest: list[str]):
    """Given trailing ' · '-separated segments, decide which is the period.
    The segment that looks date-like (digit, or 'present'/'nå') is the period;
    everything else joins as location. Returns (period, location), either may
    be None."""
    if not rest:
        return None, None
    if PERIOD_HINT_RE.search(rest[-1]):
        return rest[-1], (" · ".join(rest[:-1]) if len(rest) > 1 else None)
    return None, " · ".join(rest)


def normalize_entry_heading(text: str):
    """Split a '### ...' heading into (title, period_span).

    cvWriter's documented template is
        ### <Title>, <Company> — <City> · <Start>–<End>
    but real output also uses an all-middot form
        ### <Title>, <Company> · <City> · <Start>–<End>
    and a third form where Company is already comma-joined into the title
    and the em-dash introduces Location instead:
        ### <Title>, <Company> — <City> · <Start>–<End>   (only 2 segments after —)
    These are disambiguated by counting the segments after the em-dash: with
    3+ segments the first is Company (folded into the title, comma-joined —
    builder feedback rule "role title first, institution second"); with fewer
    than 3, the title is left as-is and everything after the em-dash is
    Location/Period. This is a heuristic (the em-dash's meaning is genuinely
    ambiguous with only 2 segments) validated against every heading shape
    seen in the real tailored_cvs/cover_letters data at the time of writing —
    see the build_html.py regression report for the cases checked.
    """
    if " — " in text:
        before, after = text.split(" — ", 1)
        after_parts = [p.strip() for p in after.split(" · ") if p.strip()]
        if len(after_parts) >= 3:
            company = after_parts[0]
            title = f"{before.strip()}, {company}".strip(", ")
            period, location = _split_period_location(after_parts[1:])
        elif len(after_parts) == 1 and not PERIOD_HINT_RE.search(after_parts[0]):
            # A single non-date segment after the em-dash reads as a subtitle
            # (e.g. 'Acme — Example Device'), not a
            # location — fold it into the title rather than mislabel it.
            title = f"{before.strip()}, {after_parts[0]}".strip(", ")
            period, location = None, None
        else:
            title = before.strip()
            period, location = _split_period_location(after_parts)
    else:
        parts = [p.strip() for p in text.split(" · ") if p.strip()]
        title = parts[0] if parts else text.strip()
        rest = parts[1:]
        if len(rest) >= 3:
            # 'Title · Company · Location · Period' (an older cvWriter dialect
            # with no em-dash at all) — fold Company into the title same as
            # the em-dash case above.
            title = f"{title}, {rest[0]}".strip(", ")
            period, location = _split_period_location(rest[1:])
        else:
            period, location = _split_period_location(rest)

    # Where, then when: the line under the title reads like
    # "Northwind Labs™ (Freelance), Bergen, Norway · Mar 2024–Present".
    span = f"{location} · {period}" if (period and location) else (period or location)
    return title, span


# Some older tailored_cvs put the period on its own plain line below the
# '### ' heading instead of embedding it in the heading (e.g. 'feb. 2025 – nå'
# on its own line). Match a line that is ENTIRELY a date/date-range so it
# still gets wrapped in <span class="period"> per builder.md's mapping rule,
# without misfiring on ordinary prose that happens to contain a year.
PERIOD_LINE_RE = re.compile(
    r'^('
    r'\d{4}(\s*[–-]\s*(\d{4}|n[aå]|now|present))?'
    r'|[A-Za-zÆØÅæøå]+\.?\s*\d{4}(\s*[–-]\s*([A-Za-zÆØÅæøå]+\.?\s*\d{4}|n[aå]|now|present))?'
    r')\.?$',
    re.I,
)


def render_section_body(lines: list[str]) -> str:
    parts: list[str] = []
    ul_buffer: list[str] = []
    entry_open = False

    def flush_ul():
        nonlocal ul_buffer
        if ul_buffer:
            items = "".join(f"<li>{inline_format(item)}</li>\n" for item in ul_buffer)
            parts.append(f"<ul>\n{items}</ul>\n")
            ul_buffer = []

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if HR_RE.match(stripped):
            # Builder rule 2026-09-14 "Clean typography and hierarchical entry
            # formatting": never emit dividing rules between entries/projects.
            continue
        if line.startswith("### "):
            flush_ul()
            if entry_open:
                parts.append("</div>\n")
            title, period_span = normalize_entry_heading(line[4:].strip())
            parts.append('<div class="entry">\n')
            parts.append(f"<h3>{inline_format(title)}</h3>\n")
            if period_span:
                parts.append(f'<p><span class="period">{escape(period_span)}</span></p>\n')
            entry_open = True
        elif stripped.startswith("- "):
            ul_buffer.append(stripped[2:].strip())
        else:
            flush_ul()
            if TOOLS_RE.match(stripped):
                parts.append(f'<p class="tools">{inline_format(stripped)}</p>\n')
            elif PERIOD_LINE_RE.match(stripped):
                parts.append(f'<p><span class="period">{escape(stripped)}</span></p>\n')
            else:
                parts.append(f"<p>{inline_format(stripped)}</p>\n")
    flush_ul()
    if entry_open:
        parts.append("</div>\n")
    return "".join(parts)


def markdown_to_cv_html(markdown_text: str) -> str:
    lines = markdown_text.split("\n")
    idx = 0
    # Skip the name + contact-line preamble (and any leading '---' rule) — the
    # Jinja template's header already renders name/contact/photo (builder rule
    # 2026-09-15 "modern slate header..."); the body starts at the first
    # '## ' section heading.
    while idx < len(lines) and not lines[idx].startswith("## "):
        idx += 1
    sections = []
    i = idx
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            heading = line[3:].strip()
            i += 1
            body_lines = []
            while i < len(lines) and not lines[i].startswith("## "):
                body_lines.append(lines[i])
                i += 1
            body_html = render_section_body(body_lines)
            sections.append(f'<div class="section">\n<h2>{escape(heading)}</h2>\n{body_html}</div>\n')
        else:
            i += 1
    return "\n".join(sections)


TITLE_PREFIX_RE = re.compile(r'^(re|ref|vedr|ang[aå]ende|emne)\s*:\s*', re.I)
# A job board's ad code at the end of a title: "(123456)", "(#123456)" or
# "(<board> 123456)". Six digits or more, so "(ISO 27001)" or "(Oslo 2025)"
# stay part of the title.
AD_CODE_RE = re.compile(r'\s*\(\s*(?:[A-Za-zÆØÅæøå.]+)?[\s#:-]*\d{6,}\s*\)\s*$', re.I)
_DATE_TOKEN = (
    r'\d{4}-\d{2}-\d{2}'
    r'|\d{1,2}\.\d{1,2}\.\d{4}'  # DD.MM.YYYY / D.M.YYYY (Norwegian/German numeric)
    r'|\d{1,2}\.\s*[A-Za-zÆØÅæøå]+\.?\s*\d{4}'
    r'|\d{1,2}/\d{1,2}/\d{4}'
    r'|[A-Za-zÆØÅæøå]+\s+\d{1,2},?\s+\d{4}'
)
DATE_LINE_RE = re.compile(rf'^({_DATE_TOKEN})\.?$')
# A dateline conventionally written as "City, <date>" on its own line (e.g.
# "Oslo, 2026-04-26") — also a date, per the same builder rule, even though
# the line isn't ENTIRELY a date.
DATELINE_WITH_CITY_RE = re.compile(rf'^[A-Za-zÆØÅæøå .]+,\s*({_DATE_TOKEN})\.?$')
CLOSING_WORDS_RE = re.compile(
    r'^(sincerely|regards|best regards|kind regards|yours sincerely|yours faithfully|'
    r'vennlig hilsen|med vennlig hilsen|hilsen)\b',
    re.I,
)


SENIORITY_PREFIX_RE = re.compile(r'^(senior|sr\.?|lead|principal|staff|junior|jr\.?)\s+', re.I)


def plain_role_title(raw_title: str) -> str:
    """Self-label as the plain role title: drop a seniority prefix ('Senior',
    'Lead', ...) and a trailing ' — <team/unit>' / ' - <team/unit>' qualifier.
    Applied to both the CV's role_title and the cover letter's title so the
    two never disagree — e.g. jobs.title "Senior Data Scientist — Data Cloud
    Acceleration" becomes "Data Scientist" in both places."""
    title = raw_title.strip()
    title = re.split(r'\s+[—–-]\s+', title, maxsplit=1)[0].strip()
    title = SENIORITY_PREFIX_RE.sub("", title).strip()
    return title or raw_title.strip()


def clean_letter_title(title: str) -> str:
    # Builder rule 2026-09-14 "Clean letter title and recipient formatting
    # without prefixes": no 'Re:' prefixes or job-board ad-code suffixes. Also run
    # through plain_role_title() so a stale 'Senior ...' title surviving in
    # older markdown is caught here too, not just at the CV header.
    title = TITLE_PREFIX_RE.sub("", title.strip())
    title = AD_CODE_RE.sub("", title)
    return plain_role_title(title.strip())


def _is_closing_line(line: str) -> bool:
    stripped = line.strip()
    return bool(CLOSING_WORDS_RE.match(stripped)) and stripped.endswith(",") and len(stripped) <= 40


def markdown_to_cover_letter_html(markdown_text: str, company: str) -> str:
    # Builder rule 2026-09-14 "Never render dates into the cover letter HTML":
    # drop any standalone date-only line, or a "City, <date>" dateline, wherever
    # it appears, before anything else is parsed.
    lines = [
        l for l in markdown_text.split("\n")
        if not DATE_LINE_RE.match(l.strip()) and not DATELINE_WITH_CITY_RE.match(l.strip())
    ]
    text = "\n".join(lines).strip()
    raw_blocks = re.split(r"\n\s*\n+", text)
    blocks = [[l.strip() for l in b.split("\n") if l.strip()] for b in raw_blocks]
    blocks = [b for b in blocks if b]

    title_idx = title_line_idx = None
    for idx, block in enumerate(blocks):
        for li, line in enumerate(block):
            if re.match(r'^\*\*(.+)\*\*$', line):
                title_idx, title_line_idx = idx, li
                break
        if title_idx is not None:
            break

    if title_idx is None:
        # No bold job-title line found — degrade to plain paragraphs so the
        # letter is still readable rather than empty. (Seen in a handful of
        # older appWriter letters with no ** marker on the title at all.)
        return "".join(f"<p>{inline_format(' '.join(b))}</p>\n" for b in blocks)

    title_block = blocks[title_idx]
    title_text = clean_letter_title(re.match(r'^\*\*(.+)\*\*$', title_block[title_line_idx]).group(1))
    # Any other lines sharing the title's block (e.g. a company name on the
    # very next line with no blank-line separator) are recipient info too.
    title_block_extra = title_block[:title_line_idx] + title_block[title_line_idx + 1:]
    recipient_lines = [l for b in blocks[:title_idx] for l in b] + title_block_extra
    remaining = blocks[title_idx + 1:]

    closing_idx = None
    for idx, block in enumerate(remaining):
        if block and _is_closing_line(block[0]):
            closing_idx = idx  # last match wins if more than one (rare)
    if closing_idx is None and remaining:
        closing_idx = len(remaining) - 1  # fallback: treat the last block as closing

    body_blocks = remaining[:closing_idx] if closing_idx is not None else remaining
    closing_block = remaining[closing_idx] if closing_idx is not None else None

    parts: list[str] = []
    if title_text:
        parts.append(f'<div class="letter-title">{escape(title_text)}</div>\n')
    if company:
        parts.append(f'<div class="letter-subtitle">{escape(company)}</div>\n')
    if recipient_lines:
        recipient_html = "<br>\n".join(escape(l) for l in recipient_lines)
        parts.append(f'<div class="recipient">\n{recipient_html}\n</div>\n')

    # Optional subject/salutation line directly after the title, before body
    # paragraphs (e.g. "Hei Ola Sigurdslid,"). Real letters show this can be
    # its own block; body paragraphs are always longer and end in ".".
    if body_blocks and len(body_blocks[0]) == 1:
        candidate = body_blocks[0][0]
        if len(candidate) <= 80 and candidate.endswith(":"):
            parts.append(f'<div class="subject">{inline_format(candidate)}</div>\n')
            body_blocks = body_blocks[1:]
        elif len(candidate) <= 80 and candidate.endswith(","):
            parts.append(f'<div class="salutation">{inline_format(candidate)}</div>\n')
            body_blocks = body_blocks[1:]

    for block in body_blocks:
        parts.append(f"<p>{inline_format(' '.join(block))}</p>\n")

    if closing_block:
        if len(closing_block) >= 1:
            parts.append(f'<div class="closing">{escape(closing_block[0])}</div>\n')
        if len(closing_block) >= 2:
            parts.append(f'<div class="signature">{escape(closing_block[1])}</div>\n')
        if len(closing_block) >= 3:
            parts.append(f'<div class="contact-footer">{escape(" ".join(closing_block[2:]))}</div>\n')

    return "".join(parts)


def slugify(title: str) -> str:
    """builder.md's slug rule: lowercase, spaces -> hyphens, strip non-alphanumeric
    (ASCII only — æøå etc. are dropped, not transliterated; matches real build
    dir names such as 'statistikk-og-datautvikler-innen-nringslivsstatistikk')."""
    s = title.lower()
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'[\s-]+', '-', s)
    return s.strip('-')


def _link_text(url: str) -> str:
    """linkedin.com/in/someone for https://www.linkedin.com/in/someone/."""
    return re.sub(r"^www\.", "", re.sub(r"^https?://", "", url)).rstrip("/")


def contact_rows(contact: dict) -> list[list[dict]]:
    """The CV header's contact line: how to reach the person, then where to
    read about them. Only the details that are set."""
    first, second = [], []
    if contact.get("email"):
        first.append({"kind": "email", "href": f"mailto:{contact['email']}", "text": contact["email"]})
    if contact.get("phone"):
        digits = re.sub(r"[^0-9+]", "", contact["phone"])
        first.append({"kind": "phone", "href": f"tel:{digits}", "text": contact["phone"]})
    if contact.get("city"):
        first.append({"kind": "city", "href": None, "text": contact["city"]})
    for kind in ("linkedin", "github", "website"):
        if contact.get(kind):
            second.append({"kind": kind, "href": contact[kind], "text": _link_text(contact[kind]), "external": True})
    return [row for row in (first, second) if row]


def render_html_pair(cv_markdown: str, cl_markdown: str, title: str, company: str) -> tuple[str, str]:
    """Markdown -> final CV/letter HTML, deterministically (see module
    docstring). Shared by --candidate and --publish so there is exactly one
    code path that can disagree with itself. Calls fail() (exits) on error;
    the `return` after each fail() is unreachable but keeps the function's
    static shape obviously total, matching this module's existing style."""
    cv_body_html = markdown_to_cv_html(cv_markdown)
    cl_body_html = markdown_to_cover_letter_html(cl_markdown, company)

    # A small number of historical tailored_cvs/cover_letters rows contain
    # pre-rendered HTML (or otherwise non-markdown content) instead of the
    # expected markdown — the converter correctly finds no '## ' sections in
    # that case and produces nothing. Fail loudly rather than silently write
    # an empty CV/letter.
    if not cv_body_html.strip():
        fail("empty_cv_body", "tailored_cvs.content produced no HTML — check it is markdown with '## ' section headings, not pre-rendered HTML")
        return "", ""
    if not cl_body_html.strip():
        fail("empty_cover_letter_body", "cover_letters.content produced no HTML — check the source markdown")
        return "", ""

    try:
        import jinja2
        from markupsafe import Markup
    except ImportError as e:  # noqa: BLE001
        fail("dependency_missing", f"jinja2/markupsafe not importable: {e}")
        return "", ""

    # User memory rule "self-label as the plain role title, drop 'Senior'":
    # applied to the CV header/<title> the same way clean_letter_title()
    # already applies it to the cover letter's title.
    role_title = plain_role_title(title)

    try:
        env = jinja2.Environment(autoescape=True, keep_trailing_newline=True)
        cv_template_text = (TEMPLATES_DIR / "cv.html.j2").read_text(encoding="utf-8")
        cl_template_text = (TEMPLATES_DIR / "cover_letter.html.j2").read_text(encoding="utf-8")
        cv_html = env.from_string(cv_template_text).render(name=NAME, role_title=role_title, body=Markup(cv_body_html),
                                                           doc_title=document_name(title, company, "CV"),
                                                           contact_rows=contact_rows(CONTACT),
                                                           has_photo=PROFILE_PHOTO.exists())
        cl_html = env.from_string(cl_template_text).render(name=NAME, role_title=role_title, body=Markup(cl_body_html),
                                                           doc_title=document_name(title, company, "Cover Letter"))
    except OSError as e:
        fail("io_error", f"could not read a template under {TEMPLATES_DIR}: {e}")
        return "", ""
    except jinja2.TemplateError as e:
        fail("template_error", f"Jinja rendering failed: {e}")
        return "", ""

    return cv_html, cl_html


def upload_to_storage(sb, run_id: str, job_id: str, cv_path: Path, cl_path: Path,
                      expected_cv_sha256: str, expected_letter_sha256: str):
    """Upload both HTML files to Storage. Returns (uploaded, errors) where
    `uploaded` is a dict {"cv_html": "...", "cover_letter_html": "..."}.
    PDFs are never uploaded — see the module docstring.
    Retries once per file on failure; any file still failing after the retry
    is reported in `errors` — the caller (only --publish calls this) treats
    any error as fatal and does not mark the job built.
    Both saved files are hash-checked before either is uploaded.

    run_id/job_id are validated (ID_PATTERN) by the caller before this is
    reached, so the object-key path segments below can't contain '/' or '..'.
    """
    bucket = sb.storage.from_(STORAGE_BUCKET)
    uploaded: dict[str, str] = {}
    errors: list[str] = []
    payloads = []
    for key, local_path, remote_name, expected_sha256 in (
        ("cv_html", cv_path, "cv.html", expected_cv_sha256),
        ("cover_letter_html", cl_path, "cover_letter.html", expected_letter_sha256),
    ):
        data = local_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise RuntimeError(f"saved {remote_name} differs from the file checked by Chrome")
        payloads.append((key, remote_name, data))
    for key, remote_name, data in payloads:
        remote_path = f"{run_id}/{job_id}/{remote_name}"
        last_err = None
        for _attempt in (1, 2):
            try:
                bucket.upload(
                    remote_path,
                    data,
                    file_options={"content-type": "text/html; charset=utf-8", "upsert": "true"},
                )
                last_err = None
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
        if last_err is None:
            uploaded[key] = f"{STORAGE_BUCKET}/{remote_path}"
        else:
            errors.append(f"{remote_path}: {last_err}")
    return uploaded, errors


def run_candidate(sb, args, job_uuid: str, title: str, company: str, cv_markdown: str, cl_markdown: str) -> None:
    slug = slugify(title)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if args.dry_run:
        temp_root = Path(tempfile.mkdtemp(prefix="build_html_dryrun_"))
        out_dir = _ensure_within(temp_root / f"{slug}-{date_str}", temp_root, "builds_dir")
    else:
        out_dir = _ensure_within(resolve_build_dir(args.job_id, job_uuid, slug, date_str), BUILDS_DIR, "builds_dir")

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        fail("io_error", f"could not create build directory {out_dir}: {e}")
        return

    cv_html, cl_html = render_html_pair(cv_markdown, cl_markdown, title, company)

    discard_pdfs(out_dir)
    cv_path = out_dir / "cv.html"
    cl_path = out_dir / "cover_letter.html"
    try:
        cv_path.write_text(cv_html, encoding="utf-8")
        cl_path.write_text(cl_html, encoding="utf-8")
    except OSError as e:
        fail("io_error", f"could not write HTML output: {e}")
        return

    # Copy the photo (or note its absence) BEFORE printing, so Chrome sees
    # the same local document the user would.
    try:
        photo_copied = copy_photo(out_dir)
    except OSError as e:
        fail("io_error", f"could not copy profile photo: {e}")
        return

    photo_bytes = (out_dir / "profile-photo.jpg").read_bytes() if photo_copied else None
    hashes = hash_evidence(cv_path.read_bytes(), cl_path.read_bytes(), photo_bytes)
    cv_measurement = measure_print(cv_path)
    letter_measurement = measure_print(cl_path, max_pages=1)
    request_pending_prints()
    evidence = {**hashes, **cv_measurement, "letter_print": letter_measurement}

    write_approval_json(out_dir, approved=False, evidence=evidence, run_id=args.run_id, job_id=args.job_id, job_uuid=job_uuid)

    result = {
        "status": "candidate",
        "run_id": args.run_id,
        "job_id": args.job_id,
        "dry_run": bool(args.dry_run),
        "builds_dir": str(out_dir),
        "artifacts": {"cv_html": str(cv_path), "cover_letter_html": str(cl_path)},
        "photo_copied": photo_copied,
        "evidence": evidence,
    }

    if args.dry_run:
        log(f"[dry-run] rendered CV: {cv_path}")
        log(f"[dry-run] rendered cover letter: {cl_path}")
        print(json.dumps(result))
        return

    # Log the candidate row BEFORE deciding success/failure below — QA reads
    # this row (and only this row) for print evidence, including the case
    # where Chrome/pdfinfo/pdftotext failed and all we have is tool_errors.
    try:
        write_event(sb, args.run_id, "builder", "candidate", args.job_id, evidence)
    except Exception as e:  # noqa: BLE001
        # If this log can't be written, QA has nothing to check — that must
        # surface as a failure, not a silent "candidate" success.
        end_task()
        fail(getattr(e, "error", "supabase_unreachable"), f"failed to log candidate pipeline_events row: {e}",
             {"evidence": evidence})
        return

    print_errors = ([f"CV: {error}" for error in cv_measurement["tool_errors"]]
                    + [f"letter: {error}" for error in letter_measurement["tool_errors"]])
    if print_errors:
        # A tool failure prevented a trustworthy measurement — distinguish
        # this from a document that was actually measured and found to
        # violate a rule (that case still returns status "candidate" below,
        # exactly so QA can fail it with the real numbers). The candidate row
        # above is evidence only; the `failed` row this writes is the outcome.
        fail("print_tool_failure", "; ".join(print_errors), {"evidence": evidence})
        return

    end_task()
    print(json.dumps(result))


def run_publish(sb, args, job_uuid: str, title: str, company: str, cv_markdown: str, cl_markdown: str,
                pdf_only: bool = False) -> None:
    """--publish, or with `pdf_only` the --pdf mode: every check a publish
    makes, then keep the printed PDFs instead of uploading and flagging."""
    try:
        if isinstance(sb, Hosted):
            latest = sb.api.call("get_latest_qa", {"run_id": args.run_id, "job_id": args.job_id})["qa"]
            qa_rows = [latest] if latest else []
        else:
            qa_rows = (
                sb.table("qa_results").select("version,status,issues,build_evidence")
                .eq("job_id", job_uuid).order("version", desc=True).limit(1).execute().data
            )
    except Exception as e:  # noqa: BLE001
        fail(getattr(e, "error", "supabase_unreachable"), f"reading qa_results failed: {e}")
        return

    if not qa_rows:
        fail("qa_not_approved", "no qa_results row exists for this job — run qa on a fresh --candidate build first")
        return
    qa = qa_rows[0]
    status = qa.get("status")
    if status not in ("pass", "warn"):
        fail("qa_not_approved", f"latest qa_results (version {qa.get('version')}) status is {status!r}, must be pass or warn")
        return

    issues = qa.get("issues") or []
    if isinstance(issues, str):
        try:
            issues = json.loads(issues)
        except ValueError:
            issues = []
    if status == "warn" and any(isinstance(i, dict) and str(i.get("severity", "")).lower() == "fail" for i in issues):
        fail("qa_not_approved", f"latest qa_results (version {qa.get('version')}) is warn but contains a fail-severity issue")
        return

    build_evidence = qa.get("build_evidence")
    if not build_evidence:
        fail("qa_missing_evidence",
             f"qa_results version {qa.get('version')} has no build_evidence — it predates the approval "
             "gate, or QA had no candidate to review; run --candidate then qa again")
        return

    # Re-render the exact same content fresh from Supabase (see the module
    # docstring for why this is a correct freshness check, not a best-effort
    # one) and require an EXACT hash match before touching disk/Storage/DB.
    cv_html, cl_html = render_html_pair(cv_markdown, cl_markdown, title, company)
    cv_bytes = cv_html.encode("utf-8")
    cl_bytes = cl_html.encode("utf-8")
    photo_bytes = PROFILE_PHOTO.read_bytes() if PROFILE_PHOTO.exists() else None
    fresh_hashes = hash_evidence(cv_bytes, cl_bytes, photo_bytes)

    mismatches = [k for k in ("cv_sha256", "letter_sha256", "photo_sha256")
                  if fresh_hashes.get(k) != build_evidence.get(k)]
    if mismatches:
        fail("qa_evidence_mismatch",
             f"content changed since QA approved it — mismatched: {', '.join(mismatches)}",
             {"qa_build_evidence": build_evidence, "fresh_hashes": fresh_hashes})
        return

    slug = slugify(title)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = _ensure_within((pdf_only and find_build_dir(job_uuid))
                             or resolve_build_dir(args.job_id, job_uuid, slug, date_str), BUILDS_DIR, "builds_dir")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        fail("io_error", f"could not create build directory {out_dir}: {e}")
        return

    cv_path = out_dir / "cv.html"
    cl_path = out_dir / "cover_letter.html"
    discard_pdfs(out_dir)
    try:
        cv_path.write_bytes(cv_bytes)
        cl_path.write_bytes(cl_bytes)
        photo_copied = copy_photo(out_dir)
        # Label the directory as an unapproved candidate the moment there's
        # anything in it, and BEFORE the re-check below can fail. Without
        # this, a fresh directory that fails the re-check (or a crash) is
        # left holding HTML with no APPROVAL.json at all — resolve_build_dir()
        # then can't tell it apart from a foreign directory and orphans it on
        # the next run for this job. Flipped to approved only once every
        # check below has actually passed.
        write_approval_json(out_dir, approved=False, evidence=fresh_hashes, run_id=args.run_id,
                             job_id=args.job_id, job_uuid=job_uuid)
    except OSError as e:
        fail("io_error", f"could not write build output: {e}")
        return

    try:
        _publish_measured(sb, args, job_uuid, qa, build_evidence, fresh_hashes, out_dir, photo_copied,
                          cv_path, cl_path, pdf_only, pdf_names(title, company))
    except SystemExit:
        # Every refusal below exits through fail(); none may leave PDFs behind.
        discard_pdfs(out_dir)
        raise


def _publish_measured(sb, args, job_uuid: str, qa: dict, build_evidence: dict, fresh_hashes: dict,
                      out_dir: Path, photo_copied: bool, cv_path: Path, cl_path: Path, pdf_only: bool,
                      pdf_file_names: tuple[str, str]) -> None:
    """The part of --publish / --pdf that runs once PDFs may exist on disk."""
    cv_pdf_path, cl_pdf_path = (out_dir / name for name in pdf_file_names)
    cv_measurement = measure_print(cv_path, keep_pdf=cv_pdf_path if pdf_only else None)
    letter_measurement = measure_print(cl_path, max_pages=1, keep_pdf=cl_pdf_path if pdf_only else None)
    request_pending_prints()
    measurement = {**cv_measurement, "letter_print": letter_measurement}
    print_errors = ([f"CV: {error}" for error in cv_measurement["tool_errors"]]
                    + [f"letter: {error}" for error in letter_measurement["tool_errors"]])
    if print_errors:
        fail("print_tool_failure", "; ".join(print_errors), {"evidence": {**fresh_hashes, **measurement}})
        return
    violations = ([f"CV: {problem}" for problem in cv_measurement["rule_violations"]]
                  + [f"letter: {problem}" for problem in letter_measurement["rule_violations"]])
    if violations:
        fail("print_rule_violation", "; ".join(violations), {"evidence": {**fresh_hashes, **measurement}})
        return
    # The hashes already proved the bytes are identical to what QA reviewed,
    # but the print *environment* (fonts, Chrome version) could still measure
    # differently. "Changed after QA" means changed, even if still passing —
    # require the numbers to match, not just stay under the limit.
    drifted = [k for k in ("visible_words", "printed_pages", "page_size", "is_a4_portrait",
                           "page_bottom_blank_mm", "max_bottom_blank_mm", "letter_print")
               if measurement.get(k) != build_evidence.get(k)]
    if drifted:
        fail("qa_evidence_mismatch",
             f"print measurement changed since QA approved it — drifted: {', '.join(drifted)}",
             {"qa_build_evidence": build_evidence, "fresh_measurement": measurement})
        return

    evidence = {**fresh_hashes, **measurement}

    if pdf_only:
        write_approval_json(out_dir, approved=True, evidence=evidence, run_id=args.run_id, job_id=args.job_id,
                             job_uuid=job_uuid, qa_version=qa.get("version"))
        print(json.dumps({
            "status": "pdf",
            "run_id": args.run_id,
            "job_id": args.job_id,
            "builds_dir": str(out_dir),
            "artifacts": {"cv_pdf": str(cv_pdf_path), "cover_letter_pdf": str(cl_pdf_path)},
            "qa_version": qa.get("version"),
            "evidence": evidence,
        }))
        return

    if isinstance(sb, Hosted):
        # The server hashes what it receives and asks the database's publish
        # gate before it uploads and marks the job built.
        try:
            published = sb.api.call("publish_build", {
                "run_id": args.run_id, "job_id": args.job_id,
                "cv_html": cv_path.read_bytes().decode("utf-8"),
                "letter_html": cl_path.read_bytes().decode("utf-8"),
                "measurement": measurement,
            })
        except Exception as e:  # noqa: BLE001
            fail(getattr(e, "error", "upload_failed"), str(e), {"evidence": evidence})
            return
        uploaded = published["storage"]
    else:
        _publish_with_service_key(sb, args, cv_path, cl_path, evidence)
        uploaded = {"cv_html": f"{STORAGE_BUCKET}/{args.run_id}/{args.job_id}/cv.html",
                    "cover_letter_html": f"{STORAGE_BUCKET}/{args.run_id}/{args.job_id}/cover_letter.html"}

    write_approval_json(out_dir, approved=True, evidence=evidence, run_id=args.run_id, job_id=args.job_id,
                         job_uuid=job_uuid, qa_version=qa.get("version"))
    _log_published(sb, args, out_dir, cv_path, cl_path, uploaded, qa, evidence, photo_copied)


def _publish_with_service_key(sb, args, cv_path: Path, cl_path: Path, evidence: dict) -> None:
    """The owner's local publish: upload, then flip the built flags."""
    try:
        uploaded, errors = upload_to_storage(sb, args.run_id, args.job_id, cv_path, cl_path,
                                             evidence["cv_sha256"], evidence["letter_sha256"])
    except (OSError, RuntimeError) as e:
        fail("upload_failed", str(e), {"evidence": evidence})
        return
    if errors:
        # Unlike the old single-mode builder, an upload failure during
        # --publish is fatal: the job must not be marked built with nothing
        # in Storage.
        fail("upload_failed", "; ".join(errors), {"evidence": evidence})
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        sb.table("jobs").update({
            "cv_built": True, "cv_built_at": now_iso,
            "cover_letter_built": True, "cover_letter_built_at": now_iso,
            # Where the files went, so readers need not guess the layout
            # (hosted users' builds live under <user_id>/<run_id>/<job_id>/).
            "cv_html_path": f"{args.run_id}/{args.job_id}/cv.html",
            "cover_letter_html_path": f"{args.run_id}/{args.job_id}/cover_letter.html",
        }).eq("run_id", args.run_id).eq("job_id", args.job_id).execute()
    except Exception as e:  # noqa: BLE001
        fail("supabase_unreachable", f"failed to update jobs build columns: {e}", {"evidence": evidence})
        return


def _log_published(sb, args, out_dir: Path, cv_path: Path, cl_path: Path, uploaded: dict, qa: dict,
                   evidence: dict, photo_copied: bool) -> None:
    try:
        write_event(sb, args.run_id, "builder", "completed", args.job_id, {
            "local_dir": str(out_dir),
            "cv_html": str(cv_path),
            "cover_letter_html": str(cl_path),
            "storage_paths": uploaded,
            "qa_version": qa.get("version"),
            "evidence": evidence,
        })
    except Exception as e:  # noqa: BLE001
        # The job IS published; only its result row is missing. Report that
        # rather than a quiet success, or the Office shows the builder working
        # forever. Running --publish again is safe and logs the row.
        end_task()
        fail(getattr(e, "error", "supabase_unreachable"),
             f"published, but failed to log the completed pipeline_events row: {e}",
             {"published": True, "evidence": evidence})
        return
    end_task()

    print(json.dumps({
        "status": "ok",
        "run_id": args.run_id,
        "job_id": args.job_id,
        "builds_dir": str(out_dir),
        "artifacts": {"cv_html": str(cv_path), "cover_letter_html": str(cl_path)},
        "photo_copied": photo_copied,
        "storage": uploaded,
        "qa_version": qa.get("version"),
        "evidence": evidence,
    }))


def main() -> None:
    # Claude/Antigravity may invoke the script with a system python that lacks
    # the repo's Supabase/Jinja dependencies. Re-exec into the existing project
    # environment before doing any work; the resolved prefix prevents a loop.
    if VENV_PYTHON.is_file() and Path(sys.prefix).resolve() != VENV_DIR.resolve():
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("job_id")
    parser.add_argument("--candidate", action="store_true",
                         help="render + measure only; no upload, no built flags, no completed event")
    parser.add_argument("--publish", action="store_true",
                         help="require the latest qa_results to approve the exact candidate, then upload + set built flags")
    parser.add_argument("--pdf", action="store_true",
                         help="print an already-published build to '<Name> - <Role> - <Company> - CV.pdf' and "
                              "'... - Cover Letter.pdf' locally; no upload, no DB writes")
    parser.add_argument("--discard-pdfs", action="store_true",
                         help="remove this job's local PDFs once the application has been sent")
    parser.add_argument("--dry-run", action="store_true",
                         help="(with --candidate only) render to a temp dir outside the repo; no upload, no DB writes")
    parser.add_argument("--token", help="a job-app script token: work through the hosted server (the plugin)")
    args = parser.parse_args()

    modes = sum((args.candidate, args.publish, args.pdf, args.discard_pdfs))
    if modes > 1:
        fail("mode_conflict", "--candidate, --publish, --pdf and --discard-pdfs are mutually exclusive")
    if modes == 0:
        fail("mode_required", "specify --candidate, --publish, --pdf or --discard-pdfs — there is no default "
                               "mode, so nothing can accidentally publish an unapproved document")
    if args.dry_run and not args.candidate:
        fail("mode_conflict", "--dry-run is only valid together with --candidate")

    # run_id/job_id end up in filesystem paths and Storage object keys — reject
    # anything that isn't a plain token before either is used for anything.
    validate_id(args.run_id, "run_id")
    validate_id(args.job_id, "job_id")

    if args.token:
        main_hosted(args)
        return
    if not NAME:
        print("WARNING: config/owner.json is missing or has no name; the CV will have no name or contact line "
              "(copy config/owner.example.json to config/owner.json)", file=sys.stderr)

    # Check Supabase env ourselves first so a missing .env produces our JSON
    # contract instead of sync_config.get_supabase_client()'s bare sys.exit(1).
    try:
        from dotenv import load_dotenv
        from sync_config import ENV_FILE
        load_dotenv(ENV_FILE)
    except ImportError:
        pass
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        fail("supabase_unreachable", "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set (scripts/supabase/.env)")

    try:
        from sync_config import get_supabase_client
        sb = get_supabase_client()
    except SystemExit:
        fail("supabase_unreachable", "could not create Supabase client")
        return

    if args.publish or (args.candidate and not args.dry_run):
        begin_task(sb, args.run_id, args.job_id, "publish" if args.publish else "candidate")

    try:
        job_rows = (
            sb.table("jobs")
            .select("id,title,company,cv_built,cover_letter_built")
            .eq("run_id", args.run_id)
            .eq("job_id", args.job_id)
            .execute()
            .data
        )
    except Exception as e:  # noqa: BLE001
        fail("supabase_unreachable", f"reading jobs failed: {e}")
        return

    if not job_rows:
        fail("job_not_found", f"no jobs row for run_id={args.run_id} job_id={args.job_id}")
        return
    job = job_rows[0]
    job_uuid, title, company = job["id"], job["title"], job["company"]

    if args.discard_pdfs:
        build_dir = find_build_dir(job_uuid)
        if build_dir:
            discard_pdfs(build_dir)
        print(json.dumps({"status": "discarded", "run_id": args.run_id, "job_id": args.job_id,
                          "builds_dir": str(build_dir) if build_dir else None}))
        return
    if args.pdf and not (job.get("cv_built") and job.get("cover_letter_built")):
        fail("not_built", "this job has no published build — run --publish (after QA approves) before --pdf")
        return

    try:
        cv_rows = (
            sb.table("tailored_cvs").select("content").eq("job_id", job_uuid)
            .order("version", desc=True).limit(1).execute().data
        )
        cl_rows = (
            sb.table("cover_letters").select("content").eq("job_id", job_uuid)
            .order("version", desc=True).limit(1).execute().data
        )
    except Exception as e:  # noqa: BLE001
        fail("supabase_unreachable", f"reading tailored_cvs/cover_letters failed: {e}")
        return

    missing = []
    if not cv_rows:
        missing.append("tailored_cvs")
    if not cl_rows:
        missing.append("cover_letters")
    if missing:
        fail("missing_content", f"no rows found in: {', '.join(missing)} for this job — run cvWriter/appWriter first")
        return

    cv_markdown = cv_rows[0]["content"]
    cl_markdown = cl_rows[0]["content"]

    if args.candidate:
        run_candidate(sb, args, job_uuid, title, company, cv_markdown, cl_markdown)
    else:
        run_publish(sb, args, job_uuid, title, company, cv_markdown, cl_markdown, pdf_only=args.pdf)


def jobapp_home() -> Path:
    """job-app/ in the folder Claude Code runs in, unless JOBAPP_HOME says
    otherwise. Inside the workspace, so the browser tools (which may only
    touch the workspace) can attach the PDFs printed there."""
    return Path(os.environ.get("JOBAPP_HOME") or Path.cwd() / "job-app").expanduser().resolve()


def find_build_dir_by_label(run_id: str, job_id: str) -> Path | None:
    """The newest build directory whose APPROVAL.json names this run and job."""
    owned = []
    for approval_path in BUILDS_DIR.glob("*/APPROVAL.json"):
        try:
            data = json.loads(approval_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("run_id") == run_id and data.get("job_id") == job_id:
            owned.append(approval_path)
    return max(owned, key=lambda path: path.stat().st_mtime).parent if owned else None


def main_hosted(args) -> None:
    """The plugin's path: the same modes, with the data from the hosted
    server. The build directory, name and photo are the logged-in user's."""
    global BUILDS_DIR, PROFILE_PHOTO, NAME, CONTACT, PRINTER, PRINT_CACHE
    from jobapp_api import Api, ApiError

    try:
        sb = Hosted(Api(args.token))
    except ApiError as e:
        fail(e.error, e.message)
        return
    home = jobapp_home()
    BUILDS_DIR = home / "builds"
    PRINTER, PRINT_CACHE = "playwright", home / ".print"
    try:
        BUILDS_DIR.mkdir(parents=True, exist_ok=True)
        PRINT_CACHE.mkdir(exist_ok=True)
    except OSError as e:
        fail("io_error", f"could not create {BUILDS_DIR}: {e}")
        return
    prune_print_cache()

    if args.discard_pdfs:
        # Local only: the PDFs (which carry the CV) must go even when the
        # server cannot be reached or the subscription has ended.
        build_dir = find_build_dir_by_label(args.run_id, args.job_id)
        if build_dir:
            discard_pdfs(build_dir)
            discard_cached_prints(build_dir)
        print(json.dumps({"status": "discarded", "run_id": args.run_id, "job_id": args.job_id,
                          "builds_dir": str(build_dir) if build_dir else None}))
        return

    if args.publish or (args.candidate and not args.dry_run):
        begin_task(sb, args.run_id, args.job_id, "publish" if args.publish else "candidate")

    try:
        inputs = sb.api.call("get_build_inputs", {"run_id": args.run_id, "job_id": args.job_id})
    except ApiError as e:
        fail(e.error, e.message)
        return
    job = inputs["job"]
    if not inputs.get("full_name"):
        fail("profile_missing", "your full name is not set: run /job-hunt-ai:setup")
        return
    NAME = inputs["full_name"]
    CONTACT = inputs.get("profile") or {}
    # The photo comes from the server, so its hash is the one the server
    # computes for the publish gate.
    # One file per job, written whole and then renamed into place, so two
    # builds at once never read each other's half-written photo.
    PROFILE_PHOTO = home / "photos" / f"{job['id']}.jpg"
    if inputs.get("photo_base64"):
        import base64
        PROFILE_PHOTO.parent.mkdir(parents=True, exist_ok=True)
        partial = PROFILE_PHOTO.with_suffix(f".{os.getpid()}.part")
        partial.write_bytes(base64.b64decode(inputs["photo_base64"]))
        partial.replace(PROFILE_PHOTO)
    else:
        PROFILE_PHOTO.unlink(missing_ok=True)
    if args.pdf and not (job.get("cv_built") and job.get("cover_letter_built")):
        fail("not_built", "this job has no published build — run --publish (after QA approves) before --pdf")
        return
    missing = [name for name, text in (("tailored_cvs", inputs.get("cv_markdown")),
                                       ("cover_letters", inputs.get("letter_markdown"))) if not text]
    if missing:
        fail("missing_content", f"no draft saved yet in: {', '.join(missing)} — run cvWriter/appWriter first")
        return
    if args.candidate:
        run_candidate(sb, args, job["id"], job["title"], job["company"], inputs["cv_markdown"], inputs["letter_markdown"])
    else:
        run_publish(sb, args, job["id"], job["title"], job["company"], inputs["cv_markdown"], inputs["letter_markdown"],
                    pdf_only=args.pdf)


if __name__ == "__main__":
    main()
