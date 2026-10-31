"""Acciones con confirmación (botones de Telegram).

Flujo: el bot recomienda con un botón ('✅ Fichar', '💰 Cláusula', '📤 Vender'…) → el
usuario pulsa → el bot pide CONFIRMACIÓN ('¿Seguro?') → el usuario confirma → se
vuelve a comprobar todo contra Biwenger → se ejecuta (o se simula si DRY_RUN). El agente
NUNCA ejecuta una compra sin este doble OK explícito, y las pujas también pasan por él:
elegir un nivel de puja abre la confirmación, no puja.

Cada acción con dinero se guarda en la BD (`data.db.PendingAction`) y el botón de
confirmar solo lleva su id. Así cada confirmación:
  - vale UNA sola vez: se marca usada de forma atómica antes de hacer nada, así que un
    doble clic o un update repetido no compran dos veces;
  - caduca a los 10 minutos (PENDING_ACTION_TTL);
  - antes de ejecutarse relee de Biwenger el precio, la cláusula, el dueño y el saldo, y
    si algo ha cambiado desde que la pediste no hace nada y te dice qué ha cambiado.

callback_data:
  bid:<pid>:<precio>                    -> selector de puja de un chollo (niveles + auto-puja)
  ask:<modo>:<pid>:<importe>[:<dueño>]  -> pedir confirmación (modos c, u, s; f abre el selector)
  confirm:<id>                          -> pedir confirmación de una acción ya guardada
  do:<id>                               -> ejecutar (tras confirmar)
  cancel[:<lote>]                       -> cancelar (e invalidar las acciones de ese mensaje)
  toggle:<clave>:<on|off>               -> automatizaciones de /auto (solo las de TOGGLES)
  unsnipe:<pid>                         -> quitar un objetivo de auto-puja

Modos: f puja en el mercado libre · t auto-puja al cierre · c pagar cláusula de un rival ·
u subir la cláusula de un jugador tuyo · s poner en venta un jugador tuyo.
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from bidding.targets import free_market_only
from bidding.valuation import bid_levels, spendable
from config.settings import settings
from core.client import BiwengerClient
from core.redact import describe_error, redact

logger = logging.getLogger(__name__)

Button = tuple[str, str]
Reply = tuple[str, list[Button] | None]

TZ = ZoneInfo("Europe/Madrid")
VERBS = {
    "f": "pujar por",
    "t": "auto-pujar al cierre por",
    "c": "pagar la cláusula de",
    "u": "subir la cláusula de",
    "s": "poner en venta a",
}
# Las únicas automatizaciones que se pueden encender o apagar desde un botón.
TOGGLES = {"auto_lineup": "Alineación automática", "auto_clause": "Subir cláusula automática"}
TOGGLE_VALUES = {"on", "off"}

UNKNOWN = "No he entendido esa acción."
STALE = ("⌛ Esa confirmación ya no vale: caducó (más de 10 minutos) o ya se usó. "
         "No he hecho nada. Vuelve a pedirla para ver los datos actuales.")
OLD_BUTTON = "Ese botón es de una versión anterior del bot. No he hecho nada; vuelve a pedirlo."


def _fmt(n: int) -> str:
    return f"{int(n):,}".replace(",", ".")


def _when(epoch: int | None) -> str:
    if not epoch:
        return "?"
    return datetime.fromtimestamp(int(epoch), TZ).strftime("%d/%m %H:%M")


def _safe_name(client: BiwengerClient, player_id: int) -> str:
    try:
        return client.get_player(player_id).name
    except Exception:  # noqa: BLE001
        return f"jugador {player_id}"


def _free_listing(client: BiwengerClient, player_id: int):
    """El anuncio del jugador en el mercado libre ahora mismo, o None si no está."""
    return next((lst for lst in free_market_only(client.get_market()) if lst.player_id == int(player_id)), None)


def handle_callback(data: str, client: BiwengerClient) -> Reply:
    """Procesa el clic de un botón. Devuelve (texto, botones) para responder."""
    parts = data.split(":")
    kind = parts[0]
    try:
        if kind == "cancel":
            if len(parts) == 2:
                from data.db import cancel_pending_batch
                cancel_pending_batch(parts[1])
            return "❌ Operación cancelada. No se ha hecho nada.", None

        if kind == "unsnipe" and len(parts) == 2:
            from data.db import cancel_snipe
            ok = cancel_snipe(parts[1])
            return ("🎯 Objetivo de auto-puja quitado." if ok else "No encontré ese objetivo activo."), None

        if kind == "toggle" and len(parts) == 3:
            return _toggle(parts[1], parts[2])

        if kind == "bid" and len(parts) == 3:
            return _bid_selector(client, int(parts[1]))

        if kind == "ask" and len(parts) in (4, 5):
            owner = int(parts[4]) if len(parts) == 5 and parts[4] else None
            return _ask(client, parts[1], int(parts[2]), int(parts[3]), owner)

        if kind == "confirm" and len(parts) == 2:
            return _confirm(parts[1])

        if kind == "do" and len(parts) == 2:
            return _do(client, parts[1]), None

        if kind in ("do", "snipe"):
            # Botones de antes de este cambio (llevaban el importe dentro): ya no ejecutan nada.
            return OLD_BUTTON, None
    except ValueError:
        return UNKNOWN, None
    return UNKNOWN, None


# --------------------------------------------------------------------------- #
# Ajustes de /auto
# --------------------------------------------------------------------------- #
def _toggle(key: str, value: str) -> Reply:
    if key not in TOGGLES or value not in TOGGLE_VALUES:
        return UNKNOWN, None
    from data.db import set_setting
    set_setting(key, value)
    estado = "activada ✅" if value == "on" else "desactivada ⚪"
    extra = ""
    if key == "auto_clause" and value == "on":
        extra = " (⚠️ usa un endpoint no confirmado del todo; vigílalo la primera vez.)"
    return f"⚙️ {TOGGLES[key]} {estado}.{extra}", None


# --------------------------------------------------------------------------- #
# Pedir confirmación: se guarda la acción y el botón solo lleva su id
# --------------------------------------------------------------------------- #
def _bid_selector(client: BiwengerClient, pid: int) -> Reply:
    """Niveles de puja para un chollo del mercado libre, calculados con el mercado y el saldo
    de AHORA. Cada nivel (y la auto-puja) es una acción guardada que aún pide confirmación."""
    from data.db import create_pending_actions

    name = _safe_name(client, pid)
    try:
        listing = _free_listing(client, pid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Selector de puja: no pude leer el mercado (%s)", describe_error(exc))
        return "⚠️ No he podido leer el mercado ahora mismo. Inténtalo en un rato.", None
    if listing is None:
        return f"🤷 {name} ya no está en el mercado libre.", None
    try:
        budget = client.get_balance()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Selector de puja: no pude leer el saldo (%s)", describe_error(exc))
        return ("⚠️ No he podido leer tu saldo, y sin saberlo no te propongo pujas. "
                "Inténtalo en un rato."), None

    levels = bid_levels(listing.price, budget)
    if not levels:
        return (
            f"💸 No te llega para pujar por {name}: el mínimo es {_fmt(listing.price)}€ y tu saldo "
            f"({_fmt(budget)}€) menos el colchón ({_fmt(settings.budget_safety_margin)}€) se queda "
            f"en {_fmt(max(spendable(budget), 0))}€."
        ), None

    common = {"player_id": pid, "player_name": name, "ref_price": listing.price,
              "ref_until": int(listing.until.timestamp())}
    order = [("competitiva", "🟢 Competitiva"), ("fuerte", "🔥 Fuerte"), ("minima", "⚪ Mínima")]
    specs = [{**common, "mode": "f", "amount": levels[key]} for key, _ in order]
    specs.append({**common, "mode": "t", "amount": levels["fuerte"]})
    batch, ids = create_pending_actions(specs)

    text = (
        f"💰 ¿Cuánto pujas por {name}? (mínimo del mercado: {_fmt(listing.price)}€)\n"
        f"Pujar el mínimo pierde si hay competencia; sube para asegurar.\n"
        f"O deja que puje solo en el último minuto (auto-puja).\n"
        f"Elige y te pido confirmación antes de hacer nada."
    )
    buttons = [(f"{label} ({_fmt(levels[key])}€)", f"confirm:{ids[i]}") for i, (key, label) in enumerate(order)]
    buttons.append((f"🎯 Auto-pujar al cierre (hasta {_fmt(levels['fuerte'])}€)", f"confirm:{ids[3]}"))
    buttons.append(("❌ Cancelar", f"cancel:{batch}"))
    return text, buttons


def _ask(client: BiwengerClient, mode: str, pid: int, amount: int, owner: int | None) -> Reply:
    """Guarda la acción de un botón de la lista (cláusula, subir cláusula, vender) y pide el OK."""
    from data.db import create_pending_actions

    if mode == "f":  # fichar del mercado libre: siempre por el selector de puja
        return _bid_selector(client, pid)
    if mode not in ("c", "u", "s") or amount <= 0:
        return UNKNOWN, None
    if mode == "c" and not owner:
        return UNKNOWN, None

    name = _safe_name(client, pid)
    ref_price = amount  # la cláusula o el precio que viste en la lista
    if mode == "u":
        # Para subir la cláusula, la referencia es la que tiene ahora.
        try:
            current = ((client.get_my_team().owned or {}).get(pid) or {}).get("clause")
        except Exception as exc:  # noqa: BLE001
            logger.warning("No pude leer tu plantilla para la cláusula (%s)", describe_error(exc))
            return "⚠️ No he podido leer tu plantilla ahora mismo. Inténtalo en un rato.", None
        if current is None:
            return f"🤷 {name} ya no está en tu plantilla.", None
        if int(current) >= amount:
            return f"🛡️ La cláusula de {name} ya está en {_fmt(current)}€. No hace falta subirla.", None
        ref_price = int(current)

    batch, ids = create_pending_actions([{
        "mode": mode, "player_id": pid, "player_name": name, "amount": amount,
        "owner_id": owner if mode == "c" else None, "ref_price": ref_price,
    }])
    return _confirm(ids[0], batch=batch)


def _confirm_text(action) -> str:
    name, amount = action.player_name or f"jugador {action.player_id}", _fmt(action.amount)
    verbo = VERBS.get(action.mode, "actuar sobre")
    if action.mode == "f":
        body = (f"🔔 Vas a {verbo} {name}: {amount}€ (mínimo del mercado: {_fmt(action.ref_price or 0)}€).\n"
                f"(Las pujas se pueden retirar en Biwenger antes del cierre.)")
    elif action.mode == "t":
        body = (f"🔔 Vas a dejar programada una auto-puja por {name} de hasta {amount}€, justo antes "
                f"del cierre de ESTE mercado ({_when(action.ref_until)}). Antes de pujar comprobaré tu saldo.")
    elif action.mode == "u":
        body = (f"🔔 Vas a {verbo} {name} de {_fmt(action.ref_price or 0)}€ a {amount}€ "
                f"(para blindarlo). Gasta saldo.")
    elif action.mode == "s":
        body = (f"🔔 Vas a {verbo} {name} por {amount}€ en el mercado.\n"
                f"(Reversible: puedes quitarlo antes del cierre.)")
    elif action.mode == "c":
        body = f"🔔 Vas a {verbo} {name} por {amount}€.\n⚠️ La cláusula es INMEDIATA e irreversible."
    else:
        body = f"🔔 Vas a {verbo} {name} por {amount}€."
    return f"{body}\n¿Confirmas? (Este botón vale una vez y caduca en 10 minutos.)"


def _confirm(action_id: str, batch: str | None = None) -> Reply:
    from data.db import peek_pending_action

    action = peek_pending_action(action_id)
    if action is None:
        return STALE, None
    buttons = [("✅ Sí, adelante", f"do:{action.id}"), ("❌ Cancelar", f"cancel:{batch or action.batch}")]
    return _confirm_text(action), buttons


# --------------------------------------------------------------------------- #
# Ejecutar: consumir la acción, comprobar que nada ha cambiado y actuar
# --------------------------------------------------------------------------- #
def _do(client: BiwengerClient, action_id: str) -> str:
    from data.db import claim_pending_action

    status, action = claim_pending_action(action_id)
    if status == "missing":
        return "No encuentro esa acción. No he hecho nada; vuelve a pedirla."
    if status == "used":
        return "⛔ Esa confirmación ya se usó o la cancelaste. No he hecho nada otra vez."
    if status == "expired":
        return STALE

    problem = _changed(client, action)
    if problem:
        return f"🛑 No he hecho nada: {problem}\nVuelve a pedirlo si te sigue interesando."
    return _execute(client, action)


def _changed(client: BiwengerClient, a) -> str | None:
    """Relee de Biwenger lo que importa para esta acción. Devuelve qué ha cambiado (o por qué
    no se puede comprobar), o None si todo sigue como cuando la pediste."""
    name = a.player_name or f"jugador {a.player_id}"
    margin = settings.budget_safety_margin
    try:
        if a.mode in ("f", "t"):
            listing = _free_listing(client, a.player_id)
            if listing is None:
                return f"{name} ya no está en el mercado libre."
            if int(listing.until.timestamp()) != a.ref_until:
                return f"el anuncio de {name} ya no es el mismo (el mercado en que lo pediste ha cerrado)."
            if listing.price != a.ref_price:
                return f"el precio mínimo de {name} ha cambiado de {_fmt(a.ref_price or 0)}€ a {_fmt(listing.price)}€."
            balance = client.get_balance()
            if a.amount > spendable(balance):
                return (f"tu saldo ({_fmt(balance)}€) menos el colchón ({_fmt(margin)}€) ya no cubre "
                        f"los {_fmt(a.amount)}€.")
        elif a.mode == "c":
            info = client.get_manager_clauses(a.owner_id).get(a.player_id)
            if info is None:
                return f"{name} ya no es de ese rival."
            if int(info.get("clause") or 0) != a.amount:
                return f"la cláusula de {name} ha cambiado de {_fmt(a.amount)}€ a {_fmt(info.get('clause') or 0)}€."
            balance = client.get_balance()
            if a.amount > spendable(balance):
                return (f"tu saldo ({_fmt(balance)}€) menos el colchón ({_fmt(margin)}€) no cubre "
                        f"la cláusula ({_fmt(a.amount)}€).")
        elif a.mode in ("u", "s"):
            team = client.get_my_team()
            owner = (team.owned or {}).get(a.player_id)
            if owner is None:
                return f"{name} ya no está en tu plantilla."
            if a.mode == "u":
                current = int(owner.get("clause") or 0)
                if current != a.ref_price:
                    return f"la cláusula de {name} ha cambiado de {_fmt(a.ref_price or 0)}€ a {_fmt(current)}€."
                # Coste: no sabemos cuánto cobra Biwenger exactamente; se exige saldo para la subida entera.
                cost = a.amount - current
                if cost > spendable(team.balance):
                    return (f"tu saldo ({_fmt(team.balance)}€) menos el colchón ({_fmt(margin)}€) no cubre "
                            f"la subida ({_fmt(cost)}€).")
            else:
                price = client.get_player(a.player_id).price
                if price != a.ref_price:
                    return f"el valor de mercado de {name} ha cambiado de {_fmt(a.ref_price or 0)}€ a {_fmt(price)}€."
        else:
            return "es una acción que no conozco."
    except Exception as exc:  # noqa: BLE001
        logger.warning("No pude revalidar la acción %s (%s)", a.mode, describe_error(exc))
        return f"no he podido comprobar en Biwenger que nada haya cambiado ({type(exc).__name__})."
    return None


def _execute(client: BiwengerClient, a) -> str:
    name = a.player_name or f"jugador {a.player_id}"
    if a.mode == "t":
        from data.db import add_snipe_target
        add_snipe_target(a.player_id, name, a.amount, a.ref_until)
        return (
            f"🎯 Objetivo marcado: pujaré por {name} hasta {_fmt(a.amount)}€ justo antes del cierre "
            f"de este mercado ({_when(a.ref_until)}), si tu saldo lo cubre. Te aviso cuando lo haga. "
            f"(Míralos con /objetivos.)"
        )
    try:
        if a.mode == "f":
            client.place_bid(a.player_id, a.amount)  # mercado libre (sin vendedor)
            accion = f"puja por {name}"
        elif a.mode == "c":
            client.pay_clause(a.player_id, a.amount, owner_id=a.owner_id)
            accion = f"pago de cláusula de {name}"
        elif a.mode == "u":
            client.raise_clause(a.player_id, a.amount)
            accion = f"subida de cláusula de {name}"
        elif a.mode == "s":
            client.list_for_sale(a.player_id, a.amount)
            accion = f"puesta en venta de {name}"
        else:
            return "Acción desconocida."
    except Exception as exc:  # noqa: BLE001
        logger.warning("Error ejecutando la acción %s (%s)", a.mode, describe_error(exc))
        return (f"⚠️ No se pudo completar: {redact(str(exc))}.\nNo lo repito solo: si quieres "
                f"intentarlo otra vez, vuelve a pedirlo. (Si acabamos de activar el modo real, "
                f"avísame y reviso el formato.)")

    if settings.dry_run:
        return (
            f"🧪 MODO PRUEBA: haría la {accion} por {_fmt(a.amount)}€, pero DRY_RUN está "
            f"activo, así que NO se ha ejecutado nada real."
        )
    return f"✅ Hecho: {accion} por {_fmt(a.amount)}€."
