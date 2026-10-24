#!/bin/bash
# Make sure this computer can build and print documents for the job-hunt-ai plugin.
#
# The build script needs Python with Jinja2. It gets its own virtual
# environment in the plugin's data folder, made once and kept across plugin
# updates, so nothing is installed into the user's own Python. It prints
# nothing when all is well; otherwise one short note per missing tool, which
# Claude reads at session start and can pass on.
set -u
DATA="${CLAUDE_PLUGIN_DATA:?}"
VENV="$DATA/venv"
notes=()

if ! command -v python3 >/dev/null 2>&1; then
  notes+=("python3 is not installed: the job-hunt-ai plugin needs Python 3.10+ to build documents.")
elif [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c "import jinja2" >/dev/null 2>&1; then
  mkdir -p "$DATA"
  if ! { python3 -m venv "$VENV" && "$VENV/bin/pip" install --quiet --disable-pip-version-check "jinja2>=3.1"; } >"$DATA/setup.log" 2>&1; then
    notes+=("Could not prepare the job-app Python environment (see $DATA/setup.log).")
  fi
fi

for tool in pdfinfo pdftotext; do
  command -v "$tool" >/dev/null 2>&1 || notes+=("$tool is missing: install poppler (macOS: brew install poppler, Debian/Ubuntu: apt install poppler-utils).")
done

chrome=""
for c in google-chrome chrome chromium "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" "/Applications/Chromium.app/Contents/MacOS/Chromium"; do
  if command -v "$c" >/dev/null 2>&1 || [ -x "$c" ]; then chrome="$c"; break; fi
done
[ -n "$chrome" ] || notes+=("Google Chrome is missing: the job-app builder prints with it, and the submitter fills forms in it.")
command -v npx >/dev/null 2>&1 || notes+=("Node.js (npx) is missing: the browser tools need it.")

if [ ${#notes[@]} -gt 0 ]; then
  echo "job-hunt-ai plugin setup notes:"
  for n in "${notes[@]}"; do echo "- $n"; done
fi
exit 0
