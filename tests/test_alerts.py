"""Tests de alertas de lesiones, cláusula defensiva y quiniela con forma."""
from __future__ import annotations

from core.models import Fixture, Player, Position, TeamState
from data.db import detect_status_changes, init_db
from quiniela.predictor import predict_result


def _p(pid, name, status="ok", price=3_000_000, last_season=190, difficulty=40):
    return Player(id=pid, name=name, position=Position.FORWARD, price=price,
                  status=status, points_last_season=last_season, fixture_difficulty=difficulty)


def test_status_change_alerts_only_on_transition():
    init_db()
    p = _p(9001, "Pépé", status="ok")
    # Primera vez: solo guarda, no avisa.
    assert detect_status_changes([p]) == []
    # Sigue ok: no avisa.
    assert detect_status_changes([p]) == []
    # Se lesiona: avisa una vez.
    p.status = "injured"
    changes = detect_status_changes([p])
    assert len(changes) == 1 and changes[0][2] == "injured"
    # Sigue lesionado: NO vuelve a avisar (nada de spam).
    assert detect_status_changes([p]) == []


def test_clause_risk_detects_cheap_clause_on_good_player():
    from core.services import clause_risks

    catalog = {9002: _p(9002, "Crack", price=5_000_000, last_season=228)}
    team = TeamState(
        team_id=1, name="Hustle Hard", balance=0, player_ids=[9002],
        owned={9002: {"clause": 5_500_000}},  # cláusula baja (1.1x el precio) -> vulnerable
    )

    class _C:
        def get_player(self, pid):
            return catalog[pid]

    risks = clause_risks(_C(), catalog, team)
    assert len(risks) == 1
    assert risks[0].player.name == "Crack"
    assert risks[0].suggested_clause == 10_000_000  # 2x el valor de mercado


def test_quiniela_reweights_form():
    # Mismo rating pero peor forma para el visitante -> favorece al local.
    fx = Fixture(
        "Local", "Visitante", home_difficulty=45, away_difficulty=45,
        home_components={"standings": 45, "homeAway": 45, "form": 20, "goalDiff": 45},  # local en buena forma
        away_components={"standings": 45, "homeAway": 45, "form": 80, "goalDiff": 45},  # visitante en mala forma
    )
    # Con la forma reponderada, el local (menor dificultad de forma) es favorito.
    assert predict_result(fx).pick == "1"


if __name__ == "__main__":
    test_status_change_alerts_only_on_transition()
    test_clause_risk_detects_cheap_clause_on_good_player()
    test_quiniela_reweights_form()
    print("OK: alertas, cláusula defensiva y quiniela con forma correctos")
