# Documento de Arquitectura – Agregador de Noticias (v2)

25/10/2026 · Fabricio Josue Ruiz Aguilar · versión editable: https://claude.ai/artifact/N3raGZTxeEY26hnUBe1fVx

## 1. Introducción y alcance

El sistema obtiene noticias de fuentes externas, las clasifica, las vuelve buscables y notifica en tiempo real a cada usuario según sus preferencias. Lo hacen cinco microservicios desacoplados por RabbitMQ, más un API Gateway y un frontend React. Esta versión reemplaza a la del 21/10/2026 y adopta el contrato de eventos del repositorio (`docs/eventos.md`) como única fuente de verdad.

**Flujo extremo a extremo:** Ingesta obtiene un artículo y publica `article.ingested` → Clasificación lo etiqueta y publica `article.classified` → Búsqueda lo indexa y Notificaciones lo cruza con las preferencias de los usuarios → el frontend recibe la alerta por WebSocket sin recargar. En paralelo, Usuarios publica `user.preferences.updated` cada vez que alguien cambia sus intereses.

**Principios de diseño**

- Cada microservicio de negocio empaqueta su backend y su propia base de datos en un mismo contenedor; la BD solo escucha dentro de ese contenedor, así ningún servicio puede leer la BD de otro.
- Los procesos asíncronos se comunican solo por eventos en RabbitMQ, nunca con llamadas REST entre servicios.
- El frontend consume REST a través de un único API Gateway y recibe cambios en vivo por WebSocket.
- Cada servicio vive en su carpeta, con su Dockerfile, y corre en su propio contenedor.
- Todo el sistema se levanta con un solo comando: `docker compose up`.
- Un servicio caído no pierde mensajes ni detiene a los demás: colas durables, mensajes persistentes, `ack` manual y cola de mensajes fallidos (DLQ).

**Alcance:** ingesta, clasificación, búsqueda, usuarios, notificaciones, gateway y frontend. Quedan fuera la autenticación avanzada (OAuth/JWT) y el despliegue en la nube; el usuario se identifica por su `user_id`.

## 2. Diagrama de arquitectura

![Diagrama de arquitectura del Agregador de Noticias](img/arquitectura.png)

El frontend solo habla con el gateway. Cada caja de servicio es un contenedor con su backend y su BD. Los cinco microservicios no se llaman entre sí: cada uno publica o consume eventos, y cada flecha de color es un evento. Notificaciones es el único servicio que consume dos colas y el único que abre un WebSocket hacia el gateway. Dentro de RabbitMQ, el exchange reparte cada evento a sus colas según la routing key, y un mensaje que falla 3 veces pasa a su `.dlq` por `newsproject.dlx`.

## 3. Microservicios, bases de datos y responsables

Son cinco microservicios de negocio, cada uno con su backend y su base de datos dentro del mismo contenedor, más el gateway, el frontend y el broker. Usuarios se separa de Notificaciones para que cada servicio tenga una sola responsabilidad: si Usuarios cae, Notificaciones sigue enviando alertas con su copia local de las preferencias.

