"""Búsqueda (responsable: Fabricio).

Indexa cada artículo clasificado en MongoDB con un índice de texto en español
(docs/arquitectura.md, sección 7). Consume article.classified (cola
busqueda.article-classified), no publica eventos y expone:
  GET /api/search?q=&category=&page=
  GET /api/articles/{id}
"""
import asyncio
import json
import os
import unicodedata
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum

import aio_pika
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import DESCENDING, TEXT

SERVICE = "busqueda"
DATABASE_URL = os.environ["DATABASE_URL"]
RABBITMQ_URL = os.environ["RABBITMQ_URL"]

DB_NAME = "busqueda"
COLLECTION = "articles"
QUEUE = "busqueda.article-classified"
PAGE_SIZE = 10
STRING_FIELDS = ("article_id", "url", "title", "summary", "source", "published_at",
                 "ingested_at", "category")
ITEM_FIELDS = ("article_id", "title", "summary", "url", "source", "category", "tags",
               "published_at", "image_url")

CATEGORIES = [
    "politica", "economia", "tecnologia", "deportes",
    "salud", "cultura", "internacional", "otros",
]
Category = Enum("Category", {c: c for c in CATEGORIES}, type=str)

# Estado compartido del proceso; lo llena el arranque en segundo plano.
state = {"client": None, "articles": None, "amqp": None}


