"""Tests del filtro que tapa secretos en los logs: el token del bot se coló tres veces en
`docker logs` por las excepciones de requests, que incluyen la URL /bot<TOKEN>/."""
from __future__ import annotations

import dataclasses
import io
import logging

import pytest
import requests

import bot.telegram_bot as telegram
from core.redact import RedactSecretsFilter, describe_error, redact, register_secret

BOT_TOKEN = "123456789:AAEfakeTokenForTestsOnly_abcdefghijk"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.c2lnbmF0dXJh"


@pytest.fixture()
def log():
    """Un logger con un handler en memoria y el filtro puesto, como en main.py."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactSecretsFilter())
    logger = logging.getLogger("test-redact")
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    yield logger, stream
    logger.removeHandler(handler)


def test_telegram_url_in_a_requests_exception_is_masked(log):
    logger, stream = log
    try:
        raise requests.exceptions.ConnectionError(
            f"HTTPSConnectionPool(host='api.telegram.org', port=443): Max retries exceeded with url: "
            f"/bot{BOT_TOKEN}/getUpdates?offset=5 (Caused by NewConnectionError)")
    except requests.RequestException as exc:
        logger.warning("Telegram falló: %s", exc)
        logger.exception("con traza")
    out = stream.getvalue()
    assert "AAEfakeToken" not in out
    assert "/bot***/getUpdates" in out
    assert out.count("***") >= 2  # el argumento del mensaje y la traza


def test_registered_secrets_and_token_shapes_are_masked(log):
    logger, stream = log
    register_secret("contraseña-muy-secreta")
    logger.info("login con %s", "contraseña-muy-secreta")
    logger.info("Authorization: Bearer %s", JWT)
    logger.info("token suelto %s y jwt %s", BOT_TOKEN, JWT)
    out = stream.getvalue()
    for secret in ("contraseña-muy-secreta", BOT_TOKEN, JWT, "AAEfake", "eyJhbGci"):
        assert secret not in out


def test_short_values_are_not_registered():
    register_secret("ab")
    assert redact("ab cd") == "ab cd"


def test_describe_error_has_no_url():
    resp = requests.Response()
    resp.status_code = 409
    resp.url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    exc = requests.HTTPError(f"409 Client Error: Conflict for url: {resp.url}", response=resp)
    assert describe_error(exc) == "HTTPError 409"


def test_get_updates_errors_do_not_carry_the_token(monkeypatch):
    monkeypatch.setattr(telegram, "settings",
                        dataclasses.replace(telegram.settings, telegram_bot_token=BOT_TOKEN))

    def boom(url, **kwargs):
        raise requests.exceptions.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr(telegram.requests, "get", boom)
    with pytest.raises(telegram.TelegramError) as info:
        telegram.get_updates(None)
    assert "AAEfake" not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__


def test_post_failures_log_only_the_error_type(monkeypatch, caplog):
    monkeypatch.setattr(telegram, "settings",
                        dataclasses.replace(telegram.settings, telegram_bot_token=BOT_TOKEN))

    def boom(url, **kwargs):
        raise requests.exceptions.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr(telegram.requests, "post", boom)
    with caplog.at_level(logging.WARNING, logger="bot.telegram_bot"):
        assert telegram._post("sendMessage", {}) is None
    assert "AAEfake" not in caplog.text and "ConnectionError" in caplog.text
