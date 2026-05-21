package knot.spec;

import java.util.ArrayList;
import java.util.List;

/**
 * A named external system (imdb, tmdb).
 *
 * <p>Sources are created via {@link Spec#addSource(String)} which sets the
 * back-reference {@code _spec} so {@link #bind(OntologyClass)} can register
 * the resulting {@link SourceBinding} on its owning spec.
 *
 * <p>Mirrors the Python {@code @dataclass(slots=True) Source}. Unlike the
 * Python record this is a regular class — the spec back-reference mutates
 * once on registration, and {@link #bind(OntologyClass)} writes to the
 * owning spec's bindings list. A {@code record} can't represent that
 * mutable back-reference cleanly.
 */
public final class Source {

    private final String name;
    private final String description;
    private Spec spec;

    /** Bare constructor — used by tests; production code goes through {@link Spec#addSource}. */
    public Source(String name, String description) {
        Names.checkName("Source", name);
        this.name = name;
        this.description = description;
    }

    /** Package-private — used by {@link Spec} to attach the back-reference. */
    Source(String name, String description, Spec spec) {
        this(name, description);
        this.spec = spec;
    }

    public String name() {
        return name;
    }

    public String description() {
        return description;
    }

    /** Package-private back-reference accessor (used by {@link SourceBinding}). */
    Spec spec() {
        return spec;
    }

    /** Package-private setter — used by {@link Spec#include(Spec)} to re-root on merge. */
    void setSpec(Spec spec) {
        this.spec = spec;
    }

    /**
     * All {@link SourceBinding} rows on the spec owned by THIS source.
     * Saves callers from filtering {@link Spec#sourceBindings()}.
     */
    public List<SourceBinding> bindings() {
        if (spec == null) {
            throw new IllegalStateException(
                    "Source '" + name + "' is not attached to a Spec "
                            + "(create via spec.addSource(...))");
        }
        var out = new ArrayList<SourceBinding>();
        for (var b : spec.sourceBindings()) {
            if (b.source() == this) {
                out.add(b);
            }
        }
        return out;
    }

    /**
     * Create a binding from this source to {@code cls} and register it on
     * the owning spec.
     *
     * <p>Weights are runtime-only — set them via
     * {@link SourceBinding#upsertWeightSql()} after deploy. Until then,
     * every slot resolves at weight 0 (the resolver's COALESCE fallback).
     */
    public SourceBinding bind(OntologyClass cls) {
        return bind(cls, null);
    }

    /** Overload of {@link #bind(OntologyClass)} accepting a binding-level description. */
    public SourceBinding bind(OntologyClass cls, String description) {
        if (spec == null) {
            throw new IllegalStateException(
                    "Source '" + name + "' not attached to a Spec — create via "
                            + "spec.addSource() rather than constructing directly");
        }
        for (var b : spec.sourceBindings()) {
            if (b.source() == this && b.ontologyClass() == cls) {
                throw new IllegalArgumentException(
                        "Spec already has a binding for '" + name + "' → '" + cls.name() + "'");
            }
        }
        var binding = new SourceBinding(this, cls, description);
        spec.registerBinding(binding);
        return binding;
    }
}
