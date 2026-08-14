"""Auto-calibración del predictor: aprende de sus propios aciertos.

Cada jornada guardamos qué predijimos y, tras jugarse, los puntos reales. Con
ese histórico ajustamos dos parámetros del predictor para minimizar el error:
  - `fixture_sensitivity`: cuánto pesa la dificultad del partido.
  - `calib_factor`: corrige un sesgo sistemático (si predice de más o de menos).
Los parámetros se guardan en la BD y el predictor los lee (ver predictor._tuned).
"""
from __future__ import annotations

import logging

from data.db import get_setting, get_training_data, set_setting

logger = logging.getLogger(__name__)

# Valores de sensibilidad a la dificultad que probamos para quedarnos con el mejor.
FIXTURE_SENSITIVITY_CANDIDATES = [0.0, 0.3, 0.6, 0.9, 1.2]
MIN_SAMPLES = 40  # datos mínimos antes de fiarnos del ajuste


def _predict_with(base, difficulty, starter_rate, sensitivity, calib):
    """Replica la fórmula del predictor con parámetros dados (para el grid search)."""
    fixture = 1.0 if difficulty is None else 1.0 + (50.0 - difficulty) / 100.0 * sensitivity
    starter = 0.6 + 0.4 * max(0.0, min(1.0, starter_rate))
    return max(base * fixture * starter * calib, 0.0)


def _mae(data, sensitivity, calib):
    total = sum(abs(_predict_with(b, d, sr, sensitivity, calib) - actual) for b, d, sr, _p, actual in data)
    return total / len(data)


def retune():
    """Recalibra el predictor con el histórico. Devuelve un resumen o None si aún
    no hay datos suficientes."""
    data = get_training_data()
    if len(data) < MIN_SAMPLES:
        return None

    # 1) Mejor sensibilidad a la dificultad (con calibración neutra).
    best_s = min(FIXTURE_SENSITIVITY_CANDIDATES, key=lambda s: _mae(data, s, 1.0))

    # 2) Factor de calibración de escala: corrige sesgo (predicho vs real).
    preds = [_predict_with(b, d, sr, best_s, 1.0) for b, d, sr, _p, _a in data]
    actuals = [a for _b, _d, _sr, _p, a in data]
    total_pred = sum(preds)
    calib = (sum(actuals) / total_pred) if total_pred > 0 else 1.0
    calib = max(0.5, min(1.5, round(calib, 3)))  # límites de seguridad

    mae_before = _mae(data, float(get_setting("fixture_sensitivity") or 0.6),
                      float(get_setting("calib_factor") or 1.0))
    mae_after = _mae(data, best_s, calib)

    set_setting("fixture_sensitivity", str(best_s))
    set_setting("calib_factor", str(calib))
    logger.info("retune: n=%s sensitivity=%s calib=%s MAE %.2f→%.2f",
                len(data), best_s, calib, mae_before, mae_after)
    return {"samples": len(data), "sensitivity": best_s, "calib": calib,
            "mae_before": round(mae_before, 2), "mae_after": round(mae_after, 2)}


def learning_status() -> str:
    """Texto para el comando /aprendizaje: cómo va la calibración."""
    data = get_training_data()
    s = get_setting("fixture_sensitivity") or "0.6 (por defecto)"
    c = get_setting("calib_factor") or "1.0 (por defecto)"
    if len(data) < MIN_SAMPLES:
        return (f"🧠 Aprendiendo… llevo {len(data)}/{MIN_SAMPLES} datos para empezar a "
                f"calibrar. En cuanto haya suficientes, el bot se ajustará solo cada jornada.")
    mae = _mae(data, float(get_setting("fixture_sensitivity") or 0.6),
               float(get_setting("calib_factor") or 1.0))
    return (f"🧠 Aprendizaje del bot:\n  Datos: {len(data)} jugadas\n"
            f"  Sensibilidad a la dificultad: {s}\n  Factor de calibración: {c}\n"
            f"  Error medio actual: {mae:.2f} pts por jugador")