| Servicio (carpeta) | Responsabilidad | Stack | Base de datos propia (mismo contenedor) | Publica | Consume | Responsable |
| --- | --- | --- | --- | --- | --- | --- |
| Ingesta (`ingesta/`) | Lee feeds RSS y sitios web, limpia el texto, deduplica y publica artículos | Python · FastAPI · feedparser · BeautifulSoup4 | MongoDB: fuentes, artículos crudos y bitácora de scraping | `article.ingested` | — | Kevin |
| Clasificación (`clasificacion/`) | Asigna categoría, etiquetas y puntaje de confianza | Python · FastAPI · scikit-learn (TF-IDF + Naive Bayes o regresión logística) | PostgreSQL: categorías, versiones del modelo con sus métricas y artículos clasificados | `article.classified` | `article.ingested` | Fabricio |
| Búsqueda (`busqueda/`) | Indexa artículos clasificados y atiende consultas de texto | Python · FastAPI | MongoDB con índice de texto | — | `article.classified` | Fabricio |
| Usuarios (`usuarios/`) | Registro de usuarios y gestión de preferencias | Python · FastAPI | PostgreSQL: usuarios y preferencias | `user.preferences.updated` | — | Ariana |
| Notificaciones (`notificaciones/`) | Cruza artículos con preferencias, guarda alertas y las emite por WebSocket | Java 21 · Spring Boot | PostgreSQL: copia de preferencias, alertas y eventos procesados | — | `article.classified`, `user.preferences.updated` | Ariel |
| API Gateway (`gateway/`) | Punto de entrada único: enruta REST y reenvía el WebSocket | Python · FastAPI · httpx · websockets | Sin BD (sin estado) | — | — | Ariana |
| Frontend (`frontend/`) | SPA: feed en vivo, búsqueda y preferencias | React · Vite, servido con Nginx | Sin BD | — | — | Todo el grupo |
| RabbitMQ | Broker de mensajes | `rabbitmq:3.13-management` | Volumen de persistencia | — | — | Todo el grupo |

Los servicios solo comparten contratos de eventos (sección 5) y de REST (sección 7), por eso cada integrante puede usar su lenguaje.

### 3.1 Método de clasificación: TF-IDF + clasificador supervisado

Clasificación usa un modelo de scikit-learn entrenado con noticias ya etiquetadas en las 8 categorías de la sección 5.4; no usa reglas manuales de palabras clave.

1. **Dataset:** noticias en español con su categoría, divididas 80 % entrenamiento y 20 % prueba, estratificadas por categoría.
2. **Vectorización:** `TfidfVectorizer` sobre título + cuerpo, con stopwords en español, unigramas y bigramas.
3. **Clasificador:** se entrenan `MultinomialNB` y `LogisticRegression` en un `Pipeline` y se comparan con validación cruzada; se queda el de mayor F1 macro.
4. **Evaluación:** precisión, exhaustividad (recall) y F1 por categoría y macro, más la matriz de confusión; estos valores van a los resultados del informe.
5. **Despliegue:** el script `clasificacion/train.py` entrena fuera de línea y guarda el pipeline con `joblib` (`model.joblib`); el servicio lo carga al arrancar y registra su versión y métricas en su BD.
6. **Inferencia por evento:** `category` = clase con mayor probabilidad (`predict_proba`), `score` = esa probabilidad. Si `score` es menor que un umbral configurable (`MIN_SCORE`, inicial 0.5), la categoría es `otros`.
7. **Etiquetas:** `tags` = los 5 términos con mayor peso TF-IDF del artículo, normalizados en minúsculas y con guiones.

Pendiente: elegir el dataset etiquetado (uno público en español o un conjunto etiquetado por el grupo) y fijar el umbral tras ver las métricas.

## 4. Comunicación síncrona, asíncrona y en tiempo real

Las consultas del usuario son síncronas (REST), todo el procesamiento de artículos y preferencias es asíncrono (RabbitMQ) y las alertas en vivo viajan por WebSocket.

| Tipo | Protocolo | Entre quiénes | Ejemplos |
| --- | --- | --- | --- |
| Síncrona | REST (HTTP/JSON) | Frontend → Gateway → Ingesta, Clasificación, Búsqueda, Usuarios, Notificaciones | `GET /api/search?q=`, `PUT /api/users/{id}/preferences`, `POST /api/sources/{id}/scrape` |
| Asíncrona | RabbitMQ (AMQP 0-9-1) | Ingesta → Clasificación → Búsqueda y Notificaciones; Usuarios → Notificaciones | `article.ingested`, `article.classified`, `user.preferences.updated` |
| Tiempo real | WebSocket | Notificaciones → Gateway → Frontend | `/ws/notifications?user_id=<id>`: el artículo aparece sin recargar |

**Reglas de comunicación**

