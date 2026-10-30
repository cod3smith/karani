#!/bin/sh
# Run one of the generators with a Python that can import karani.
#
# The reference pages are generated from karani itself -- the argparse
# parser, the MCP server's tool listing, the pydantic config schema -- so
# the docs build needs the package importable. Which interpreter that is
# differs by machine, and hard-coding `python3` in package.json fails on
# every setup where karani is not installed globally (which is all of them).
#
# Tried in order:
#   $KARANI_PYTHON        an interpreter named explicitly. Wins, always.
#   ../.venv/bin/python   the repository's own virtualenv, after `uv sync`.
#   uv run --project ..   let uv resolve it, which also works with no venv.
#   python3               karani installed globally, or already on the path.
#
# Usage: scripts/python.sh scripts/generate_cli.py
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/../.." && pwd)

if [ -n "${KARANI_PYTHON:-}" ]; then
  exec "$KARANI_PYTHON" "$@"
fi

if [ -x "$repo/.venv/bin/python" ]; then
  exec "$repo/.venv/bin/python" "$@"
fi

if command -v uv >/dev/null 2>&1; then
  exec uv run --project "$repo" python "$@"
fi

if python3 -c 'import karani' >/dev/null 2>&1; then
  exec python3 "$@"
fi

echo "error: no Python that can import karani." >&2
echo "The reference pages are generated from karani itself, so the docs" >&2
echo "build needs the package importable. Either run 'uv sync' in $repo," >&2
echo "or set KARANI_PYTHON to an interpreter that has karani installed." >&2
exit 1
