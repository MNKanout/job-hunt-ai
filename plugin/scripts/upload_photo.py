#!/usr/bin/env python3
"""scripts/upload_photo.py — save the photo printed on your CV (the plugin).

    python3 scripts/upload_photo.py --token <script token> <photo.jpg|photo.png>
    python3 scripts/upload_photo.py --token <script token> --remove

The image goes straight from disk to the job-app server's save_photo tool, so
it never passes through the conversation. JPEG or PNG, at most 2 MB. Prints
one JSON status line.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jobapp_api import Api, ApiError  # noqa: E402

MAX_BYTES = 2 * 1024 * 1024


def fail(error: str, message: str) -> None:
    print(json.dumps({"status": "failed", "error": error, "message": message}))
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", required=True)
    parser.add_argument("photo", nargs="?")
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    if args.remove:
        payload = {"remove": True}
    else:
        if not args.photo:
            fail("bad_arguments", "give the path of a JPEG or PNG photo, or --remove")
        path = Path(args.photo).expanduser()
        if not path.is_file():
            fail("not_found", f"no such file: {path}")
        data = path.read_bytes()
        if not (data[:3] == b"\xff\xd8\xff" or data[:4] == b"\x89PNG"):
            fail("bad_image", "the photo must be a JPEG or PNG file")
        if len(data) > MAX_BYTES:
            fail("bad_image", "the photo is larger than 2 MB; save a smaller copy first")
        payload = {"image_base64": base64.b64encode(data).decode()}
    try:
        result = Api(args.token).call("save_photo", payload)
    except ApiError as e:
        fail(e.error, e.message)
        return
    print(json.dumps(result))


if __name__ == "__main__":
    main()
