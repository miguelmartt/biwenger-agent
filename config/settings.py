"""Carga de configuración centralizada desde variables de entorno (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    biwenger_email: str = os.getenv("BIWENGER_EMAIL", "")
    biwenger_password: str = os.getenv("BIWENGER_PASSWORD", "")
    # Token de sesión (JWT) para cuentas con login social (Google/Facebook/Apple),
    # que no tienen contraseña propia de Biwenger. Si está puesto, se usa este
    # token directamente y se omite el login por email/contraseña.
    biwenger_token: str = os.getenv("BIWENGER_TOKEN", "")
    biwenger_league_id: str = os.getenv("BIWENGER_LEAGUE_ID", "")
    biwenger_user_id: str = os.getenv("BIWENGER_USER_ID", "")
    biwenger_app_version: str = os.getenv("BIWENGER_APP_VERSION", "")

    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Opcional: nombre para personalizar el saludo diario. Vacío = saludo genérico.
    owner_name: str = os.getenv("OWNER_NAME", "")

    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./biwenger.db")

    dry_run: bool = _bool("DRY_RUN", True)
    budget_safety_margin: int = int(os.getenv("BUDGET_SAFETY_MARGIN", "1000000"))


settings = Settings()
