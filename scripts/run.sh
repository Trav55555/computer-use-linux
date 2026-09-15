#!/bin/sh
set -eu
plugin_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec uv run --frozen --no-dev --project "$plugin_root" python3 "$plugin_root/scripts/server.py"
