"""Unit tests for knot.sql_gen — expression-tree → sqlglot AST → SQL strings."""

from __future__ import annotations

import pytest
from sqlglot import exp

from knot.metaschema import (
    AggFunc,
    Between,
    BoolExpr,
    BoolOp,
    Compare,
    CompareOp,
    FilteredRelation,
    FormatDerivation,
    GroupByMode,
    Literal_,
    Matches,
    OntologyClass,
    RecursiveTraversal,
    RelationAggregate,
    RelationAll,
    RelationAny,
    RelationCount,
    RelationFirst,
    RelationProject,
    RelationRef,
    ResolutionPolicy,
    ScalarDerivation,
    Slot,
    SlotPath,
    TypeDefinition,
    Within,
)
from knot.sql_gen import (
    UnsupportedDerivationError,
    emit_sql,
    emit_trust_resolved_cte,
    emit_validation_query,
    to_sqlglot,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

string_t = TypeDefinition(name="string", base="str")
int_t = TypeDefinition(name="integer", base="int")
float_t = TypeDefinition(name="float", base="float")


def _cls(name: str, slots: list[Slot] | None = None) -> OntologyClass:
    return OntologyClass(name=name, slots=slots or [])


def _slot(name: str, range_=None, policy: ResolutionPolicy = ResolutionPolicy.ARGMAX_TRUST) -> Slot:
    return Slot(name=name, range=range_ or string_t, resolution_policy=policy)


def _path(cls: OntologyClass, *slots: Slot) -> SlotPath:
    return SlotPath(from_class=cls, slots=list(slots))


def sql(node, dialect: str = "duckdb") -> str:
    return emit_sql(node, dialect=dialect)


# ---------------------------------------------------------------------------
# Literal_
# ---------------------------------------------------------------------------

class TestLiteral:
    def test_integer(self):
        node = Literal_(value=42)
        result = to_sqlglot(node)
        assert isinstance(result, exp.Literal)
        assert result.sql(dialect="duckdb") == "42"

    def test_string(self):
        node = Literal_(value="hello")
        result = to_sqlglot(node)
        assert result.sql(dialect="duckdb") == "'hello'"

    def test_float(self):
        node = Literal_(value=3.14)
        result = to_sqlglot(node)
        assert "3.14" in result.sql(dialect="duckdb")

    def test_none(self):
        node = Literal_(value=None)
        result = to_sqlglot(node)
        assert result.sql(dialect="duckdb") == "NULL"

    def test_bool_true(self):
        node = Literal_(value=True)
        result = to_sqlglot(node)
        assert result.sql(dialect="duckdb") == "TRUE"


# ---------------------------------------------------------------------------
# SlotPath
# ---------------------------------------------------------------------------

class TestSlotPath:
    def test_single_slot_with_class(self):
        cls = _cls("Movie")
        slot = _slot("title")
        node = _path(cls, slot)
        result = to_sqlglot(node)
        assert result.sql(dialect="duckdb") == "movie.title"

    def test_sentinel_class_no_table_qualifier(self):
        from knot.metaschema import _sentinel_class
        slot = _slot("year")
        node = _path(_sentinel_class, slot)
        result = to_sqlglot(node)
        # No table qualifier for sentinel class
        assert result.sql(dialect="duckdb") == "year"

    def test_multi_slot_uses_terminal(self):
        """Multi-slot path: only the terminal slot name in the column."""
        cls = _cls("Movie")
        director_slot = _slot("director")
        name_slot = _slot("name")
        node = _path(cls, director_slot, name_slot)
        result = to_sqlglot(node)
        assert result.sql(dialect="duckdb") == "movie.name"


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

class TestCompare:
    def test_gt_over_slot_path(self):
        """Movie.year > 1990 → movie.year > 1990"""
        cls = _cls("Movie")
        slot = _slot("year", int_t)
        path = _path(cls, slot)
        node = Compare(op=CompareOp.GT, left=path, right=Literal_(value=1990))
        assert sql(node) == "movie.year > 1990"

    def test_eq(self):
        cls = _cls("Credit")
        slot = _slot("role")
        path = _path(cls, slot)
        node = Compare(op=CompareOp.EQ, left=path, right=Literal_(value="director"))
        assert sql(node) == "credit.role = 'director'"

    def test_neq(self):
        cls = _cls("Movie")
        slot = _slot("year", int_t)
        path = _path(cls, slot)
        node = Compare(op=CompareOp.NEQ, left=path, right=Literal_(value=2000))
        assert sql(node) == "movie.year <> 2000"

    def test_lte(self):
        cls = _cls("Movie")
        slot = _slot("year", int_t)
        path = _path(cls, slot)
        node = Compare(op=CompareOp.LTE, left=path, right=Literal_(value=2000))
        assert sql(node) == "movie.year <= 2000"

    def test_is_null(self):
        cls = _cls("Movie")
        slot = _slot("title")
        path = _path(cls, slot)
        node = Compare(op=CompareOp.IS_NULL, left=path)
        assert sql(node) == "movie.title IS NULL"

    def test_is_not_null(self):
        cls = _cls("Movie")
        slot = _slot("title")
        path = _path(cls, slot)
        node = Compare(op=CompareOp.IS_NOT_NULL, left=path)
        assert "IS NULL" in sql(node)
        assert "NOT" in sql(node)


# ---------------------------------------------------------------------------
# BoolExpr
# ---------------------------------------------------------------------------

class TestBoolExpr:
    def test_and_two_compares(self):
        """(Movie.year > 1990) AND (Movie.runtime_minutes > 90)"""
        cls = _cls("Movie")
        year_slot = _slot("year", int_t)
        runtime_slot = _slot("runtime_minutes", int_t)
        c1 = Compare(op=CompareOp.GT, left=_path(cls, year_slot), right=Literal_(value=1990))
        c2 = Compare(op=CompareOp.GT, left=_path(cls, runtime_slot), right=Literal_(value=90))
        node = BoolExpr(op=BoolOp.AND, operands=[c1, c2])
        result = sql(node)
        assert "movie.year > 1990" in result
        assert "movie.runtime_minutes > 90" in result
        assert " AND " in result

    def test_or_two_compares(self):
        cls = _cls("Movie")
        year_slot = _slot("year", int_t)
        c1 = Compare(op=CompareOp.LT, left=_path(cls, year_slot), right=Literal_(value=1990))
        c2 = Compare(op=CompareOp.GT, left=_path(cls, year_slot), right=Literal_(value=2000))
        node = BoolExpr(op=BoolOp.OR, operands=[c1, c2])
        result = sql(node)
        assert " OR " in result

    def test_not(self):
        cls = _cls("Movie")
        slot = _slot("year", int_t)
        c = Compare(op=CompareOp.EQ, left=_path(cls, slot), right=Literal_(value=1990))
        node = BoolExpr(op=BoolOp.NOT, operands=[c])
        result = sql(node)
        assert "NOT" in result

    def test_operator_overload_and(self):
        """Test that & operator on Compare produces BoolExpr correctly."""
        cls = _cls("Movie")
        year_slot = _slot("year", int_t)
        runtime_slot = _slot("runtime_minutes", int_t)
        c1 = Compare(op=CompareOp.GT, left=_path(cls, year_slot), right=Literal_(value=1990))
        c2 = Compare(op=CompareOp.GT, left=_path(cls, runtime_slot), right=Literal_(value=90))
        combined = c1 & c2
        result = sql(combined)
        assert " AND " in result


# ---------------------------------------------------------------------------
# Within
# ---------------------------------------------------------------------------

class TestWithin:
    def test_within_emits_in(self):
        """slot.within(['Action', 'Drama']) → genre IN ('Action', 'Drama')"""
        cls = _cls("Movie")
        slot = _slot("genre")
        path = _path(cls, slot)
        node = Within(left=path, values=[Literal_(value="Action"), Literal_(value="Drama")])
        result = sql(node)
        assert "movie.genre IN" in result
        assert "'Action'" in result
        assert "'Drama'" in result


# ---------------------------------------------------------------------------
# Between
# ---------------------------------------------------------------------------

class TestBetween:
    def test_inclusive_between(self):
        """Movie.year.between(1990, 2000) → movie.year BETWEEN 1990 AND 2000"""
        cls = _cls("Movie")
        slot = _slot("year", int_t)
        path = _path(cls, slot)
        node = Between(left=path, lower=Literal_(value=1990), upper=Literal_(value=2000))
        result = sql(node)
        assert "movie.year BETWEEN 1990 AND 2000" == result

    def test_exclusive_between(self):
        """Exclusive bounds → compound: year > 1990 AND year < 2000"""
        cls = _cls("Movie")
        slot = _slot("year", int_t)
        path = _path(cls, slot)
        node = Between(left=path, lower=Literal_(value=1990), upper=Literal_(value=2000), inclusive=False)
        result = sql(node)
        assert "movie.year > 1990" in result
        assert "movie.year < 2000" in result
        assert "AND" in result


# ---------------------------------------------------------------------------
# Matches
# ---------------------------------------------------------------------------

class TestMatches:
    def test_like_pattern(self):
        """Pattern without regex metacharacters → LIKE."""
        cls = _cls("Movie")
        slot = _slot("title")
        path = _path(cls, slot)
        node = Matches(left=path, pattern="The %")
        result = sql(node)
        assert "LIKE" in result
        assert "'The %'" in result

    def test_regex_duckdb(self):
        """Pattern with ^ → regexp for DuckDB (REGEXP_MATCHES)."""
        cls = _cls("Movie")
        slot = _slot("title")
        path = _path(cls, slot)
        node = Matches(left=path, pattern="^The .*")
        result = sql(node, dialect="duckdb")
        assert "REGEXP_MATCHES" in result or "regexp_matches" in result.lower()
        assert "^The .*" in result

    def test_regex_trino(self):
        """Pattern with ^ → REGEXP_LIKE for Trino."""
        cls = _cls("Movie")
        slot = _slot("title")
        path = _path(cls, slot)
        node = Matches(left=path, pattern="^The .*")
        result = sql(node, dialect="trino")
        assert "REGEXP_LIKE" in result or "regexp_like" in result.lower()

    def test_regex_spark(self):
        """Pattern with ^ → RLIKE for Spark."""
        cls = _cls("Movie")
        slot = _slot("title")
        path = _path(cls, slot)
        node = Matches(left=path, pattern="^The .*")
        result = sql(node, dialect="spark")
        assert "RLIKE" in result or "rlike" in result.lower()


# ---------------------------------------------------------------------------
# RelationRef
# ---------------------------------------------------------------------------

class TestRelationRef:
    def test_basic_select_star(self):
        cls = _cls("Credit")
        slot = _slot("person")
        node = RelationRef(from_class=cls, slot=slot)
        result = sql(node)
        assert "SELECT" in result
        assert "credit" in result


# ---------------------------------------------------------------------------
# FilteredRelation
# ---------------------------------------------------------------------------

class TestFilteredRelation:
    def test_filtered_relation_adds_where(self):
        cls = _cls("Credit")
        slot = _slot("person")
        role_slot = _slot("role")
        rel = RelationRef(from_class=cls, slot=slot)
        filt = Compare(
            op=CompareOp.EQ,
            left=_path(cls, role_slot),
            right=Literal_(value="director"),
        )
        node = FilteredRelation(relation=rel, filter=filt)
        result = sql(node)
        assert "WHERE" in result
        assert "credit.role = 'director'" in result


# ---------------------------------------------------------------------------
# RelationProject
# ---------------------------------------------------------------------------

class TestRelationProject:
    def test_projects_column(self):
        cls = _cls("Credit")
        slot = _slot("person")
        project_slot = _slot("name")
        rel = RelationRef(from_class=cls, slot=slot)
        project = _path(cls, project_slot)
        node = RelationProject(relation=rel, project=project)
        result = sql(node)
        assert "SELECT" in result
        assert "credit.name" in result


# ---------------------------------------------------------------------------
# RelationCount
# ---------------------------------------------------------------------------

class TestRelationCount:
    def test_count_star(self):
        cls = _cls("Credit")
        slot = _slot("person")
        rel = RelationRef(from_class=cls, slot=slot)
        node = RelationCount(relation=rel)
        result = sql(node)
        assert "COUNT(*)" in result

    def test_count_distinct(self):
        cls = _cls("Credit")
        slot = _slot("person")
        rel = RelationRef(from_class=cls, slot=slot)
        node = RelationCount(relation=rel, distinct=True)
        result = sql(node)
        assert "DISTINCT" in result


# ---------------------------------------------------------------------------
# RelationAggregate
# ---------------------------------------------------------------------------

class TestRelationAggregate:
    def test_sum(self):
        cls = _cls("Movie")
        slot = _slot("gross", float_t)
        budget_slot = _slot("budget", float_t)
        rel = RelationRef(from_class=cls, slot=slot)
        operand = _path(cls, budget_slot)
        node = RelationAggregate(relation=rel, func=AggFunc.SUM, operand=operand)
        result = sql(node)
        assert "SUM(movie.budget)" in result

    def test_avg(self):
        cls = _cls("Movie")
        slot = _slot("year", int_t)
        rel = RelationRef(from_class=cls, slot=slot)
        operand = _path(cls, slot)
        node = RelationAggregate(relation=rel, func=AggFunc.AVG, operand=operand)
        result = sql(node)
        assert "AVG" in result

    def test_max(self):
        cls = _cls("Movie")
        slot = _slot("year", int_t)
        rel = RelationRef(from_class=cls, slot=slot)
        operand = _path(cls, slot)
        node = RelationAggregate(relation=rel, func=AggFunc.MAX, operand=operand)
        result = sql(node)
        assert "MAX" in result

    def test_min(self):
        cls = _cls("Movie")
        slot = _slot("year", int_t)
        rel = RelationRef(from_class=cls, slot=slot)
        operand = _path(cls, slot)
        node = RelationAggregate(relation=rel, func=AggFunc.MIN, operand=operand)
        result = sql(node)
        assert "MIN" in result

    def test_collect_duckdb(self):
        cls = _cls("Movie")
        slot = _slot("tag")
        rel = RelationRef(from_class=cls, slot=slot)
        operand = _path(cls, slot)
        node = RelationAggregate(relation=rel, func=AggFunc.COLLECT, operand=operand)
        result_duckdb = sql(node, dialect="duckdb")
        assert "ARRAY_AGG" in result_duckdb

    def test_collect_spark(self):
        cls = _cls("Movie")
        slot = _slot("tag")
        rel = RelationRef(from_class=cls, slot=slot)
        operand = _path(cls, slot)
        node = RelationAggregate(relation=rel, func=AggFunc.COLLECT, operand=operand)
        result_spark = sql(node, dialect="spark")
        assert "COLLECT_LIST" in result_spark

    def test_group_by_source(self):
        cls = _cls("Movie")
        slot = _slot("year", int_t)
        rel = RelationRef(from_class=cls, slot=slot)
        operand = _path(cls, slot)
        node = RelationAggregate(
            relation=rel, func=AggFunc.SUM, operand=operand, group_by=GroupByMode.SOURCE
        )
        result = sql(node)
        assert "GROUP BY" in result


# ---------------------------------------------------------------------------
# RelationAny / RelationAll / RelationFirst
# ---------------------------------------------------------------------------

class TestRelationAny:
    def test_exists(self):
        cls = _cls("Credit")
        slot = _slot("person")
        rel = RelationRef(from_class=cls, slot=slot)
        node = RelationAny(relation=rel)
        result = sql(node)
        assert "EXISTS" in result


class TestRelationAll:
    def test_not_exists(self):
        cls = _cls("Credit")
        slot = _slot("person")
        role_slot = _slot("role")
        rel = RelationRef(from_class=cls, slot=slot)
        body = Compare(
            op=CompareOp.IS_NOT_NULL,
            left=_path(cls, role_slot),
        )
        node = RelationAll(relation=rel, body=body)
        result = sql(node)
        assert "NOT" in result
        assert "EXISTS" in result


class TestRelationFirst:
    def test_limit_1(self):
        cls = _cls("Credit")
        slot = _slot("person")
        name_slot = _slot("name")
        rel = RelationRef(from_class=cls, slot=slot)
        project = _path(cls, name_slot)
        node = RelationFirst(relation=rel, project=project)
        result = sql(node)
        assert "LIMIT 1" in result
        assert "credit.name" in result

    def test_order_by(self):
        cls = _cls("Credit")
        slot = _slot("person")
        name_slot = _slot("name")
        year_slot = _slot("year", int_t)
        rel = RelationRef(from_class=cls, slot=slot)
        node = RelationFirst(
            relation=rel,
            project=_path(cls, name_slot),
            order_by=[_path(cls, year_slot)],
        )
        result = sql(node)
        assert "ORDER BY" in result
        assert "LIMIT 1" in result


# ---------------------------------------------------------------------------
# RecursiveTraversal — must raise
# ---------------------------------------------------------------------------

class TestRecursiveTraversal:
    def test_raises_unsupported(self):
        cls = _cls("Title")
        child_slot = _slot("children")
        name_slot = _slot("name")
        start = RelationRef(from_class=cls, slot=child_slot)
        step = SlotPath(from_class=cls, slots=[name_slot])
        node = RecursiveTraversal(start=start, step=step)
        with pytest.raises(UnsupportedDerivationError, match="recursive"):
            to_sqlglot(node)


# ---------------------------------------------------------------------------
# ScalarDerivation / FormatDerivation
# ---------------------------------------------------------------------------

class TestScalarDerivation:
    def test_wraps_inner_expression(self):
        cls = _cls("Movie")
        slot = _slot("year", int_t)
        path = _path(cls, slot)
        compare = Compare(op=CompareOp.GT, left=path, right=Literal_(value=1990))
        node = ScalarDerivation(expression=compare)
        result = sql(node)
        assert "movie.year > 1990" in result


class TestFormatDerivation:
    def test_concat_template(self):
        cls = _cls("Person")
        last_slot = _slot("last_name")
        first_slot = _slot("first_name")
        node = FormatDerivation(
            template="{last}, {first}",
            slots=[_path(cls, last_slot), _path(cls, first_slot)],
        )
        result = sql(node)
        # Should contain both columns and separator literal
        assert "last_name" in result
        assert "first_name" in result
        assert "', '" in result or ", " in result


# ---------------------------------------------------------------------------
# emit_trust_resolved_cte
# ---------------------------------------------------------------------------

class TestTrustResolvedCTE:
    def _make_movie_class(self) -> OntologyClass:
        year_slot = _slot("year", int_t, policy=ResolutionPolicy.ARGMAX_TRUST)
        genre_slot = _slot("genre", string_t, policy=ResolutionPolicy.MODE)
        title_slot = _slot("title", string_t, policy=ResolutionPolicy.UNIQUE_OR_FAIL)
        return OntologyClass(name="Movie", slots=[year_slot, genre_slot, title_slot])

    def test_cte_name_format(self):
        cls = self._make_movie_class()
        cte = emit_trust_resolved_cte(cls)
        sql_str = cte.sql(dialect="duckdb")
        assert "__trust_resolved__Movie" in sql_str

    def test_cte_contains_canonical_id(self):
        cls = self._make_movie_class()
        cte = emit_trust_resolved_cte(cls)
        sql_str = cte.sql(dialect="duckdb")
        assert "canonical_id" in sql_str

    def test_cte_argmax_trust_duckdb(self):
        year_slot = _slot("year", int_t, policy=ResolutionPolicy.ARGMAX_TRUST)
        cls = OntologyClass(name="Movie", slots=[year_slot])
        cte = emit_trust_resolved_cte(cls, dialect="duckdb")
        sql_str = cte.sql(dialect="duckdb")
        assert "ARGMAX" in sql_str or "argmax" in sql_str.lower()
        assert "year_value" in sql_str
        assert "year_trust" in sql_str

    def test_cte_argmax_trust_spark(self):
        year_slot = _slot("year", int_t, policy=ResolutionPolicy.ARGMAX_TRUST)
        cls = OntologyClass(name="Movie", slots=[year_slot])
        cte = emit_trust_resolved_cte(cls, dialect="spark")
        sql_str = cte.sql(dialect="spark")
        # Spark uses max_by
        assert "MAX_BY" in sql_str or "max_by" in sql_str.lower()

    def test_cte_mode_trino(self):
        genre_slot = _slot("genre", string_t, policy=ResolutionPolicy.MODE)
        cls = OntologyClass(name="Movie", slots=[genre_slot])
        cte = emit_trust_resolved_cte(cls, dialect="trino")
        sql_str = cte.sql(dialect="trino")
        assert "MODE" in sql_str or "mode" in sql_str.lower()

    def test_cte_unique_or_fail(self):
        title_slot = _slot("title", string_t, policy=ResolutionPolicy.UNIQUE_OR_FAIL)
        cls = OntologyClass(name="Movie", slots=[title_slot])
        cte = emit_trust_resolved_cte(cls, dialect="duckdb")
        sql_str = cte.sql(dialect="duckdb")
        assert "CASE WHEN" in sql_str
        assert "COUNT" in sql_str
        assert "disagreement" in sql_str

    def test_cte_median_numeric(self):
        score_slot = _slot("score", float_t, policy=ResolutionPolicy.MEDIAN_NUMERIC)
        cls = OntologyClass(name="Movie", slots=[score_slot])
        cte = emit_trust_resolved_cte(cls, dialect="duckdb")
        sql_str = cte.sql(dialect="duckdb")
        assert "PERCENTILE_CONT" in sql_str or "percentile_cont" in sql_str.lower()

    def test_cte_latest_watermark(self):
        ts_slot = _slot("updated_at", string_t, policy=ResolutionPolicy.LATEST_WATERMARK)
        cls = OntologyClass(name="Movie", slots=[ts_slot])
        cte = emit_trust_resolved_cte(cls, dialect="duckdb")
        sql_str = cte.sql(dialect="duckdb")
        assert "asserted_at" in sql_str

    def test_cte_group_by_canonical_id(self):
        year_slot = _slot("year", int_t, policy=ResolutionPolicy.ARGMAX_TRUST)
        cls = OntologyClass(name="Movie", slots=[year_slot])
        cte = emit_trust_resolved_cte(cls, dialect="duckdb")
        sql_str = cte.sql(dialect="duckdb")
        assert "GROUP BY" in sql_str
        assert "canonical_id" in sql_str


# ---------------------------------------------------------------------------
# Dialect smoke tests — same expression across all three dialects
# ---------------------------------------------------------------------------

class TestDialectSmoke:
    def _year_gt_node(self) -> Compare:
        cls = _cls("Movie")
        slot = _slot("year", int_t)
        path = _path(cls, slot)
        return Compare(op=CompareOp.GT, left=path, right=Literal_(value=1990))

    def test_duckdb(self):
        result = sql(self._year_gt_node(), dialect="duckdb")
        assert "movie.year > 1990" == result

    def test_trino(self):
        result = sql(self._year_gt_node(), dialect="trino")
        assert "movie.year > 1990" == result

    def test_spark(self):
        result = sql(self._year_gt_node(), dialect="spark")
        assert "movie.year > 1990" == result

    def test_between_all_dialects(self):
        cls = _cls("Movie")
        slot = _slot("year", int_t)
        path = _path(cls, slot)
        node = Between(left=path, lower=Literal_(value=1990), upper=Literal_(value=2000))
        for dialect in ("duckdb", "trino", "spark"):
            result = sql(node, dialect=dialect)
            assert "BETWEEN 1990 AND 2000" in result, f"Failed for dialect {dialect}"

    def test_within_all_dialects(self):
        cls = _cls("Movie")
        slot = _slot("genre")
        path = _path(cls, slot)
        node = Within(left=path, values=[Literal_(value="Action"), Literal_(value="Drama")])
        for dialect in ("duckdb", "trino", "spark"):
            result = sql(node, dialect=dialect)
            assert "IN" in result, f"Failed for dialect {dialect}"

    def test_bool_and_all_dialects(self):
        cls = _cls("Movie")
        year_slot = _slot("year", int_t)
        runtime_slot = _slot("runtime_minutes", int_t)
        c1 = Compare(op=CompareOp.GT, left=_path(cls, year_slot), right=Literal_(value=1990))
        c2 = Compare(op=CompareOp.GT, left=_path(cls, runtime_slot), right=Literal_(value=90))
        node = BoolExpr(op=BoolOp.AND, operands=[c1, c2])
        for dialect in ("duckdb", "trino", "spark"):
            result = sql(node, dialect=dialect)
            assert "AND" in result, f"Failed for dialect {dialect}"


# ---------------------------------------------------------------------------
# emit_validation_query
# ---------------------------------------------------------------------------

class TestValidationQuery:
    def test_basic_shape(self):
        """emit_validation_query returns a SELECT with the 5-column shape."""
        cls = _cls("Movie")
        slot = _slot("year", int_t)
        path = _path(cls, slot)
        constraint = Compare(op=CompareOp.IS_NULL, left=path)
        result = emit_validation_query(constraint)
        assert "SELECT" in result
        assert "rule_id" in result
        assert "class_name" in result
        assert "slot_name" in result
        assert "offending_pk" in result
        assert "detail" in result


# ---------------------------------------------------------------------------
# Unregistered type raises
# ---------------------------------------------------------------------------

def test_unregistered_type_raises():
    class UnknownNode:
        pass

    with pytest.raises(NotImplementedError, match="UnknownNode"):
        to_sqlglot(UnknownNode())
