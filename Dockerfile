FROM python:3.12-slim

WORKDIR /app

# Zona horaria de España para que los cron de alineación/mercado cuadren.
ENV TZ=Europe/Madrid

# Versiones exactas (requirements.lock): una dependencia que cambia sola no puede romper el bot.
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY . .

# Carpeta donde vive la BD SQLite (se monta como volumen en docker-compose).
RUN mkdir -p /data

CMD ["python", "main.py"]