def utc_now():
    return datetime.now(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def fold(text):
    """Minúsculas y sin tildes: "Vacunación" -> "vacunacion"."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def log(stage, **fields):
    """Una línea JSON por evento (regla 10)."""
    print(json.dumps({"ts": iso(utc_now()), "service": SERVICE, "stage": stage, **fields},
                     ensure_ascii=False), flush=True)


async def with_retries(name, connect):
    """Reintenta `connect` con espera creciente (1, 2, 4… hasta 30 s)."""
    delay = 1
    attempt = 1
    while True:
        try:
            return await connect()
        except Exception as exc:  # noqa: BLE001 — cualquier fallo de conexión se reintenta
            log(f"{name}.retry", attempt=attempt, wait_s=delay, error=str(exc))
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)
            attempt += 1


async def connect_db():
    client = AsyncIOMotorClient(DATABASE_URL, tz_aware=True, serverSelectionTimeoutMS=2000)
    try:
        await client.admin.command("ping")
    except Exception:
        client.close()
        raise
    return client


async def init_db(articles):
    # Se indexan copias sin tildes (campo `search`): MongoDB aplica el stemmer
    # español antes de ignorar tildes, y "vacunación" y "vacunacion" darían raíces
    # distintas. Con texto y consulta sin tildes, ambas formas se encuentran.
    await articles.create_index(
        [("search.title", TEXT), ("search.summary", TEXT), ("search.tags", TEXT)],
        weights={"search.title": 3, "search.tags": 2, "search.summary": 1},
        default_language="spanish",
        name="text_search",
    )
    await articles.create_index("category")
    await articles.create_index([("published_at", DESCENDING)])
    log("db.ready")


class InvalidMessage(Exception):
    """JSON roto o campo obligatorio ausente: va directo a la .dlq."""


def parse_message(raw):
    """Valida el sobre y el payload de article.classified y arma el documento."""
    try:
        event = json.loads(raw)
        p = event["payload"]
        missing = [f for f in STRING_FIELDS
                   if not isinstance(p.get(f), str) or not p[f].strip()]
        if missing:
            raise InvalidMessage(f"faltan campos: {', '.join(missing)}")
        if p["category"] not in CATEGORIES:
            raise InvalidMessage(f"categoría inválida: {p['category']}")
        tags = p.get("tags")
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise InvalidMessage("tags debe ser una lista de textos")
        score = p.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1:
            raise InvalidMessage("score debe ser un número entre 0 y 1")
        image_url = p.get("image_url")
        if image_url is not None and not isinstance(image_url, str):
            raise InvalidMessage("image_url debe ser texto o null")
        article_id = str(uuid.UUID(p["article_id"]))
        doc = {
            "_id": article_id,
            "article_id": article_id,
            "url": p["url"],
            "title": p["title"],
            "summary": p["summary"],
            "source": p["source"],
            "published_at": parse_ts(p["published_at"]),
            "ingested_at": parse_ts(p["ingested_at"]),
            "image_url": image_url,
            "category": p["category"],
            "tags": tags,
            "score": float(score),
            "search": {
                "title": fold(p["title"]),
                "summary": fold(p["summary"]),
                "tags": [fold(t) for t in tags],
            },
        }
    except InvalidMessage:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise InvalidMessage(f"{type(exc).__name__}: {exc}") from exc
    return event, doc


async def on_message(message):
    try:
        event, doc = parse_message(message.body)
    except InvalidMessage as exc:
        log("message.rejected", error=str(exc))
        await message.reject(requeue=False)
        return

    ids = {"event_id": event.get("event_id"), "article_id": doc["article_id"]}
    doc["indexed_at"] = utc_now()
    try:
        # Upsert por article_id: el mismo artículo dos veces queda en un solo documento.
        await state["articles"].replace_one({"_id": doc["_id"]}, doc, upsert=True)
    except Exception as exc:  # noqa: BLE001 — BD caída: error transitorio, se reintenta
        log("article.retry", error=str(exc), **ids)
        await message.nack(requeue=True)
        return

    await message.ack()
    latency_ms = round((doc["indexed_at"] - doc["ingested_at"]).total_seconds() * 1000)
    log("article.indexed", category=doc["category"], latency_ms=latency_ms, **ids)


async def startup():
    client = await with_retries("db", connect_db)
    articles = client[DB_NAME][COLLECTION]
    await init_db(articles)
    state["client"], state["articles"] = client, articles

    # connect_robust reconecta (y recupera canal y consumidor) si RabbitMQ se cae.
    connection = await with_retries("rabbitmq", lambda: aio_pika.connect_robust(RABBITMQ_URL))
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=10)
    # La cola la crea rabbitmq-init; aquí solo se verifica que exista.
    queue = await channel.declare_queue(QUEUE, passive=True)
    await queue.consume(on_message)
    state["amqp"] = connection
    log("rabbitmq.ready", queue=QUEUE)


@asynccontextmanager
async def lifespan(_app):
    # El arranque va en segundo plano para que /health responda 503 mientras tanto.
    task = asyncio.create_task(startup())
    yield
    task.cancel()
    if state["amqp"] is not None:
        await state["amqp"].close()
    if state["client"] is not None:
        state["client"].close()


app = FastAPI(title=SERVICE, lifespan=lifespan)


async def db_ok():
    if state["client"] is None:
        return False
    try:
        await asyncio.wait_for(state["client"].admin.command("ping"), timeout=2)
        return True
    except Exception:  # noqa: BLE001
        return False


def rabbitmq_ok():
    return state["amqp"] is not None and not state["amqp"].is_closed


def db_unavailable():
    return JSONResponse({"database": "disconnected"}, status_code=503)


@app.get("/health")
async def health():
    db, mq = await db_ok(), rabbitmq_ok()
    body = {
        "status": "ok" if db and mq else "unavailable",
        "service": SERVICE,
        "database": "connected" if db else "disconnected",
        "rabbitmq": "connected" if mq else "disconnected",
    }
    return JSONResponse(body, status_code=200 if db and mq else 503)


def to_item(doc):
    return {f: iso(doc[f]) if isinstance(doc.get(f), datetime) else doc.get(f)
            for f in ITEM_FIELDS}


def build_search(q, category):
    """Devuelve (filtro, proyección, orden) para find()."""
    query = {}
    if category is not None:
        query["category"] = category.value
    q = (q or "").strip()
    if not q:
        return query, None, [("published_at", DESCENDING)]
    query["$text"] = {"$search": fold(q)}
    score = {"score": {"$meta": "textScore"}}
    return query, score, [("score", {"$meta": "textScore"}), ("published_at", DESCENDING)]


@app.get("/api/search")
async def search(
    q: str | None = Query(None, max_length=200),
    category: Category | None = None,
    page: int = Query(1, ge=1),
):
    if state["articles"] is None:
        return db_unavailable()
    query, projection, sort = build_search(q, category)
    try:
        total = await state["articles"].count_documents(query)
        cursor = (state["articles"].find(query, projection).sort(sort)
                  .skip((page - 1) * PAGE_SIZE).limit(PAGE_SIZE))
        docs = await cursor.to_list(length=PAGE_SIZE)
    except Exception as exc:  # noqa: BLE001 — BD caída
        log("search.error", error=str(exc))
        return db_unavailable()
    return {
        "items": [to_item(d) for d in docs],
        "page": page,
        "page_size": PAGE_SIZE,
        "total": total,
    }


@app.get("/api/articles/{article_id}")
async def get_article(article_id: uuid.UUID):
    if state["articles"] is None:
        return db_unavailable()
    try:
        doc = await state["articles"].find_one({"_id": str(article_id)})
    except Exception as exc:  # noqa: BLE001 — BD caída
        log("article.error", error=str(exc), article_id=str(article_id))
        return db_unavailable()
    if doc is None:
        return JSONResponse({"detail": "artículo no encontrado"}, status_code=404)
    doc.pop("_id")
    doc.pop("search", None)  # copia interna para el índice de texto
    return {k: iso(v) if isinstance(v, datetime) else v for k, v in doc.items()}