1. Ningún servicio llama a otro por REST dentro de un flujo asíncrono; solo publica o consume eventos.
2. Las colas son durables y los mensajes persistentes (`delivery_mode: 2`); el consumidor hace `ack` solo después de procesar y guardar.
3. Un mensaje que falla 3 veces pasa a su DLQ y no bloquea la cola principal (sección 5.5).
4. Los consumidores son idempotentes: procesar dos veces el mismo artículo no genera duplicados.
5. El frontend nunca habla con RabbitMQ ni con las bases de datos; solo con el gateway.
6. Cada servicio reintenta la conexión al broker y a su BD al arrancar, con espera creciente.

## 5. Topología RabbitMQ y contratos de eventos

Un exchange `topic` (`newsproject.events`) reparte tres eventos a cuatro colas, una por consumidor y por evento. Cada cola tiene su DLQ, alimentada por el exchange `newsproject.dlx`. La topología se carga sola al arrancar desde `infra/rabbitmq/definitions.json`. El detalle completo de cada evento (JSON de ejemplo y campos) está en [`docs/eventos.md`](eventos.md).

### 5.1 Exchanges, colas y routing keys

| Cola | Routing key | Productor | Consumidor | DLQ |
| --- | --- | --- | --- | --- |
| `clasificacion.article-ingested` | `article.ingested` | Ingesta | Clasificación | `clasificacion.article-ingested.dlq` |
| `busqueda.article-classified` | `article.classified` | Clasificación | Búsqueda | `busqueda.article-classified.dlq` |
| `notificaciones.article-classified` | `article.classified` | Clasificación | Notificaciones | `notificaciones.article-classified.dlq` |
| `notificaciones.user-preferences-updated` | `user.preferences.updated` | Usuarios | Notificaciones | `notificaciones.user-preferences-updated.dlq` |

- Exchange principal: `newsproject.events`, tipo `topic`, durable.
- Exchange de mensajes fallidos: `newsproject.dlx`, tipo `direct`, durable; enruta a cada `.dlq` por su nombre.
- Las colas principales son de tipo **quorum** con `x-delivery-limit: 3`, `x-dead-letter-exchange: newsproject.dlx` y `x-dead-letter-routing-key: <cola>.dlq`.
- Las routing keys siguen el formato `<entidad>.<acción en pasado>`: un evento describe algo que ya ocurrió.
- Las dos colas de `article.classified` reciben cada una su propia copia del mensaje; agregar un consumidor nuevo no obliga a cambiar a Clasificación.

### 5.2 Sobre común (envelope)

Todos los mensajes comparten este envoltorio, en JSON UTF-8 con `content_type: application/json`. El contenido específico va en `payload`.

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
| --- | --- | --- |
| `event_id` | UUID v4 | Único por mensaje; sirve para trazas y para descartar reentregas del mismo mensaje |
| `event_type` | string | Igual a la routing key |
| `occurred_at` | ISO 8601 UTC | Momento en que el productor publicó; base para medir latencia |
| `version` | entero | Versión del esquema del `payload`; sube solo con cambios incompatibles |
| `payload` | objeto | Ver 5.3 |

### 5.3 Eventos

| Evento | Publica | Consume | Campos del `payload` |
| --- | --- | --- | --- |
| `article.ingested` | Ingesta | Clasificación | `article_id` (UUID v5 de la URL normalizada), `url`, `title`, `body`, `source`, `published_at`, `hash` |
| `article.classified` | Clasificación | Búsqueda, Notificaciones | `article_id`, `url`, `title`, `summary`, `source`, `published_at`, `ingested_at`, `category`, `tags`, `score` |
| `user.preferences.updated` | Usuarios | Notificaciones | `user_id`, `categories`, `keywords`, `notifications_enabled` |

`article_id` se calcula a partir de la URL normalizada, así la misma noticia scrapeada dos veces tiene siempre el mismo id. `ingested_at` es la copia del `occurred_at` de `article.ingested` y permite medir la latencia extremo a extremo. En `user.preferences.updated`, `categories` y `keywords` reemplazan por completo la lista anterior.

