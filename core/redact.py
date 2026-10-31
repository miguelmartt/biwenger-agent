"""Que ningún secreto acabe en los logs.

`docker logs` guarda en claro todo lo que el proceso escribe, y los logs se copian y se
comparten para depurar. Un filtro de logging tapa con *** el token del bot de Telegram y
el token y la contraseña de Biwenger: los del .env y los que llegan después (el token
renovado con /token y el que devuelve el login). Además tapa por patrón cualquier cosa con
forma de token aunque no esté registrada: la URL `/bot<TOKEN>/` de Telegram (requests la
mete en el texto de sus excepciones), un «Bearer …» y un JWT. Se aplica al mensaje, a sus
argumentos y a la traza de la excepción.

`install_log_redaction()` se llama al arrancar (main.py), justo después de configurar el log.
"""
from __future__ import annotations

import logging
import re
import threading

from config.settings import settings

MASK = "***"
# Un valor tan corto se confundiría con texto normal: no se registra como secreto.
MIN_SECRET_LENGTH = 6

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # https://api.telegram.org/bot<TOKEN>/getUpdates
    (re.compile(r"/bot\d+:[^/\s'\"?]+"), "/bot" + MASK),
    # Un token de bot suelto (123456789:AAE…)
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}"), MASK),
    # Cabecera Authorization
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1" + MASK),
    # JWT (el token de sesión de Biwenger lo es)
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"), MASK),
]

_secrets: set[str] = set()
_lock = threading.Lock()


def register_secret(value: str | None) -> None:
    """Añade un valor que nunca debe salir en un log (p. ej. un token recién renovado)."""
    value = (value or "").strip()
    if len(value) >= MIN_SECRET_LENGTH:
        with _lock:
            _secrets.add(value)


def redact(text: str) -> str:
    """El texto con los secretos tapados."""
    if not text:
        return text
    with _lock:
        known = sorted(_secrets, key=len, reverse=True)
    for secret in known:
        text = text.replace(secret, MASK)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def describe_error(exc: BaseException) -> str:
    """Lo justo para el log: el tipo de la excepción y, si es una respuesta HTTP, su código.
    Sin el texto: el de requests incluye la URL, y la de Telegram lleva el token."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return f"{type(exc).__name__} {status}" if status else type(exc).__name__


class RedactSecretsFilter(logging.Filter):
    """Filtro para los handlers: reescribe el registro ya sin secretos."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - un formato roto no debe tumbar el log
            message = str(record.msg)
        record.msg = redact(message)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def _register_settings_secrets() -> None:
    for value in (settings.telegram_bot_token, settings.biwenger_token, settings.biwenger_password):
        register_secret(value)


def install_log_redaction() -> None:
    """Pone el filtro en todos los handlers del log raíz (por ahí pasan los registros de
    todos los módulos, también los de requests, urllib3 y APScheduler). Idempotente."""
    _register_settings_secrets()
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in root.handlers:
        if not any(isinstance(f, RedactSecretsFilter) for f in handler.filters):
            handler.addFilter(RedactSecretsFilter())


_register_settings_secrets()
