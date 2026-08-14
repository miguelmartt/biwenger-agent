"""Modelos de datos compartidos por todos los módulos.

Deliberadamente simples (dataclasses) para no acoplar el resto del sistema
a la forma exacta de la respuesta JSON de Biwenger. `client.py` es el único
sitio que debería tener que cambiar si Biwenger cambia su API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum


class Position(IntEnum):
    """Codificación numérica de posición que usa Biwenger en la API real."""
    GOALKEEPER = 1
    DEFENDER = 2
    MIDFIELDER = 3
    FORWARD = 4
    COACH = 5

    @property
    def label(self) -> str:
        return {1: "GK", 2: "DF", 3: "MF", 4: "FW", 5: "COACH"}[int(self)]


@dataclass
class Player:
    id: int
    name: str
    position: Position
    price: int
    price_increment: int = 0          # variación de precio del último día (€)
    status: str = "ok"                # "ok", "injured", "sanctioned", "doubt", ...
    fitness: list[int] = field(default_factory=list)  # puntos de las últimas jornadas (temporada actual)
    slug: str | None = None
    team_id: int | None = None
    is_owned_by_me: bool = False
    clause: int | None = None         # cláusula de rescisión si lo tienes tú
    points_last_season: int = 0       # puntos totales de la temporada pasada (baseline al inicio)
    # Dificultad del próximo partido según Biwenger (0-100, ~50 neutral, mayor = más difícil).
    # Mezcla clasificación, local/visitante, forma y diferencia de goles del rival.
    fixture_difficulty: float | None = None
    # Histórico de precios [(YYMMDD, precio), ...] ordenable cronológicamente,
    # para analizar tendencia/momentum en el motor de economía.
    price_history: list[tuple[int, int]] = field(default_factory=list)
    # Proxy de titularidad (0-1): fracción de partidos jugados respecto a un
    # titular fijo. 1.0 = juega siempre; bajo = suplente/rotación. Neutral (1.0)
    # al inicio de temporada, gana señal conforme avanza la liga.
    starter_rate: float = 1.0

    @property
    def price_trend_pct(self) -> float:
        if not self.price:
            return 0.0
        return round(self.price_increment / self.price * 100, 2)

    @property
    def is_injured_or_suspended(self) -> bool:
        return self.status not in ("ok", None, "")


@dataclass
class MarketListing:
    """Un jugador a la venta en el mercado.

    `seller` es None si el jugador sale del mercado libre (pool general);
    si tiene valor, lo está vendiendo un manager de la liga -> NO fichar
    (regla del usuario).
    """
    player_id: int
    price: int
    until: datetime
    seller_id: int | None = None
    seller_name: str | None = None

    @property
    def is_from_teammate(self) -> bool:
        return self.seller_id is not None


@dataclass
class Lineup:
    formation: str
    player_ids: list[int]
    captain_id: int | None = None


@dataclass
class TeamState:
    team_id: int
    name: str
    balance: int
    player_ids: list[int]
    lineup: Lineup | None = None
    owned: dict[int, dict] = field(default_factory=dict)  # id -> {price, clause, date}


@dataclass
class Fixture:
    """Un partido de la jornada, con la dificultad que Biwenger da a cada equipo
    (menor = más fácil = favorito) y el desglose de componentes (clasificación,
    local/visitante, forma, goles) para poder reponderar la forma reciente."""
    home: str
    away: str
    home_difficulty: float | None = None
    away_difficulty: float | None = None
    status: str = ""
    home_components: dict[str, float] = field(default_factory=dict)
    away_components: dict[str, float] = field(default_factory=dict)
    date: int = 0  # epoch de inicio del partido


@dataclass
class UpcomingFixture:
    """Un partido futuro de un equipo (para el calendario multi-jornada).

    `difficulty` es la que Biwenger asigna a ESTE equipo en ese partido
    (0-100, ~50 neutral, mayor = más difícil). Sale de `nextGames`."""
    opponent: str
    is_home: bool
    difficulty: float | None = None
    date: int = 0


@dataclass
class LivePlayerEvent:
    """Un evento en vivo de un jugador tuyo (gol, asistencia, tarjeta...)."""
    player_id: int
    player_name: str
    event_type: int      # 1=gol, 2=gol de penalti, 3=asistencia, 4=amarilla, 5=roja
    minute: int
    round_id: int