### 5.4 Categorías válidas

`politica`, `economia`, `tecnologia`, `deportes`, `salud`, `cultura`, `internacional`, `otros`

### 5.5 Reglas para los consumidores

1. **Idempotencia por entidad:** los eventos de artículo se deduplican por `article_id` (upsert); los de preferencias por `user_id`, ignorando un evento con `occurred_at` más antiguo que el ya guardado. Un mensaje repetido se confirma con `ack` y se descarta.
2. **`ack` manual** solo después de procesar y guardar con éxito; nunca `auto-ack`.
3. **Reintentos:** ante un error transitorio, `nack` con `requeue=true`; la cola quorum cuenta las entregas y al pasar de 3 envía el mensaje a su `.dlq`. Un mensaje inválido (JSON roto, campo obligatorio ausente) va directo con `nack` y `requeue=false`.
4. **Campos desconocidos** se ignoran, para poder añadir campos sin romper a nadie.
5. **Cambios incompatibles** (renombrar o quitar campos) suben `version` y se acuerdan por pull request aprobado por los cuatro integrantes.
6. **Prefetch** de 10 mensajes por consumidor, para repartir carga si se escala un servicio a varias réplicas.

## 6. Tiempo real con WebSockets

El WebSocket se usa solo para notificaciones: el artículo nuevo llega al navegador sin recargar en cuanto Notificaciones procesa `article.classified`.

- **Canal:** `ws://localhost:8080/ws/notifications?user_id=<id>`. El gateway lo reenvía a `ws://notificaciones:8080/ws?user_id=<id>`.
- **Cuándo se envía:** Notificaciones recibe `article.classified` y lo compara con su copia local de preferencias. Hay coincidencia si la `category` está en `categories` o alguna `keyword` aparece en `tags` o `title`, y `notifications_enabled` es verdadero.
- **Qué guarda:** primero guarda la alerta en su BD y luego la envía a las sesiones abiertas de ese usuario. Así ninguna alerta se pierde si el usuario no está conectado.
- **Reflejo en el frontend:** el feed agrega el artículo al inicio y aumenta el contador de no leídos, sin consulta REST adicional.
- **Reconexión:** al reconectar, el frontend pide `GET /api/notifications?user_id=<id>&unread=true` y recupera lo que llegó mientras estaba desconectado.

El formato del mensaje enviado al cliente (`notification.new_article`) está en [`docs/eventos.md`](eventos.md), sección 7. El frontend registra la hora de llegada de cada mensaje; con `ingested_at` y `sent_at` se calcula la latencia de la sección 10.

## 7. API REST por servicio

El frontend accede a todo por el gateway (`http://localhost:8080/api`), que enruta por prefijo al servicio correspondiente. Cada prefijo pertenece a un solo servicio, así no hay rutas en conflicto.

| Prefijo en el gateway | Servicio destino |
| --- | --- |
| `/api/sources` | Ingesta |
| `/api/categories`, `/api/classify`, `/api/model` | Clasificación |
| `/api/search`, `/api/articles` | Búsqueda |
| `/api/users` | Usuarios |
| `/api/notifications` | Notificaciones |
| `/ws/notifications` | Notificaciones (WebSocket) |

