#!/usr/bin/env bash
# Start the app. Requires `uv` and a logged-in `claude` CLI.
set -euo pipefail
cd "$(dirname "$0")"
exec uv run --quiet python -m src.web "$@"
