"""scripts/jobapp_api.py — the local scripts' door to the hosted job-app server.

Under the Claude Code plugin there is no service key on the user's machine.
An agent asks the `script_token` tool for a short-lived token and passes it to
a script as `--token`. The token carries the server's address in its first
part, so nothing else needs configuring:

    api = Api(token)
    api.call("log_event", {"run_id": ..., "agent": "builder", "status": "started"})

Only the standard library, so the scripts that need nothing else
(ingest_posting.py) run on a bare `python3`.
"""
from __future__ import annotations

import base64
import binascii
import http.client
import json
import urllib.request
from urllib.parse import unquote, urlsplit

PREFIX = "jat1"
TIMEOUT_S = 60
# Cloudflare's bot check refuses Python's default "Python-urllib/3.x" user
# agent (error 1010), so every request names itself.
USER_AGENT = "job-app-plugin (+https://github.com/MNKanout/job-hunt-ai)"


class ApiError(Exception):
    """A tool refused, or the server could not be reached. `error` is the
    tool's stable code (`token_expired`, `qa_not_approved`, ...)."""

    def __init__(self, error: str, message: str = "", details=None):
        super().__init__(message or error)
        self.error = error
        self.message = message or error
        self.details = details


def token_origin(token: str) -> str:
    """The server a token belongs to: https, or plain http on this computer
    only (for development)."""
    bad = ApiError("bad_token", "this is not a job-app script token; ask the script_token tool for a new one")
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != PREFIX:
        raise bad
    try:
        origin = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)).decode()
        url = urlsplit(origin)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise bad from None
    local = url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1")
    if not (url.scheme == "https" or local) or not url.hostname or url.path or url.query or url.username:
        raise ApiError("bad_token", f"the token names an unexpected server: {origin}")
    return origin


class Api:
    def __init__(self, token: str):
        self.token = token
        self.origin = token_origin(token)

    def call(self, tool: str, payload: dict) -> dict:
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
                   "User-Agent": USER_AGENT}
        try:
            status, raw = self._post(f"/api/{tool}", json.dumps(payload).encode(), headers)
        except http.client.HTTPException as e:
            raise ApiError("supabase_unreachable", f"the job-app server's reply to {tool} was cut off: {e!r}") from e
        except OSError as e:
            raise ApiError("supabase_unreachable", f"could not reach the job-app server: {e}") from e
        try:
            body = json.loads(raw.decode() or "{}")
        except ValueError:
            raise ApiError("supabase_unreachable", f"HTTP {status} from {tool}, and not JSON") from None
        if body.get("status") == "failed":
            raise ApiError(body.get("error", "failed"), body.get("message", ""), body.get("details"))
        return body

    def _post(self, path: str, data: bytes, headers: dict) -> tuple[int, bytes]:
        """One POST on its own connection, kept open until the whole reply is read.

        Not urllib.request: it sends "Connection: close", so the server hangs
        up right after replying, and Claude Code's sandbox proxy can lose the
        end of a reply when the server hangs up first (anthropics/claude-code
        #99650). Here the server never closes the connection; we do, once
        Content-Length bytes are in. The proxy is the one urllib would use
        (HTTPS_PROXY, NO_PROXY), reached with CONNECT."""
        url = urlsplit(self.origin)
        port = url.port or (443 if url.scheme == "https" else 80)
        connection = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
        proxy = None if urllib.request.proxy_bypass(url.hostname) else urllib.request.getproxies().get(url.scheme)
        if proxy:
            p = urlsplit(proxy if "://" in proxy else f"http://{proxy}")
            conn = connection(p.hostname, p.port or 80, timeout=TIMEOUT_S)
            tunnel = {}
            if p.username:
                cred = f"{unquote(p.username)}:{unquote(p.password or '')}".encode()
                tunnel["Proxy-Authorization"] = "Basic " + base64.b64encode(cred).decode()
            conn.set_tunnel(url.hostname, port, headers=tunnel)
        else:
            conn = connection(url.hostname, port, timeout=TIMEOUT_S)
        try:
            conn.request("POST", path, body=data, headers=headers)
            res = conn.getresponse()
            return res.status, res.read()
        finally:
            conn.close()
