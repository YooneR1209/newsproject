import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException

# Variables globales para los clientes (se implementarán luego en database.py y rabbitmq.py)
db_client = None
rabbitmq_conn = None

async def automatic_scraping_cycle():
    """Ciclo automático que corre cada 10 minutos."""
    while True:
        try:
            print("Iniciando ciclo automático de scraping de feeds...")
            # TODO: Llamar a la lógica de leer bases de datos y scrapear
        except Exception as e:
            print(f"Error en el ciclo de scraping: {e}")
        
        # Esperar 600 segundos (10 minutos)
        await asyncio.sleep(600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- STARTUP ---
    print("Iniciando microservicio Ingesta...")
    
    # TODO: Conectar a MongoDB con motor (DATABASE_URL)
    # TODO: Conectar a RabbitMQ con aio-pika usando connect_robust (RABBITMQ_URL)
    # TODO: Crear colecciones, índices y cargar fuentes iniciales si la base está vacía
    
    # Lanzar la tarea en segundo plano para el ciclo automático de 10 minutos
    scraping_task = asyncio.create_task(automatic_scraping_cycle())
    
    yield
    
    # --- SHUTDOWN ---
    print("Apagando microservicio Ingesta...")
    scraping_task.cancel()
    # TODO: Cerrar conexiones de db_client y rabbitmq_conn

# Instancia de FastAPI operando por defecto en el puerto interno 8000
app = FastAPI(title="API Ingesta de Noticias", lifespan=lifespan)

@app.get("/health")
async def health_check():
    """
    Verifica que MongoDB y RabbitMQ estén conectados.
    Devuelve 200/503.
    """
    # TODO: Validar el estado real de las conexiones
    db_connected = True 
    rabbitmq_connected = True
    
    if db_connected and rabbitmq_connected:
        return {"status": "ok"}
    
    # Si alguna conexión falla, el sistema responde con 503
    raise HTTPException(status_code=503, detail="Service Unavailable: Database or RabbitMQ disconnected")

# TODO: Incluir el router con los endpoints de fuentes (GET /api/sources, POST /api/sources, etc.)