| Servicio | Método y ruta | Descripción |
| --- | --- | --- |
| Ingesta | `GET /api/sources` | Lista las fuentes configuradas |
| Ingesta | `POST /api/sources` | Registra una fuente nueva (RSS o sitio) |
| Ingesta | `POST /api/sources/{id}/scrape` | Dispara un scraping manual; responde `202 Accepted` y publica `article.ingested` por cada artículo nuevo |
| Clasificación | `GET /api/categories` | Lista las categorías válidas |
| Clasificación | `POST /api/classify` | Clasifica un texto de prueba y devuelve categoría, tags y score; no publica eventos |
| Clasificación | `GET /api/model` | Versión del modelo activo y sus métricas (precisión, recall, F1) |
| Búsqueda | `GET /api/search?q=&category=&page=` | Busca artículos por texto y categoría, paginado |
| Búsqueda | `GET /api/articles/{id}` | Detalle de un artículo |
| Usuarios | `POST /api/users` | Registra un usuario |
| Usuarios | `GET /api/users/{id}` | Datos y preferencias del usuario |
| Usuarios | `PUT /api/users/{id}/preferences` | Reemplaza categorías y keywords; publica `user.preferences.updated` |
| Notificaciones | `GET /api/notifications?user_id=&unread=` | Alertas guardadas del usuario |
| Notificaciones | `PATCH /api/notifications/{id}/read` | Marca una alerta como leída |

Cada servicio expone también `GET /health`, que revisa su conexión a RabbitMQ y a su BD; Docker lo usa como healthcheck. Si un servicio está caído, el gateway responde `503` solo para su prefijo y el resto sigue funcionando.

## 8. Docker y despliegue

Todo arranca con `docker compose up -d --build`: 9 contenedores en la red `newsproject-net`. Cada microservicio de negocio es **un solo contenedor con su backend y su base de datos dentro**. Solo el gateway, el frontend y el panel de RabbitMQ publican puertos al equipo anfitrión.

| Contenedor | Contiene | Imagen base | Puerto al anfitrión | Depende de |
| --- | --- | --- | --- | --- |
| `rabbitmq` | Broker | `rabbitmq:3.13-management` | 15672 (panel); 5672 solo en desarrollo | — |
| `rabbitmq-init` | Carga `definitions.json` y termina | `curlimages/curl` | — | `rabbitmq` (healthy) |
| `ingesta` | FastAPI + MongoDB | `mongo:7` + Python | — | `rabbitmq-init` (completado) |
| `clasificacion` | FastAPI + PostgreSQL | `postgres:16` + Python | — | `rabbitmq-init` (completado) |
| `busqueda` | FastAPI + MongoDB | `mongo:7` + Python | — | `rabbitmq-init` (completado) |
| `usuarios` | FastAPI + PostgreSQL | `postgres:16` + Python | — | `rabbitmq-init` (completado) |
| `notificaciones` | Spring Boot + PostgreSQL | `postgres:16` + JRE 21 copiado de `eclipse-temurin:21-jre` | — | `rabbitmq-init` (completado) |
| `gateway` | FastAPI, sin BD | `python:3.12-slim` | 8080 | los cinco servicios (healthy) |
| `frontend` | Build de React servido con Nginx | `node:20` (build) + `nginx` | 3000 | `gateway` |

Cada contenedor de servicio ejecuta dos procesos con `supervisord`: la base de datos, que escucha solo en `localhost` dentro del contenedor, y el backend. Las plantillas están en cada carpeta (`Dockerfile` y `supervisord.conf`); en PostgreSQL el programa `db` es `docker-entrypoint.sh postgres -c listen_addresses=localhost` y en MongoDB es `docker-entrypoint.sh mongod --bind_ip 127.0.0.1`.

- **Datos persistentes:** cada BD guarda sus datos en un volumen con nombre (`ingesta_data`, `clasificacion_data`, etc.), así reconstruir el contenedor no borra la información.
- **Aislamiento:** la BD escucha solo en `localhost`, por lo que ningún otro contenedor puede conectarse a ella; la regla "ningún servicio lee la BD de otro" queda garantizada por la red.
- **Arranque:** `supervisord` inicia la BD y el backend sin esperar uno al otro, así que el backend reintenta la conexión a su BD y a RabbitMQ con espera creciente. `/health` responde 200 solo cuando ambas conexiones están activas.
- **Credenciales:** cada servicio tiene su usuario y clave de BD en `.env` (`INGESTA_DB_USER`, `CLASIFICACION_DB_USER`, etc.); `.env.example` lleva valores de ejemplo y `.env` nunca se sube.
- **Inspeccionar una BD:** `docker exec -it newsproject-clasificacion psql -U <usuario> clasificacion` (o `mongosh` en Ingesta y Búsqueda); no hace falta exponer puertos de BD.
- **Topología:** al cambiar las colas a quorum hay que recrear el volumen de RabbitMQ (`docker compose down -v`), porque una cola existente no cambia de tipo.

