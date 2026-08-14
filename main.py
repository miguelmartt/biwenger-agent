"""Punto de entrada.

  python main.py --once   -> ejecuta un ciclo manual (útil para probar) y termina
  python main.py          -> arranca el bot interactivo + el scheduler (uso en VPS/Docker)
"""
from __future__ import annotations

import argparse
import logging
import threading

from bot.polling import run_polling
from data.db import init_db
from scheduler.jobs import build_scheduler, job_economy_report, job_suggest_lineup, job_sync_state

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def run_once() -> None:
    init_db()  # crea las tablas si no existen (en modo scheduler ya lo hace build_scheduler)
    job_sync_state()
    job_suggest_lineup()
    job_economy_report()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Ejecuta un ciclo manual y termina")
    args = parser.parse_args()

    if args.once:
        run_once()
        return

    # Bot interactivo (comandos) en un hilo daemon + scheduler en el hilo principal.
    threading.Thread(target=run_polling, daemon=True, name="telegram-polling").start()
    build_scheduler().start()


if __name__ == "__main__":
    main()
