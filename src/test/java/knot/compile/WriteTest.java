package knot.compile;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;

import org.junit.jupiter.api.Test;

import knot.ast.types.Array;
import knot.ast.types.Enum;
import knot.ast.types.Primitive;
import knot.ast.types.Vector;
import knot.spec.OntologyClass;
import knot.spec.Spec;
import knot.spec.SourceBinding;

/**
 * Tests for {@link Write} — ported from:
 * <ul>
 *   <li>{@code tests/unit/test_compile_data_io.py}</li>
 *   <li>{@code tests/unit/test_compile_validate_rows.py}</li>
 *   <li>{@code tests/unit/test_compile_update_slot.py}</li>
 *   <li>{@code tests/unit/test_async_er.py}</li>
 *   <li>{@code tests/unit/test_compile_assign_canonicals.py}</li>
 * </ul>
 *
 * <p>SQL-shape only — substring matches against emitted SQL. No SQL parser dep.
 */
class WriteTest {

    private static final WriteOptions DEFAULTS = WriteOptions.defaults();

    // -------------------------------------------------------------------------
    // Fixtures
    // -------------------------------------------------------------------------

    /** Basic movie spec: Movie + Person + one imdb binding. Mirrors Python movie_spec fixture. */
    private static Spec movieSpec() {
        var spec = Spec.builder().identifierSlotName("canonical_id").build();
        var person = spec.addClass("Person");
        person.slot("name", Primitive.TEXT, true, false, null);

        var movie = spec.addClass("Movie");
        movie.slot("name", Primitive.TEXT, true, false, null);
        movie.slot("year", Primitive.INTEGER, false, false, null);
        movie.slot("genres", new Array(Primitive.TEXT), false, false, null);
        movie.slot("director", person);

        var imdb = spec.addSource("imdb");
        imdb.bind(movie);
        imdb.bind(person);
        return spec;
    }

    /** imdb_movie binding from movieSpec. */
    private static SourceBinding movieBinding(Spec spec) {
        var movie = (OntologyClass) spec.classes().get("Movie");
        var imdb = spec.sources().stream().filter(s -> s.name().equals("imdb")).findFirst().orElseThrow();
        return movie.bindingFor(imdb);
    }

    /** Kitchen-sink spec with every interesting slot type for update_slot tests. */
    private static Spec kitchenSinkSpec() {
        var spec = Spec.builder().identifierSlotName("canonical_id").build();
        var person = spec.addClass("Person");
        person.slot("name", Primitive.TEXT, true, false, null);
        var movie = spec.addClass("Movie");
        movie.slot("title", Primitive.TEXT, true, false, null);
        movie.slot("year", Primitive.INTEGER, false, false, null);
        movie.slot("genres", new Array(Primitive.TEXT), false, false, null);
        movie.slot("title_embedding", new Vector(384, "cosine"), false, false, null);
        movie.slot("director", person);
        var imdb = spec.addSource("imdb");
        imdb.bind(movie);
        imdb.bind(person);
        return spec;
    }

    private static SourceBinding kitchenSinkBinding(Spec spec) {
        var movie = (OntologyClass) spec.classes().get("Movie");
        var imdb = spec.sources().stream().filter(s -> s.name().equals("imdb")).findFirst().orElseThrow();
        return movie.bindingFor(imdb);
    }

    /** Spec with all primitive types + vector for validate_rows tests. */
    private static Spec allTypesSpec() {
        var spec = Spec.builder().identifierSlotName("canonical_id").build();
        var movie = spec.addClass("Movie");
        movie.slot("title", Primitive.TEXT, true, false, null);
        movie.slot("year", Primitive.INTEGER, true, false, null);
        movie.slot("score", Primitive.FLOAT, false, false, null);
        movie.slot("active", Primitive.BOOLEAN, false, false, null);
        movie.slot("release_date", Primitive.DATE, false, false, null);
        movie.slot("created_at", Primitive.TIMESTAMP, false, false, null);
        movie.slot("genres", new Array(Primitive.TEXT), false, false, null);
        movie.slot("embedding", new Vector(128, "cosine"), false, false, null);
        var imdb = spec.addSource("imdb");
        imdb.bind(movie);
        return spec;
    }

    private static SourceBinding allTypesBinding(Spec spec) {
        var movie = (OntologyClass) spec.classes().get("Movie");
        var imdb = spec.sources().stream().filter(s -> s.name().equals("imdb")).findFirst().orElseThrow();
        return movie.bindingFor(imdb);
    }

