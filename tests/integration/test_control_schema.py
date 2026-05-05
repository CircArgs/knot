"""Integration tests for the knot control-plane schema."""
import pytest
import psycopg

from knot.control_db import apply_schema


def test_apply_schema_idempotent(postgres_dsn):
    """apply_schema is safe to call multiple times; second call must not raise."""
    apply_schema(postgres_dsn)
    apply_schema(postgres_dsn)


def test_compiled_workflows_columns(pg_conn):
    """compiled_workflows has the expected columns with the right data types."""
    rows = pg_conn.execute(
        """
        SELECT column_name, data_type, character_maximum_length
        FROM information_schema.columns
        WHERE table_name = 'compiled_workflows'
        ORDER BY ordinal_position
        """
    ).fetchall()
    by_name = {r[0]: r for r in rows}

    assert "hash" in by_name
    assert by_name["hash"][1] == "character"
    assert by_name["hash"][2] == 64

    assert "canonicalizer_version" in by_name
    assert by_name["canonicalizer_version"][1] == "smallint"

    assert "spec" in by_name
    assert by_name["spec"][1] == "jsonb"

    assert "created_at" in by_name
    assert by_name["created_at"][1] == "timestamp with time zone"


def test_pipeline_runs_fk_compile_hash(pg_conn):
    """pipeline_runs.compile_hash has a foreign-key constraint to compiled_workflows."""
    rows = pg_conn.execute(
        """
        SELECT tc.constraint_name, tc.constraint_type
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
           AND tc.table_name = kcu.table_name
        WHERE tc.table_name = 'pipeline_runs'
          AND tc.constraint_type = 'FOREIGN KEY'
          AND kcu.column_name = 'compile_hash'
        """
    ).fetchall()
    assert len(rows) >= 1, "Expected a FK constraint on pipeline_runs.compile_hash"


def test_pipeline_runs_status_check(pg_conn):
    """pipeline_runs.status CHECK rejects values outside the allowed set."""
    # Insert a valid compiled_workflows row first so the FK is satisfied.
    pg_conn.execute(
        """
        INSERT INTO compiled_workflows (hash, spec)
        VALUES ('a' * 64, '{}')
        ON CONFLICT DO NOTHING
        """.replace("'a' * 64", "'" + "a" * 64 + "'")
    )

    with pytest.raises(psycopg.errors.CheckViolation):
        pg_conn.execute(
            """
            INSERT INTO pipeline_runs
                (compile_hash, scope, started_at, status)
            VALUES (%s, 'Movie', now(), 'invalid_status')
            """,
            ("a" * 64,),
        )


def test_user_corrections_fk_pipeline_runs(pg_conn):
    """_user_corrections.applied_at_run_id has a FK to pipeline_runs."""
    rows = pg_conn.execute(
        """
        SELECT tc.constraint_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
           AND tc.table_name = kcu.table_name
        WHERE tc.table_name = '_user_corrections'
          AND tc.constraint_type = 'FOREIGN KEY'
          AND kcu.column_name = 'applied_at_run_id'
        """
    ).fetchall()
    assert len(rows) >= 1, "Expected a FK on _user_corrections.applied_at_run_id"


def test_compiled_workflows_hash_is_64_chars(pg_conn):
    """compiled_workflows.hash is declared CHAR(64); verify via information_schema and round-trip."""
    # Confirm the column declaration is character(64).
    row = pg_conn.execute(
        """
        SELECT character_maximum_length
        FROM information_schema.columns
        WHERE table_name = 'compiled_workflows' AND column_name = 'hash'
        """
    ).fetchone()
    assert row is not None
    assert row[0] == 64, f"Expected CHAR(64), got CHAR({row[0]})"

    # A 65-char string must be rejected (exceeds declared length).
    long_hash = "d" * 65
    with pytest.raises(psycopg.Error):
        pg_conn.execute(
            "INSERT INTO compiled_workflows (hash, spec) VALUES (%s, '{}')",
            (long_hash,),
        )

    # A 64-char string must succeed.
    good_hash = "c" * 64
    pg_conn.execute(
        "INSERT INTO compiled_workflows (hash, spec) VALUES (%s, '{}') ON CONFLICT DO NOTHING",
        (good_hash,),
    )
    row = pg_conn.execute(
        "SELECT hash FROM compiled_workflows WHERE hash = %s", (good_hash,)
    ).fetchone()
    assert row is not None
    assert len(row[0].strip()) == 64
