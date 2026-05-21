package knot.spec;

import java.util.List;
import knot.ast.expr.Ref;

/**
 * {@code cls.bindingsCol().get("source_identifier")} returns a {@link Ref}
 * to a bindings-table column that isn't a spec slot. Closes the last
 * user-facing {@code Raw(...)} escape hatch — the ER worker's pending
 * queue, per-source filters, and audit reads all author through typed
 * Refs now.
 *
 * <p>Mirrors the Python {@code _BindingsColAccess} helper. Only valid in
 * BINDINGS-layer queries ({@code cls.unresolved()},
 * {@code cls.fromSource(s)}); postgres errors at execute time if used
 * against the resolved or all_sources views.
 *
 * <p>Typos raise {@link IllegalArgumentException} at lookup time.
 */
public final class BindingsColAccess {

    /**
     * The closed set of columns the bindings table carries that aren't
     * spec-declared slots. Mirrors the Python {@code _BINDINGS_COLUMNS}
     * tuple in {@code knot/spec.py}.
     */
    public static final List<String> BINDINGS_COLUMNS = List.of(
            "source_name", "source_identifier", "er_metadata", "raw_payload");

    private final OntologyClass cls;

    BindingsColAccess(OntologyClass cls) {
        this.cls = cls;
    }

    public Ref get(String name) {
        if (!BINDINGS_COLUMNS.contains(name)) {
            throw new IllegalArgumentException(
                    "'" + name + "' is not a bindings-table column; valid: "
                            + BINDINGS_COLUMNS + ". For spec-declared slots use cls.col()");
        }
        return new Ref(cls.name(), name);
    }
}
