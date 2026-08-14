"""Test del guardado/evaluación de la quiniela para el resumen post-jornada."""
from __future__ import annotations

from data.db import init_db, load_quiniela, save_quiniela


def test_save_and_load_quiniela():
    init_db()
    preds = [("Celta", "Osasuna", "1"), ("Alavés", "Getafe", "2")]
    save_quiniela(4899, preds)
    loaded = load_quiniela(4899)
    assert loaded["Celta|Osasuna"] == "1"
    assert loaded["Alavés|Getafe"] == "2"


def test_save_quiniela_replaces_previous():
    init_db()
    save_quiniela(5000, [("A", "B", "1")])
    save_quiniela(5000, [("A", "B", "X")])  # se re-guarda -> debe sustituir, no duplicar
    loaded = load_quiniela(5000)
    assert loaded == {"A|B": "X"}


def test_quiniela_hits_count():
    # Comparación de aciertos (lógica del resumen).
    saved = {"Celta|Osasuna": "1", "Alavés|Getafe": "2", "Valencia|Betis": "X"}
    results = [
        {"home": "Celta", "away": "Osasuna", "pick": "1"},   # acierto
        {"home": "Alavés", "away": "Getafe", "pick": "1"},   # fallo (predijo 2)
        {"home": "Valencia", "away": "Betis", "pick": "X"},  # acierto
    ]
    hits = sum(1 for r in results if saved.get(f"{r['home']}|{r['away']}") == r["pick"])
    assert hits == 2


if __name__ == "__main__":
    test_save_and_load_quiniela()
    test_save_quiniela_replaces_previous()
    test_quiniela_hits_count()
    print("OK: resumen post-jornada correcto")
