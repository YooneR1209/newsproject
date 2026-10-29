"""Pruebas de las funciones puras, del consumidor y de los endpoints sin BD real.

TestClient se usa sin `with`, así que el lifespan (MongoDB y RabbitMQ) no arranca.
"""
import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import Category, InvalidMessage, build_search, fold, parse_message, to_item

client = TestClient(main.app)

VALID_EVENT = {
    "event_id": "0e8d2b44-6a1f-4f5e-9c3d-72b1a8d5e620",
    "event_type": "article.classified",
    "occurred_at": "2026-10-26T12:00:02Z",
    "version": 1,
    "payload": {
        "article_id": "5f2a9c1e-8d3b-4c7a-b1e2-0a9d4e6f7c31",
        "url": "https://example.com/nota",
        "title": "Elección presidencial",
        "summary": "Resumen de la noticia.",
        "source": "example",
        "published_at": "2026-10-26T10:00:00Z",
        "ingested_at": "2026-10-26T12:00:00Z",
        "image_url": None,
        "category": "politica",
        "tags": ["elecciones"],
        "score": 0.87,
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


def fake_message(body):
    message = MagicMock(body=body)
    message.ack, message.nack, message.reject = AsyncMock(), AsyncMock(), AsyncMock()
    return message


@pytest.fixture
def articles(monkeypatch):
    collection = MagicMock()
    collection.replace_one = AsyncMock()
    monkeypatch.setitem(main.state, "articles", collection)
    return collection


# --- parse_message -----------------------------------------------------------

def test_parse_valid_message():
    _, doc = parse_message(event_with())
    assert doc["_id"] == doc["article_id"] == VALID_EVENT["payload"]["article_id"]
    assert doc["published_at"] == datetime(2026, 10, 26, 10, tzinfo=timezone.utc)
    assert doc["image_url"] is None
    assert doc["tags"] == ["elecciones"]
    assert doc["search"] == {"title": "eleccion presidencial", "summary": "resumen de la noticia.",
                             "tags": ["elecciones"]}


def test_fold_removes_accents_and_case():
    assert fold("Vacunación TECNOLOGÍA Güiza Ñandú") == "vacunacion tecnologia guiza nandu"


def test_parse_ignores_unknown_fields_and_missing_image():
    _, doc = parse_message(event_with(image_url=..., extra="x"))
    assert doc["image_url"] is None
    assert "extra" not in doc


def test_parse_accepts_empty_tags():
    _, doc = parse_message(event_with(tags=[]))
    assert doc["tags"] == []


@pytest.mark.parametrize("changes", [
    {"title": ...},
    {"summary": ""},
    {"ingested_at": ...},
    {"category": "farandula"},
    {"tags": "elecciones"},
    {"tags": ...},
    {"score": 1.5},
    {"score": ...},
    {"score": True},
    {"article_id": "no-es-uuid"},
    {"published_at": "ayer"},
    {"image_url": 3},
])
def test_parse_invalid_payload(changes):
    with pytest.raises(InvalidMessage):
        parse_message(event_with(**changes))


@pytest.mark.parametrize("raw", [b"{roto", b"[]", b'{"event_id": "x"}'])
def test_parse_broken_envelope(raw):
    with pytest.raises(InvalidMessage):
        parse_message(raw)


# --- consumidor --------------------------------------------------------------

def test_on_message_upserts_and_acks(articles):
    message = fake_message(event_with())
    asyncio.run(main.on_message(message))
    (flt, doc), kwargs = articles.replace_one.call_args
    assert flt == {"_id": VALID_EVENT["payload"]["article_id"]}
    assert kwargs == {"upsert": True}
    assert isinstance(doc["indexed_at"], datetime)
    message.ack.assert_awaited_once()


def test_on_message_rejects_invalid(articles):
    message = fake_message(event_with(category="farandula"))
    asyncio.run(main.on_message(message))
    message.reject.assert_awaited_once_with(requeue=False)
    articles.replace_one.assert_not_called()


def test_on_message_requeues_on_db_error(articles):
    articles.replace_one.side_effect = RuntimeError("BD caída")
    message = fake_message(event_with())
    asyncio.run(main.on_message(message))
    message.nack.assert_awaited_once_with(requeue=True)
    message.ack.assert_not_called()


# --- búsqueda ----------------------------------------------------------------

def test_build_search_without_q_sorts_by_date():
    query, projection, sort = build_search("  ", Category.deportes)
    assert query == {"category": "deportes"}
    assert projection is None
    assert sort == [("published_at", -1)]


def test_build_search_with_q_uses_text_score():
    query, projection, sort = build_search("Elección", None)
    assert query == {"$text": {"$search": "eleccion"}}
    assert projection == {"score": {"$meta": "textScore"}}
    assert sort[0] == ("score", {"$meta": "textScore"})


def test_to_item_formats_dates_and_keeps_contract_fields():
    _, doc = parse_message(event_with())
    item = to_item(doc)
    assert set(item) == {"article_id", "title", "summary", "url", "source", "category",
                         "tags", "published_at", "image_url"}
    assert item["published_at"] == "2026-10-26T10:00:00Z"


# --- endpoints sin BD ---------------------------------------------------------

def test_health_without_connections_is_503():
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["database"] == "disconnected"


def test_search_unknown_category_is_422():
    assert client.get("/api/search", params={"category": "farandula"}).status_code == 422


def test_search_invalid_page_is_422():
    assert client.get("/api/search", params={"page": 0}).status_code == 422


def test_search_without_db_is_503():
    assert client.get("/api/search", params={"q": "ecuador"}).status_code == 503


def test_article_invalid_id_is_422():
    assert client.get("/api/articles/no-es-uuid").status_code == 422
