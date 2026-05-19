#!/usr/bin/env bash
# Build .ipynb (executed, outputs baked in) from marimo .py sources.
# Run this before committing — the .ipynb is the GitHub-rendered
# artifact; the .py is the marimo source of truth.
set -euo pipefail

cd "$(dirname "$0")"

VENV="${VENV:-../.venv}"
PY="$VENV/bin/python"
MARIMO="$VENV/bin/marimo"

# marimo notebooks use the ``.marimo.py`` convention so the VS Code
# extension can claim them via workbench.editorAssociations without
# stealing every .py in the project. Plain ``.py`` files
# (``_demo.py``, ``_viz.py``, the ``media_spec/`` package) stay
# regular Python files.
for src in *.marimo.py; do
    out="${src%.marimo.py}.ipynb"
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
