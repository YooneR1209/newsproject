"""API Gateway — microservicio (responsable: Ariana).

Punto de entrada único. Enruta REST por prefijo hacia los cinco
microservicios y reenvía el WebSocket de notificaciones. Sin BD ni broker
propio.
"""
import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

SERVICE = "gateway"

INGESTA_URL = os.environ["INGESTA_URL"]
CLASIFICACION_URL = os.environ["CLASIFICACION_URL"]
BUSQUEDA_URL = os.environ["BUSQUEDA_URL"]
USUARIOS_URL = os.environ["USUARIOS_URL"]
NOTIFICACIONES_URL = os.environ["NOTIFICACIONES_URL"]

SERVICES = {
    "ingesta": INGESTA_URL,
    "clasificacion": CLASIFICACION_URL,
    "busqueda": BUSQUEDA_URL,
    "usuarios": USUARIOS_URL,
    "notificaciones": NOTIFICACIONES_URL,
}

ROUTES = {
    "/api/sources": "ingesta",
    "/api/categories": "clasificacion",
    "/api/classify": "clasificacion",
    "/api/model": "clasificacion",
    "/api/search": "busqueda",
    "/api/articles": "busqueda",
    "/api/users": "usuarios",
    "/api/notifications": "notificaciones",
}

NOTIFICACIONES_WS_URL = os.environ.get(
    "NOTIFICACIONES_WS_URL",
    NOTIFICACIONES_URL.replace("http://", "ws://") + "/ws",
)

HOP_BY_HOP = {
    "host", "content-length", "connection", "keep-alive",
    "transfer-encoding", "upgrade", "te", "trailer",
    "proxy-authorization", "proxy-authenticate",
}
RESPONSE_SKIP = HOP_BY_HOP | {"content-encoding"}

state = {"client": None}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def log(stage, **fields):
    """Una línea JSON por evento."""
    print(json.dumps({"ts": utc_now(), "service": SERVICE, "stage": stage, **fields},
                     ensure_ascii=False), flush=True)


def clean_headers(headers, exclude=HOP_BY_HOP):
    return {k: v for k, v in headers.items() if k.lower() not in exclude}


def match_route(path: str):
    for prefix, service in ROUTES.items():
        if path == prefix or path.startswith(prefix + "/"):
            return service
    return None


async def startup():
    timeout = httpx.Timeout(connect=3.0, read=15.0, write=15.0, pool=15.0)
    state["client"] = httpx.AsyncClient(timeout=timeout)


async def shutdown():
    if state["client"] is not None:
        await state["client"].aclose()


@asynccontextmanager
async def lifespan(_app):
    await startup()
    yield
    await shutdown()


app = FastAPI(title=SERVICE, lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE}


@app.get("/api/status")
async def api_status():
    async def check(name, url):
        try:
            start = time.monotonic()
            resp = await state["client"].get(f"{url}/health", timeout=2.0)
            latency_ms = round((time.monotonic() - start) * 1000)
            if resp.status_code == 200:
                return name, {"status": "ok", "http_status": resp.status_code, "latency_ms": latency_ms}
            return name, {"status": "down", "http_status": resp.status_code,
                          "latency_ms": latency_ms, "error": f"HTTP {resp.status_code}"}
        except httpx.ConnectError:
            return name, {"status": "down", "http_status": None, "error": "ConnectError"}
        except httpx.TimeoutException:
            return name, {"status": "down", "http_status": None, "error": "TimeoutException"}
        except httpx.RequestError as exc:
            return name, {"status": "down", "http_status": None, "error": type(exc).__name__}

    results = {}
    overall = "ok"
    checks = [check(name, url) for name, url in SERVICES.items()]
    for name, info in await asyncio.gather(*checks):
        results[name] = info
        if info["status"] != "ok":
            overall = "degraded"

    return JSONResponse({
        "status": overall,
        "gateway": "ok",
        "checked_at": utc_now(),
        "services": results,
    })


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(path: str, request: Request):
    full_path = request.url.path
    service = match_route(full_path)
    if service is None:
        return JSONResponse({"detail": "ruta no encontrada"}, status_code=404)

    start = time.monotonic()
    try:
        resp = await state["client"].request(
            method=request.method,
            url=f"{SERVICES[service]}{full_path}",
            params=request.query_params,
            content=await request.body(),
            headers=clean_headers(request.headers),
        )
        latency_ms = round((time.monotonic() - start) * 1000)
        log("proxy", method=request.method, path=full_path, service=service,
            status=resp.status_code, latency_ms=latency_ms)

        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=clean_headers(resp.headers, exclude=RESPONSE_SKIP),
            media_type=resp.headers.get("content-type"),
        )
    except httpx.RequestError as exc:
        latency_ms = round((time.monotonic() - start) * 1000)
        log("proxy.unavailable", method=request.method, path=full_path, service=service,
            error=type(exc).__name__, latency_ms=latency_ms)
        return JSONResponse(
            {"detail": f"servicio {service} no disponible", "service": service},
            status_code=503,
        )


@app.websocket("/ws/notifications")
async def ws_notifications(websocket: WebSocket):
    await websocket.accept()
    user_id = websocket.query_params.get("user_id")
    if not user_id:
        await websocket.close(code=1008, reason="user_id requerido")
        return

    ws_url = f"{NOTIFICACIONES_WS_URL}?user_id={quote(user_id)}"
    try:
        upstream = await websockets.connect(ws_url, open_timeout=5)
    except (websockets.exceptions.InvalidHandshake, OSError, asyncio.TimeoutError) as exc:
        log("ws.upstream_unavailable", user_id=user_id, error=type(exc).__name__)
        await websocket.close(code=1013, reason="notificaciones no disponible")
        return

    log("ws.open", user_id=user_id)

    async def client_to_upstream():
        try:
            while True:
                await upstream.send(await websocket.receive_text())
        except (WebSocketDisconnect, websockets.exceptions.ConnectionClosed, RuntimeError):
            pass

    async def upstream_to_client():
        try:
            async for msg in upstream:
                text = msg if isinstance(msg, str) else msg.decode("utf-8")
                await websocket.send_text(text)
        except (websockets.exceptions.ConnectionClosed, WebSocketDisconnect, RuntimeError):
            pass

    c2u = asyncio.create_task(client_to_upstream())
    u2c = asyncio.create_task(upstream_to_client())
    done, pending = await asyncio.wait({c2u, u2c}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    await upstream.close()
    if u2c in done:
        # Notificaciones cerró la conexión: avisar al navegador para que reconecte.
        try:
            await websocket.close(code=1013, reason="notificaciones no disponible")
        except Exception:  # noqa: BLE001 — el cliente ya pudo haberse ido
            pass
    log("ws.closed", user_id=user_id)