## 9. Estructura del repositorio y flujo Git

```
newsproject/
├── docker-compose.yml
├── docker-compose.override.yml   (puerto 5672 en desarrollo)
├── .env.example
├── .gitignore
├── README.md
├── docs/
│   ├── arquitectura.md           (este documento)
│   ├── eventos.md                (contrato de eventos)
│   └── img/arquitectura.png
├── infra/
│   └── rabbitmq/definitions.json
├── ingesta/          (Dockerfile + supervisord.conf: backend y MongoDB)
├── clasificacion/    (Dockerfile + supervisord.conf: backend y PostgreSQL)
├── busqueda/         (Dockerfile + supervisord.conf: backend y MongoDB)
├── usuarios/         (Dockerfile + supervisord.conf: backend y PostgreSQL)
├── notificaciones/   (Dockerfile + supervisord.conf: backend y PostgreSQL)
├── gateway/          (Dockerfile)
└── frontend/         (Dockerfile + nginx.conf)
```

**Flujo de ramas**

1. Cada integrante desarrolla su servicio en su rama: `kevin-dev`, `fabricio-dev`, `ariana-dev`, `ariel-dev`.
2. Los cambios llegan a `main` por pull request revisado por otro integrante.
3. Los contratos de eventos (sección 5) y de REST (sección 7) solo cambian por pull request aprobado por los cuatro.
4. Antes de cada merge, `docker compose up -d --build` debe levantar el sistema completo.
5. Al terminar se publica un único release `v1.0` desde `main`; no hay releases intermedios.

| Entregable | Ubicación |
| --- | --- |
| Documento de Arquitectura | `docs/arquitectura.md` |
| Contrato de eventos | `docs/eventos.md` |
| Código con rama por integrante, Dockerfiles y compose | Repositorio Git |
| README con `docker compose up` | Raíz del repositorio |
| Declaratoria de IA firmada | `docs/declaratoria-ia.pdf` |
| Release v1.0 | Pestaña Releases del repositorio |

## 10. Métricas y trazabilidad

| Métrica | Cómo se calcula | Dónde se registra |
| --- | --- | --- |
| Latencia extremo a extremo (ms) | llegada al frontend − `ingested_at` | Frontend, por cada mensaje WebSocket |
| Latencia por etapa (ms) | `occurred_at` de `article.classified` − `ingested_at` (clasificación); `sent_at` − `occurred_at` de `article.classified` (notificación) | Logs de cada servicio |
| Throughput (art/s) | artículos indexados por Búsqueda ÷ duración de la prueba | Búsqueda |
| Calidad de clasificación | precisión, recall y F1 macro sobre el 20 % de prueba | `GET /api/model` |
| Disponibilidad bajo fallos (%) | eventos procesados ÷ eventos publicados, con un servicio detenido y luego reiniciado | Panel de RabbitMQ y conteos en cada BD |
| Tiempo de recuperación (s) | desde que el servicio reinicia hasta que su cola queda en 0 mensajes | Panel de RabbitMQ |

Cada servicio escribe logs en JSON con `event_id`, `article_id`, la etapa y la marca de tiempo, para poder seguir un artículo de punta a punta.

## 11. Escenario de demo y pruebas de tolerancia a fallos

**Guion del flujo normal**

