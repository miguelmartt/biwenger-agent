"""Aísla los tests: cada uno arranca con una base de datos limpia, para que el
estado (config aprendida, objetivos, chollos vistos...) no se filtre entre tests."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fresh_db():
    try:
        from data.db import Base, engine
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
    except Exception:  # noqa: BLE001
        pass
    yield
