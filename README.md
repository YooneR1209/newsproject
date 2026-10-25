# newsproject
Instalación y ejecución

git clone https://github.com/<TU_USUARIO>/newsproject.git
cd newsproject
git checkout ariana-dev  # kevin-dev o fabricio-dev
cp .env.example .env
docker compose up -d

Agregador de noticias con microservicios (Sistemas Distribuidos, UNL).
Scrapers publican artículos a colas, un servicio los clasifica y otro envía notificaciones personalizadas en tiempo real.

## Arquitectura

| Servicio | Responsable | Tecnología | Base de datos |
|---|---|---|---|
| ingesta | Kevin | Python (FastAPI) | MongoDB |
| clasificacion | Fabricio | Python (FastAPI) | PostgreSQL |
| busqueda | Fabricio | Python (FastAPI) | MongoDB |
| usuarios | Ariana | Python (FastAPI) | PostgreSQL |
| gateway | Ariana | Python (FastAPI) | — |
| notificaciones | Ariel | Java 21 (Spring Boot) | PostgreSQL |
| frontend | Todos | React | — |

La comunicación asíncrona entre servicios usa RabbitMQ. El contrato de eventos
(exchange, routing keys, colas y JSON de cada mensaje) está en
[`docs/eventos.md`](docs/eventos.md). **Léanlo antes de programar.**

## Requisitos

- Docker y Docker Compose v2 (`docker compose version`)
- Git

## Cómo levantar la infraestructura

```bash
git clone <URL_DEL_REPOSITORIO>
cd newsproject
cp .env.example .env
```

Abre `.env` y cambia las claves (el archivo `.env` nunca se sube a Git).
Luego:

```bash
docker compose up -d
```

Esto levanta RabbitMQ, PostgreSQL y MongoDB, y carga automáticamente
el exchange, las colas y los bindings.

### Verificar que todo está bien

```bash
docker compose ps -a
```

Debe verse así:

- `newsproject-rabbitmq`, `newsproject-postgres` y `newsproject-mongo` en `healthy`
- `newsproject-rabbitmq-init` en `Exited (0)` (es correcto: corre una vez y termina)

Comprobaciones opcionales:

```bash
docker logs newsproject-rabbitmq-init        # debe terminar con "Definiciones importadas"
docker exec newsproject-rabbitmq rabbitmqctl list_queues name   # 8 colas
docker exec newsproject-postgres psql -U <POSTGRES_USER> -c '\l' # usuarios, clasificacion, notificaciones
```

### Accesos desde tu máquina

| Recurso | Dirección |
|---|---|
| Panel de RabbitMQ | http://localhost:15672 (usuario y clave del `.env`) |
| RabbitMQ (AMQP) | `localhost:5672` |
| PostgreSQL | `localhost:5432` |
| MongoDB | `localhost:27017` |

### Desde dentro de un contenedor (para tus servicios en Docker)

Usa el nombre del servicio, no `localhost`:

- RabbitMQ: `amqp://<usuario>:<clave>@rabbitmq:5672`
- PostgreSQL: `postgres:5432`, con la base de tu servicio (`usuarios`, `clasificacion` o `notificaciones`)
- MongoDB: `mongo:27017`

## Flujo de trabajo con Git

- Cada integrante trabaja en su rama: `ariel-dev`, `ariana-dev`, `kevin-dev`, `fabricio-dev`.
- Se desarrolla el servicio propio dentro de su carpeta (`ingesta/`, `usuarios/`, etc.).
- Los cambios se integran a `main` mediante merge (preferiblemente con pull request).
- Un solo release al final: `v1.0`. No se crean releases intermedios.

## Cómo agregar tu servicio al docker-compose

1. Crea un `Dockerfile` dentro de la carpeta de tu servicio.
2. Agrega tu servicio en `docker-compose.yml` (hay una plantilla comentada al final de `services`).
3. Usa `depends_on` con `condition: service_healthy` para esperar a RabbitMQ y a la base de datos.
4. Conéctalo a la red `newsproject-net`.
5. Prueba que `docker compose up -d --build` siga funcionando completo.

## Problemas comunes

**Un contenedor falla y aparece una carpeta en lugar de un archivo (por ejemplo `infra/postgres/init.sql`).**
Docker crea una carpeta cuando un archivo montado no existe en la ruta indicada, y la deja con dueño `root`.
Borra la carpeta y asegúrate de que el archivo existe:

```bash
docker compose down -v
sudo rm -rf infra/postgres/init.sql   # solo si es una carpeta
git checkout -- infra/                # restaura los archivos del repositorio
docker compose up -d
```

**Las bases de datos `usuarios`, `clasificacion` y `notificaciones` no existen.**
El script `init.sql` solo se ejecuta la primera vez, con el volumen vacío. Recrea los volúmenes
(se borran los datos locales):

```bash
docker compose down -v
docker compose up -d
```

**Error de puerto ocupado (5432, 5672, 27017).**
Tienes ese servicio instalado localmente. Detenlo (por ejemplo `sudo systemctl stop postgresql`)
o cambia el puerto de la izquierda en `docker-compose.yml` (`"5433:5432"`).

**RabbitMQ aparece como no saludable justo después de arrancar.**
Espera 20 a 30 segundos la primera vez y vuelve a ejecutar `docker compose ps -a`.

## Comandos útiles

```bash
docker compose up -d            # levantar
docker compose logs -f <servicio>   # ver logs
docker compose down             # detener (conserva datos)
docker compose down -v          # detener y borrar datos
```
