"""Slide-view probe. Five cells, each meant to render as one slide
when toggled to slides view in the marimo UI. Verifies that:

  - markdown headings show as slide titles
  - syntax-highlighted code blocks render at slide scale
  - pandas DataFrames render at slide scale
  - mo.mermaid() renders inline
  - cell outputs in general fit on a slide

If any of these look wrong in slides view, we'll need the layout
sidecar (``layouts/00_slides_probe.slides.json``) to tune slide
breaks + fragment timing.
"""

import marimo

__generated_with = "0.23.5"
app = marimo.App(layout_file="layouts/00_slides_probe.marimo.slides.json")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # knot

    a reflective ontology compiler

    ---

    *(slide 1 — title)*
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## just define some classes

    ```python
    from knot import Spec, types

    spec = Spec(identifier_slot_name="canonical_id", schema="knot")

    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)
    movie.slot("title_embedding", types.VECTOR(384))
    ```

    *(slide 2 — code block in markdown)*
    """)
    return


@app.cell
def _():
    # Slide 3 — a real pandas DataFrame. Tests how marimo renders a
    # tabular output inside slide view.
    import pandas as pd

    df = pd.DataFrame(
        [
            {"source": "imdb", "rows": 70, "embedded": 70, "resolved": 70},
            {"source": "tmdb", "rows": 68, "embedded": 68, "resolved": 68},
            {"source": "rottentomatoes", "rows": 65, "embedded": 65, "resolved": 65},
        ]
    )
    df
    return


@app.cell
def _(mo):
    # Slide 4 — mermaid diagram. Tests that mermaid renders inline at
    # slide scale (the cross-domain spec graphs would go here).
    mo.mermaid(
        """
        graph LR
            Movie[Movie] -->|director| Person[Person]
            MovieCredit[MovieCredit] -->|movie| Movie
            MovieCredit -->|person| Person
            imdb([imdb]) -.-> Movie
            imdb -.-> Person
            imdb -.-> MovieCredit
        """
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## ✔️ probe complete

    If you can read each of the previous 4 cells as its own
    readable slide, the default cell-per-slide mapping is good
    enough — we don't need the JSON sidecar.

    If anything looks crowded / unreadable, we'll author
    ``layouts/00_slides_probe.slides.json`` to tune slide
    breaks.

    *(slide 5 — outcome)*
    """)
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
