# Búsqueda: backend FastAPI + MongoDB en el mismo contenedor (supervisord).
FROM mongo:7

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv supervisor \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
COPY . .
COPY supervisord.conf /etc/supervisor/conf.d/app.conf

EXPOSE 8000
ENTRYPOINT []
CMD ["supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]
