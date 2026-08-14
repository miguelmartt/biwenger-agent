"""Motor de economía: qué vender, qué comprar (chollos) y qué vigilar.

Combina el rendimiento esperado (cerebro V2, `lineup.predictor`) con la
tendencia de precio (`economy.trends`) para tomar decisiones con ventaja:
vender antes de las caídas, comprar barato lo que va a rendir y subir.
Tono "equilibrado": busca oportunidades sin arriesgar el saldo de más.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.models import Player
from economy.trends import Trend, analyze_trend
from lineup.predictor import predict


@dataclass
class Assessment:
    player: Player
    trend: Trend
    expected_points: float

    @property
    def value_ratio(self) -> float:
        """Puntos esperados por cada millón de euros de precio (relación calidad/precio)."""
        millions = self.player.price / 1_000_000 or 0.0001
        return round(self.expected_points / millions, 2)

    @property
    def bargain_score(self) -> float:
        """Puntuación de chollo: relación calidad/precio ponderada por el momentum."""
        momentum = {"subiendo": 1.15, "estable": 1.0, "techo": 0.85, "bajando": 0.6, "sin_datos": 0.9}
        return round(self.value_ratio * momentum[self.trend.state], 2)


def _assess(player: Player) -> Assessment:
    return Assessment(player=player, trend=analyze_trend(player.price_history), expected_points=predict(player))


@dataclass
class ClauseTarget:
    player: Player
    clause: int
    expected_points: float
    owner_name: str
    owner_id: int = 0

    @property
    def value_ratio(self) -> float:
        millions = self.clause / 1_000_000 or 0.0001
        return round(self.expected_points / millions, 2)


@dataclass
class ClauseRisk:
    """Un jugador TUYO bueno con la cláusula baja: riesgo de que te lo roben."""
    player: Player
    clause: int
    expected_points: float
    suggested_clause: int


@dataclass
class EconomyReport:
    sell: list[Assessment] = field(default_factory=list)
    bargains: list[Assessment] = field(default_factory=list)
    watch: list[Assessment] = field(default_factory=list)
    clause_targets: list[ClauseTarget] = field(default_factory=list)
    clause_risks: list[ClauseRisk] = field(default_factory=list)

    def as_text(self) -> str:
        lines = ["📊 Informe de economía"]

        if self.sell:
            lines.append("\n🔻 Plantea vender (bajando o en techo):")
            for a in self.sell:
                lines.append(
                    f"  {a.trend.emoji} {a.player.name} — {a.player.price:,}€ "
                    f"({a.trend.change_3d_pct:+.1f}% 3d)".replace(",", ".")
                )
        else:
            lines.append("\n🔻 Nada urgente que vender: tu plantilla aguanta valor.")

        if self.bargains:
            lines.append("\n💎 Chollos del mercado libre (mejor rendimiento/precio):")
            for a in self.bargains:
                lines.append(
                    f"  {a.trend.emoji} {a.player.name} — {a.player.price:,}€ · "
                    f"{a.expected_points} pts/j · valor {a.value_ratio}".replace(",", ".")
                )
        else:
            lines.append("\n💎 Sin chollos claros en el mercado libre ahora mismo.")

        if self.watch:
            lines.append("\n📈 Subiendo con fuerza (vigilar / comprar pronto):")
            for a in self.watch:
                lines.append(
                    f"  {a.trend.emoji} {a.player.name} — {a.player.price:,}€ "
                    f"({a.trend.change_3d_pct:+.1f}% 3d)".replace(",", ".")
                )

        if self.clause_targets:
            lines.append("\n🎯 Cláusulas rentables a tu alcance (tú decides si fichar):")
            for t in self.clause_targets:
                lines.append(
                    f"  💰 {t.player.name} ({t.owner_name}) — cláusula {t.clause:,}€ · "
                    f"{t.expected_points} pts/j".replace(",", ".")
                )

        if self.clause_risks:
            lines.append("\n🛡️ Protege a tus cracks (cláusula baja, riesgo de robo):")
            for r in self.clause_risks:
                lines.append(
                    f"  ⚠️ {r.player.name} — cláusula {r.clause:,}€ · {r.expected_points} pts/j "
                    f"→ súbela a ~{r.suggested_clause:,}€".replace(",", ".")
                )

        return "\n".join(lines)

    def action_buttons(self) -> list[tuple[str, str]]:
        """Botones '✅ Fichar' para cada recomendación accionable.

        callback_data compacto: 'ask:f:<pid>:<importe>' (mercado libre) o
        'ask:c:<pid>:<clausula>' (cláusula). El flujo pide confirmación antes
        de ejecutar nada; con DRY_RUN solo simula.
        """
        buttons: list[tuple[str, str]] = []
        # Vender: poner en el mercado a tus jugadores en caída (precio de mercado).
        for a in self.sell:
            k = a.player.price // 1000
            buttons.append((f"📤 Vender {a.player.name} ({k}k)", f"ask:s:{a.player.id}:{a.player.price}"))
        # Fichar chollos: abre el selector de nivel de puja (min/competitiva/fuerte).
        for a in self.bargains:
            k = a.player.price // 1000
            buttons.append((f"✅ Fichar {a.player.name} ({k}k)", f"bid:{a.player.id}:{a.player.price}"))
        for t in self.clause_targets:
            k = t.clause // 1000
            buttons.append((f"💰 Cláusula {t.player.name} ({k}k)", f"ask:c:{t.player.id}:{t.clause}:{t.owner_id}"))
        for r in self.clause_risks:
            k = r.suggested_clause // 1000
            buttons.append((f"🛡️ Subir cláusula {r.player.name} ({k}k)", f"ask:u:{r.player.id}:{r.suggested_clause}"))
        return buttons


def build_report(
    my_players: list[Player],
    market_players: list[Player],
    available_budget: int | None = None,
    max_items: int = 6,
) -> EconomyReport:
    # Vender: los míos en bajada o techo, peores primero.
    mine = [_assess(p) for p in my_players]
    sell = sorted(
        [a for a in mine if a.trend.state in ("bajando", "techo")],
        key=lambda a: a.trend.change_3d_pct,
    )[:max_items]

    # Chollos: mercado libre asequible, mejor puntuación de chollo, sin los que caen.
    market = [_assess(p) for p in market_players]
    affordable = [
        a for a in market
        if a.expected_points > 0
        and a.trend.state != "bajando"
        and (available_budget is None or a.player.price <= available_budget)
    ]
    bargains = sorted(affordable, key=lambda a: a.bargain_score, reverse=True)[:max_items]

    # Vigilar: los que suben con fuerza (aunque no sean chollos, para no perderlos de vista).
    watch = sorted(
        [a for a in market if a.trend.state == "subiendo"],
        key=lambda a: a.trend.change_3d_pct,
        reverse=True,
    )[:max_items]

    return EconomyReport(sell=sell, bargains=bargains, watch=watch)


def simulate_swap(current_budget: int, sell: Player, buy: Player) -> dict:
    """Simula vender `sell` y comprar `buy`, sin ejecutar nada."""
    new_budget = current_budget + sell.price - buy.price
    return {
        "sell": sell.name,
        "buy": buy.name,
        "budget_before": current_budget,
        "budget_after": new_budget,
        "viable": new_budget >= 0,
    }
