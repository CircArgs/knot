"""Build-time version source for the knot package.

`pyproject.toml` reads ``__version__`` from this file via
``[tool.hatch.version]``. CI overwrites it before each build.

Runtime ``knot.__version__`` is read from installed package metadata
via ``importlib.metadata`` — see ``knot/__init__.py``.
"""

__version__ = "0.0.0"
