package knot.compile;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;

import org.junit.jupiter.api.Test;

import knot.ast.expr.Raw;
import knot.ast.types.Array;
import knot.ast.types.Primitive;
import knot.ast.types.Vector;
import knot.spec.ClassKind;
import knot.spec.OntologyClass;
import knot.spec.Spec;
import knot.spec.VirtualClass;

/**
 * Tests for {@link Ddl#emitDdl(Spec, DdlOptions)} — ported from
 * {@code tests/unit/test_compile_ddl.py}. SQL-shape only (substring matches);
 * no SQL parser dep.
 *
 * <p>Fixture: a movie spec with Movie + Person + Credit (concrete) and a Title
 * abstract mixin plus a {@code DirectedMovie} virtual class — mirrors the
 * Python {@code movie_spec} pytest fixture.
 */
class DdlTest {

    // ---------------------------------------------------------------------
    // Fixture builders
    // ---------------------------------------------------------------------

    /**
     * Three concrete classes (Movie, Person, Credit) + one abstract mixin
     * (Title that contributes a {@code year} slot) + one virtual class
     * (DirectedMovie). Mirrors the Python {@code movie_spec} fixture.
     */
    private static Spec movieSpec() {
        Spec spec = new Spec("canonical_id");

        OntologyClass title = spec.addClass("Title", ClassKind.ABSTRACT, null, null, null);
        title.slot("year", Primitive.INTEGER);

        OntologyClass movie = spec.addClass("Movie", ClassKind.CONCRETE, title, null, null);
        movie.slot("name", Primitive.TEXT);
        movie.slot("genres", new Array(Primitive.TEXT));

        OntologyClass person = spec.addClass("Person");
        person.slot("name", Primitive.TEXT);

        OntologyClass credit = spec.addClass("Credit");
        credit.slot("role", Primitive.TEXT, /* required= */ true, false, null);
        credit.slot("movie", movie);
        credit.slot("person", person);

        // Virtual class — directed movies = movies where some credit has
        // role='director' and refers to this movie.
        movie.addVirtual("DirectedMovie",
                new Raw("EXISTS (SELECT 1 FROM credit WHERE role = 'director')"));

        var imdb = spec.addSource("imdb");
        imdb.bind(movie);
        imdb.bind(person);
        imdb.bind(credit);

        return spec;
    }

    private static Spec specWithVectorSlot() {
        return specWithVectorSlot("cosine");
    }

    private static Spec specWithVectorSlot(String metric) {
        Spec spec = new Spec("canonical_id");
        OntologyClass movie = spec.addClass("Movie");
        movie.slot("title", Primitive.TEXT);
        movie.slot("title_embedding", new Vector(384, metric));
        spec.addSource("imdb").bind(movie);
        return spec;
    }

    private static Spec specWithNestedVirtual() {
        Spec spec = new Spec("canonical_id");
        OntologyClass movie = spec.addClass("Movie");
        movie.slot("year", Primitive.INTEGER);

        VirtualClass v1 = movie.addVirtual("V1", new Raw("year >= 1900"));
        VirtualClass v2 = v1.addVirtual("V2", new Raw("year >= 1950"));
        v2.addVirtual("V3", new Raw("year >= 2000"));
        return spec;
    }

    // ---------------------------------------------------------------------
    // Top-level shape — counts, schema, weight, abstract handling
    // ---------------------------------------------------------------------

    @Test
    void defaultEmitsBindingsAndViewsPerConcrete() {
        List<String> stmts = Ddl.emitDdl(movieSpec());
        assertThat(stmts.get(0)).startsWith("CREATE SCHEMA IF NOT EXISTS knot_data");

        long canonical = stmts.stream()
                .filter(s -> s.startsWith("CREATE TABLE")
                        && !s.contains("_bindings")
                        && !s.contains("source_weight"))
                .count();
        long bindings = stmts.stream()
                .filter(s -> s.startsWith("CREATE TABLE") && s.contains("_bindings"))
                .count();
        long weight = stmts.stream()
                .filter(s -> s.startsWith("CREATE TABLE") && s.contains("source_weight"))
                .count();
        long resolvedViews = stmts.stream().filter(s -> s.contains("_resolved AS")).count();
        long allSourcesViews = stmts.stream().filter(s -> s.contains("_all_sources AS")).count();
        long virtualViews = stmts.stream()
                .filter(s -> (s.startsWith("CREATE VIEW") || s.startsWith("CREATE OR REPLACE VIEW"))
                        && !s.contains("_resolved AS")
                        && !s.contains("_all_sources AS"))
                .count();

        assertThat(canonical).isZero();           // no canonical table per class
        assertThat(bindings).isEqualTo(3);        // Movie + Person + Credit
        assertThat(weight).isEqualTo(1);          // source_weight
        assertThat(resolvedViews).isEqualTo(3);
        assertThat(allSourcesViews).isEqualTo(3);
        assertThat(virtualViews).isEqualTo(1);    // DirectedMovie
    }

