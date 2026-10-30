"""Tests de utilidades de Telegram (troceo de mensajes largos)."""
from __future__ import annotations

from bot.telegram_bot import _split_text


def test_short_message_single_chunk():
    assert _split_text("hola") == ["hola"]


def test_long_message_split_under_limit():
    text = "\n".join(f"linea {i}" for i in range(2000))  # muy largo
    parts = _split_text(text, limit=1000)
    assert len(parts) > 1
    assert all(len(p) <= 1000 for p in parts)
    # No se pierde contenido (todas las líneas siguen ahí).
    assert "\n".join(parts).count("linea ") == 2000


def test_giant_single_line_is_hard_split():
    parts = _split_text("x" * 2500, limit=1000)
    assert all(len(p) <= 1000 for p in parts)
    assert "".join(parts) == "x" * 2500


if __name__ == "__main__":
    test_short_message_single_chunk()
    test_long_message_split_under_limit()
    test_giant_single_line_is_hard_split()
    print("OK: troceo de mensajes correcto")
