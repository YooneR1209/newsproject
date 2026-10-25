"""API Gateway — plantilla base (responsable: Ariana).

Punto de entrada único. Reemplazar por la implementación real del
enrutamiento por prefijo (docs/arquitectura.md, sección 7):
  /api/sources                                 -> INGESTA_URL
  /api/categories, /api/classify, /api/model   -> CLASIFICACION_URL
  /api/search, /api/articles                   -> BUSQUEDA_URL
  /api/users                                   -> USUARIOS_URL
  /api/notifications                           -> NOTIFICACIONES_URL
  /ws/notifications  (WebSocket)               -> NOTIFICACIONES_URL /ws
Si un servicio no responde, devolver 503 solo para su prefijo.
"""
from fastapi import FastAPI

app = FastAPI(title="gateway")


@app.get("/health")
def health():
    return {"status": "ok", "service": "gateway"}