    @Test
    void abstractClassHasNoTable() {
        List<String> stmts = Ddl.emitDdl(movieSpec());
        assertThat(stmts).noneMatch(s -> s.contains("knot_data.title ("));
    }

    @Test
    void noCanonicalTableEmitted() {
        List<String> stmts = Ddl.emitDdl(movieSpec());
        for (String name : List.of("movie", "person", "credit", "title")) {
            assertThat(stmts).noneMatch(s ->
                    s.startsWith("CREATE TABLE")
                            && s.contains("knot_data." + name + " (")
                            && !s.contains("_bindings"));
        }
    }

    // ---------------------------------------------------------------------
    // Bindings table shape — slot inheritance, array/classref types, raw_payload, er_metadata, PK
    // ---------------------------------------------------------------------

    @Test
    void concreteInheritsSlotsIntoBindings() {
        List<String> stmts = Ddl.emitDdl(movieSpec());
        String bindings = stmts.stream()
                .filter(s -> s.contains("movie_bindings") && s.startsWith("CREATE TABLE"))
                .findFirst().orElseThrow();
        assertThat(bindings).contains("year integer");
        assertThat(bindings).contains("name text");
    }

    @Test
    void arrayRendersAsPostgresArrayOnBindings() {
        List<String> stmts = Ddl.emitDdl(movieSpec());
        String bindings = stmts.stream()
                .filter(s -> s.contains("movie_bindings") && s.startsWith("CREATE TABLE"))
                .findFirst().orElseThrow();
        assertThat(bindings).contains("genres text[]");
    }

    @Test
    void classrefRendersAsTextOnBindings() {
        List<String> stmts = Ddl.emitDdl(movieSpec());
        String bindings = stmts.stream()
                .filter(s -> s.contains("credit_bindings") && s.startsWith("CREATE TABLE"))
                .findFirst().orElseThrow();
        assertThat(bindings).contains("movie text");
        assertThat(bindings).contains("person text");
    }

    @Test
    void bindingsCarryRawPayloadJsonbColumn() {
        List<String> stmts = Ddl.emitDdl(movieSpec());
        String bindings = stmts.stream()
                .filter(s -> s.startsWith("CREATE TABLE") && s.contains("movie_bindings"))
                .findFirst().orElseThrow();
        assertThat(bindings).contains("raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb");
    }

    @Test
    void bindingsTableHasErMetadataJsonbDefaultEmpty() {
        List<String> stmts = Ddl.emitDdl(movieSpec());
        String bindings = stmts.stream()
                .filter(s -> s.contains("movie_bindings"))
                .findFirst().orElseThrow();
        assertThat(bindings).contains("er_metadata jsonb NOT NULL DEFAULT '{}'::jsonb");
    }

    @Test
    void bindingsTableSlotsNullablePkDropsCanonical() {
        List<String> stmts = Ddl.emitDdl(movieSpec());
        String bindings = stmts.stream()
                .filter(s -> s.contains("movie_bindings"))
                .findFirst().orElseThrow();
        // canonical_id is NULLABLE in bindings (no NOT NULL)
        assertThat(bindings).doesNotContain("canonical_id text NOT NULL");
        assertThat(bindings).contains("canonical_id text");
        // source_name, source_identifier always NOT NULL — they're the PK.
        assertThat(bindings).contains("source_name text NOT NULL");
        assertThat(bindings).contains("source_identifier text NOT NULL");
        // PK = (source, source_id); no SCD2 valid_from in the key.
        assertThat(bindings).contains("PRIMARY KEY (source_name, source_identifier)");
        assertThat(bindings).doesNotContain("valid_from");
        assertThat(bindings).doesNotContain("valid_to");
    }

