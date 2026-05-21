package knot.compile;

/**
 * Keyword arguments for {@link Ddl#emitDdl(knot.spec.Spec, DdlOptions)}.
 *
 * <p>Mirrors the Python {@code emit_ddl(spec, *, schema=…, bindings_suffix=…, …)}
 * keyword-only parameter pack. A {@code record} with a {@link #defaults()} factory
 * keeps construction one-liner-able for the common case while staying explicit
 * about every knob — no positional kwargs, no hidden defaults sprinkled across
 * call sites.
 *
 * <p>Use {@link Builder} (via {@link #builder()}) when only a few fields differ
 * from the defaults; constructing a {@code DdlOptions} directly is also fine when
 * every value is meaningful at the call site.
 *
 * @param schema postgres schema for the data plane. Created with {@code IF NOT EXISTS}
 *               regardless of {@link #ifNotExists}.
 * @param bindingsSuffix suffix appended to the class name to form the bindings table
 *                       name (default {@code "_bindings"}).
 * @param resolvedSuffix suffix appended to the class name for the resolved view
 *                       (default {@code "_resolved"}).
 * @param allSourcesSuffix suffix appended to the class name for the all-sources
 *                         provenance view (default {@code "_all_sources"}).
 * @param weightTableName name of the runtime weight-policy table (default
 *                        {@code "source_weight"}).
 * @param ifNotExists when {@code true}, emit {@code CREATE TABLE IF NOT EXISTS},
 *                    {@code CREATE OR REPLACE VIEW}, {@code CREATE INDEX IF NOT EXISTS}.
 * @param emitBindings when {@code false}, skip the per-class bindings tables entirely.
 * @param emitResolvedViews when {@code false}, skip the per-class {@code _resolved} views.
 * @param emitAllSourcesViews when {@code false}, skip the per-class {@code _all_sources} views.
 * @param emitVirtualViews when {@code false}, skip the per-virtual-class views.
 * @param emitIndexes when {@code false}, skip btree + HNSW indexes on bindings tables.
 * @param emitWeightTable when {@code false}, skip the invariant weight-policy table.
 * @param emitDescriptions when {@code true}, follow each entity with
 *                        {@code COMMENT ON …} for any non-empty {@code description}.
 */
public record DdlOptions(
        String schema,
        String bindingsSuffix,
        String resolvedSuffix,
        String allSourcesSuffix,
        String weightTableName,
        boolean ifNotExists,
        boolean emitBindings,
        boolean emitResolvedViews,
        boolean emitAllSourcesViews,
        boolean emitVirtualViews,
        boolean emitIndexes,
        boolean emitWeightTable,
        boolean emitDescriptions) {

    /** The Python {@code emit_ddl} kwarg defaults, verbatim. */
    public static DdlOptions defaults() {
        return new DdlOptions(
                "knot_data",
                "_bindings",
                "_resolved",
                "_all_sources",
                "source_weight",
                false,
                true,
                true,
                true,
                true,
                true,
                true,
                false);
    }

    /** Start from {@link #defaults()} and override fields. */
    public static Builder builder() {
        return new Builder(defaults());
    }

    /** Start from this options instance and override fields. */
    public Builder toBuilder() {
        return new Builder(this);
    }

    /**
     * Fluent override-the-defaults builder. Mirrors how Python callers pass
     * only the kwargs they care about.
     */
    public static final class Builder {
        private String schema;
        private String bindingsSuffix;
        private String resolvedSuffix;
        private String allSourcesSuffix;
        private String weightTableName;
        private boolean ifNotExists;
        private boolean emitBindings;
        private boolean emitResolvedViews;
        private boolean emitAllSourcesViews;
        private boolean emitVirtualViews;
        private boolean emitIndexes;
        private boolean emitWeightTable;
        private boolean emitDescriptions;

        private Builder(DdlOptions seed) {
            this.schema = seed.schema;
            this.bindingsSuffix = seed.bindingsSuffix;
            this.resolvedSuffix = seed.resolvedSuffix;
            this.allSourcesSuffix = seed.allSourcesSuffix;
            this.weightTableName = seed.weightTableName;
            this.ifNotExists = seed.ifNotExists;
            this.emitBindings = seed.emitBindings;
            this.emitResolvedViews = seed.emitResolvedViews;
            this.emitAllSourcesViews = seed.emitAllSourcesViews;
            this.emitVirtualViews = seed.emitVirtualViews;
            this.emitIndexes = seed.emitIndexes;
            this.emitWeightTable = seed.emitWeightTable;
            this.emitDescriptions = seed.emitDescriptions;
        }

        public Builder schema(String v) { this.schema = v; return this; }
        public Builder bindingsSuffix(String v) { this.bindingsSuffix = v; return this; }
        public Builder resolvedSuffix(String v) { this.resolvedSuffix = v; return this; }
        public Builder allSourcesSuffix(String v) { this.allSourcesSuffix = v; return this; }
        public Builder weightTableName(String v) { this.weightTableName = v; return this; }
        public Builder ifNotExists(boolean v) { this.ifNotExists = v; return this; }
        public Builder emitBindings(boolean v) { this.emitBindings = v; return this; }
        public Builder emitResolvedViews(boolean v) { this.emitResolvedViews = v; return this; }
        public Builder emitAllSourcesViews(boolean v) { this.emitAllSourcesViews = v; return this; }
        public Builder emitVirtualViews(boolean v) { this.emitVirtualViews = v; return this; }
        public Builder emitIndexes(boolean v) { this.emitIndexes = v; return this; }
        public Builder emitWeightTable(boolean v) { this.emitWeightTable = v; return this; }
        public Builder emitDescriptions(boolean v) { this.emitDescriptions = v; return this; }

        public DdlOptions build() {
            return new DdlOptions(
                    schema,
                    bindingsSuffix,
                    resolvedSuffix,
                    allSourcesSuffix,
                    weightTableName,
                    ifNotExists,
                    emitBindings,
                    emitResolvedViews,
                    emitAllSourcesViews,
                    emitVirtualViews,
                    emitIndexes,
                    emitWeightTable,
                    emitDescriptions);
        }
    }
}
