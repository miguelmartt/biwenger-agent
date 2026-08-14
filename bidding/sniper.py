"""Motor de auto-puja ("sniping") pre-autorizado.

el usuario marca objetivos con un tope de precio; el bot puja por ellos en el
último minuto antes de que cierre el mercado, dentro de ese tope. Sigue siendo
decisión del usuario (autoriza de antemano con límite), pero le quita el tener
que estar pendiente del cierre. Solo actúa sobre el mercado libre (nunca por
jugadores que venden compañeros de liga). Respeta DRY_RUN.
"""
from __future__ import annotations

import logging
import time

from bidding.targets import free_market_only
from config.settings import settings
from core.client import BiwengerClient
from data.db import active_snipe_targets, mark_snipe

logger = logging.getLogger(__name__)

# Cuánto antes del cierre se dispara la puja (segundos). El job corre cada
# minuto, así que una ventana de ~90s asegura pujar en el último minuto.
SNIPE_WINDOW_SECONDS = 90


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

    for target in targets:
        pid = int(target.player_id)
        listing = listings.get(pid)
        if listing is None:
            continue  # ya no está en el mercado libre; esperamos por si vuelve
        seconds_left = listing.until.timestamp() - now
        if seconds_left > SNIPE_WINDOW_SECONDS:
            continue  # todavía no es el momento

        amount = int(target.max_bid)  # pujamos el tope autorizado (maximiza ganar la subasta)
        try:
            client.place_bid(pid, amount)
            mark_snipe(pid, "done", f"pujado {amount}")
            if settings.dry_run:
                messages.append(
                    f"🎯 [MODO PRUEBA] Pujaría por {target.player_name}: {amount:,}€ "
                    f"(cierre inminente).".replace(",", ".")
                )
            else:
                messages.append(
                    f"🎯 ¡Sniping! He pujado por {target.player_name}: {amount:,}€ justo antes "
                    f"del cierre, como autorizaste.".replace(",", ".")
                )
        except Exception as exc:  # noqa: BLE001
            mark_snipe(pid, "error", str(exc))
            messages.append(f"⚠️ No pude pujar por {target.player_name}: {exc}")

    return messages
