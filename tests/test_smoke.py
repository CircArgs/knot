def test_postgres_up(pg_conn):
    cur = pg_conn.cursor()
    cur.execute("SELECT 1")
    assert cur.fetchone()[0] == 1


def test_neo4j_up(neo4j_driver):
    with neo4j_driver.session() as s:
        result = s.run("RETURN 1 AS x").single()
        assert result["x"] == 1


def test_neo4j_apoc_loaded(neo4j_driver):
    with neo4j_driver.session() as s:
        result = s.run("RETURN apoc.version() AS v").single()
        assert result["v"] is not None
