"""Tests de lo que hacía que el bot se quedara «pocho»: qué jornada toca y la versión de la app.

Biwenger cambia la X-Version de su app sin avisar y responde 400 «Old version»; el cliente
debe leer la nueva de su web y seguir solo. Y la jornada fiable sale de activeEvents, no de
rounds/league (que entre jornadas apunta a la terminada o a una aplazada).
"""
from __future__ import annotations

import time

import pytest

import bot.telegram_bot
import core.client
from core.client import BiwengerClient, BiwengerVersionError, detect_app_version
from data.db import get_setting, init_db

COMPETITION = {
    "activeEvents": [
        {"type": "round", "id": 4909, "name": "Jornada 11", "start": 2_000, "games": []},
    ],
    "season": {"rounds": [
        {"id": 4899, "name": "Jornada 9", "status": "finished"},
        {"id": 4900, "name": "Jornada 10", "status": "finished"},
        {"id": 4905, "name": "Jornada 3 (aplazada)", "status": "finished"},
        {"id": 4909, "name": "Jornada 11", "status": "pending"},
        {"id": 4937, "name": "Jornada 7 (aplazada)", "status": "finished"},
    ]},
}


def _client_with(data) -> BiwengerClient:
    client = BiwengerClient(app_version="631", league_id="1", team_id="2")
    client._competition_cache = (time.monotonic(), data)
    return client


def test_active_round_comes_from_active_events():
    client = _client_with(COMPETITION)
    assert client.get_active_round_id() == 4909
    assert client.get_current_round_id() == 4909


def test_recent_round_before_kickoff_is_the_last_finished_one():
    # Antes de que empiece la 11, la que tiene puntos es la 10 (no una aplazada).
    assert _client_with(COMPETITION).get_recent_round_id(now_epoch=1_000) == 4900


def test_recent_round_once_started_is_the_active_one():
    assert _client_with(COMPETITION).get_recent_round_id(now_epoch=3_000) == 4909


def test_round_number_reads_the_name_not_the_id():
    client = _client_with(COMPETITION)
    assert client.get_round_number(4909) == 11
    assert client.get_round_number(4900) == 10
    assert client.get_round_number(None) == 0
    assert client.get_round_number(123) == 0


class _Resp:
    def __init__(self, status: int, text: str = "", payload=None):
        self.status_code = status
        self.text = text
        self._payload = payload
        self.content = b"x" if payload is not None else b""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def _versioned_api(client: BiwengerClient, accepted: str):
    """Una API falsa que solo acepta la versión `accepted` y responde 400 «Old version» al resto."""
    calls: list[str | None] = []

    def request(method, url, **kwargs):
        version = client._session.headers.get("X-Version")
        calls.append(version)
        if version != accepted:
            return _Resp(400, '{"status":400,"message":"Old version"}')
        return _Resp(200, payload={"data": {"ok": True}})

    client._session.request = request
    return calls


@pytest.fixture()
def notices(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(bot.telegram_bot, "send_message", lambda text, *a, **k: sent.append(text))
    return sent


def test_old_version_is_detected_adopted_and_remembered(monkeypatch, notices):
    init_db()
    client = BiwengerClient(app_version="631", league_id="1", team_id="2")
    client._token = "t"
    calls = _versioned_api(client, accepted="632")
    monkeypatch.setattr(core.client, "detect_app_version", lambda session=None: "632")

    assert client._request("GET", "https://example.test/api") == {"data": {"ok": True}}
    assert calls[-1] == "632"
    assert get_setting("biwenger_app_version") == "632"
    assert len(notices) == 1 and "632" in notices[0]
    # Otro proceso que arranque después ya usa la versión buena sin preguntar.
    assert BiwengerClient()._app_version == "632"


def test_old_version_without_a_readable_web_fails_loudly(monkeypatch, notices):
    init_db()
    client = BiwengerClient(app_version="631", league_id="1", team_id="2")
    client._token = "t"
    _versioned_api(client, accepted="632")
    monkeypatch.setattr(core.client, "detect_app_version", lambda session=None: None)

    with pytest.raises(BiwengerVersionError):
        client._request("GET", "https://example.test/api")
    assert notices == []


def test_detect_app_version_reads_the_cdn_path():
    class _Session:
        def get(self, url, **kwargs):
            return _Resp(200, '<script src="https://cdn.biwenger.com/app/v632/main.js"></script>')

    assert detect_app_version(_Session()) == "632"
