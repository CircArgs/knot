import marimo

__generated_with = "0.23.5"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell
def _(mo):
    mo.md(r"""
    # knot — spec builder tinker

    Build a `Spec` step by step using the dataclass builder. Edit the
    build cell and downstream cells re-render reactively.
    """)
    return


@app.cell
def _():
    from knot import SourceMap, Spec, VirtualClass, types

    return SourceMap, Spec, VirtualClass, types


@app.cell
def _(SourceMap, Spec, types):
    spec = Spec(id="movies", version="0.1")

    title = spec.add_class("Title", kind="abstract")
    title.slot("canonical_id", types.TEXT, identifier=True)
    title.slot("name", types.TEXT, required=True)

    movie = spec.add_class("Movie", is_a=title)
    movie.slot("year", types.INTEGER)
    movie.slot("runtime_minutes", types.INTEGER)
    movie.slot("genres", types.ARRAY(types.TEXT))

    person = spec.add_class("Person")
    person.slot("canonical_id", types.TEXT, identifier=True)
    person.slot("name", types.TEXT, required=True)

    credit = spec.add_class("Credit")
    credit.slot("canonical_id", types.TEXT, identifier=True)
    credit.slot("role", types.TEXT, required=True)
    credit.slot("movie", movie)
    credit.slot("person", person)

    spec.add_virtual_class(
        "DirectedMovie",
        base=movie,
        where=movie.has_any(credit, role="director"),
    )

    spec.add_constraint(
        "year_sane",
        primary=movie,
        body=movie.col.year >= 1888,
    )

    imdb = spec.add_source("imdb")
    binding = spec.bind(
        imdb,
        movie,
        identifier=movie["canonical_id"],
        accuracy=0.85,
    )
    binding.map(
        year=SourceMap(uses=("release_year",), sql="release_year"),
        runtime_minutes=SourceMap(
            uses=("runtime",),
            sql="(regexp_match(runtime, '[0-9]+'))[1]::int",
        ),
    )
    return movie, spec


@app.cell
def _(mo, spec):
    mo.md(f"""
    ## Spec summary

    | Field | Value |
    | - | - |
    | id | `{spec.id}` |
    | version | `{spec.version}` |
    | classes | {len(spec.classes)} |
    | sources | {len(spec.sources)} |
    | source_bindings | {len(spec.source_bindings)} |
    | constraints | {len(spec.constraints)} |
    """)
    return


@app.cell
def _(VirtualClass, mo, spec):
    class_lines = ["## Classes\n"]
    for cls in spec.classes:
        if isinstance(cls, VirtualClass):
            class_lines.append(
                f"### `{cls.name}` — virtual view of `{cls.is_a.name}`\n\n"
                f"```sql\n{cls.definition}\n```\n"
            )
        else:
            is_a_str = f" → `{cls.is_a.name}`" if cls.is_a else ""
            class_lines.append(f"### `{cls.name}` — {cls.kind}{is_a_str}\n")
            if not cls.slots:
                class_lines.append("_no own slots_\n")
            else:
                class_lines.append("| slot | type | identifier | required |\n| - | - | - | - |\n")
                for s in cls.slots:
                    class_lines.append(
                        f"| `{s.name}` | `{s.type}` | "
                        f"{'yes' if s.identifier else ''} | "
                        f"{'yes' if s.required else ''} |\n"
                    )
            class_lines.append("\n")
    mo.md("\n".join(class_lines))
    return


@app.cell
def _(mo, spec):
    binding_lines = ["## Source bindings\n"]
    for b in spec.source_bindings:
        binding_lines.append(
            f"### `{b.source.name}` → `{b.class_.name}`"
            f" (accuracy = {b.accuracy}, derived Beta = {b.beta_prior})\n"
        )
        binding_lines.append(f"identifier slot: `{b.identifier_slot.name}`\n\n")
        if b.mappings:
            binding_lines.append("| slot | SQL projection |\n| - | - |\n")
            for slot_name, sql in b.mappings.items():
                binding_lines.append(f"| `{slot_name}` | `{sql}` |\n")
        binding_lines.append("\n")
    mo.md("\n".join(binding_lines))
    return


@app.cell
def _(mo, spec):
    constraint_lines = ["## Constraints\n"]
    for c in spec.constraints:
        constraint_lines.append(
            f"### `{c.name}` on `{c.primary.name}` (severity: {c.severity})\n\n"
            f"```sql\n{c.body}\n```\n"
        )
    mo.md("\n".join(constraint_lines))
    return


@app.cell
def _(movie):
    # Demonstrate is_a / slot inheritance + typed types
    inherited = movie["name"]
    own = movie["year"]
    genres = movie["genres"]
    chain = [c.name for c in movie.chain()]
    {
        "chain": chain,
        "inherited (Title.name)": (inherited.name, str(inherited.type), inherited.required),
        "own (Movie.year)": (own.name, str(own.type), own.required),
        "array (Movie.genres)": (genres.name, str(genres.type), genres.is_fk),
    }
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Tinker

    Things to try in the build cell above:

    - Tune the binding's `accuracy=...` (0.0 - 1.0) — `b.beta_prior` re-derives.
    - Slot types come from `knot.types`: `types.INTEGER`, `types.ARRAY(types.TEXT)`, `movie`.
    - Add another class + binding for `tmdb`.
    - Add another constraint with a SQL body of your choice.
    """)
    return


if __name__ == "__main__":
    app.run()