    // ---------------------------------------------------------------------
    // Kwargs — if_not_exists, schema/suffix overrides, emit_bindings, emit_descriptions
    // ---------------------------------------------------------------------

    @Test
    void ifNotExistsKwarg() {
        List<String> stmts = Ddl.emitDdl(movieSpec(),
                DdlOptions.builder().ifNotExists(true).build());
        assertThat(stmts.stream().filter(s -> s.contains("CREATE TABLE")))
                .allSatisfy(s -> assertThat(s).contains("CREATE TABLE IF NOT EXISTS"));
        assertThat(stmts.stream().filter(s -> s.contains("VIEW")))
                .allSatisfy(s -> assertThat(s).contains("CREATE OR REPLACE VIEW"));
    }

    @Test
    void emitBindingsFalseSkipsBindingsTables() {
        List<String> stmts = Ddl.emitDdl(movieSpec(),
                DdlOptions.builder().emitBindings(false).build());
        assertThat(stmts).noneMatch(s -> s.contains("_bindings"));
    }

    @Test
    void schemaAndSuffixKwargs() {
        List<String> stmts = Ddl.emitDdl(movieSpec(),
                DdlOptions.builder().schema("foo").bindingsSuffix("__src").build());
        assertThat(stmts).anyMatch(s -> s.contains("foo.movie__src"));
        assertThat(stmts).noneMatch(s -> s.contains("knot_data."));
    }

    @Test
    void emitDescriptionsCurrentlyNoop() {
        List<String> noDesc = Ddl.emitDdl(movieSpec(),
                DdlOptions.builder().emitDescriptions(false).build());
        List<String> withDesc = Ddl.emitDdl(movieSpec(),
                DdlOptions.builder().emitDescriptions(true).build());
        assertThat(noDesc).noneMatch(s -> s.contains("COMMENT ON"));
        assertThat(withDesc).noneMatch(s -> s.contains("COMMENT ON"));
    }

    @Test
    void noFkAltersEmitted() {
        List<String> stmts = Ddl.emitDdl(movieSpec());
        assertThat(stmts).noneMatch(s -> s.startsWith("ALTER TABLE"));
    }

    // ---------------------------------------------------------------------
    // Indexes — btree on canonical_id, full not partial, if_not_exists, disable
    // ---------------------------------------------------------------------

    @Test
    void canonicalIdxEmittedPerConcreteBindingsTable() {
        List<String> stmts = Ddl.emitDdl(movieSpec());
        List<String> idx = stmts.stream().filter(s -> s.startsWith("CREATE INDEX")).toList();
        assertThat(idx).hasSize(3);
        assertThat(idx).anyMatch(s -> s.contains("movie_bindings_canonical_idx"));
        assertThat(idx).anyMatch(s -> s.contains("credit_bindings_canonical_idx"));
        assertThat(idx).anyMatch(s -> s.contains("person_bindings_canonical_idx"));
    }

    @Test
    void indexesAreFullNotPartial() {
        List<String> stmts = Ddl.emitDdl(movieSpec());
        for (String s : stmts) {
            if (s.startsWith("CREATE INDEX")) {
                assertThat(s).doesNotContain("WHERE");
                assertThat(s).doesNotContain("valid_to");
            }
        }
    }

    @Test
    void canonicalIdxKeyedOnIdentifierOnly() {
        List<String> stmts = Ddl.emitDdl(movieSpec());
        String idx = stmts.stream()
                .filter(s -> s.contains("movie_bindings_canonical_idx"))
                .findFirst().orElseThrow();
        assertThat(idx).contains("(canonical_id)");
    }

    @Test
    void indexesIdempotentWithIfNotExists() {
        List<String> stmts = Ddl.emitDdl(movieSpec(),
                DdlOptions.builder().ifNotExists(true).build());
        assertThat(stmts.stream().filter(s -> s.startsWith("CREATE INDEX")))
                .allSatisfy(s -> assertThat(s).contains("CREATE INDEX IF NOT EXISTS"));
    }

    @Test
    void indexesCanBeDisabled() {
        List<String> stmts = Ddl.emitDdl(movieSpec(),
                DdlOptions.builder().emitIndexes(false).build());
        assertThat(stmts).noneMatch(s -> s.startsWith("CREATE INDEX"));
    }

    @Test
    void indexesSuppressedWhenBindingsSuppressed() {
        List<String> stmts = Ddl.emitDdl(movieSpec(),
                DdlOptions.builder().emitBindings(false).build());
        assertThat(stmts).noneMatch(s -> s.startsWith("CREATE INDEX"));
    }

