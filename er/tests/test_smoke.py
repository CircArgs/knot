from fastapi.testclient import TestClient
from knot_er.main import app


def test_healthz():
    with TestClient(app) as client:
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["service"] == "knot-er"


def test_resolve_identifier_passthrough():
    with TestClient(app) as client:
        r = client.post(
            "/resolve",
            json={
                "source": "imdb",
                "class_name": "Movie",
                "identifier_slot": "imdb_id",
                "rows": [
                    {"imdb_id": "tt001", "title": "A"},
                    {"imdb_id": "tt002", "title": "B"},
                ],
            },
        )
        assert r.status_code == 200
        assert r.json()["canonical_ids"] == ["tt001", "tt002"]
