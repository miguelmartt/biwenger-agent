"""Aísla los tests: cada uno arranca con una base de datos limpia, para que el
estado (config aprendida, objetivos, chollos vistos...) no se filtre entre tests.

La base de datos de los tests es SIEMPRE SQLite en memoria. Se fuerza aquí, antes de
importar nada del proyecto (config.settings lee el entorno y el .env al importarse),
así que ni un DATABASE_URL en el entorno ni uno en el .env pueden hacer que los tests
vacíen una base de datos real. Por lo mismo se vacían las credenciales y se fuerza
DRY_RUN: un test nunca puede hablar con Biwenger ni con Telegram de verdad.
"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["DRY_RUN"] = "true"
for _name in ("BIWENGER_EMAIL", "BIWENGER_PASSWORD", "BIWENGER_TOKEN", "BIWENGER_LEAGUE_ID",
              "BIWENGER_USER_ID", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "OWNER_NAME"):
    os.environ[_name] = ""  # load_dotenv no pisa variables que ya existen, aunque estén vacías

import pytest  # noqa: E402

from data.db import Base, engine  # noqa: E402

if engine.url.database not in (None, "", ":memory:"):
    pytest.exit(f"Los tests solo corren contra SQLite en memoria, no contra {engine.url!r}", returncode=2)


@pytest.fixture(autouse=True)
def _fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
