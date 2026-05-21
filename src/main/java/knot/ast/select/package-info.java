/**
 * Read substrate — SELECT-shape AST nodes.
 *
 * <p>Pure data records describing queries over the {@code Spec}. Rendering lives in
 * {@code knot.compile.QueryCompiler} (sealed-interface dispatch sibling of
 * {@code ExprCompiler}).
 *
 * <p>The user surface is fluent immutable: every builder method on {@link knot.ast.select.Query}
 * ({@code .where()}, {@code .orderBy()}, {@code .limit()}, etc.) returns a new {@code Query} —
 * the AST is never mutated. {@code OntologyClass} exposes four explicit layer-targeted entry
 * points that <em>return</em> a {@code Query}: {@code cls.resolved()} (argmax view),
 * {@code cls.allSources()} (per-source jsonb provenance), {@code cls.fromSource(s)} (one
 * source's raw bindings), and {@code cls.unresolved()} (bindings still waiting on ER, across
 * every source). There is no default — every read declares its layer.
 *
 * <p>The AST carries {@code className} as a string (same convention as {@code Ref} in
 * {@code knot.ast.expr}) so the node itself is decoupled from {@code OntologyClass} instances.
 * A {@code Query} constructed via the class-side entry points also holds a back-reference to
 * its owning {@code Spec} so {@code q.sql()} compiles itself end-to-end.
 */
package knot.ast.select;
