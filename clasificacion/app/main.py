"""Clasificación (responsable: Fabricio).

Asigna categoría, etiquetas y confianza a cada artículo con un modelo TF-IDF
(docs/arquitectura.md, sección 3.1). Consume article.ingested (cola
clasificacion.article-ingested), publica article.classified y expone:
  GET  /api/categories
  POST /api/classify
  GET  /api/model

Si model/model.joblib no existe arranca en modo sin modelo: todo funciona
igual, pero cada artículo se clasifica como "otros" con score 0.0.
"""
import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import aio_pika
import asyncpg
import joblib
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

SERVICE = "clasificacion"
DATABASE_URL = os.environ["DATABASE_URL"]
RABBITMQ_URL = os.environ["RABBITMQ_URL"]
MIN_SCORE = float(os.environ.get("MIN_SCORE", "0.5"))

EXCHANGE = "newsproject.events"
QUEUE = "clasificacion.article-ingested"
REQUIRED_FIELDS = ("article_id", "url", "title", "body", "source", "published_at", "hash")
SUMMARY_WORDS = 200
TOP_TAGS = 5

MODEL_DIR = Path(__file__).resolve().parent.parent / "model"
MODEL_PATH = MODEL_DIR / "model.joblib"
METRICS_PATH = MODEL_DIR / "metrics.json"

