#!/usr/bin/env python3
"""Start the Playwright MCP server on the pipeline's own Chrome profile.

    python3 scripts/playwright_mcp.py [playwright-mcp args...]   # what plugin/.mcp.json runs
    python3 scripts/playwright_mcp.py --login [url]              # log in by hand, once

The submitter applies on job platforms that need an account. It never sees a
password: the user logs in once, by hand, in a Chrome profile that belongs to
this pipeline, and every later run reuses that session. The profile lives in
the home directory (not the repo) because it holds login cookies, which must
never be committed. It is created here when missing, so a fresh machine needs
no setup beyond logging in again.

`.mcp.json` cannot hold this path itself: it would have to be absolute, and
`~` / `${HOME}` are not expanded there.

Passing `--isolated` (or any flag that names the browser's state) skips the
profile and behaves exactly like plain Playwright MCP.
Set JOB_APP_CHROME_PROFILE to use a different folder.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Pinned: the browser server sees the user's logged-in sessions, so a new
# release is taken on purpose, not on the next start.
PACKAGE = "@playwright/mcp@0.0.82"
DEFAULT_PROFILE = Path.home() / ".auto-apply-ai-agent-fleet" / "chrome-profile"
# Flags that already say where the browser's state comes from; adding a
# profile on top of any of them would contradict it.
OWN_BROWSER_FLAGS = {"--isolated", "--user-data-dir", "--cdp-endpoint", "--extension",
                     "--config", "--remote-endpoint", "--endpoint"}
CHROME_CANDIDATES = ("google-chrome", "chrome", "chromium")
CHROME_APPS = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
               "/Applications/Chromium.app/Contents/MacOS/Chromium")


def profile_dir(env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("JOB_APP_CHROME_PROFILE") or DEFAULT_PROFILE).expanduser()


def ensure_profile(path: Path) -> Path:
    """Create the profile folder, private to this user, if it isn't there."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def server_argv(args: list[str], env: dict[str, str] | None = None) -> list[str]:
    """The npx command line for the given pass-through args."""
    flags = {arg.split("=", 1)[0] for arg in args}
    if flags & OWN_BROWSER_FLAGS:
        return ["npx", PACKAGE, *args]
    return ["npx", PACKAGE, "--user-data-dir", str(ensure_profile(profile_dir(env))), *args]


def chrome_path() -> str | None:
    for candidate in (*(shutil.which(name) for name in CHROME_CANDIDATES), *CHROME_APPS):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def login(url: str | None) -> int:
    chrome = chrome_path()
    if not chrome:
        print("Chrome was not found. Install Google Chrome, then run this again.", file=sys.stderr)
        return 1
    profile = ensure_profile(profile_dir())
    print(f"Opening Chrome on the pipeline profile: {profile}")
    print("Log in to each job platform you use, then close the window. "
          "Close it before starting a pipeline run: only one Chrome can use the profile at a time.")
    return subprocess.call([chrome, f"--user-data-dir={profile}", "--no-first-run", *([url] if url else [])])


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["--login"]:
        sys.exit(login(args[1] if len(args) > 1 else None))
    argv = server_argv(args)
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main()
