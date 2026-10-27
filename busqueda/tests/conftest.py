import os
import sys
from pathlib import Path

# app.main lee estas variables al importarse; las pruebas no se conectan a nada.
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017/test")
os.environ.setdefault("RABBITMQ_URL", "amqp://guest:guest@localhost/")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
