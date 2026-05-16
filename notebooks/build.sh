#!/usr/bin/env bash
# Build .ipynb (executed, outputs baked in) from marimo .py sources.
# Run this before committing — the .ipynb is the GitHub-rendered
# artifact; the .py is the marimo source of truth.
set -euo pipefail

cd "$(dirname "$0")"

VENV="${VENV:-../.venv}"
PY="$VENV/bin/python"
MARIMO="$VENV/bin/marimo"

# Every .py except the shared spec module + this script's helpers.
for src in *.py; do
    case "$src" in
        movies_spec.py) continue ;;
    esac
    out="${src%.py}.ipynb"
    echo "→ $src → $out"
    # marimo export → ipynb (no execution, no outputs)
    "$MARIMO" export ipynb "$src" -o "$out" --include-outputs 2>/dev/null || \
        "$MARIMO" export ipynb "$src" -o "$out"
    # Execute the ipynb in-place so outputs land in the JSON.
    # cwd = notebooks/ so `from movies_spec import …` resolves.
    "$PY" - <<EOF
import nbformat
from nbclient import NotebookClient
path = "$out"
nb = nbformat.read(path, as_version=4)
NotebookClient(
    nb, timeout=60, kernel_name="python3",
    resources={"metadata": {"path": "."}},
).execute()
nbformat.write(nb, path)
EOF
done

echo "done."
