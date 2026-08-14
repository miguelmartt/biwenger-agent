"""Acciones con confirmación (botones de Telegram).

Flujo: el bot recomienda con un botón '✅ Fichar' → el usuario pulsa → el bot pide
CONFIRMACIÓN ('¿Seguro?') → el usuario confirma → se ejecuta (o se simula si
DRY_RUN). El agente NUNCA ejecuta una compra sin este doble OK explícito.

callback_data:
  ask:f:<pid>:<importe>   -> pedir confirmación de fichaje del mercado libre
  ask:c:<pid>:<clausula>  -> pedir confirmación de pago de cláusula
  do:f / do:c ...          -> ejecutar (tras confirmar)
  cancel                   -> cancelar
"""
from __future__ import annotations

import logging

from bidding.valuation import bid_levels
from config.settings import settings
from core.client import BiwengerClient

logger = logging.getLogger(__name__)

Button = tuple[str, str]


def _fmt(n: int) -> str:
    return f"{int(n):,}".replace(",", ".")


def _safe_name(client: BiwengerClient, player_id: int) -> str:
    try:
        return client.get_player(player_id).name
    except Exception:  # noqa: BLE001
        return f"jugador {player_id}"


def handle_callback(data: str, client: BiwengerClient) -> tuple[str, list[Button] | None]:
    """Procesa el clic de un botón. Devuelve (texto, botones) para responder."""
    parts = data.split(":")
    kind = parts[0]

    if kind == "cancel":
        return "❌ Operación cancelada. No se ha hecho nada.", None

    if kind == "unsnipe" and len(parts) == 2:
        from data.db import cancel_snipe
        ok = cancel_snipe(parts[1])
        return ("🎯 Objetivo de auto-puja quitado." if ok else "No encontré ese objetivo activo."), None

    # Activar/desactivar una automatización.
    if kind == "toggle" and len(parts) == 3:
        from data.db import set_setting
        key, value = parts[1], parts[2]
        set_setting(key, value)
        nombre = {"auto_lineup": "Alineación automática", "auto_clause": "Subir cláusula automática"}.get(key, key)
        estado = "activada ✅" if value == "on" else "desactivada ⚪"
        extra = ""
        if key == "auto_clause" and value == "on":
            extra = " (⚠️ usa un endpoint no confirmado del todo; vigílalo la primera vez.)"
        return f"⚙️ {nombre} {estado}.{extra}", None

    # Selector de nivel de puja para un chollo del mercado libre.
    if kind == "bid" and len(parts) == 3:
        pid, price = int(parts[1]), int(parts[2])
        name = _safe_name(client, pid)
        try:
            budget = client.get_balance()
        except Exception:  # noqa: BLE001
            budget = price * 2
        levels = bid_levels(price, budget)
        text = (
            f"💰 ¿Cuánto pujas por {name}? (mínimo del mercado: {_fmt(price)}€)\n"
            f"Pujar el mínimo pierde si hay competencia; sube para asegurar.\n"
            f"O deja que puje solo en el último minuto (auto-puja)."
        )
        buttons = [
            (f"🟢 Competitiva ({_fmt(levels['competitiva'])}€)", f"do:f:{pid}:{levels['competitiva']}"),
            (f"🔥 Fuerte ({_fmt(levels['fuerte'])}€)", f"do:f:{pid}:{levels['fuerte']}"),
            (f"⚪ Mínima ({_fmt(levels['minima'])}€)", f"do:f:{pid}:{levels['minima']}"),
            (f"🎯 Auto-pujar al cierre (hasta {_fmt(levels['fuerte'])}€)", f"snipe:{pid}:{levels['fuerte']}"),
            ("❌ Cancelar", "cancel"),
        ]
        return text, buttons

    # Marcar objetivo de auto-puja (sniping): puja sola en el cierre hasta el tope.
    if kind == "snipe" and len(parts) == 3:
        from data.db import add_snipe_target
        pid, max_bid = int(parts[1]), int(parts[2])
        name = _safe_name(client, pid)
        add_snipe_target(pid, name, max_bid)
        return (
            f"🎯 Objetivo marcado: pujaré por {name} hasta {_fmt(max_bid)}€ justo antes del "
            f"cierre del mercado. Te aviso cuando lo haga. (Míralos con /objetivos.)"
        ), None

    if kind == "ask" and len(parts) >= 4:
        mode, pid, amount = parts[1], int(parts[2]), int(parts[3])
        other = parts[4] if len(parts) > 4 else ""  # id de vendedor/dueño (cláusula)
        name = _safe_name(client, pid)
        verbo = {
            "f": "fichar del mercado a",
            "c": "pagar la cláusula de",
            "u": "subir la cláusula de",
            "s": "poner en venta a",
        }.get(mode, "actuar sobre")
        if mode == "u":
            text = f"🔔 Vas a {verbo} {name} hasta {_fmt(amount)}€ (para blindarlo).\n¿Confirmas?"
        elif mode == "s":
            text = f"🔔 Vas a {verbo} {name} por {_fmt(amount)}€ en el mercado.\n(Reversible: puedes quitarlo antes del cierre.)\n¿Confirmas?"
        elif mode == "c":
            text = (
                f"🔔 Vas a {verbo} {name} por {_fmt(amount)}€.\n"
                f"⚠️ La cláusula es INMEDIATA e irreversible.\n¿Confirmas?"
            )
        else:
            text = f"🔔 Vas a {verbo} {name} por {_fmt(amount)}€.\n¿Confirmas?"
        do_data = f"do:{mode}:{pid}:{amount}" + (f":{other}" if other else "")
        buttons = [("✅ Sí, adelante", do_data), ("❌ Cancelar", "cancel")]
        return text, buttons

    if kind == "do" and len(parts) >= 4:
        mode, pid, amount = parts[1], int(parts[2]), int(parts[3])
        other = int(parts[4]) if len(parts) > 4 else None
        return _execute(client, mode, pid, amount, other), None

    return "No he entendido esa acción.", None


def _execute(client: BiwengerClient, mode: str, player_id: int, amount: int, other_id: int | None = None) -> str:
    name = _safe_name(client, player_id)
    try:
        if mode == "f":
            client.place_bid(player_id, amount)  # mercado libre (sin vendedor)
            accion = f"puja por {name}"
        elif mode == "c":
            client.pay_clause(player_id, amount, owner_id=other_id)
            accion = f"pago de cláusula de {name}"
        elif mode == "u":
            client.raise_clause(player_id, amount)
            accion = f"subida de cláusula de {name}"
        elif mode == "s":
            client.list_for_sale(player_id, amount)
            accion = f"puesta en venta de {name}"
        else:
            return "Acción desconocida."
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error ejecutando acción")
        return f"⚠️ No se pudo completar: {exc}. (Si acabamos de activar el modo real, avísame y reviso el formato.)"

    if settings.dry_run:
        return (
            f"🧪 MODO PRUEBA: haría la {accion} por {_fmt(amount)}€, pero DRY_RUN está "
            f"activo, así que NO se ha ejecutado nada real."
        )
    return f"✅ Hecho: {accion} por {_fmt(amount)}€."
