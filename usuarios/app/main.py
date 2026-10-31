"""Usuarios — microservicio (responsable: Ariana).

Registro de usuarios y gestión de preferencias.
Publica user.preferences.updated y expone (docs/arquitectura.md, sección 7):
  POST /api/users
  GET  /api/users?email=
  GET  /api/users/{id}
  PUT  /api/users/{id}/preferences
"""
import asyncio
import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aio_pika
import asyncpg
from asyncpg.exceptions import UniqueViolationError
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

SERVICE = "usuarios"
DATABASE_URL = os.environ["DATABASE_URL"]
RABBITMQ_URL = os.environ["RABBITMQ_URL"]
EXCHANGE = "newsproject.events"
ROUTING_KEY_PREF = "user.preferences.updated"
CATEGORIAS_VALIDAS = [
    "politica", "economia", "tecnologia", "deportes",
    "salud", "cultura", "internacional", "otros",
]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id UUID PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS preferences (
    user_id UUID PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    categories TEXT[] NOT NULL DEFAULT '{}',
    keywords TEXT[] NOT NULL DEFAULT '{}',
    notifications_enabled BOOLEAN NOT NULL DEFAULT true,
    updated_at TIMESTAMPTZ
);
"""

# Estado compartido del proceso; lo llena el arranque en segundo plano.
state = {"pool": None, "amqp": None, "channel": None, "exchange": None}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def log(stage, **fields):
    """Una línea JSON por evento (regla 10 de docs/eventos.md)."""
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


def iso_z(value):
    """Formatea un datetime aware a ISO 8601 UTC con sufijo Z."""
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def no_disponible():
    if state["pool"] is None or state["exchange"] is None:
        return JSONResponse({"detail": "servicio no disponible"}, status_code=503)
    return None


async def usuario_a_formato(pool, user_id):
    """Devuelve el diccionario de respuesta del punto 4, o None si no existe."""
    row = await pool.fetchrow("""
        SELECT u.user_id, u.email, u.name, u.created_at,
               p.categories, p.keywords, p.notifications_enabled, p.updated_at
        FROM users u
        LEFT JOIN preferences p ON p.user_id = u.user_id
        WHERE u.user_id = $1
    """, user_id)
    if row is None:
        return None
    return {
        "user_id": str(row["user_id"]),
        "email": row["email"],
        "name": row["name"],
        "created_at": iso_z(row["created_at"]),
        "preferences": {
            "categories": list(row["categories"] or []),
            "keywords": list(row["keywords"] or []),
            "notifications_enabled": row["notifications_enabled"],
            "updated_at": iso_z(row["updated_at"]) if row["updated_at"] else None,
        },
    }


async def usuario_a_formato_por_email(pool, email):
    """Busca por email normalizado y devuelve el formato de respuesta o None."""
    row = await pool.fetchrow("""
        SELECT u.user_id, u.email, u.name, u.created_at,
               p.categories, p.keywords, p.notifications_enabled, p.updated_at
        FROM users u
        LEFT JOIN preferences p ON p.user_id = u.user_id
        WHERE u.email = $1
    """, email)
    if row is None:
        return None
    return {
        "user_id": str(row["user_id"]),
        "email": row["email"],
        "name": row["name"],
        "created_at": iso_z(row["created_at"]),
        "preferences": {
            "categories": list(row["categories"] or []),
            "keywords": list(row["keywords"] or []),
            "notifications_enabled": row["notifications_enabled"],
            "updated_at": iso_z(row["updated_at"]) if row["updated_at"] else None,
        },
    }


async def publicar_preferencias_actualizadas(state, user_id, categories, keywords,
                                             notifications_enabled, occurred_at):
    """Publica user.preferences.updated en newsproject.events (persistente)."""
    event = {
        "event_id": str(uuid.uuid4()),
        "event_type": "user.preferences.updated",
        "occurred_at": occurred_at,
        "version": 1,
        "payload": {
            "user_id": str(user_id),
            "categories": categories,
            "keywords": keywords,
            "notifications_enabled": notifications_enabled,
        },
    }
    await state["exchange"].publish(
        aio_pika.Message(
            json.dumps(event, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=event["event_id"],
        ),
        routing_key=ROUTING_KEY_PREF,
    )
    log("preferences.published", event_id=event["event_id"], user_id=str(user_id))
    return event["event_id"]


class UserCreate(BaseModel):
    email: str
    name: str


class PreferencesUpdate(BaseModel):
    categories: list[str]
    keywords: list[str]
    notifications_enabled: bool = True


async def startup():
    pool = await with_retries("db", lambda: asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5))
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)
    state["pool"] = pool
    log("db.ready")

    connection = await with_retries("rabbitmq", lambda: aio_pika.connect_robust(RABBITMQ_URL))
    channel = await connection.channel()
    # El exchange ya lo crea rabbitmq-init; aquí solo se obtiene.
    exchange = await channel.get_exchange(EXCHANGE)
    state["channel"] = channel
    state["exchange"] = exchange
    state["amqp"] = connection

    def _on_connection_lost(*_args):
        log("rabbitmq.connection_lost")

    def _on_reconnected(*_args):
        log("rabbitmq.reconnected")

    connection.close_callbacks.add(_on_connection_lost)
    connection.reconnect_callbacks.add(_on_reconnected)

    log("rabbitmq.ready")


@asynccontextmanager
async def lifespan(_app):
    # El arranque va en segundo plano para que /health responda mientras tanto.
    task = asyncio.create_task(startup())
    yield
    task.cancel()
    if state["amqp"] is not None:
        await state["amqp"].close()
    if state["pool"] is not None:
        await state["pool"].close()


app = FastAPI(title=SERVICE, lifespan=lifespan)


@app.get("/health")
async def health():
    db_ok = False
    try:
        async with state["pool"].acquire(timeout=2) as conn:
            await conn.fetchval("SELECT 1")
        db_ok = True
    except Exception:  # noqa: BLE001
        pass
    mq_ok = (
        state["amqp"] is not None
        and not state["amqp"].is_closed
        and state["amqp"].connected.is_set()
        and not state["amqp"].reconnecting
    )
    body = {
        "status": "ok" if db_ok and mq_ok else "unavailable",
        "service": SERVICE,
        "db": "ok" if db_ok else "error",
        "rabbitmq": "ok" if mq_ok else "error",
    }
    return JSONResponse(body, status_code=200 if db_ok and mq_ok else 503)


@app.post("/api/users", status_code=201)
async def crear_usuario(body: UserCreate):
    if (resp := no_disponible()) is not None:
        return resp
    email = body.email.strip().lower()
    name = body.name.strip()
    if not EMAIL_RE.match(email):
        return JSONResponse({"detail": "email inválido"}, status_code=422)
    if not name:
        return JSONResponse({"detail": "el nombre no puede estar vacío"}, status_code=422)

    user_id = uuid.uuid4()
    try:
        async with state["pool"].acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO users (user_id, email, name) VALUES ($1, $2, $3)",
                    user_id, email, name,
                )
                await conn.execute(
                    "INSERT INTO preferences (user_id) VALUES ($1)",
                    user_id,
                )
    except UniqueViolationError:
        return JSONResponse({"detail": "el email ya está registrado"}, status_code=409)

    return await usuario_a_formato(state["pool"], user_id)


@app.get("/api/users")
async def buscar_usuario_por_email(email: str = Query(...)):
    if (resp := no_disponible()) is not None:
        return resp
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        return JSONResponse({"detail": "email inválido"}, status_code=422)

    usuario = await usuario_a_formato_por_email(state["pool"], email)
    if usuario is None:
        return JSONResponse({"detail": "usuario no encontrado"}, status_code=404)
    return usuario


@app.get("/api/users/{user_id}")
async def obtener_usuario(user_id: uuid.UUID):
    if (resp := no_disponible()) is not None:
        return resp
    usuario = await usuario_a_formato(state["pool"], user_id)
    if usuario is None:
        return JSONResponse({"detail": "usuario no encontrado"}, status_code=404)
    return usuario


@app.put("/api/users/{user_id}/preferences")
async def actualizar_preferencias(user_id: uuid.UUID, body: PreferencesUpdate):
    if (resp := no_disponible()) is not None:
        return resp
    # --- normalizar categorías ---
    cats_norm = [c.strip().lower() for c in body.categories]
    invalid_cats = [c for c in cats_norm if c not in CATEGORIAS_VALIDAS]
    if invalid_cats:
        return JSONResponse({
            "detail": f"categorías inválidas: {', '.join(invalid_cats)}. Válidas: {', '.join(CATEGORIAS_VALIDAS)}"
        }, status_code=422)

    # deduplicar categorías conservando el orden
    categories = []
    seen_cats = set()
    for c in cats_norm:
        if c not in seen_cats:
            categories.append(c)
            seen_cats.add(c)

    # --- validar keywords ---
    keywords = []
    seen_keys = set()
    for k in body.keywords:
        k = k.strip().lower()
        if not k or k in seen_keys:
            continue
        if len(k) > 50:
            return JSONResponse({"detail": f"la palabra clave '{k}' excede los 50 caracteres"}, status_code=422)
        seen_keys.add(k)
        keywords.append(k)

    if len(keywords) > 20:
        return JSONResponse({
            "detail": f"máximo 20 palabras clave, se enviaron {len(keywords)}"
        }, status_code=422)

    # verificar que el usuario existe
    existe = await state["pool"].fetchval("SELECT 1 FROM users WHERE user_id = $1", user_id)
    if not existe:
        return JSONResponse({"detail": "usuario no encontrado"}, status_code=404)

    # Transacción: actualizar preferencias y publicar el evento.
    try:
        async with state["pool"].acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow("""
                    UPDATE preferences
                    SET categories = $2, keywords = $3, notifications_enabled = $4, updated_at = now()
                    WHERE user_id = $1
                    RETURNING updated_at
                """, user_id, categories, keywords, body.notifications_enabled)
                occurred_at = iso_z(row["updated_at"])
                await publicar_preferencias_actualizadas(
                    state, user_id, categories, keywords, body.notifications_enabled, occurred_at
                )
    except Exception as exc:  # noqa: BLE001 — BD o broker: se revierte y responde 503
        log("preferences.publish_failed", error=str(exc), user_id=str(user_id))
        return JSONResponse(
            {"detail": "no se pudo publicar el evento, intente de nuevo"}, status_code=503
        )

    return await usuario_a_formato(state["pool"], user_id)
