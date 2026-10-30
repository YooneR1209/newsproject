"""Ingesta — plantilla base (responsable: Kevin).

Reemplazar por la implementación real. Contrato en docs/eventos.md y
endpoints en docs/arquitectura.md (sección 7):
  GET  /api/sources
  POST /api/sources
  POST /api/sources/{id}/scrape  -> publica article.ingested
"""
from fastapi import FastAPI

app = FastAPI(title="ingesta")


@app.get("/health")
def health():
    # TODO: responder 200 solo si MongoDB y RabbitMQ están conectados.
    return {"status": "ok", "service": "ingesta"}
