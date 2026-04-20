#!/usr/bin/env bash
# Double-clickable launcher for NonvisualAudio (macOS).
# Finder opens .command files in Terminal, which gives you a live stderr feed.
#
# Environment:
#   NVA_DEBUG=1   enables verbose logging (default when launched via this script)

set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo "Error: .venv not found. Run:"
    echo "  python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
    exit 1
fi

export NVA_DEBUG="${NVA_DEBUG:-1}"
echo "Starting NonvisualAudio (NVA_DEBUG=$NVA_DEBUG)..."
exec .venv/bin/python -m nonvisualaudio
