"""Test de la renovación del token en caliente (comando /token, sin tocar el servidor)."""
from __future__ import annotations

from bot.commands import handle_command
from data.db import get_setting, init_db


class _Client:
    def __init__(self):
        self.updated = None

    def update_token(self, new_token):
        self.updated = new_token
        from data.db import set_setting
        set_setting("biwenger_token", new_token)


def test_token_command_updates_token():
    init_db()
    c = _Client()
    text, _ = handle_command("/token eyJnuevo.token.aqui", c)
    assert "actualizado" in text.lower()
    assert c.updated == "eyJnuevo.token.aqui"
    assert get_setting("biwenger_token") == "eyJnuevo.token.aqui"


def test_token_command_without_arg_shows_usage():
    text, _ = handle_command("/token", _Client())
    assert "Uso:" in text


if __name__ == "__main__":
    test_token_command_updates_token()
    test_token_command_without_arg_shows_usage()
    print("OK: renovación de token en caliente correcta")
