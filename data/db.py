"""Persistencia de histórico (SQLAlchemy). Biwenger no da histórico largo vía
API, así que guardamos snapshots nosotros mismos desde el primer día."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, create_engine
from sqlalchemy import Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from config.settings import settings


class Base(DeclarativeBase):
    pass


class PlayerSnapshot(Base):
    __tablename__ = "player_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    price: Mapped[int] = mapped_column(Integer)
    price_trend_pct: Mapped[float] = mapped_column(Float)
    points: Mapped[float] = mapped_column(Float, default=0.0)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class BidLog(Base):
    __tablename__ = "bid_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[str] = mapped_column(String, index=True)
    amount: Mapped[int] = mapped_column(Integer)
    dry_run: Mapped[bool] = mapped_column(default=True)
    placed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    result: Mapped[str] = mapped_column(String, default="pending")  # pending|won|lost


class PlayerStatusRow(Base):
    """Último estado conocido de cada jugador, para detectar lesiones/sanciones nuevas."""
    __tablename__ = "player_status"

    player_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="ok")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SeenLiveEvent(Base):
    """Eventos en vivo ya avisados, para no repetir el mismo gol/asistencia."""
    __tablename__ = "seen_live_events"

    key: Mapped[str] = mapped_column(String, primary_key=True)  # round:player:type:minute
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class QuinielaPrediction(Base):
    """Pronósticos de quiniela guardados, para evaluarlos tras la jornada."""
    __tablename__ = "quiniela_predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    round_id: Mapped[str] = mapped_column(String, index=True)
    home: Mapped[str] = mapped_column(String)
    away: Mapped[str] = mapped_column(String)
    pick: Mapped[str] = mapped_column(String)  # 1 | X | 2


class ConfigKV(Base):
    """Ajustes en caliente (clave/valor). P.ej. el token de Biwenger renovado,
    para poder actualizarlo desde Telegram sin tocar el .env ni reconstruir."""
    __tablename__ = "config"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String)


def get_setting(key: str) -> str | None:
    try:
        with get_session() as s:
            row = s.get(ConfigKV, key)
            return row.value if row else None
    except Exception:  # noqa: BLE001 - si la tabla aún no existe, no rompas
        return None


def set_setting(key: str, value: str) -> None:
    with get_session() as s:
        row = s.get(ConfigKV, key)
        if row:
            row.value = value
        else:
            s.add(ConfigKV(key=key, value=value))
        s.commit()


class SeenBargain(Base):
    """Chollos ya avisados (alerta proactiva), para no repetir el mismo aviso."""
    __tablename__ = "seen_bargains"

    player_id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def filter_new_bargains(player_ids):
    """De una lista de ids de chollo, devuelve los NUEVOS (no avisados) y los marca."""
    new = []
    with get_session() as s:
        for pid in player_ids:
            if s.get(SeenBargain, str(pid)) is None:
                s.add(SeenBargain(player_id=str(pid)))
                new.append(pid)
        s.commit()
    return new


class SeenSellAlert(Base):
    """Jugadores por los que ya avisamos 'vende ya', para no repetir el aviso.
    Se limpia cuando el jugador deja de estar en riesgo, para poder re-avisar
    si vuelve a caer más adelante."""
    __tablename__ = "seen_sell_alerts"

    player_id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def filter_new_sell_alerts(current_ids):
    """De los jugadores en riesgo de venta AHORA, devuelve los NUEVOS (no avisados)
    y marca; además olvida a los que ya no están en riesgo (para poder re-avisar)."""
    current = {str(i) for i in current_ids}
    new = []
    with get_session() as s:
        existing = {r.player_id for r in s.query(SeenSellAlert).all()}
        for pid in current - existing:
            s.add(SeenSellAlert(player_id=pid))
            new.append(int(pid))
        for pid in existing - current:  # ya no está en riesgo: olvidar
            s.query(SeenSellAlert).filter_by(player_id=pid).delete()
        s.commit()
    return new


class SnipeTarget(Base):
    """Objetivo de auto-puja: un jugador que el bot pujará en el último minuto
    antes del cierre, hasta un tope que autorizó el usuario."""
    __tablename__ = "snipe_targets"

    player_id: Mapped[str] = mapped_column(String, primary_key=True)
    player_name: Mapped[str] = mapped_column(String)
    max_bid: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|done|error|cancelled
    result: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def add_snipe_target(player_id, name, max_bid) -> None:
    with get_session() as s:
        row = s.get(SnipeTarget, str(player_id))
        if row:
            row.player_name, row.max_bid, row.status, row.result = name, int(max_bid), "pending", ""
        else:
            s.add(SnipeTarget(player_id=str(player_id), player_name=name, max_bid=int(max_bid)))
        s.commit()


def active_snipe_targets():
    with get_session() as s:
        return s.query(SnipeTarget).filter_by(status="pending").all()


def list_snipe_targets():
    with get_session() as s:
        return s.query(SnipeTarget).order_by(SnipeTarget.created_at.desc()).all()


def mark_snipe(player_id, status, result="") -> None:
    with get_session() as s:
        row = s.get(SnipeTarget, str(player_id))
        if row:
            row.status, row.result = status, result
            s.commit()


def cancel_snipe(player_id) -> bool:
    with get_session() as s:
        row = s.get(SnipeTarget, str(player_id))
        if row and row.status == "pending":
            row.status = "cancelled"
            s.commit()
            return True
    return False


class PredictionLog(Base):
    """Predicción de puntos de un jugador en una jornada, con los ingredientes
    para recalcularla con otros parámetros. Tras la jornada se rellena `actual`,
    y con eso el bot se auto-calibra (aprende de sus aciertos)."""
    __tablename__ = "prediction_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    round_id: Mapped[str] = mapped_column(String, index=True)
    player_id: Mapped[str] = mapped_column(String)
    base: Mapped[float] = mapped_column(Float)                 # forma/baseline (sin fixture ni titularidad)
    difficulty: Mapped[float | None] = mapped_column(Float, nullable=True)
    starter_rate: Mapped[float] = mapped_column(Float, default=1.0)
    predicted: Mapped[float] = mapped_column(Float)
    actual: Mapped[float | None] = mapped_column(Float, nullable=True)


def save_predictions(round_id, rows) -> int:
    """Guarda predicciones de una jornada (idempotente: no duplica por jugador)."""
    n = 0
    with get_session() as s:
        existing = {
            r.player_id for r in s.query(PredictionLog).filter_by(round_id=str(round_id)).all()
        }
        for r in rows:
            if str(r["player_id"]) in existing:
                continue
            s.add(PredictionLog(
                round_id=str(round_id), player_id=str(r["player_id"]),
                base=float(r["base"]), difficulty=r["difficulty"],
                starter_rate=float(r["starter_rate"]), predicted=float(r["predicted"]),
            ))
            n += 1
        s.commit()
    return n


def record_actuals(round_id, points_map) -> int:
    """Rellena los puntos reales de una jornada en las predicciones guardadas."""
    n = 0
    with get_session() as s:
        rows = s.query(PredictionLog).filter_by(round_id=str(round_id), actual=None).all()
        for r in rows:
            pts = points_map.get(int(r.player_id))
            if pts is not None:
                r.actual = float(pts)
                n += 1
        s.commit()
    return n


def get_training_data():
    """Devuelve (base, difficulty, starter_rate, predicted, actual) de todo lo ya jugado."""
    with get_session() as s:
        rows = s.query(PredictionLog).filter(PredictionLog.actual.isnot(None)).all()
        return [(r.base, r.difficulty, r.starter_rate, r.predicted, r.actual) for r in rows]


def save_quiniela(round_id, predictions) -> None:
    """Guarda (sustituyendo) los pronósticos de una jornada. `predictions` es una
    lista de (home, away, pick)."""
    with get_session() as s:
        s.query(QuinielaPrediction).filter_by(round_id=str(round_id)).delete()
        for home, away, pick in predictions:
            s.add(QuinielaPrediction(round_id=str(round_id), home=home, away=away, pick=pick))
        s.commit()


def load_quiniela(round_id) -> dict[str, str]:
    """Devuelve {"Local vs Visitante": pick} de una jornada guardada."""
    with get_session() as s:
        rows = s.query(QuinielaPrediction).filter_by(round_id=str(round_id)).all()
        return {f"{r.home}|{r.away}": r.pick for r in rows}


class RivalRosterRow(Base):
    """Último roster conocido de cada rival, para detectar sus movimientos
    (fichajes/ventas) comparando entre sincronizaciones."""
    __tablename__ = "rival_roster"

    manager_id: Mapped[str] = mapped_column(String, primary_key=True)
    player_id: Mapped[str] = mapped_column(String, primary_key=True)


def detect_rival_moves(current):
    """Compara el roster actual de los rivales con el último guardado y devuelve
    los movimientos nuevos, actualizando el estado.

    `current` es {manager_id: {"name": str, "player_ids": iterable[int]}}.
    Devuelve [(manager_name, added_ids: list[int], removed_ids: list[int])] solo
    para los managers que hayan cambiado algo. La PRIMERA vez (sin estado previo)
    no reporta nada: solo siembra el estado, para no soltar un aviso gigante.
    """
    moves = []
    with get_session() as s:
        had_state = s.query(RivalRosterRow).first() is not None
        for mid, info in current.items():
            mid_s = str(mid)
            prev = {
                r.player_id for r in s.query(RivalRosterRow).filter_by(manager_id=mid_s).all()
            }
            now = {str(pid) for pid in info.get("player_ids", [])}
            added = now - prev
            removed = prev - now
            # Reescribe el roster guardado de este manager.
            s.query(RivalRosterRow).filter_by(manager_id=mid_s).delete()
            for pid in now:
                s.add(RivalRosterRow(manager_id=mid_s, player_id=pid))
            if had_state and (added or removed):
                moves.append((
                    info.get("name", mid_s),
                    [int(x) for x in added],
                    [int(x) for x in removed],
                ))
        s.commit()
    return moves


def filter_new_live_events(events):
    """De una lista de LivePlayerEvent, devuelve solo los NUEVOS (no avisados aún)
    y los marca como vistos. Evita repetir el mismo evento cada pocos minutos."""
    new = []
    with get_session() as s:
        for e in events:
            key = f"{e.round_id}:{e.player_id}:{e.event_type}:{e.minute}"
            if s.get(SeenLiveEvent, key) is None:
                s.add(SeenLiveEvent(key=key))
                new.append(e)
        s.commit()
    return new


# check_same_thread solo aplica a SQLite; connect_args se ignora en otros motores.
_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, future=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return SessionLocal()


# Estados de Biwenger que consideramos "malos" (avisan de que el jugador no rinde).
BAD_STATUSES = {"injured", "sanctioned", "doubt", "suspended"}


def detect_status_changes(players):
    """Compara el status actual de cada jugador con el guardado y devuelve los
    que acaban de EMPEORAR (pasar a lesionado/sancionado/duda). Actualiza la BD.

    En la primera vez que ve a un jugador solo lo guarda (no avisa), para no
    disparar alertas de jugadores que ya estaban lesionados de antes.
    """
    changes = []
    with get_session() as s:
        for p in players:
            row = s.get(PlayerStatusRow, str(p.id))
            new_status = p.status or "ok"
            if row is None:
                s.add(PlayerStatusRow(player_id=str(p.id), name=p.name, status=new_status))
                continue
            if row.status != new_status:
                if new_status in BAD_STATUSES and row.status not in BAD_STATUSES:
                    changes.append((p, row.status, new_status))
                row.status = new_status
                row.name = p.name
                row.updated_at = datetime.utcnow()
        s.commit()
    return changes


def save_player_snapshots(players) -> int:
    """Guarda un snapshot (precio/tendencia/puntos) de una lista de Player.

    Se llama periódicamente para construir el histórico que Biwenger no expone.
    Devuelve cuántas filas insertó.
    """
    rows = [
        PlayerSnapshot(
            player_id=str(p.id),
            name=p.name,
            price=p.price,
            price_trend_pct=p.price_trend_pct,
            points=float(sum(p.fitness)) if p.fitness else 0.0,
        )
        for p in players
    ]
    if not rows:
        return 0
    with get_session() as s:
        s.add_all(rows)
        s.commit()
    return len(rows)
