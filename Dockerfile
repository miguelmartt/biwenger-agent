FROM python:3.12-slim

WORKDIR /app

# Zona horaria de España para que los cron de alineación/mercado cuadren.
# Sin .pyc: el sistema de archivos del contenedor es de solo lectura (docker-compose.yml).
ENV TZ=Europe/Madrid \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Versiones exactas (requirements.lock): una dependencia que cambia sola no puede romper el bot.
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
# PuLP trae su propio CBC y, si no es ejecutable, intenta darle permisos al importarse; como
# usuario sin privilegios y con el disco de solo lectura no podría (y la alineación fallaría).
RUN python -c "import importlib.util, pathlib; d = pathlib.Path(importlib.util.find_spec('pulp').origin).parent; [p.chmod(0o755) for p in d.glob('solverdir/cbc/*/*/cbc')]"

# Usuario sin privilegios (uid/gid fijos 10001, los de la migración de DEPLOY_VPS.md).
# /data es lo único en lo que escribe (la BD SQLite, montada como volumen); el resto, /tmp.
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --home-dir /nonexistent \
       --shell /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown app:app /data \
    && chmod 700 /data

COPY . .
# El código es de root y el usuario del bot solo lo lee (también config/league_rules.py,
# aunque en el servidor tenga permisos más cerrados).
RUN chmod -R a+rX,go-w /app

USER 10001:10001

CMD ["python", "main.py"]