CATEGORIES = [
    "politica", "economia", "tecnologia", "deportes",
    "salud", "cultura", "internacional", "otros",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    name TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS model_versions (
    id              SERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL,
    algorithm       TEXT NOT NULL,
    precision_macro DOUBLE PRECISION,
    recall_macro    DOUBLE PRECISION,
    f1_macro        DOUBLE PRECISION,
    active          BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (created_at, algorithm)
);
CREATE TABLE IF NOT EXISTS classified_articles (
    article_id    UUID PRIMARY KEY,
    url           TEXT NOT NULL,
    title         TEXT NOT NULL,
    summary       TEXT NOT NULL,
    source        TEXT NOT NULL,
    published_at  TIMESTAMPTZ NOT NULL,
    ingested_at   TIMESTAMPTZ NOT NULL,
    image_url     TEXT,
    hash          TEXT NOT NULL,
    category      TEXT NOT NULL REFERENCES categories (name),
    tags          TEXT[] NOT NULL DEFAULT '{}',
    score         DOUBLE PRECISION NOT NULL,
    model_version INTEGER REFERENCES model_versions (id),
    classified_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# Upsert por article_id. Si llega el mismo artículo con el mismo hash es una
# reentrega: no se toca la fila y RETURNING no devuelve nada.
UPSERT = """
INSERT INTO classified_articles
    (article_id, url, title, summary, source, published_at, ingested_at, image_url,
     hash, category, tags, score, model_version, classified_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, now())
ON CONFLICT (article_id) DO UPDATE SET
    url = EXCLUDED.url, title = EXCLUDED.title, summary = EXCLUDED.summary,
    source = EXCLUDED.source, published_at = EXCLUDED.published_at,
    ingested_at = EXCLUDED.ingested_at, image_url = EXCLUDED.image_url,
    hash = EXCLUDED.hash, category = EXCLUDED.category, tags = EXCLUDED.tags,
    score = EXCLUDED.score, model_version = EXCLUDED.model_version,
    classified_at = EXCLUDED.classified_at
WHERE classified_articles.hash IS DISTINCT FROM EXCLUDED.hash
RETURNING article_id
"""

# Estado compartido del proceso; lo llena el arranque en segundo plano.
state = {"pool": None, "amqp": None, "exchange": None, "model": None, "model_version": None,
         "metrics": None}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def log(stage, **fields):
    """Una línea JSON por evento (regla 10)."""
    print(json.dumps({"ts": utc_now(), "service": SERVICE, "stage": stage, **fields},
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


def load_model():
    """Carga el pipeline si existe; si no, el servicio sigue en modo sin modelo."""
    if not MODEL_PATH.exists():
        log("model.missing", path=str(MODEL_PATH))
        return None
    model = joblib.load(MODEL_PATH)
    log("model.loaded", path=str(MODEL_PATH))
    return model


async def init_db(pool):
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(SCHEMA)
            await conn.executemany(
                "INSERT INTO categories (name) VALUES ($1) ON CONFLICT DO NOTHING",
                [(c,) for c in CATEGORIES],
            )
    log("db.ready")


async def register_model_version(pool):
    """Registra las métricas de model/metrics.json como versión activa."""
    if not METRICS_PATH.exists():
        log("model.metrics_missing", path=str(METRICS_PATH))
        return None
    metrics = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    state["metrics"] = metrics
    created_at = parse_ts(metrics["created_at"])
    async with pool.acquire() as conn:
        async with conn.transaction():
            version_id = await conn.fetchval(
                """
                INSERT INTO model_versions
                    (created_at, algorithm, precision_macro, recall_macro, f1_macro, active)
                VALUES ($1, $2, $3, $4, $5, TRUE)
                ON CONFLICT (created_at, algorithm) DO UPDATE SET active = TRUE
                RETURNING id
                """,
                created_at, metrics["algorithm"], metrics.get("precision_macro"),
                metrics.get("recall_macro"), metrics.get("f1_macro"),
            )
            await conn.execute(
                "UPDATE model_versions SET active = FALSE WHERE id <> $1", version_id)
    log("model.version_registered", model_version=version_id)
    return version_id


class InvalidMessage(Exception):
    """JSON roto o campo obligatorio ausente: va directo a la .dlq."""


def parse_message(raw):
    """Valida el sobre y el payload de article.ingested."""
    try:
        event = json.loads(raw)
        payload = event["payload"]
        missing = [f for f in REQUIRED_FIELDS
                   if not isinstance(payload.get(f), str) or not payload[f].strip()]
        if missing:
            raise InvalidMessage(f"faltan campos: {', '.join(missing)}")
        uuid.UUID(payload["article_id"])
        parse_ts(payload["published_at"])
        parse_ts(event["occurred_at"])
    except InvalidMessage:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise InvalidMessage(f"{type(exc).__name__}: {exc}") from exc
    return event, payload


def make_summary(body):
    words = body.split()
    summary = " ".join(words[:SUMMARY_WORDS])
    return summary + "…" if len(words) > SUMMARY_WORDS else summary


def top_tags(model, text):
    """Los términos con mayor peso TF-IDF del artículo, en formato etiqueta."""
    tfidf = model.named_steps["tfidf"]
    row = tfidf.transform([text]).tocoo()
    names = tfidf.get_feature_names_out()
    best = sorted(zip(row.data, row.col), reverse=True)[:TOP_TAGS]
    return [names[col].lower().replace(" ", "-") for _, col in best]


def classify(title, body):
    """Devuelve (category, score, tags). Sin modelo: ("otros", 0.0, [])."""
    model = state["model"]
    if model is None:
        return "otros", 0.0, []
    text = title + ". " + body  # igual que entry_text en tools/collect_dataset.py
    probs = model.predict_proba([text])[0]
    best = probs.argmax()
    category, score = str(model.classes_[best]), round(float(probs[best]), 2)
    if score < MIN_SCORE or category not in CATEGORIES:
        category = "otros"
    return category, score, top_tags(model, text)


async def on_message(message):
    try:
        event, p = parse_message(message.body)
    except InvalidMessage as exc:
        log("message.rejected", error=str(exc))
        await message.reject(requeue=False)
        return

    ids = {"event_id": event.get("event_id"), "article_id": p["article_id"]}
    category, score, tags = classify(p["title"], p["body"])
    summary = make_summary(p["body"])
    image_url = p.get("image_url")

    try:
        async with state["pool"].acquire() as conn:
            # Si la publicación falla, la transacción se revierte y la reentrega
            # vuelve a procesar el artículo desde cero.
            async with conn.transaction():
                changed = await conn.fetchval(
                    UPSERT,
                    uuid.UUID(p["article_id"]), p["url"], p["title"], summary, p["source"],
                    parse_ts(p["published_at"]), parse_ts(event["occurred_at"]), image_url,
                    p["hash"], category, tags, score, state["model_version"],
                )
                if changed is None:
                    log("article.duplicate", **ids)
                else:
                    await publish_classified(event, p, summary, image_url, category, tags, score)
    except Exception as exc:  # noqa: BLE001 — BD o broker: error transitorio, se reintenta
        log("article.retry", error=str(exc), **ids)
        await message.nack(requeue=True)
        return

    await message.ack()
    if changed is not None:
        log("article.classified", category=category, score=score, **ids)


async def publish_classified(event, p, summary, image_url, category, tags, score):
    out = {
        "event_id": str(uuid.uuid4()),
        "event_type": "article.classified",
        "occurred_at": utc_now(),
        "version": 1,
        "payload": {
            "article_id": p["article_id"],
            "url": p["url"],
            "title": p["title"],
            "summary": summary,
            "source": p["source"],
            "published_at": p["published_at"],
            "ingested_at": event["occurred_at"],
            "image_url": image_url,
            "category": category,
            "tags": tags,
            "score": score,
        },
    }
    await state["exchange"].publish(
        aio_pika.Message(
            json.dumps(out, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=out["event_id"],
        ),
        routing_key=out["event_type"],
    )


async def startup():
    state["model"] = load_model()

    pool = await with_retries("db", lambda: asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5))
    await init_db(pool)
    if state["model"] is not None:
        state["model_version"] = await register_model_version(pool)
    state["pool"] = pool

    # connect_robust reconecta (y recupera canal y consumidor) si RabbitMQ se cae.
    connection = await with_retries("rabbitmq", lambda: aio_pika.connect_robust(RABBITMQ_URL))
    channel = await connection.channel()  # con publisher confirms: publish espera al broker
    await channel.set_qos(prefetch_count=10)
    # Exchange y cola los crea rabbitmq-init; aquí solo se verifica que existan.
    state["exchange"] = await channel.get_exchange(EXCHANGE)
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
    if state["pool"] is not None:
        await state["pool"].close()


app = FastAPI(title=SERVICE, lifespan=lifespan)


async def db_ok():
    if state["pool"] is None:
        return False
    try:
        async with state["pool"].acquire(timeout=2) as conn:
            await conn.fetchval("SELECT 1")
        return True
    except Exception:  # noqa: BLE001
        return False


def rabbitmq_ok():
    return state["amqp"] is not None and not state["amqp"].is_closed


@app.get("/health")
async def health():
    db, mq = await db_ok(), rabbitmq_ok()
    body = {
        "status": "ok" if db and mq else "unavailable",
        "service": SERVICE,
        "database": "connected" if db else "disconnected",
        "rabbitmq": "connected" if mq else "disconnected",
        "model": "loaded" if state["model"] is not None else "missing",
    }
    return JSONResponse(body, status_code=200 if db and mq else 503)


def model_missing():
    return JSONResponse({"model": "missing"}, status_code=503)


class ClassifyRequest(BaseModel):
    title: str = Field(max_length=1000)
    body: str = Field(max_length=100_000)

    @field_validator("title", "body")
    @classmethod
    def not_blank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("no puede estar vacío")
        return value


@app.get("/api/categories")
def list_categories():
    return {"categories": CATEGORIES}


@app.post("/api/classify")
def classify_text(req: ClassifyRequest):
    """Clasifica un texto de prueba; no guarda nada ni publica eventos."""
    if state["model"] is None:
        return model_missing()
    category, score, tags = classify(req.title, req.body)
    return {"category": category, "score": score, "tags": tags}


@app.get("/api/model")
async def active_model():
    if state["model"] is None:
        return model_missing()
    if state["model_version"] is None:
        # Modelo cargado pero sin model/metrics.json: no hay métricas que mostrar.
        return {"model": "loaded", "version": None}
    try:
        async with state["pool"].acquire(timeout=2) as conn:
            row = await conn.fetchrow(
                "SELECT id, created_at, algorithm, precision_macro, recall_macro, f1_macro"
                " FROM model_versions WHERE active")
    except Exception:  # noqa: BLE001 — pool aún no creado o BD caída
        return JSONResponse({"model": "loaded", "database": "disconnected"}, status_code=503)
    if row is None:
        return {"model": "loaded", "version": None}
    return {
        "model": "loaded",
        "version": row["id"],
        "created_at": row["created_at"].astimezone(timezone.utc)
                                       .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "algorithm": row["algorithm"],
        "precision_macro": row["precision_macro"],
        "recall_macro": row["recall_macro"],
        "f1_macro": row["f1_macro"],
        # metrics.json completo: métricas por categoría, matriz de confusión, tamaños.
        "metrics": state["metrics"],
    }