    // ---------------------------------------------------------------------
    // Vector slots — extension gating, HNSW operator class, per-metric
    // ---------------------------------------------------------------------

    @Test
    void vectorExtensionOnlyWhenUsed() {
        assertThat(Ddl.emitDdl(movieSpec()))
                .allSatisfy(s -> assertThat(s).doesNotContain("CREATE EXTENSION"));
    }

    @Test
    void vectorExtensionEmittedOnceWhenUsed() {
        List<String> stmts = Ddl.emitDdl(specWithVectorSlot());
        List<String> ext = stmts.stream().filter(s -> s.contains("CREATE EXTENSION")).toList();
        assertThat(ext).containsExactly("CREATE EXTENSION IF NOT EXISTS vector;");
    }

    @Test
    void vectorColumnOnBindingsOnly() {
        List<String> stmts = Ddl.emitDdl(specWithVectorSlot());
        String bindings = stmts.stream()
                .filter(s -> s.contains("movie_bindings"))
                .findFirst().orElseThrow();
        assertThat(bindings).contains("title_embedding vector(384)");
        for (String s : stmts) {
            if (s.contains("movie_bindings")) continue;
            assertThat(s).doesNotContain("title_embedding vector(384)");
        }
    }

    @Test
    void vectorHnswIndexOnlyOnBindings() {
        List<String> stmts = Ddl.emitDdl(specWithVectorSlot("cosine"));
        List<String> hnsw = stmts.stream().filter(s -> s.contains("USING hnsw")).toList();
        assertThat(hnsw).hasSize(1);
        assertThat(hnsw.get(0)).contains("movie_bindings_title_embedding_hnsw_idx");
        assertThat(hnsw.get(0)).contains("vector_cosine_ops");
    }

    @Test
    void vectorHnswPicksOpsClassPerMetric() {
        for (var pair : List.of(
                List.of("cosine", "vector_cosine_ops"),
                List.of("l2", "vector_l2_ops"),
                List.of("ip", "vector_ip_ops"))) {
            String metric = pair.get(0);
            String ops = pair.get(1);
            List<String> stmts = Ddl.emitDdl(specWithVectorSlot(metric));
            List<String> hnsw = stmts.stream().filter(s -> s.contains("USING hnsw")).toList();
            assertThat(hnsw).as("metric=%s", metric).isNotEmpty();
            assertThat(hnsw).as("metric=%s", metric).allSatisfy(s -> assertThat(s).contains(ops));
        }
    }

    @Test
    void vectorHnswRespectsIfNotExists() {
        List<String> stmts = Ddl.emitDdl(specWithVectorSlot(),
                DdlOptions.builder().ifNotExists(true).build());
        List<String> hnsw = stmts.stream().filter(s -> s.contains("USING hnsw")).toList();
        assertThat(hnsw).isNotEmpty();
        assertThat(hnsw).allSatisfy(s -> assertThat(s).contains("CREATE INDEX IF NOT EXISTS"));
    }

    @Test
    void vectorHnswSuppressedWhenIndexesDisabled() {
        List<String> stmts = Ddl.emitDdl(specWithVectorSlot(),
                DdlOptions.builder().emitIndexes(false).build());
        assertThat(stmts).allSatisfy(s -> assertThat(s).doesNotContain("USING hnsw"));
        // Extension + column still emit — column type and the extension are not indexes.
        assertThat(stmts).anyMatch(s -> s.contains("CREATE EXTENSION"));
        assertThat(stmts).anyMatch(s -> s.contains("title_embedding vector(384)"));
    }

    @Test
    void vectorIndexesSkippedWhenBindingsSkipped() {
        List<String> stmts = Ddl.emitDdl(specWithVectorSlot(),
                DdlOptions.builder().emitBindings(false).build());
        List<String> hnsw = stmts.stream().filter(s -> s.contains("USING hnsw")).toList();
        assertThat(hnsw).isEmpty();
    }

