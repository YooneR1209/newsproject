"""Búsqueda — plantilla base (responsable: Fabricio).

Reemplazar por la implementación real. Consume article.classified
(cola busqueda.article-classified) y expone (docs/arquitectura.md, sección 7):
  GET /api/search?q=&category=&page=
  GET /api/articles/{id}
"""
from fastapi import FastAPI

app = FastAPI(title="busqueda")


@app.get("/health")
def health():
    # TODO: responder 200 solo si MongoDB y RabbitMQ están conectados.
    return {"status": "ok", "service": "busqueda"}
