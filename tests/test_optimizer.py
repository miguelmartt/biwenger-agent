"""Test de humo: verifica que el solver de alineación produce un resultado
coherente (11 titulares, 1 portero, formación válida) con datos ficticios."""
from __future__ import annotations

from core.models import Player, Position
from lineup.optimizer import FORMATIONS, best_lineup


def _make_player(id_, name, position, fitness, injured=False):
    return Player(
        id=id_,
        name=name,
        position=position,
        price=1_000_000,
        fitness=fitness,
        is_owned_by_me=True,
        status="injured" if injured else "ok",
    )


def build_fake_squad() -> list[Player]:
    squad = []
    squad += [_make_player(f"gk{i}", f"GK {i}", Position.GOALKEEPER, [3, 4, 2]) for i in range(2)]
    squad += [_make_player(f"df{i}", f"DF {i}", Position.DEFENDER, [5, 6, 4, 7]) for i in range(6)]
    squad += [_make_player(f"mf{i}", f"MF {i}", Position.MIDFIELDER, [6, 5, 8, 7]) for i in range(6)]
    squad += [_make_player(f"fw{i}", f"FW {i}", Position.FORWARD, [8, 9, 6]) for i in range(4)]
    # un jugador lesionado con muy buena forma no debería ser convocado nunca
    squad.append(_make_player("injured_star", "Estrella Lesionada", Position.FORWARD, [15, 14, 16], injured=True))
    return squad


def test_best_lineup_has_11_starters_and_one_goalkeeper():
    squad = build_fake_squad()
    result = best_lineup(squad)

    assert len(result.starters) == 11
    assert result.formation in FORMATIONS
    goalkeepers = [p for p in result.starters if p.position == Position.GOALKEEPER]
    assert len(goalkeepers) == 1


def test_injured_player_never_selected():
    squad = build_fake_squad()
    result = best_lineup(squad)

    starter_ids = {p.id for p in result.starters}
    assert "injured_star" not in starter_ids


def test_captain_is_highest_expected_starter():
    from lineup.predictor import predict

    squad = build_fake_squad()
    result = best_lineup(squad)

    assert result.captain is not None
    assert result.captain in result.starters
    best = max(result.starters, key=predict)
    assert predict(result.captain) == predict(best)


if __name__ == "__main__":
    test_best_lineup_has_11_starters_and_one_goalkeeper()
    test_injured_player_never_selected()
    print("OK: tests del optimizador pasan")
