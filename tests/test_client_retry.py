"""Tests de la política de reintentos del cliente de Biwenger: nunca se repite una escritura
que pudo llegar a Biwenger (pujar o pagar dos veces), se ve el error real y el login no se
reintenta con la contraseña mal (bloqueo de la cuenta)."""
from __future__ import annotations

from http.client import RemoteDisconnected

import pytest
import requests
from tenacity import wait_none
from urllib3.exceptions import MaxRetryError, NewConnectionError, ProtocolError

import core.client
from core.client import BiwengerAuthError, BiwengerClient


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    monkeypatch.setattr(core.client, "RETRY_WAIT", wait_none())


class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload if payload is not None else {"data": {"ok": True}}
        self.text = text
        self.content = b"x"

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error", response=self)


def _connect_refused():
    return requests.exceptions.ConnectionError(
        MaxRetryError(None, "/api", reason=NewConnectionError(None, "Connection refused")))


def _reset_after_send():
    return requests.exceptions.ConnectionError(
        ProtocolError("Connection aborted.", RemoteDisconnected("Remote end closed connection")))


def _client(outcomes):
    """Un cliente cuya sesión responde con `outcomes` en orden (excepción o respuesta)."""
    client = BiwengerClient(app_version="631", league_id="1", team_id="2")
    client._token = "t"
    calls: list[str] = []

    def request(method, url, **kwargs):
        calls.append(method)
        outcome = outcomes[min(len(calls), len(outcomes)) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    client._session.request = request
    return client, calls


# --------------------------------------------------------------------------- #
# Lecturas: se reintentan
# --------------------------------------------------------------------------- #
def test_get_retries_network_errors():
    client, calls = _client([requests.exceptions.ReadTimeout("lento"), _Resp()])
    assert client._request("GET", "https://example.test/user") == {"data": {"ok": True}}
    assert calls == ["GET", "GET"]


def test_get_retries_server_errors_but_not_client_errors():
    client, calls = _client([_Resp(503), _Resp()])
    client._request("GET", "https://example.test/user")
    assert len(calls) == 2

    client, calls = _client([_Resp(404)])
    with pytest.raises(requests.HTTPError):
        client._request("GET", "https://example.test/user")
    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# Escrituras: nunca dos veces
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("failure", [
    requests.exceptions.ReadTimeout("la puja salió y no llegó la respuesta"),
    _reset_after_send(),
    _Resp(500),
    _Resp(409),
])
def test_post_that_may_have_reached_biwenger_is_not_retried(failure):
    client, calls = _client([failure, _Resp()])
    with pytest.raises(requests.RequestException):
        client._request("POST", "https://example.test/offers", json={"amount": 1})
    assert calls == ["POST"]


def test_post_is_retried_only_if_the_connection_never_opened():
    client, calls = _client([_connect_refused(), requests.exceptions.ConnectTimeout("no conecta"), _Resp()])
    assert client._request("POST", "https://example.test/offers", json={}) == {"data": {"ok": True}}
    assert calls == ["POST", "POST", "POST"]


def test_the_real_error_is_raised_not_a_retry_error():
    client, _ = _client([_connect_refused()] * 5)
    with pytest.raises(requests.exceptions.ConnectionError):
        client._request("PUT", "https://example.test/user/players/1", json={})


# --------------------------------------------------------------------------- #
# Login: la contraseña mal no se reintenta
# --------------------------------------------------------------------------- #
def _login_client(outcomes):
    client = BiwengerClient(app_version="631", league_id="1", team_id="2")
    client._token = None
    posts: list[int] = []

    def post(url, **kwargs):
        posts.append(1)
        outcome = outcomes[min(len(posts), len(outcomes)) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    client._session.post = post
    return client, posts


@pytest.mark.parametrize("status", [400, 401, 403, 429])
def test_login_with_client_error_is_tried_once(status):
    client, posts = _login_client([_Resp(status, text="Invalid credentials")])
    with pytest.raises(BiwengerAuthError):
        client.login()
    assert len(posts) == 1


def test_login_retries_network_and_server_errors():
    client, posts = _login_client([_connect_refused(), _Resp(502), _Resp(200, payload={"token": "eyJx.y.z"})])
    client.login()
    assert len(posts) == 3 and client._token == "eyJx.y.z"
