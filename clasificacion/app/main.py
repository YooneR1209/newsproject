"""Clasificación — plantilla base (responsable: Fabricio).

Reemplazar por la implementación real: TF-IDF + Naive Bayes o regresión
logística (docs/arquitectura.md, sección 3.1). Consume article.ingested
(cola clasificacion.article-ingested), publica article.classified y expone:
  GET  /api/categories
  POST /api/classify
  GET  /api/model
"""
from fastapi import FastAPI

app = FastAPI(title="clasificacion")


@app.get("/health")
def health():
    # TODO: responder 200 solo si PostgreSQL y RabbitMQ están conectados
    # y el modelo (model.joblib) está cargado.
    return {"status": "ok", "service": "clasificacion"}
