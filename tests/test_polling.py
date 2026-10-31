"""Tests del bucle de Telegram: solo atiende a tu chat privado, no repite nada tras un
reinicio (backlog descartado, offset guardado antes de actuar, botones viejos ignorados)
y borra del chat el mensaje con el token."""
from __future__ import annotations

import dataclasses

import pytest

import bot.polling as polling
from data.db import get_setting

ME = 1001
STARTED = 1_000_000


class _Telegram:
    """Sustituye a las llamadas a Telegram de bot.polling y apunta lo que se hace."""

    def __init__(self, monkeypatch):
        self.sent: list[str] = []
        self.edited: list[str] = []
        self.answered: list[tuple] = []
        self.deleted: list[int] = []
        self.updates_calls: list[int | None] = []
        self.pending_updates: list[dict] = []
        monkeypatch.setattr(polling, "send_message", lambda text, buttons=None: self.sent.append(text))
        monkeypatch.setattr(polling, "edit_message", lambda mid, text, buttons=None: self.edited.append(text))
        monkeypatch.setattr(polling, "send_typing", lambda: None)
        monkeypatch.setattr(polling, "answer_callback_query",
                            lambda cid, text=None: self.answered.append((cid, text)))
        monkeypatch.setattr(polling, "delete_message", lambda mid: self.deleted.append(mid) or True)
        monkeypatch.setattr(polling, "get_updates", self._get_updates)

    def _get_updates(self, offset, timeout=30):
        self.updates_calls.append(offset)
        return self.pending_updates


@pytest.fixture()
def tg(monkeypatch):
    monkeypatch.setattr(polling, "settings", dataclasses.replace(polling.settings, telegram_chat_id=str(ME)))
    return _Telegram(monkeypatch)


@pytest.fixture()
def calls(monkeypatch):
    """Lo que llega a los manejadores de comandos y botones."""
    seen: list[str] = []
    monkeypatch.setattr(polling, "handle_command", lambda text, client: seen.append(text) or ("ok", None))
    monkeypatch.setattr(polling, "handle_callback", lambda data, client: seen.append(data) or ("hecho", None))
    return seen


def _message(text, sender=ME, chat_id=ME, chat_type="private", date=STARTED + 10):
    return {"message_id": 55, "date": date, "text": text,
            "from": {"id": sender}, "chat": {"id": chat_id, "type": chat_type}}


def _callback(data, sender=ME, chat_id=ME, chat_type="private", date=STARTED + 10):
    return {"callback_query": {"id": "cb1", "data": data, "from": {"id": sender},
                               "message": {"message_id": 77, "date": date,
                                           "chat": {"id": chat_id, "type": chat_type}}}}


# --------------------------------------------------------------------------- #
# Autorización
# --------------------------------------------------------------------------- #
def test_command_from_owner_in_private_chat_is_handled(tg, calls):
    polling.handle_update({"message": _message("/parte")}, None, STARTED)
    assert calls == ["/parte"]


@pytest.mark.parametrize("kwargs", [
    {"sender": 2002},                               # otro usuario en tu chat (imposible en privado, pero por si acaso)
    {"chat_id": -100123, "chat_type": "group"},     # el bot metido en un grupo
    {"chat_type": "group"},                         # un chat con tu id que no es privado
    {"chat_id": 2002, "sender": 2002},              # otra persona que escribe al bot
])
def test_messages_from_anyone_else_are_ignored(tg, calls, kwargs):
    polling.handle_update({"message": _message("/token robado", **kwargs)}, None, STARTED)
    polling.handle_update(_callback("ask:c:1:2:3", **kwargs), None, STARTED)
    assert calls == [] and tg.sent == [] and tg.edited == []


def test_edited_messages_are_ignored(tg, calls):
    polling.handle_update({"edited_message": _message("/parte")}, None, STARTED)
    assert calls == []


def test_unconfigured_chat_id_authorizes_nobody(monkeypatch, tg, calls):
    monkeypatch.setattr(polling, "settings", dataclasses.replace(polling.settings, telegram_chat_id=""))
    polling.handle_update({"message": _message("/parte")}, None, STARTED)
    assert calls == []


# --------------------------------------------------------------------------- #
# Nada se repite tras un reinicio
# --------------------------------------------------------------------------- #
def test_action_buttons_from_before_start_are_ignored(tg, calls):
    polling.handle_update(_callback("do:abc123", date=STARTED - 1), None, STARTED)
    assert calls == []
    assert tg.answered and "antes de reiniciar" in tg.answered[0][1]


def test_navigation_buttons_from_before_start_still_work(tg, calls, monkeypatch):
    monkeypatch.setattr(polling, "with_nav", lambda command, buttons: buttons)
    polling.handle_update(_callback("cmd:liga", date=STARTED - 3600), None, STARTED)
    assert calls == ["/liga"]


def test_skip_backlog_drops_pending_updates(tg):
    tg.pending_updates = [{"update_id": 41}]
    assert polling.skip_backlog() == 42
    assert tg.updates_calls == [-1]
    assert get_setting(polling.OFFSET_KEY) == "42"


def test_skip_backlog_never_goes_back(tg):
    from data.db import set_setting
    set_setting(polling.OFFSET_KEY, "100")
    tg.pending_updates = []
    assert polling.skip_backlog() == 100


def test_offset_is_saved_before_acting(tg, monkeypatch):
    # Si el proceso cae mientras ejecuta una acción, al volver no la repite.
    seen_offsets: list[str | None] = []

    class Stop(BaseException):
        pass

    def crash(update, client, started_at):
        seen_offsets.append(get_setting(polling.OFFSET_KEY))
        raise Stop

    monkeypatch.setattr(polling, "handle_update", crash)
    monkeypatch.setattr(polling, "set_commands", lambda commands: None)
    monkeypatch.setattr(polling, "BiwengerClient", lambda: None)
    monkeypatch.setattr(polling, "settings",
                        dataclasses.replace(polling.settings, telegram_chat_id=str(ME), telegram_bot_token="x"))
    # Sin backlog al arrancar; luego llega un clic nuevo (update 7).
    monkeypatch.setattr(polling, "get_updates",
                        lambda offset, timeout=30: [] if offset == -1 else [{"update_id": 7}])
    with pytest.raises(Stop):
        polling.run_polling()
    assert seen_offsets == ["8"]


# --------------------------------------------------------------------------- #
# /token
# --------------------------------------------------------------------------- #
def test_token_message_is_deleted_after_storing(tg):
    class _Client:
        def update_token(self, token):
            self.token = token

    client = _Client()
    polling.handle_update({"message": _message("/token eyJabc.def.ghi")}, client, STARTED)
    assert client.token == "eyJabc.def.ghi"
    assert tg.deleted == [55]
    assert "borrado" in tg.sent[0]
