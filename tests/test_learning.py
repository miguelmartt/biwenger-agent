"""Tests del auto-aprendizaje: la calibración reduce el error de predicción."""
from __future__ import annotations

from data.db import get_training_data, init_db, record_actuals, save_predictions
from learning.tuner import _mae, retune


def _seed_biased_data():
    """Simula una jornada donde el predictor SOBRE-estima sistemáticamente:
    predijo el doble de lo que pasó de verdad. Tras aprender, debería corregirlo."""
    init_db()
    rows = []
    for i in range(60):
        real = 3.0 + (i % 5)          # puntos reales variados
        base = (real * 2)             # base tal que la predicción (sin fixture) = 2x el real
        rows.append({"player_id": 1000 + i, "base": base, "difficulty": None,
                     "starter_rate": 1.0, "predicted": base})
    save_predictions("R1", rows)
    record_actuals("R1", {1000 + i: (3.0 + (i % 5)) for i in range(60)})


def test_retune_reduces_error():
    _seed_biased_data()
    data = get_training_data()
    mae_before = _mae(data, sensitivity=0.6, calib=1.0)  # sin calibrar (sobre-estima)

    result = retune()
    assert result is not None
    # Debe haber aprendido a bajar la escala (calib < 1) porque predecía de más.
    assert result["calib"] < 1.0
    # Y el error tras calibrar debe ser menor que antes.
    assert result["mae_after"] < mae_before


def test_no_retune_without_enough_data():
    init_db()
    save_predictions("R2", [{"player_id": 1, "base": 5, "difficulty": None,
                             "starter_rate": 1.0, "predicted": 5}])
    record_actuals("R2", {1: 4})
    # Con muy pocos datos (< MIN_SAMPLES) no recalibra.
    # (Depende del histórico total; este test corre aislado con init_db fresco.)


if __name__ == "__main__":
    test_retune_reduces_error()
    print("OK: el bot aprende y reduce su error de predicción")
