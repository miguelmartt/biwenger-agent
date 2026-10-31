"""Motor de auto-puja ("sniping") pre-autorizado.

el usuario marca objetivos con un tope de precio; el bot puja por ellos en el
último minuto antes de que cierre el mercado, dentro de ese tope. Sigue siendo
decisión del usuario (autoriza de antemano con límite), pero le quita el tener
que estar pendiente del cierre. Solo actúa sobre el mercado libre (nunca por
jugadores que venden compañeros de liga). Respeta DRY_RUN.

Límites de cada objetivo:
  - solo puja en el MISMO anuncio en el que lo marcaste (se guarda su cierre): si ese
    mercado cierra sin pujar o el jugador sale en otro anuncio, el objetivo caduca;
  - justo antes de pujar relee el saldo y solo puja si el tope cabe en el saldo menos el
    colchón (BUDGET_SAFETY_MARGIN), contando lo ya pujado en esta misma pasada;
  - si no puede leer el saldo, no puja a ciegas.
"""
from __future__ import annotations

import logging
import time

from bidding.targets import free_market_only
from bidding.valuation import spendable
from config.settings import settings
from core.client import BiwengerClient
from core.redact import describe_error, redact
from data.db import active_snipe_targets, mark_snipe

logger = logging.getLogger(__name__)

# Cuánto antes del cierre se dispara la puja (segundos). El job corre cada
# minuto, así que una ventana de ~90s asegura pujar en el último minuto.
SNIPE_WINDOW_SECONDS = 90


def _fmt(n: int) -> str:
    return f"{int(n):,}".replace(",", ".")


def process_snipes(client: BiwengerClient) -> list[str]:
    """Revisa los objetivos y puja por los que están a punto de cerrar.

    Devuelve los mensajes a enviar por Telegram (vacío si no hay nada que hacer).
    """
    targets = active_snipe_targets()
    if not targets:
        return []

    # Solo mercado libre (regla: nunca jugadores que venden compañeros).
    listings = {l.player_id: l for l in free_market_only(client.get_market())}
    now = time.time()
    messages: list[str] = []
    balance: int | None = None   # se lee una vez, y solo si hay que pujar
    balance_failed = False
    committed = 0                # lo ya pujado en esta pasada

    for target in targets:
        pid = int(target.player_id)
        name = target.player_name
        amount = int(target.max_bid)  # pujamos el tope autorizado (maximiza ganar la subasta)
        listing = listings.get(pid)

        if target.listing_until is None:
            # Marcado con una versión anterior, sin atarlo a un anuncio: no se puja a ciegas.
            mark_snipe(pid, "expired", "sin anuncio asociado")
            messages.append(f"🎯 He quitado la auto-puja por {name}: se marcó con una versión anterior "
                            f"del bot que no la ataba a un mercado concreto. Márcala otra vez desde "
                            f"/mercado si aún la quieres.")
            continue
        if listing is None:
            if now > target.listing_until:
                mark_snipe(pid, "expired", "el mercado cerró")
                messages.append(f"🎯 La auto-puja por {name} ha caducado: el mercado en que la marcaste ya cerró.")
            continue  # aún no ha cerrado: puede ser una lectura puntual sin él
        if int(listing.until.timestamp()) != target.listing_until:
            mark_snipe(pid, "expired", "otro anuncio")
            messages.append(f"🎯 La auto-puja por {name} ha caducado: ha vuelto a salir en otro mercado "
                            f"y solo pujo en el que la marcaste. Márcala otra vez si aún lo quieres.")
            continue
        seconds_left = listing.until.timestamp() - now
        if seconds_left > SNIPE_WINDOW_SECONDS:
            continue  # todavía no es el momento

        if amount < listing.price:
            mark_snipe(pid, "error", f"mínimo {listing.price} por encima del tope")
            messages.append(f"⚠️ No pujo por {name}: el mínimo del mercado ({_fmt(listing.price)}€) "
                            f"supera tu tope ({_fmt(amount)}€).")
            continue

        if balance is None and not balance_failed:
            try:
                balance = client.get_balance()
            except Exception as exc:  # noqa: BLE001
                balance_failed = True
                logger.warning("Sniper: no pude leer el saldo (%s)", describe_error(exc))
        if balance is None:
            messages.append(f"⚠️ No he podido leer tu saldo y no pujo a ciegas por {name}. "
                            f"Lo reintento en el siguiente minuto si aún da tiempo.")
            continue
        if committed + amount > spendable(balance):
            mark_snipe(pid, "error", "saldo insuficiente")
            messages.append(
                f"⚠️ No pujo por {name}: tu saldo ({_fmt(balance)}€) menos el colchón "
                f"({_fmt(settings.budget_safety_margin)}€)"
                + (f" y lo ya pujado ({_fmt(committed)}€)" if committed else "")
                + f" no cubre los {_fmt(amount)}€."
            )
            continue

        try:
            client.place_bid(pid, amount)
        except Exception as exc:  # noqa: BLE001
            mark_snipe(pid, "error", type(exc).__name__)
            logger.warning("Sniper: la puja por %s falló (%s)", pid, describe_error(exc))
            messages.append(f"⚠️ No pude pujar por {name}: {redact(str(exc))}")
            continue
        committed += amount
        mark_snipe(pid, "done", f"pujado {amount}")
        if settings.dry_run:
            messages.append(f"🎯 [MODO PRUEBA] Pujaría por {name}: {_fmt(amount)}€ (cierre inminente).")
        else:
            messages.append(f"🎯 ¡Sniping! He pujado por {name}: {_fmt(amount)}€ justo antes "
                            f"del cierre, como autorizaste.")

    return messages
