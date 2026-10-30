"""Usuarios — plantilla base (responsable: Ariana).

Reemplazar por la implementación real. Publica user.preferences.updated
y expone (docs/arquitectura.md, sección 7):
  POST /api/users
  GET  /api/users/{id}
  PUT  /api/users/{id}/preferences
"""
from fastapi import FastAPI

app = FastAPI(title="usuarios")


@app.get("/health")
def health():
    # TODO: responder 200 solo si PostgreSQL y RabbitMQ están conectados.
    return {"status": "ok", "service": "usuarios"}
