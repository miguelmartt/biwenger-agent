"""Tests de los avisos de fallo: uno al romperse, uno al volver, nunca uno por ejecución."""
from __future__ import annotations

from core.client import BiwengerTokenExpired
from scheduler.health import JobHealth


def _health():
    sent: list[str] = []
    return JobHealth(sent.append), sent


def test_failing_job_alerts_once_and_once_on_recovery():
    health, sent = _health()
    for _ in range(5):  # falla cada media hora durante horas
        health.on_error("sync_state", RuntimeError("Old version"))
    assert len(sent) == 1 and "sincronizar" in sent[0] and "Old version" in sent[0]
    health.on_success("sync_state")
    health.on_success("sync_state")
    assert len(sent) == 2 and sent[1].startswith("✅")


def test_frequent_job_needs_two_failures_in_a_row():
    health, sent = _health()
    health.on_error("sniper", RuntimeError("timeout"))
    health.on_success("sniper")  # un corte puntual: ni aviso ni «ya funciona»
    health.on_error("sniper", RuntimeError("timeout"))
    assert sent == []
    health.on_error("sniper", RuntimeError("timeout"))
    assert len(sent) == 1


def test_expired_token_is_a_single_alert_for_every_job():
    health, sent = _health()
    for job in ("sync_state", "daily_report", "auto_lineup", "token_health"):
        health.on_error(job, BiwengerTokenExpired("401"))
    assert len(sent) == 1 and "/token" in sent[0]
    health.on_success("sync_state")
    assert len(sent) == 2 and "token" in sent[1].lower()


def test_a_broken_notifier_does_not_raise():
    def boom(_text):
        raise ConnectionError("Telegram caído")

    JobHealth(boom).on_error("daily_report", RuntimeError("x"))  # no debe lanzar
