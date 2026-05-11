from fastapi.testclient import TestClient
from knot_ai.main import app


def test_healthz():
    with TestClient(app) as client:
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["service"] == "knot-ai"


def test_suggest_stub_returns_structure():
    with TestClient(app) as client:
        r = client.post("/suggest", json={"prompt": "show me all movies"})
        assert r.status_code == 200
        body = r.json()
        assert "query" in body and "explanation" in body