    @Test
    void vectorConstructionRejectsBadDim() {
        assertThatThrownBy(() -> new Vector(0, "cosine"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("positive integer");
        assertThatThrownBy(() -> new Vector(-1, "cosine"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("positive integer");
    }

    @Test
    void vectorConstructionRejectsUnknownMetric() {
        assertThatThrownBy(() -> new Vector(384, "manhattan"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("metric");
    }

    // ---------------------------------------------------------------------
    // Virtual-of-virtual — nested chain FROMs concrete root, ANDs ancestors,
    // emitted in depth order
    // ---------------------------------------------------------------------

    @Test
    void virtualOfVirtualFromTargetsConcreteRoot() {
        List<String> stmts = Ddl.emitDdl(specWithNestedVirtual());
        String v3 = stmts.stream()
                .filter(s -> s.split("\n", 2)[0].toLowerCase().contains("knot_data.v3 as"))
                .findFirst().orElseThrow();
        assertThat(v3).contains("FROM knot_data.movie_resolved");
    }

    @Test
    void virtualOfVirtualWhereCombinesAncestorPredicates() {
        List<String> stmts = Ddl.emitDdl(specWithNestedVirtual());
        String v3 = stmts.stream()
                .filter(s -> s.split("\n", 2)[0].toLowerCase().contains("knot_data.v3 as"))
                .findFirst().orElseThrow();
        String where = v3.split("WHERE", 2)[1];
        // V3's WHERE includes its own + v2's + v1's predicates — three "year" mentions
        // since every ancestor predicate compares year.
        assertThat(where).contains("year");
    }

    @Test
    void virtualOfVirtualParentEmittedBeforeChild() {
        List<String> stmts = Ddl.emitDdl(specWithNestedVirtual());
        List<String> virtualViews = stmts.stream()
                .filter(s -> s.contains("VIEW")
                        && !s.split("\n", 2)[0].contains("_resolved")
                        && !s.split("\n", 2)[0].contains("_all_sources"))
                .toList();
        int v1Idx = -1, v2Idx = -1, v3Idx = -1;
        for (int i = 0; i < virtualViews.size(); i++) {
            String header = virtualViews.get(i).split("\n", 2)[0];
            if (header.contains("knot_data.v1 AS")) v1Idx = i;
            if (header.contains("knot_data.v2 AS")) v2Idx = i;
            if (header.contains("knot_data.v3 AS")) v3Idx = i;
        }
        assertThat(v1Idx).isLessThan(v2Idx);
        assertThat(v2Idx).isLessThan(v3Idx);

        // FROM always targets the concrete root, not the parent virtual.
        assertThat(virtualViews.get(v2Idx)).contains("FROM knot_data.movie_resolved");
        assertThat(virtualViews.get(v3Idx)).contains("FROM knot_data.movie_resolved");
    }

    // ---------------------------------------------------------------------
    // views-ddl path — only views, no bindings/indexes/weight
    // ---------------------------------------------------------------------

    @Test
    void viewsOnlyPath() {
        List<String> stmts = Ddl.emitDdl(movieSpec(),
                DdlOptions.builder()
                        .emitBindings(false)
                        .emitIndexes(false)
                        .emitWeightTable(false)
                        .build());
        assertThat(stmts).noneMatch(s -> s.startsWith("CREATE TABLE"));
        assertThat(stmts).noneMatch(s -> s.startsWith("CREATE INDEX"));
        // CREATE SCHEMA is always emitted; the rest should be view-shaped or nothing.
        assertThat(stmts.get(0)).contains("CREATE SCHEMA");
    }

    // ---------------------------------------------------------------------
    // DdlOptions defaults — every field matches the Python signature default
    // ---------------------------------------------------------------------

    @Test
    void ddlOptionsDefaultsMatchPython() {
        DdlOptions d = DdlOptions.defaults();
        assertThat(d.schema()).isEqualTo("knot_data");
        assertThat(d.bindingsSuffix()).isEqualTo("_bindings");
        assertThat(d.resolvedSuffix()).isEqualTo("_resolved");
        assertThat(d.allSourcesSuffix()).isEqualTo("_all_sources");
        assertThat(d.weightTableName()).isEqualTo("source_weight");
        assertThat(d.ifNotExists()).isFalse();
        assertThat(d.emitBindings()).isTrue();
        assertThat(d.emitResolvedViews()).isTrue();
        assertThat(d.emitAllSourcesViews()).isTrue();
        assertThat(d.emitVirtualViews()).isTrue();
        assertThat(d.emitIndexes()).isTrue();
        assertThat(d.emitWeightTable()).isTrue();
        assertThat(d.emitDescriptions()).isFalse();
    }
}
