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

Las colas principales son de tipo **quorum** con `x-delivery-limit: 3`. Cada una tiene su cola de mensajes fallidos `<nombre>.dlq`, alimentada por el exchange `newsproject.dlx` (tipo `direct`): RabbitMQ cuenta las entregas y, al pasar de 3, mueve el mensaje a la `.dlq` sin lógica extra en el consumidor. La topología se carga sola desde `infra/rabbitmq/definitions.json`.

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
| `event_id` | UUID v4 | Único por mensaje; sirve para trazas y para descartar reentregas del mismo mensaje. La idempotencia de negocio va por entidad (sección 5) |
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
    "image_url": "https://ejemplo.com/fotos/noticia-1.jpg",
    "hash": "sha256-del-contenido"
  }
}
```

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `article_id` | UUID v5 | sí | Identificador del artículo en todo el sistema. Se calcula con `uuid5(NAMESPACE_URL, url_normalizada)`: la misma noticia scrapeada dos veces tiene siempre el mismo `article_id` |
| `url` | string | sí | URL original |
| `title` | string | sí | |
| `body` | string | sí | Texto sin HTML |
| `source` | string | sí | Nombre o dominio de la fuente |
| `published_at` | ISO 8601 UTC | sí | Fecha de publicación original |
| `image_url` | string o `null` | no | Imagen de portada que trae el feed (URL absoluta `https://`). `null` o ausente si no hay. Ver sección 3.4 |
| `hash` | string | sí | SHA-256 de título + cuerpo (sin `image_url`); Ingesta solo republica un artículo si su hash cambió |

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
    "image_url": "https://ejemplo.com/fotos/noticia-1.jpg",
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
| `image_url` | string o `null` | no | Copiado tal cual de `article.ingested`; no se usa para clasificar |
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

### 3.4 Imagen de portada (`image_url`)

Campo **opcional** de `article.ingested` y `article.classified`, para mostrar la portada de cada noticia en el frontend.

- **Origen:** Ingesta lo toma del propio feed, en este orden: `media:content` de tipo imagen, `media:thumbnail`, `enclosure` de tipo `image/*` y, si no hay ninguno, la primera `<img>` del contenido o resumen. Solo se acepta una URL absoluta `https://`; si no hay, va `null`.
- **No se descarga** ni se guarda la imagen: el frontend la carga desde el medio y muestra un marcador si no hay o no carga.
- **Recorrido:** Clasificación la copia sin cambios; Búsqueda y Notificaciones la guardan y la devuelven en sus respuestas REST y en el mensaje WebSocket (sección 7).
- **Compatibilidad:** es un campo nuevo y opcional, y los consumidores ignoran lo que no conocen (regla 5 de la sección 5), así que `version` sigue en 1.
- **No afecta al modelo ni al dataset:** el clasificador usa solo `title + ". " + body`, y `clean_html` elimina las `<img>`, así que el texto es idéntico con o sin imagen.

## 4. Categorías válidas

`politica`, `economia`, `tecnologia`, `deportes`, `salud`, `cultura`, `internacional`, `otros`

## 5. Reglas para los consumidores

1. **Idempotencia por entidad:** los eventos de artículo se guardan con upsert por `article_id`; los de preferencias por `user_id`, ignorando un evento con `occurred_at` más antiguo que el ya guardado. Un mensaje repetido se confirma con `ack` y se descarta.
2. **Confirmación manual (`ack`)** solo después de procesar y guardar con éxito; nunca `auto-ack`.
3. **Reintentos:** ante un error transitorio, `nack` con `requeue=true`; la cola quorum cuenta las entregas y al pasar de 3 envía el mensaje a su `.dlq`. Un mensaje inválido (JSON roto o campo obligatorio ausente) va directo a la `.dlq` con `nack` y `requeue=false`.
4. **Prefetch** de 10 mensajes por consumidor.
5. **Campos desconocidos:** se ignoran (permite añadir campos sin romper a nadie).
6. **Cambios incompatibles** (renombrar o quitar campos): se sube `version` y se acuerdan por pull request aprobado por los 4 integrantes.
7. **Sin llamadas REST entre servicios** para estos flujos; solo eventos.

## 6. Trazabilidad

La latencia extremo a extremo se calcula como la hora en que el artículo llega a la pantalla del usuario menos `ingested_at`. Las métricas completas están en `docs/arquitectura.md`, sección 10.

## 7. Mensaje WebSocket al frontend

No es un evento de RabbitMQ, pero también es contrato. Notificaciones lo envía por `/ws/notifications?user_id=<id>` (vía gateway) cuando un `article.classified` coincide con las preferencias del usuario:

```json
{
  "type": "notification.new_article",
  "data": {
    "notification_id": "3d9e1c5a-7b2f-4e61-8a0d-5c4b3a2f1e90",
    "article_id": "5f2a9c1e-8d3b-5c7a-b1e2-0a9d4e6f7c31",
    "title": "Título de la noticia",
    "summary": "Resumen corto del artículo.",
    "category": "tecnologia",
    "tags": ["inteligencia-artificial"],
    "url": "https://ejemplo.com/noticia-1",
    "image_url": "https://ejemplo.com/fotos/noticia-1.jpg",
    "ingested_at": "2026-10-25T14:30:00Z",
    "sent_at": "2026-10-25T14:30:03Z"
  }
}
```

`image_url` puede ser `null`; el frontend muestra entonces un marcador con la categoría.
