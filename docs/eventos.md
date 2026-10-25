# Contrato de eventos — newsproject

Este documento es la **única fuente de verdad** de la comunicación asíncrona entre microservicios. Cualquier cambio se hace por pull request y debe ser aprobado por los 4 integrantes.

## 1. Topología RabbitMQ

| Elemento | Valor |
|---|---|
| Exchange | `newsproject.events` |
| Tipo | `topic` |
| Durable | sí |
| Formato de mensaje | JSON (UTF-8), `content_type: application/json` |
| Entrega | `delivery_mode: 2` (persistente) |

### Routing keys

Formato `<entidad>.<acción en pasado>`: un evento describe algo que ya ocurrió.

| Routing key | Publica | Descripción |
|---|---|---|
| `article.ingested` | Ingesta | Artículo recién obtenido de una fuente |
| `article.classified` | Clasificación | Artículo con categoría y etiquetas asignadas |
| `user.preferences.updated` | Usuarios | El usuario cambió sus intereses |

### Colas (una por servicio consumidor y por evento)

| Cola | Routing key | Consume |
|---|---|---|
| `clasificacion.article-ingested` | `article.ingested` | Clasificación |
| `busqueda.article-classified` | `article.classified` | Búsqueda |
| `notificaciones.article-classified` | `article.classified` | Notificaciones |
| `notificaciones.user-preferences-updated` | `user.preferences.updated` | Notificaciones |

Cada cola tiene su cola de mensajes fallidos: `<nombre>.dlq` (dead-letter), a la que pasa el mensaje tras 3 reintentos fallidos.

## 2. Sobre común (envelope)

Todos los mensajes comparten este envoltorio. El contenido específico va en `payload`.

```json
{
  "event_id": "b7c1f6de-4a0e-4d2a-9d1e-3f6a2c8e9a10",
  "event_type": "article.ingested",
  "occurred_at": "2026-10-25T14:30:00Z",
  "version": 1,
  "payload": { }
}
```

| Campo | Tipo | Notas |
|---|---|---|
| `event_id` | UUID v4 | Único por mensaje. Los consumidores lo usan para ser **idempotentes** (ignorar duplicados) |
| `event_type` | string | Igual a la routing key |
| `occurred_at` | ISO 8601 UTC | Se usa para medir latencia extremo a extremo |
| `version` | entero | Versión del esquema del payload |
| `payload` | objeto | Ver secciones siguientes |

## 3. Eventos

### 3.1 `article.ingested`

Publica: **Ingesta**. Consume: **Clasificación**.

```json
{
  "event_id": "b7c1f6de-4a0e-4d2a-9d1e-3f6a2c8e9a10",
  "event_type": "article.ingested",
  "occurred_at": "2026-10-25T14:30:00Z",
  "version": 1,
  "payload": {
    "article_id": "5f2a9c1e-8d3b-4c7a-b1e2-0a9d4e6f7c31",
    "url": "https://ejemplo.com/noticia-1",
    "title": "Título de la noticia",
    "body": "Texto limpio del artículo...",
    "source": "ejemplo.com",
    "published_at": "2026-10-25T13:55:00Z",
    "hash": "sha256-del-contenido"
  }
}
```

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `article_id` | UUID | sí | Identificador del artículo en todo el sistema |
| `url` | string | sí | URL original |
| `title` | string | sí | |
| `body` | string | sí | Texto sin HTML |
| `source` | string | sí | Nombre o dominio de la fuente |
| `published_at` | ISO 8601 UTC | sí | Fecha de publicación original |
| `hash` | string | sí | SHA-256 de título + cuerpo, para deduplicar |

### 3.2 `article.classified`

Publica: **Clasificación**. Consume: **Búsqueda** y **Notificaciones**.

```json
{
  "event_id": "0e8d2b44-6a1f-4f5e-9c3d-72b1a8d5e620",
  "event_type": "article.classified",
  "occurred_at": "2026-10-25T14:30:02Z",
  "version": 1,
  "payload": {
    "article_id": "5f2a9c1e-8d3b-4c7a-b1e2-0a9d4e6f7c31",
    "url": "https://ejemplo.com/noticia-1",
    "title": "Título de la noticia",
    "summary": "Resumen corto del artículo.",
    "source": "ejemplo.com",
    "published_at": "2026-10-25T13:55:00Z",
    "ingested_at": "2026-10-25T14:30:00Z",
    "category": "tecnologia",
    "tags": ["inteligencia-artificial", "ecuador"],
    "score": 0.87
  }
}
```

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `article_id` | UUID | sí | El mismo de `article.ingested` |
| `url`, `title`, `source`, `published_at` | — | sí | Copiados del evento original |
| `summary` | string | sí | Primeras ~200 palabras o resumen generado |
| `ingested_at` | ISO 8601 UTC | sí | Copia de `occurred_at` de `article.ingested`; necesario para calcular latencia |
| `category` | string | sí | Una de las categorías de la sección 4 |
| `tags` | string[] | sí | Puede ser vacío |
| `score` | número 0–1 | sí | Confianza de la clasificación |

### 3.3 `user.preferences.updated`

Publica: **Usuarios**. Consume: **Notificaciones**.

```json
{
  "event_id": "9c4e7a12-3b5d-4e8f-a6c1-1d2e3f4a5b6c",
  "event_type": "user.preferences.updated",
  "occurred_at": "2026-10-25T14:25:00Z",
  "version": 1,
  "payload": {
    "user_id": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
    "categories": ["tecnologia", "deportes"],
    "keywords": ["rabbitmq", "ecuador"],
    "notifications_enabled": true
  }
}
```

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `user_id` | UUID | sí | |
| `categories` | string[] | sí | Reemplaza por completo la lista anterior (no es un diff) |
| `keywords` | string[] | sí | Reemplaza por completo la lista anterior |
| `notifications_enabled` | booleano | sí | |

Notificaciones guarda una copia local de las preferencias a partir de este evento, así no depende de que Usuarios esté disponible.

## 4. Categorías válidas

`politica`, `economia`, `tecnologia`, `deportes`, `salud`, `cultura`, `internacional`, `otros`

## 5. Reglas para los consumidores

1. **Idempotencia:** si llega un `event_id` ya procesado, se hace `ack` y se descarta.
2. **Confirmación manual (`ack`)** solo después de procesar y guardar con éxito.
3. **Reintentos:** hasta 3 veces; después, el mensaje va a la `.dlq`.
4. **Campos desconocidos:** se ignoran (permite añadir campos sin romper a nadie).
5. **Cambios incompatibles** (renombrar o quitar campos): se sube `version` y se avisa al grupo antes de publicar.
6. **Sin llamadas REST entre servicios** para estos flujos; solo eventos.

## 6. Trazabilidad

La latencia extremo a extremo se calcula como la hora en que el artículo llega a la pantalla del usuario menos `ingested_at` (o `published_at` del evento original, según la métrica que definan en el informe).
