"""Pruebas de las funciones puras y de los endpoints en modo sin modelo.

TestClient se usa sin `with`, así que el lifespan (BD y RabbitMQ) no arranca.
"""
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import Pipeline

from app import main
from app.main import ClassifyRequest, InvalidMessage, make_summary, parse_message

client = TestClient(main.app)

VALID_EVENT = {
    "event_id": "0b6f1f9e-5a43-4a7e-9a55-3f1d2f7c9b10",
    "event_type": "article.ingested",
    "occurred_at": "2026-10-26T12:00:00Z",
    "version": 1,
    "payload": {
        "article_id": "6f9619ff-8b86-d011-b42d-00cf4fc964ff",
        "url": "https://example.com/nota",
        "title": "Título",
        "body": "Cuerpo de la noticia.",
        "source": "example",
        "published_at": "2026-10-26T10:00:00Z",
        "hash": "abc123",
        "image_url": None,
    },
}


def event_with(**payload_changes):
    event = json.loads(json.dumps(VALID_EVENT))
    for key, value in payload_changes.items():
        if value is ...:
            del event["payload"][key]
        else:
            event["payload"][key] = value
    return json.dumps(event).encode()


@pytest.fixture
def no_model(monkeypatch):
    monkeypatch.setitem(main.state, "model", None)
    monkeypatch.setitem(main.state, "model_version", None)


@pytest.fixture
def fake_model(monkeypatch):
    model = Pipeline([("tfidf", TfidfVectorizer()), ("clf", MultinomialNB())])
    model.fit(
        ["gol partido fútbol liga", "gol fútbol estadio", "elecciones congreso ley", "ley presidente congreso"],
        ["deportes", "deportes", "politica", "politica"],
    )
    monkeypatch.setitem(main.state, "model", model)
    monkeypatch.setitem(main.state, "model_version", None)
    return model


# --- make_summary ---------------------------------------------------------

def test_summary_short_body_is_unchanged():
    assert make_summary("Una noticia corta.") == "Una noticia corta."


def test_summary_normalizes_whitespace():
    assert make_summary("  hola\n\n mundo\t ") == "hola mundo"


def test_summary_exactly_limit_has_no_ellipsis():
    body = " ".join(f"p{i}" for i in range(main.SUMMARY_WORDS))
    assert make_summary(body) == body


def test_summary_truncates_long_body():
    body = " ".join(f"p{i}" for i in range(main.SUMMARY_WORDS + 50))
    summary = make_summary(body)
    assert summary.endswith("…")
    assert len(summary.rstrip("…").split()) == main.SUMMARY_WORDS
    assert summary.startswith("p0 p1 ")


# --- parse_message ----------------------------------------------------------

def test_parse_valid_message():
    event, payload = parse_message(json.dumps(VALID_EVENT).encode())
    assert event["event_id"] == VALID_EVENT["event_id"]
    assert payload["title"] == "Título"


def test_parse_ignores_unknown_fields():
    _, payload = parse_message(event_with(extra="x"))
    assert payload["extra"] == "x"


@pytest.mark.parametrize("field", main.REQUIRED_FIELDS)
def test_parse_rejects_missing_field(field):
    with pytest.raises(InvalidMessage, match=field):
        parse_message(event_with(**{field: ...}))


@pytest.mark.parametrize("value", ["", "   ", None, 42])
def test_parse_rejects_blank_or_non_string_title(value):
    with pytest.raises(InvalidMessage):
        parse_message(event_with(title=value))


@pytest.mark.parametrize("raw", [b"{no es json", b"[]", b'{"payload": "x"}', b"{}"])
def test_parse_rejects_malformed(raw):
    with pytest.raises(InvalidMessage):
        parse_message(raw)


def test_parse_rejects_bad_uuid_and_dates():
    with pytest.raises(InvalidMessage):
        parse_message(event_with(article_id="no-es-uuid"))
    with pytest.raises(InvalidMessage):
        parse_message(event_with(published_at="ayer"))


# --- ClassifyRequest --------------------------------------------------------

def test_classify_request_strips_text():
    req = ClassifyRequest(title="  Hola ", body=" mundo ")
    assert (req.title, req.body) == ("Hola", "mundo")


@pytest.mark.parametrize("data", [
    {"title": "", "body": "x"},
    {"title": "x", "body": "   "},
    {"title": "x"},
    {"body": "x"},
    {"title": 1, "body": "x"},
])
def test_classify_request_rejects_invalid(data):
    with pytest.raises(ValidationError):
        ClassifyRequest(**data)


# --- Endpoints ----------------------------------------------------------------

def test_categories():
    res = client.get("/api/categories")
    assert res.status_code == 200
    assert res.json() == {"categories": main.CATEGORIES}
    assert len(main.CATEGORIES) == 8


def test_classify_without_model_is_503(no_model):
    res = client.post("/api/classify", json={"title": "Hola", "body": "mundo"})
    assert res.status_code == 503
    assert res.json() == {"model": "missing"}


def test_model_without_model_is_503(no_model):
    res = client.get("/api/model")
    assert res.status_code == 503
    assert res.json() == {"model": "missing"}


def test_classify_invalid_body_is_422(no_model):
    res = client.post("/api/classify", json={"title": "", "body": "mundo"})
    assert res.status_code == 422


def test_classify_with_model(fake_model, monkeypatch):
    monkeypatch.setattr(main, "MIN_SCORE", 0.0)
    res = client.post("/api/classify", json={"title": "Gol en el estadio", "body": "partido de fútbol"})
    assert res.status_code == 200
    data = res.json()
    assert data["category"] == "deportes"
    assert 0.0 < data["score"] <= 1.0
    assert 0 < len(data["tags"]) <= main.TOP_TAGS
    assert all(t == t.lower() and " " not in t for t in data["tags"])


def test_classify_low_score_falls_back_to_otros(fake_model, monkeypatch):
    monkeypatch.setattr(main, "MIN_SCORE", 1.01)
    res = client.post("/api/classify", json={"title": "Gol", "body": "fútbol"})
    assert res.json()["category"] == "otros"