    /** 3-class spec: Person, Movie (FK director → Person), Credit (FK movie + person). */
    private static Spec movieCreditSpec() {
        var spec = Spec.builder().identifierSlotName("canonical_id").build();
        var person = spec.addClass("Person");
        person.slot("name", Primitive.TEXT, true, false, null);
        var movie = spec.addClass("Movie");
        movie.slot("title", Primitive.TEXT, true, false, null);
        movie.slot("director", person);
        var credit = spec.addClass("Credit");
        credit.slot("role", Primitive.TEXT, false, false, null);
        credit.slot("movie", movie);
        credit.slot("person", person);
        var imdb = spec.addSource("imdb");
        imdb.bind(person);
        imdb.bind(movie);
        imdb.bind(credit);
        return spec;
    }

    private static SourceBinding bindingFor(Spec spec, String className) {
        var cls = (OntologyClass) spec.classes().get(className);
        var imdb = spec.sources().stream().filter(s -> s.name().equals("imdb")).findFirst().orElseThrow();
        return cls.bindingFor(imdb);
    }

    // -------------------------------------------------------------------------
    // emitBindingWriteSql — basic shape
    // -------------------------------------------------------------------------

    @Test
    void writeSqlReturnsInsertWithOnConflict() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        var sql = Write.emitBindingWriteSql(b, null, DEFAULTS);
        assertThat(sql).contains("INSERT INTO");
        assertThat(sql).contains("ON CONFLICT");
    }

    @Test
    void writeSqlTargetsBindingsTableAndBakesSource() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        var sql = Write.emitBindingWriteSql(b, null, DEFAULTS);
        assertThat(sql).contains("INSERT INTO knot_data.movie_bindings");
        assertThat(sql).contains("'imdb'");
        assertThat(sql).contains("jsonb_array_elements(%(rows)s::jsonb)");
    }

    @Test
    void writeSqlUsesPkConflictTarget() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        var sql = Write.emitBindingWriteSql(b, null, DEFAULTS);
        assertThat(sql).contains("ON CONFLICT (source_name, source_identifier) DO UPDATE SET");
    }

    @Test
    void writeSqlPreservesCanonicalIdAndErMetadata() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        var sql = Write.emitBindingWriteSql(b, null, DEFAULTS);
        String setBlock = sql.split("ON CONFLICT", 2)[1];
        assertThat(setBlock).doesNotContain("canonical_id = EXCLUDED");
        assertThat(setBlock).doesNotContain("er_metadata = EXCLUDED");
    }

    @Test
    void writeSqlOverwritesNonIdentitySlots() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        var sql = Write.emitBindingWriteSql(b, null, DEFAULTS);
        String setBlock = sql.split("ON CONFLICT", 2)[1];
        assertThat(setBlock).contains("year = EXCLUDED.year");
        assertThat(setBlock).contains("name = EXCLUDED.name");
        assertThat(setBlock).contains("raw_payload = EXCLUDED.raw_payload");
    }

    @Test
    void writeSqlIncludesRawPayloadColumn() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        var sql = Write.emitBindingWriteSql(b, null, DEFAULTS);
        assertThat(sql).contains("raw_payload)");
        assertThat(sql).contains("r AS __raw_payload");
        assertThat(sql).contains("raw.__raw_payload");
    }

    @Test
    void writeSqlUsesJsonbExtractionForPassthroughText() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        var sql = Write.emitBindingWriteSql(b, null, DEFAULTS);
        // name is a passthrough TEXT slot.
        assertThat(sql).contains("raw.name::text");
    }

    @Test
    void writeSqlArrayGoesthroughUnnestRoundTrip() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        var sql = Write.emitBindingWriteSql(b, null, DEFAULTS);
        assertThat(sql).contains("jsonb_array_elements(raw.__raw_payload->'genres')");
    }

    @Test
    void writeSqlDeterministicAcrossCalls() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        assertThat(Write.emitBindingWriteSql(b, null, DEFAULTS))
                .isEqualTo(Write.emitBindingWriteSql(b, null, DEFAULTS));
    }

    @Test
    void writeSqlSchemaAndSuffixKwargs() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        var sql = Write.emitBindingWriteSql(b, null, new WriteOptions("alt", "__s"));
        assertThat(sql).contains("alt.movie__s");
        assertThat(sql).doesNotContain("knot_data.movie_bindings");
    }

    @Test
    void writeSqlApostropheInSourceNameEscaped() {
        var spec2 = Spec.builder().identifierSlotName("canonical_id").build();
        spec2.addClass("Movie");
        var src2 = spec2.addSource("o'brien");
        var b2 = src2.bind((OntologyClass) spec2.classes().get("Movie"));
        var sql = Write.emitBindingWriteSql(b2, null, DEFAULTS);
        assertThat(sql).contains("'o''brien'");
    }

    @Test
    void writeSqlAbstractClassRejected() {
        var spec = Spec.builder().identifierSlotName("canonical_id").build();
        var abs = spec.addAbstractClass("A");
        var src = spec.addSource("s");
        var b = new SourceBinding(src, abs);
        assertThatThrownBy(() -> Write.emitBindingWriteSql(b, null, DEFAULTS))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("abstract");
    }

    @Test
    void writeSqlEmitsNoDoBlock() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        assertThat(Write.emitBindingWriteSql(b, null, DEFAULTS)).doesNotContain("DO $$");
    }

    // -------------------------------------------------------------------------
    // emitBindingWriteSql — returning kwarg
    // -------------------------------------------------------------------------

    @Test
    void returningNullNoReturningClause() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        var sql = Write.emitBindingWriteSql(b, null, DEFAULTS);
        assertThat(sql).doesNotContain("RETURNING");
    }

    @Test
    void returningStarAppendsReturningStarAndSemicolon() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        var sql = Write.emitBindingWriteSql(b, "*", DEFAULTS);
        assertThat(sql).contains("RETURNING *");
        assertThat(sql.strip()).endsWith(";");
    }

    @Test
    void returningListAppendsNamedColumns() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        var sql = Write.emitBindingWriteSql(b, List.of("year", "name"), DEFAULTS);
        assertThat(sql).contains("RETURNING year, name");
        assertThat(sql.strip()).endsWith(";");
    }

    @Test
    void returningBadSlotThrowsIllegalArgument() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        assertThatThrownBy(() -> Write.emitBindingWriteSql(b, List.of("bogus_slot"), DEFAULTS))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("bogus_slot");
    }

    // -------------------------------------------------------------------------
    // emitValidateRowsSql
    // -------------------------------------------------------------------------

    @Test
    void validateRowsContainsRowsParameter() {
        var spec = allTypesSpec();
        var b = allTypesBinding(spec);
        var sql = Write.emitValidateRowsSql(b, DEFAULTS);
        assertThat(sql).contains("%(rows)s::jsonb");
    }

    @Test
    void validateRowsOutputColumnsPresent() {
        var spec = allTypesSpec();
        var b = allTypesBinding(spec);
        var sql = Write.emitValidateRowsSql(b, DEFAULTS);
        for (var col : List.of("row_index", "source_identifier", "violation_kind",
                "slot_name", "detail", "payload")) {
            assertThat(sql).as("column '%s' missing", col).contains(col);
        }
    }

    @Test
    void validateRowsMissingSourceIdentifierCheck() {
        var spec = allTypesSpec();
        var b = allTypesBinding(spec);
        var sql = Write.emitValidateRowsSql(b, DEFAULTS);
        assertThat(sql).contains("missing_source_identifier");
        assertThat(sql).contains("payload->>'source_identifier' IS NULL");
    }

    @Test
    void validateRowsRequiredSlotMissingChecks() {
        var spec = allTypesSpec();
        var b = allTypesBinding(spec);
        var sql = Write.emitValidateRowsSql(b, DEFAULTS);
        assertThat(sql).contains("missing_required_slot");
        assertThat(sql).contains("'title'");
        assertThat(sql).contains("'year'");
    }

    @Test
    void validateRowsIntegerRegexCheck() {
        var spec = allTypesSpec();
        var b = allTypesBinding(spec);
        var sql = Write.emitValidateRowsSql(b, DEFAULTS);
        assertThat(sql).contains("'^-?[0-9]+$'");
        assertThat(sql).contains("'year'");
    }

    @Test
    void validateRowsFloatRegexCheck() {
        var spec = allTypesSpec();
        var b = allTypesBinding(spec);
        var sql = Write.emitValidateRowsSql(b, DEFAULTS);
        assertThat(sql).contains("[0-9]+)");
    }

    @Test
    void validateRowsVectorJsonbArrayLengthCheck() {
        var spec = allTypesSpec();
        var b = allTypesBinding(spec);
        var sql = Write.emitValidateRowsSql(b, DEFAULTS);
        assertThat(sql).contains("jsonb_array_length");
        assertThat(sql).contains("128");
        assertThat(sql).contains("jsonb_typeof");
    }

    @Test
    void validateRowsOptionalSlotNoMissingCheck() {
        var spec = Spec.builder().identifierSlotName("canonical_id").build();
        var cls = spec.addClass("Thing");
        cls.slot("optional_year", Primitive.INTEGER, false, false, null);
        var src = spec.addSource("s");
        var b = src.bind(cls);
        var sql = Write.emitValidateRowsSql(b, DEFAULTS);
        assertThat(sql).doesNotContain("missing_required_slot");
        // Still gets the integer type-coercion check.
        assertThat(sql).contains("'^-?[0-9]+$'");
        assertThat(sql).contains("'optional_year'");
    }

    @Test
    void validateRowsIdentifierSlotSkipped() {
        var spec = Spec.builder().identifierSlotName("canonical_id").build();
        var cls = spec.addClass("Thing");
        var src = spec.addSource("s");
        var b = src.bind(cls);
        var sql = Write.emitValidateRowsSql(b, DEFAULTS);
        assertThat(sql).contains("missing_source_identifier");
        assertThat(sql).doesNotContain("'canonical_id'");
    }

    @Test
    void validateRowsExplicitSqlMappingSkipsTypeCheck() {
        var spec = Spec.builder().identifierSlotName("canonical_id").build();
        var cls = spec.addClass("Movie");
        cls.slot("runtime_minutes", Primitive.INTEGER, false, false, null);
        var src = spec.addSource("imdb");
        var b = src.bind(cls);
        b.slot("runtime_minutes", "runtime", "(regexp_match(runtime, '[0-9]+'))[1]::int");
        var sql = Write.emitValidateRowsSql(b, DEFAULTS);
        assertThat(sql).doesNotContain("type_coercion_failed");
    }

    @Test
    void validateRowsTextAndArrayNoTypeCheck() {
        var spec = Spec.builder().identifierSlotName("canonical_id").build();
        var cls = spec.addClass("Thing");
        cls.slot("name", Primitive.TEXT, false, false, null);
        cls.slot("tags", new Array(Primitive.TEXT), false, false, null);
        var src = spec.addSource("s");
        var b = src.bind(cls);
        var sql = Write.emitValidateRowsSql(b, DEFAULTS);
        assertThat(sql).doesNotContain("type_coercion_failed");
    }

    @Test
    void validateRowsClassRefNoTypeCheck() {
        var spec = Spec.builder().identifierSlotName("canonical_id").build();
        var person = spec.addClass("Person");
        person.slot("name", Primitive.TEXT, false, false, null);
        var movie = spec.addClass("Movie");
        movie.slot("director", person);
        var src = spec.addSource("s");
        var b = src.bind(movie);
        var sql = Write.emitValidateRowsSql(b, DEFAULTS);
        assertThat(sql).doesNotContain("type_coercion_failed");
    }

    @Test
    void validateRowsBooleanCheck() {
        var spec = Spec.builder().identifierSlotName("canonical_id").build();
        var cls = spec.addClass("Thing");
        cls.slot("active", Primitive.BOOLEAN, false, false, null);
        var src = spec.addSource("s");
        var b = src.bind(cls);
        var sql = Write.emitValidateRowsSql(b, DEFAULTS);
        assertThat(sql).contains("type_coercion_failed");
        assertThat(sql).contains("'active'");
        assertThat(sql).containsIgnoringCase("true");
        assertThat(sql).containsIgnoringCase("false");
    }

    @Test
    void validateRowsDateCheck() {
        var spec = Spec.builder().identifierSlotName("canonical_id").build();
        var cls = spec.addClass("Thing");
        cls.slot("release_date", Primitive.DATE, false, false, null);
        var src = spec.addSource("s");
        var b = src.bind(cls);
        var sql = Write.emitValidateRowsSql(b, DEFAULTS);
        assertThat(sql).contains("type_coercion_failed");
        assertThat(sql).contains("'release_date'");
        assertThat(sql).contains("[0-9]{4}");
    }

    @Test
    void validateRowsSchemaKwargsAccepted() {
        var spec = allTypesSpec();
        var b = allTypesBinding(spec);
        var sql = Write.emitValidateRowsSql(b, new WriteOptions("alt", "__b"));
        assertThat(sql).contains("%(rows)s::jsonb");
    }

    @Test
    void validateRowsSourceSlotRemappingUsesSourceField() {
        var spec = Spec.builder().identifierSlotName("canonical_id").build();
        var cls = spec.addClass("Movie");
        cls.slot("year", Primitive.INTEGER, true, false, null);
        var src = spec.addSource("imdb");
        var b = src.bind(cls);
        b.slot("year", "release_year");
        var sql = Write.emitValidateRowsSql(b, DEFAULTS);
        assertThat(sql).contains("release_year");
    }

    @Test
    void validateRowsPayloadColumnInEveryBranch() {
        var spec = allTypesSpec();
        var b = allTypesBinding(spec);
        var sql = Write.emitValidateRowsSql(b, DEFAULTS);
        for (var branch : sql.split("UNION ALL")) {
            assertThat(branch).contains("payload");
        }
    }

    @Test
    void validateRowsEnumCheck() {
        var spec = Spec.builder().identifierSlotName("canonical_id").build();
        var cls = spec.addClass("Movie");
        cls.slot("status", new Enum(List.of("active", "archived")), false, false, null);
        var src = spec.addSource("s");
        var b = src.bind(cls);
        var sql = Write.emitValidateRowsSql(b, DEFAULTS);
        assertThat(sql).contains("type_coercion_failed");
        assertThat(sql).contains("'status'");
        assertThat(sql).contains("'active'");
        assertThat(sql).contains("'archived'");
        assertThat(sql).contains("NOT IN");
    }

    @Test
    void validateRowsOnlyTextProducesMinimalSql() {
        var spec = Spec.builder().identifierSlotName("canonical_id").build();
        var cls = spec.addClass("Thing");
        cls.slot("label", Primitive.TEXT, false, false, null);
        var src = spec.addSource("s");
        var b = src.bind(cls);
        var sql = Write.emitValidateRowsSql(b, DEFAULTS);
        assertThat(sql).contains("missing_source_identifier");
        assertThat(sql).doesNotContain("missing_required_slot");
        assertThat(sql).doesNotContain("type_coercion_failed");
    }

    // -------------------------------------------------------------------------
    // emitUpdateSlotSql
    // -------------------------------------------------------------------------

    @Test
    void updateSlotBasicShape() {
        var spec = kitchenSinkSpec();
        var b = kitchenSinkBinding(spec);
        var sql = Write.emitUpdateSlotSql(b, "title", DEFAULTS);
        assertThat(sql).contains("UPDATE knot_data.movie_bindings AS b");
        assertThat(sql).contains("SET title = (r->>'title')::text");
        assertThat(sql).contains("FROM jsonb_array_elements(%(rows)s::jsonb) AS r");
        assertThat(sql).contains("b.source_name = 'imdb'");
        assertThat(sql).contains("b.source_identifier = (r->>'source_identifier')");
    }

    @Test
    void updateSlotBakesSourceLiteral() {
        var spec = kitchenSinkSpec();
        var b = kitchenSinkBinding(spec);
        var sql = Write.emitUpdateSlotSql(b, "title", DEFAULTS);
        assertThat(sql).contains("'imdb'");
        assertThat(sql).doesNotContain("%(source_name)s");
    }

    @Test
    void updateSlotVectorCast() {
        var spec = kitchenSinkSpec();
        var b = kitchenSinkBinding(spec);
        var sql = Write.emitUpdateSlotSql(b, "title_embedding", DEFAULTS);
        assertThat(sql).contains("SET title_embedding = (r->>'title_embedding')::vector(384)");
    }

    @Test
    void updateSlotIntegerCast() {
        var spec = kitchenSinkSpec();
        var b = kitchenSinkBinding(spec);
        var sql = Write.emitUpdateSlotSql(b, "year", DEFAULTS);
        assertThat(sql).contains("SET year = (r->>'year')::integer");
    }

    @Test
    void updateSlotArrayRoundTrip() {
        var spec = kitchenSinkSpec();
        var b = kitchenSinkBinding(spec);
        var sql = Write.emitUpdateSlotSql(b, "genres", DEFAULTS);
        assertThat(sql).contains("ARRAY(SELECT (value #>> '{}')::text");
        assertThat(sql).contains("jsonb_array_elements(r->'genres')");
    }

    @Test
    void updateSlotClassRefCastsToText() {
        var spec = kitchenSinkSpec();
        var b = kitchenSinkBinding(spec);
        var sql = Write.emitUpdateSlotSql(b, "director", DEFAULTS);
        assertThat(sql).contains("SET director = (r->>'director')::text");
    }

    @Test
    void updateSlotSchemaAndSuffixKwargs() {
        var spec = kitchenSinkSpec();
        var b = kitchenSinkBinding(spec);
        var sql = Write.emitUpdateSlotSql(b, "title", new WriteOptions("alt", "__s"));
        assertThat(sql).contains("alt.movie__s");
        assertThat(sql).doesNotContain("knot_data.movie_bindings");
    }

    @Test
    void updateSlotApostropheEscaping() {
        var spec = Spec.builder().identifierSlotName("canonical_id").build();
        var movie = spec.addClass("Movie");
        movie.slot("title", Primitive.TEXT, true, false, null);
        var src = spec.addSource("o'brien");
        var b = src.bind(movie);
        var sql = Write.emitUpdateSlotSql(b, "title", DEFAULTS);
        assertThat(sql).contains("'o''brien'");
    }

    @Test
    void updateSlotRejectsIdentifierSlot() {
        var spec = kitchenSinkSpec();
        var b = kitchenSinkBinding(spec);
        assertThatThrownBy(() -> Write.emitUpdateSlotSql(b, "canonical_id", DEFAULTS))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("identifier slot");
    }

    @Test
    void updateSlotRejectsUnknownSlot() {
        var spec = kitchenSinkSpec();
        var b = kitchenSinkBinding(spec);
        assertThatThrownBy(() -> Write.emitUpdateSlotSql(b, "not_a_slot", DEFAULTS))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void updateSlotRejectsAbstractClass() {
        var spec = Spec.builder().identifierSlotName("canonical_id").build();
        var abs = spec.addAbstractClass("A");
        var src = spec.addSource("s");
        var b = new SourceBinding(src, abs);
        assertThatThrownBy(() -> Write.emitUpdateSlotSql(b, "any_slot", DEFAULTS))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("abstract");
    }

    // -------------------------------------------------------------------------
    // emitRetractSql
    // -------------------------------------------------------------------------

    @Test
    void retractSqlBasicShape() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        var sql = Write.emitRetractSql(b, DEFAULTS);
        assertThat(sql).contains("DELETE FROM knot_data.movie_bindings");
        assertThat(sql).contains("WHERE canonical_id = %(canonical_id)s");
        assertThat(sql).contains("AND source_name = 'imdb'");
        assertThat(sql).contains("AND source_identifier = %(source_identifier)s");
    }

    @Test
    void retractSqlSchemaKwarg() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        var sql = Write.emitRetractSql(b, new WriteOptions("alt", "_bindings"));
        assertThat(sql).contains("DELETE FROM alt.movie_bindings");
    }

    // -------------------------------------------------------------------------
    // emitAssignCanonicalSql
    // -------------------------------------------------------------------------

    @Test
    void assignCanonicalSafeUpdateNullGuard() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        var sql = Write.emitAssignCanonicalSql(b, DEFAULTS);
        assertThat(sql).contains("UPDATE knot_data.movie_bindings");
        assertThat(sql).contains("SET canonical_id = %(canonical_id)s");
        assertThat(sql).contains("AND canonical_id IS NULL");
    }

    @Test
    void assignCanonicalBakesSourceLiteral() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        var sql = Write.emitAssignCanonicalSql(b, DEFAULTS);
        assertThat(sql).contains("source_name = 'imdb'");
        assertThat(sql).doesNotContain("%(source_name)s");
    }

    @Test
    void assignCanonicalRuntimePlaceholders() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        var sql = Write.emitAssignCanonicalSql(b, DEFAULTS);
        assertThat(sql).contains("%(canonical_id)s");
        assertThat(sql).contains("%(source_identifier)s");
        assertThat(sql).contains("%(er_metadata)s");
    }

    @Test
    void assignCanonicalErMetadataCoalesce() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        var sql = Write.emitAssignCanonicalSql(b, DEFAULTS);
        assertThat(sql).contains("er_metadata = COALESCE(%(er_metadata)s::jsonb, er_metadata)");
    }

    @Test
    void assignCanonicalSchemaKwarg() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        var sql = Write.emitAssignCanonicalSql(b, new WriteOptions("alt", "_bindings"));
        assertThat(sql).contains("UPDATE alt.movie_bindings");
    }

    @Test
    void assignCanonicalForwardTranslatesFkSlots() {
        var spec = movieCreditSpec();
        var b = bindingFor(spec, "Movie");
        var sql = Write.emitAssignCanonicalSql(b, DEFAULTS);
        assertThat(sql).contains("director = COALESCE(");
        assertThat(sql).contains("SELECT canonical_id FROM knot_data.person_bindings");
        assertThat(sql).contains("AND source_identifier = knot_data.movie_bindings.director");
        assertThat(sql).contains("AND canonical_id IS NOT NULL");
    }

    @Test
    void assignCanonicalBackwardFanoutToReferrers() {
        var spec = movieCreditSpec();
        var b = bindingFor(spec, "Movie");
        var sql = Write.emitAssignCanonicalSql(b, DEFAULTS);
        assertThat(sql).contains("fanout_credit AS (");
        assertThat(sql).contains("UPDATE knot_data.credit_bindings");
        assertThat(sql).contains(
                "movie = CASE WHEN movie = %(source_identifier)s THEN %(canonical_id)s");
    }

    @Test
    void assignCanonicalFanoutGatedOnStamp() {
        var spec = movieCreditSpec();
        var b = bindingFor(spec, "Movie");
        var sql = Write.emitAssignCanonicalSql(b, DEFAULTS);
        assertThat(sql).contains("WHERE EXISTS (SELECT 1 FROM stamp)");
    }

    @Test
    void assignCanonicalFanoutScopedToSource() {
        var spec = movieCreditSpec();
        var b = bindingFor(spec, "Movie");
        var sql = Write.emitAssignCanonicalSql(b, DEFAULTS);
        String fanout = sql.substring(sql.indexOf("fanout_credit AS ("));
        assertThat(fanout).contains("source_name = 'imdb'");
    }

    @Test
    void assignCanonicalFanoutGroupedPerReferringClass() {
        var spec = movieCreditSpec();
        var b = bindingFor(spec, "Person");
        var sql = Write.emitAssignCanonicalSql(b, DEFAULTS);
        // Person is referenced by both Movie.director and Credit.person.
        assertThat(sql).contains("fanout_movie AS (");
        assertThat(sql).contains("fanout_credit AS (");
        String movieFanout = sql.substring(sql.indexOf("fanout_movie AS ("));
        assertThat(movieFanout).contains("director = CASE WHEN director = %(source_identifier)s");
    }

    @Test
    void assignCanonicalNoRegisterCte() {
        var spec = movieCreditSpec();
        var b = bindingFor(spec, "Movie");
        var sql = Write.emitAssignCanonicalSql(b, DEFAULTS);
        assertThat(sql).doesNotContain("register AS");
        assertThat(sql).doesNotContain("INSERT INTO knot_data.movie ");
    }

    @Test
    void assignCanonicalLeafClassNoFanout() {
        var spec = movieCreditSpec();
        var b = bindingFor(spec, "Credit");
        var sql = Write.emitAssignCanonicalSql(b, DEFAULTS);
        assertThat(sql).doesNotContain("fanout_");
    }

    @Test
    void assignCanonicalNoFkSlotsNoForwardTranslation() {
        var spec = movieCreditSpec();
        var b = bindingFor(spec, "Person");
        var sql = Write.emitAssignCanonicalSql(b, DEFAULTS);
        // Person has no ClassRef slots — no subquery lookup into person_bindings for FK.
        assertThat(sql).doesNotContain("SELECT canonical_id FROM knot_data.person_bindings");
    }

    // -------------------------------------------------------------------------
    // emitAssignCanonicalsSql (batched)
    // -------------------------------------------------------------------------

    @Test
    void assignCanonicalsSqlContainsAssignmentsPlaceholder() {
        var spec = movieCreditSpec();
        var b = bindingFor(spec, "Movie");
        var sql = Write.emitAssignCanonicalsSql(b, DEFAULTS);
        assertThat(sql).contains("%(assignments)s::jsonb");
    }

    @Test
    void assignCanonicalsSqlContainsJsonbToRecordset() {
        var spec = movieCreditSpec();
        var b = bindingFor(spec, "Movie");
        var sql = Write.emitAssignCanonicalsSql(b, DEFAULTS);
        assertThat(sql).contains("jsonb_to_recordset");
        assertThat(sql).contains("AS a(canonical_id text, source_identifier text, er_metadata jsonb)");
    }

    @Test
    void assignCanonicalsSqlStampFiltersCanonicalIdIsNull() {
        var spec = movieCreditSpec();
        var b = bindingFor(spec, "Movie");
        var sql = Write.emitAssignCanonicalsSql(b, DEFAULTS);
        assertThat(sql).contains("canonical_id IS NULL");
    }

    @Test
    void assignCanonicalsSqlForwardFkTranslation() {
        var spec = movieCreditSpec();
        var b = bindingFor(spec, "Credit");
        var sql = Write.emitAssignCanonicalsSql(b, DEFAULTS);
        assertThat(sql).contains("movie_bindings");
        assertThat(sql).contains("COALESCE");
    }

    @Test
    void assignCanonicalsSqlNoForwardFkWhenNoClassRefSlots() {
        var spec = movieCreditSpec();
        var b = bindingFor(spec, "Movie");
        var sql = Write.emitAssignCanonicalsSql(b, DEFAULTS);
        // Movie has no ClassRef slots — stamp SET block should not contain LIMIT 1 lookups.
        assertThat(sql).doesNotContain("LIMIT 1");
    }

    @Test
    void assignCanonicalsSqlBackwardFanoutPresent() {
        var spec = movieCreditSpec();
        var b = bindingFor(spec, "Movie");
        var sql = Write.emitAssignCanonicalsSql(b, DEFAULTS);
        assertThat(sql).contains("fanout_credit AS (");
        assertThat(sql).contains("credit_bindings");
    }

    @Test
    void assignCanonicalsSqlNoFanoutWhenNoReferrers() {
        var spec = movieCreditSpec();
        var b = bindingFor(spec, "Credit");
        var sql = Write.emitAssignCanonicalsSql(b, DEFAULTS);
        assertThat(sql).doesNotContain("fanout_");
    }

    @Test
    void assignCanonicalsSqlSourceNameLiteralAppearsAtLeastTwice() {
        var spec = movieCreditSpec();
        var b = bindingFor(spec, "Movie");
        var sql = Write.emitAssignCanonicalsSql(b, DEFAULTS);
        assertThat(sql).contains("'imdb'");
        assertThat(sql.split("source_name = 'imdb'").length - 1).isGreaterThanOrEqualTo(2);
    }

    @Test
    void assignCanonicalsSqlSchemaAndSuffixKwargs() {
        var spec = movieCreditSpec();
        var b = bindingFor(spec, "Movie");
        var sql = Write.emitAssignCanonicalsSql(b, new WriteOptions("alt", "__s"));
        assertThat(sql).contains("alt.movie__s");
        assertThat(sql).doesNotContain("knot_data.movie_bindings");
    }

    // -------------------------------------------------------------------------
    // emitRecanonicalizeSql
    // -------------------------------------------------------------------------

    @Test
    void recanonicalizeBasicShape() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        var sql = Write.emitRecanonicalizeSql(b, DEFAULTS);
        assertThat(sql).contains("WITH old_state AS (");
        assertThat(sql).contains("stamp AS (");
        assertThat(sql).contains("UPDATE knot_data.movie_bindings");
        assertThat(sql).contains("SET canonical_id = %(new_canonical_id)s");
        assertThat(sql).contains("AND canonical_id IS NOT NULL");
    }

    @Test
    void recanonicalizeRuntimePlaceholders() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        var sql = Write.emitRecanonicalizeSql(b, DEFAULTS);
        assertThat(sql).contains("%(new_canonical_id)s");
        assertThat(sql).contains("%(source_identifier)s");
        assertThat(sql).contains("%(er_metadata)s");
        assertThat(sql).contains("source_name = 'imdb'");
        assertThat(sql).doesNotContain("%(source_name)s");
    }

    @Test
    void recanonicalizeErMetadataCoalesce() {
        var spec = movieSpec();
        var b = movieBinding(spec);
        var sql = Write.emitRecanonicalizeSql(b, DEFAULTS);
        assertThat(sql).contains("er_metadata = COALESCE(%(er_metadata)s::jsonb, er_metadata)");
    }

    @Test
    void recanonicalizeCascadesToReferrers() {
        var spec = movieCreditSpec();
        var b = bindingFor(spec, "Movie");
        var sql = Write.emitRecanonicalizeSql(b, DEFAULTS);
        assertThat(sql).contains("cascade_credit_movie AS (");
        assertThat(sql).contains("UPDATE knot_data.credit_bindings");
        assertThat(sql).contains("SET movie = %(new_canonical_id)s");
        assertThat(sql).contains("WHERE movie = (SELECT old_id FROM old_state)");
    }

    @Test
    void recanonicalizeCascadeSourceAgnostic() {
        var spec = movieCreditSpec();
        var b = bindingFor(spec, "Movie");
        var sql = Write.emitRecanonicalizeSql(b, DEFAULTS);
        String cascade = sql.substring(sql.indexOf("cascade_credit_movie"));
        assertThat(cascade).doesNotContain("source_name = 'imdb'");
    }

    @Test
    void recanonicalizeNoRegisterCte() {
        var spec = movieCreditSpec();
        var b = bindingFor(spec, "Movie");
        var sql = Write.emitRecanonicalizeSql(b, DEFAULTS);
        assertThat(sql).doesNotContain("register AS");
        assertThat(sql).doesNotContain("INSERT INTO knot_data.movie ");
    }

    // -------------------------------------------------------------------------
    // WriteOptions
    // -------------------------------------------------------------------------

    @Test
    void writeOptionsDefaultsMatchPython() {
        var d = WriteOptions.defaults();
        assertThat(d.schema()).isEqualTo("knot_data");
        assertThat(d.bindingsSuffix()).isEqualTo("_bindings");
    }
}
