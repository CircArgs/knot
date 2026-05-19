#!/usr/bin/env bash
# Build .ipynb (executed, outputs baked in) from marimo .py sources.
# Run this before committing — the .ipynb is the GitHub-rendered
# artifact; the .py is the marimo source of truth.
set -euo pipefail

cd "$(dirname "$0")"

VENV="${VENV:-../.venv}"
PY="$VENV/bin/python"
MARIMO="$VENV/bin/marimo"

# Every .py except shared helpers (``_*.py`` private modules) and
# the spec package (``movies_spec/`` is a directory, not a top-level
# .py file, so the glob skips it automatically).
for src in *.py; do
    case "$src" in
        _*.py) continue ;;
    esac
    out="${src%.py}.ipynb"
    echo "→ $src → $out"
    # marimo export → ipynb (NO --include-outputs; that would execute
    # the notebook and we want NotebookClient to be the only executor
    # so side-effects don't run twice).
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