1. Con `docker compose up -d --build` corriendo, el usuario se registra en el frontend y elige la categoría `tecnologia`.
2. En el panel de RabbitMQ (puerto 15672) se ve llegar `user.preferences.updated` a Notificaciones.
3. Desde el frontend se dispara un scraping (`POST /api/sources/{id}/scrape`).
4. En el panel se ve pasar `article.ingested` y luego `article.classified` hacia las dos colas.
5. El artículo aparece en el feed por WebSocket, sin recargar, y sube el contador de no leídos.
6. El usuario lo busca con `GET /api/search?q=` y lo encuentra indexado.

**Pruebas de tolerancia a fallos**

| Prueba | Qué se hace | Resultado esperado |
| --- | --- | --- |
| Búsqueda caída | `docker compose stop busqueda`, se repite el scraping, luego `start` | Los mensajes esperan en `busqueda.article-classified`; al reiniciar se consumen y los artículos quedan indexados. Las alertas siguen llegando mientras tanto |
| Usuarios caído | `docker compose stop usuarios` y se dispara un scraping | Notificaciones sigue enviando alertas con su copia local de preferencias; solo falla `/api/users` (503) |
| Mensaje envenenado | Se publica desde el panel un `article.ingested` sin `title` | Clasificación lo rechaza; el mensaje termina en `clasificacion.article-ingested.dlq` y la cola principal sigue fluyendo |
| Duplicado | Se scrapea dos veces la misma fuente | Mismo `article_id`: ni Búsqueda ni Notificaciones duplican artículos o alertas |

## 12. Decisiones y riesgos

| Decisión o riesgo | Tratamiento |
| --- | --- |
| RabbitMQ en lugar de Kafka | Cumple el requisito del curso y es más simple de operar con Docker |
| Un exchange `topic` con una cola por consumidor y evento | Permite agregar consumidores sin tocar a los productores |
| Usuarios separado de Notificaciones | Una responsabilidad por servicio; Notificaciones no depende de Usuarios en tiempo de ejecución |
| Backend y BD de cada servicio en un mismo contenedor | El servicio y sus datos se levantan, detienen y reinician como una unidad, y la BD no es accesible desde fuera. A cambio, cada contenedor corre dos procesos (supervisord) y el servicio no puede escalar a varias réplicas, porque cada réplica tendría su propia BD |
| Colas quorum con `x-delivery-limit: 3` | RabbitMQ cuenta los reintentos y envía a la DLQ sin lógica extra en cada consumidor |
| `article_id` como UUID v5 de la URL | El mismo artículo tiene siempre el mismo id, lo que hace idempotentes a todos los consumidores |
| Clasificador TF-IDF + Naive Bayes o regresión logística | Rápido, explicable y medible con precisión, recall y F1; depende de tener un dataset etiquetado |
| Un servicio no encuentra el broker al arrancar | Espera a `rabbitmq-init` y reintenta con espera creciente |
| Fuentes de scraping que bloquean o cambian de formato | Preferir feeds RSS; registrar el error en la bitácora de Ingesta y seguir con las demás fuentes |
| Pocas noticias etiquetadas en alguna categoría | Medir F1 por categoría; si es bajo, agregar ejemplos o fusionar la categoría con `otros` |
| Contratos que cambian a mitad del proyecto | Campo `version` en cada evento y cambios solo por pull request aprobado por los cuatro |

## 13. Pendientes

- [x] `definitions.json`: colas principales a tipo quorum con `x-delivery-limit: 3`
- [x] `docker-compose.yml`: BD dentro del contenedor de cada servicio, credenciales por servicio y puerto 5672 en `docker-compose.override.yml`
- [x] `docs/eventos.md`: `article_id` como UUID v5 e idempotencia por entidad
- [x] README corregido y `.idea/` fuera del repositorio
- [ ] Implementar cada servicio sobre su plantilla (`/health` real, consumidores, endpoints)
- [ ] Elegir el dataset etiquetado para Clasificación y fijar `MIN_SCORE`
- [ ] Informe: actualizar herramientas (Notificaciones en Java) y reemplazar la metodología de Raft/Paxos que quedó de la plantilla
