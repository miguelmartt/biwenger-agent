"""Cliente HTTP no oficial de Biwenger.

Endpoints CONFIRMADOS en vivo inspeccionando una sesión real en
biwenger.as.com. La autenticación y las tres rutas de lectura
(user, market, players) están verificadas devolviendo 200. Las acciones de
escritura (pujar, alinear) están implementadas según la forma documentada por
la comunidad (pablopb3/biwenger-api, jbujalance/biwenger-java-api) y respetan
DRY_RUN: conviene validarlas una vez con una acción pequeña real antes de
confiar en ellas del todo.

## Detalles de autenticación descubiertos
- Base API de la liga:  https://biwenger.as.com/api/v2
- Base datos de LaLiga: https://cf.biwenger.com/api/v2   (público, sin CORS fuera del navegador)
- Login: POST /api/v2/auth/login {email, password} -> {token}  (JWT)
- Headers obligatorios en toda petición autenticada:
    Authorization: Bearer <token>
    X-League:  id de la liga            (p.ej. 123456)
    X-User:    id del EQUIPO en la liga (p.ej. 7654321)  <-- OJO: NO es el id de cuenta
    X-Version: versión actual de la app (p.ej. 631). Si no coincide -> 400 "Old version".
    X-Lang:    "es"
  El id de cuenta NO sirve como X-User: da 401 "Invalid user".
  El X-User correcto es el id del equipo dentro de la liga (viene en el login,
  en lastSession.leagues[].user.id, y lo resuelve auto este cliente).
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

import requests
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError

from config.settings import settings
from core.models import (
    Fixture,
    LivePlayerEvent,
    Lineup,
    MarketListing,
    Player,
    Position,
    TeamState,
    UpcomingFixture,
)
from core.redact import register_secret

logger = logging.getLogger(__name__)

API_BASE = "https://biwenger.as.com/api/v2"
CF_BASE = "https://cf.biwenger.com/api/v2"
COMPETITION = "la-liga"
WEB_URL = "https://biwenger.as.com/"
# La web carga su bundle desde cdn.biwenger.com/app/v<versión>/…: ese número es el X-Version vigente.
APP_VERSION_RE = re.compile(r"cdn\.biwenger\.com/app/v(\d+)")
# Los datos de la competición son pesados y varios jobs los piden seguidos: se reusan un minuto.
COMPETITION_TTL = 60

# Reintentos. Solo se repite lo que no puede hacer nada dos veces:
#  - las lecturas (GET), ante fallos de red, timeouts y errores 5xx/429 de Biwenger;
#  - las escrituras (POST/PUT/DELETE: pujar, pagar una cláusula, vender…), SOLO si la conexión
#    falló antes de mandar nada (DNS, conexión rechazada, timeout al conectar). Si la petición
#    llegó a salir, aunque se corte la respuesta, NO se repite: podría pujar o pagar dos veces.
#  - el login, ante fallos de red y 5xx, pero nunca ante un 4xx (contraseña mal): repetirlo
#    solo acerca el bloqueo de la cuenta.
# Tras el último intento se relanza el error real (no un RetryError que lo esconde).
RETRY_ATTEMPTS = 3
RETRY_WAIT = wait_exponential(multiplier=1, min=1, max=10)
IDEMPOTENT_METHODS = {"GET", "HEAD", "OPTIONS"}


def _connect_failed(exc: BaseException) -> bool:
    """¿Falló la conexión ANTES de enviar la petición? Entonces repetirla es seguro."""
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return True
    if not isinstance(exc, requests.exceptions.ConnectionError):
        return False
    reason = exc.args[0] if exc.args else None
    reason = getattr(reason, "reason", reason)  # urllib3 MaxRetryError -> la causa
    return isinstance(reason, (NewConnectionError, ConnectTimeoutError))


def _retryable(method: str, exc: BaseException) -> bool:
    if not isinstance(exc, requests.RequestException):
        return False
    if method.upper() not in IDEMPOTENT_METHODS:
        return _connect_failed(exc)
    if isinstance(exc, requests.HTTPError):
        status = exc.response.status_code if exc.response is not None else 0
        return status >= 500 or status == 429
    return True


class _LoginServerError(RuntimeError):
    """Biwenger respondió 5xx al login: es un fallo suyo, se puede reintentar."""


def _login_retryable(exc: BaseException) -> bool:
    return isinstance(exc, (requests.ConnectionError, requests.Timeout, _LoginServerError))


def detect_app_version(session: requests.Session | None = None) -> str | None:
    """La versión de la app que sirve ahora la web de Biwenger, o None si no se puede leer."""
    try:
        resp = (session or requests).get(
            WEB_URL, timeout=15, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"}
        )
    except requests.RequestException as exc:
        logger.warning("No pude leer la web de Biwenger para ver su versión: %s", exc)
        return None
    match = APP_VERSION_RE.search(resp.text or "")
    return match.group(1) if match else None


class BiwengerAuthError(RuntimeError):
    """El login falló o el token/headers no son válidos."""


class BiwengerVersionError(RuntimeError):
    """Biwenger rechaza la X-Version por 'Old version' (subió de versión la app)."""


class BiwengerTokenExpired(RuntimeError):
    """El token manual (login social) ha caducado y no hay forma de re-loguear solo."""


class BiwengerClient:
    def __init__(
        self,
        app_version: str | None = None,
        league_id: str | None = None,
        team_id: str | None = None,
    ) -> None:
        self._session = requests.Session()
        # User-Agent de navegador real: la API de Biwenger (y su CDN cf.biwenger,
        # tras Cloudflare) devuelve 403 a clientes que parecen bots (python-requests).
        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://biwenger.as.com",
                "Referer": "https://biwenger.as.com/",
            }
        )
        # Token manual (login social). Se busca primero en la BD (renovable en
        # caliente vía /token) y si no, en el .env.
        self._token: str | None = self._active_token()
        self._manual_token = bool(self._token)
        # La versión detectada y guardada en la BD manda sobre la del .env, que se queda vieja.
        self._app_version = app_version or self._stored_app_version() or settings.biwenger_app_version or "631"
        self._competition_cache: tuple[float, dict] | None = None
        self._league_id = league_id or settings.biwenger_league_id
        self._team_id = team_id or settings.biwenger_user_id  # X-User = id del equipo
        self._account_id: int | None = None
        if self._token:
            self._session.headers.update(self._base_headers())

    @staticmethod
    def _active_token() -> str | None:
        """El token vigente: el guardado en la BD (renovado) o, si no, el del .env."""
        try:
            from data.db import get_setting
            db_token = get_setting("biwenger_token")
        except Exception:  # noqa: BLE001
            db_token = None
        register_secret(db_token)  # que nunca salga en un log
        return db_token or settings.biwenger_token or None

    def _sync_token(self) -> None:
        """Si el token de la BD ha cambiado (lo renovaste con /token), lo adopta."""
        active = self._active_token()
        if active and active != self._token:
            self._token = active
            self._manual_token = True
            self._session.headers.update(self._base_headers())

    @staticmethod
    def _stored_app_version() -> str | None:
        try:
            from data.db import get_setting
            return get_setting("biwenger_app_version")
        except Exception:  # noqa: BLE001
            return None

    def _sync_app_version(self) -> None:
        """Si otra sesión (el bot o el scheduler) ya detectó una versión nueva, la adopta."""
        stored = self._stored_app_version()
        if stored and stored != self._app_version:
            self._app_version = stored
            self._session.headers.update(self._base_headers())

    def _adopt_new_app_version(self) -> bool:
        """Biwenger ha rechazado la versión: lee la nueva de su web y la adopta. True si cambió."""
        detected = detect_app_version(self._session)
        if not detected or detected == str(self._app_version):
            return False
        old, self._app_version = self._app_version, detected
        self._session.headers.update(self._base_headers())
        try:
            from data.db import set_setting
            set_setting("biwenger_app_version", detected)
        except Exception as exc:  # noqa: BLE001
            logger.warning("No pude guardar la versión nueva de la app: %s", exc)
        logger.info("Biwenger actualizó su app: X-Version %s -> %s", old, detected)
        try:
            from bot.telegram_bot import send_message
            send_message(f"🔄 Biwenger ha actualizado su app (versión {old} → {detected}). "
                         "Me he adaptado solo; no tienes que hacer nada.")
        except Exception:  # noqa: BLE001
            pass
        return True

    def update_token(self, new_token: str) -> None:
        """Guarda un token nuevo (renovación en caliente desde Telegram)."""
        from data.db import set_setting
        register_secret(new_token)
        set_setting("biwenger_token", new_token.strip())
        self._token = new_token.strip()
        self._manual_token = True
        self._session.headers.update(self._base_headers())

    # ------------------------------------------------------------------ #
    # Autenticación
    # ------------------------------------------------------------------ #
    def login(self) -> None:
        """Login con email y contraseña. Reintenta fallos de red y 5xx, nunca un 4xx."""
        Retrying(
            stop=stop_after_attempt(RETRY_ATTEMPTS), wait=RETRY_WAIT,
            retry=retry_if_exception(_login_retryable), reraise=True,
        )(self._login_once)

    def _login_once(self) -> None:
        resp = self._session.post(
            f"{API_BASE}/auth/login",
            json={"email": settings.biwenger_email, "password": settings.biwenger_password},
            headers={"X-Lang": "es", "X-Version": self._app_version},
            timeout=15,
        )
        if resp.status_code >= 500:
            raise _LoginServerError(f"Login falló ({resp.status_code}): Biwenger no responde bien")
        if resp.status_code != 200:
            raise BiwengerAuthError(f"Login falló ({resp.status_code}): {resp.text[:200]}")

        self._token = resp.json().get("token")
        if not self._token:
            raise BiwengerAuthError("Login OK pero no se recibió token")
        register_secret(self._token)

        self._session.headers.update(self._base_headers())
        # Resolver league_id / team_id automáticamente si no venían en el .env.
        self._resolve_context()
        logger.info(
            "Login OK (league=%s, team=%s, version=%s)",
            self._league_id, self._team_id, self._app_version,
        )

    def _base_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "X-Lang": "es",
            "X-Version": str(self._app_version),
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if self._league_id:
            headers["X-League"] = str(self._league_id)
        if self._team_id:
            headers["X-User"] = str(self._team_id)
        return headers

    def _resolve_context(self) -> None:
        """Rellena league_id y team_id (X-User) desde /account si faltan.

        /account devuelve las ligas del usuario, y dentro de cada liga el id
        del equipo (que es lo que va en X-User). Solo hace falta si no lo has
        puesto a mano en el .env.
        """
        if self._league_id and self._team_id:
            return
        resp = self._session.get(
            f"{API_BASE}/account",
            headers={k: v for k, v in self._base_headers().items() if k not in ("X-League", "X-User")},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        self._account_id = data.get("account", {}).get("id")
        leagues = data.get("leagues", [])
        if not leagues:
            raise BiwengerAuthError("La cuenta no tiene ligas asociadas")
        league = leagues[0]  # con una sola liga; si hay varias, elige por id en settings
        self._league_id = self._league_id or str(league["id"])
        self._team_id = self._team_id or str(league.get("user", {}).get("id"))
        self._session.headers.update(self._base_headers())

    def _ensure_auth(self) -> None:
        if self._token:
            # En modo token manual necesitamos resolver league/team si faltan.
            if self._manual_token and (not self._league_id or not self._team_id):
                self._resolve_context()
            return
        self.login()

    def _request(self, method: str, url: str, **kwargs):
        """Una petición autenticada a Biwenger, con la política de reintentos de arriba
        (las escrituras no se repiten si la petición llegó a salir)."""
        return Retrying(
            stop=stop_after_attempt(RETRY_ATTEMPTS), wait=RETRY_WAIT,
            retry=retry_if_exception(lambda exc: _retryable(method, exc)), reraise=True,
        )(self._send, method, url, **kwargs)

    def _send(self, method: str, url: str, **kwargs):
        self._sync_token()  # adopta el token si lo renovaste con /token (otra sesión)
        self._sync_app_version()
        self._ensure_auth()
        resp = self._session.request(method, url, timeout=15, **kwargs)
        if _old_version(resp) and self._adopt_new_app_version():
            resp = self._session.request(method, url, timeout=15, **kwargs)
        if _old_version(resp):
            raise BiwengerVersionError(
                f"Biwenger rechaza X-Version={self._app_version} y no he podido leer la nueva de su web. "
                "Actualiza BIWENGER_APP_VERSION al número de cdn.biwenger.com/app/vXXX."
            )
        if resp.status_code == 401:
            if self._manual_token:
                # No hay credenciales con las que re-loguear: hay que renovar el token a mano.
                raise BiwengerTokenExpired(
                    "El token de Biwenger (BIWENGER_TOKEN) ha caducado o dejó de ser válido. "
                    "Sácalo de nuevo del navegador (localStorage 'satellizer_token') y "
                    "actualízalo en el .env."
                )
            self._token = None
            self.login()
            resp = self._session.request(method, url, timeout=15, **kwargs)
        resp.raise_for_status()
        return resp.json() if resp.content else None

    # ------------------------------------------------------------------ #
    # Lectura de estado  (endpoints confirmados)
    # ------------------------------------------------------------------ #
    def get_my_team(self) -> TeamState:
        fields = "*,lineup(type,captain,playersID),players(id,owner),balance"
        data = self._request("GET", f"{API_BASE}/user", params={"fields": fields})["data"]

        owned = {p["id"]: p.get("owner", {}) for p in data.get("players", [])}
        lineup_raw = data.get("lineup") or {}
        lineup = None
        if lineup_raw.get("playersID"):
            lineup = Lineup(
                formation=lineup_raw.get("type", ""),
                player_ids=list(lineup_raw.get("playersID", [])),
                captain_id=lineup_raw.get("captain"),
            )
        return TeamState(
            team_id=int(self._team_id),
            name=data.get("name", ""),
            balance=int(data.get("balance", 0)),
            player_ids=list(owned.keys()),
            lineup=lineup,
            owned=owned,
        )

    def get_balance(self) -> int:
        data = self._request("GET", f"{API_BASE}/user", params={"fields": "balance"})["data"]
        return int(data.get("balance", 0))

    def get_market(self) -> list[MarketListing]:
        """Devuelve TODOS los jugadores a la venta (libres y de compañeros).

        Usa `free_market_only()` abajo si solo quieres los del mercado libre
        (regla del usuario: no fichar a los que venden compañeros).
        """
        data = self._request("GET", f"{API_BASE}/market")["data"]
        listings: list[MarketListing] = []
        for sale in data.get("sales", []):
            user = sale.get("user")
            listings.append(
                MarketListing(
                    player_id=sale["player"]["id"],
                    price=int(sale.get("price", 0)),
                    until=datetime.fromtimestamp(sale["until"], tz=timezone.utc),
                    seller_id=user.get("id") if user else None,
                    seller_name=user.get("name") if user else None,
                )
            )
        return listings

    def get_player(self, player_id: int) -> Player:
        """Detalle completo de un jugador (nombre, posición, precio, tendencia, puntos)."""
        fields = "*,prices,reports(round,points)"
        data = self._request(
            "GET",
            f"{API_BASE}/players/{COMPETITION}/{player_id}",
            params={"lang": "es", "fields": fields},
        )["data"]
        return _player_from_json(data)

    def get_all_players(self) -> dict[int, Player]:
        """Catálogo completo de LaLiga enriquecido (precio, puntos, temporada pasada
        y dificultad del próximo partido).

        Usa `/api/v2/competitions/la-liga/data` en biwenger.as.com (NO cf.biwenger,
        que da 403 tras Cloudflare). Trae `players` (con teamID, pointsLastSeason,
        pointsHome/Away, fitness) y `teams` (con nextGames y la dificultad que
        calcula Biwenger). Cruza ambos para poner a cada jugador la dificultad de
        su próximo partido.
        """
        data = self._competition_data()

        difficulty_by_team = _difficulty_by_team(data.get("teams", {}), _active_difficulty(data))
        raw_players = data.get("players") or {}

        # Titularidad: nº de partidos jugados esta temporada respecto al máximo de
        # la liga (un titular fijo). Al inicio (todos a 0) queda neutral.
        max_games = 0
        for pd in raw_players.values():
            games = int(pd.get("playedHome", 0) or 0) + int(pd.get("playedAway", 0) or 0)
            max_games = max(max_games, games)

        result: dict[int, Player] = {}
        for pid, pdata in raw_players.items():
            try:
                player = _player_from_json(pdata)
            except (KeyError, ValueError):
                continue
            player.points_last_season = int(pdata.get("pointsLastSeason", 0) or 0)
            if player.team_id in difficulty_by_team:
                player.fixture_difficulty = difficulty_by_team[player.team_id]
            games = int(pdata.get("playedHome", 0) or 0) + int(pdata.get("playedAway", 0) or 0)
            player.starter_rate = (games / max_games) if max_games > 0 else 1.0
            result[int(pid)] = player
        return result

    def get_team_fixtures(self, weeks: int = 5) -> dict[int, list[UpcomingFixture]]:
        """Calendario multi-jornada: para cada equipo, sus próximos `weeks` partidos
        con la dificultad que Biwenger asigna a ESE equipo.

        No pide nada nuevo a la API: reutiliza `nextGames` del mismo `data` de la
        competición que ya alimenta el catálogo (dato interno de Biwenger, sin
        webs externas). Es la base del planificador de calendario.
        """
        data = self._competition_data()
        return _team_fixtures(data.get("teams", {}), weeks, _active_difficulty(data))

    def _competition_data(self) -> dict:
        """Datos de LaLiga (jugadores, equipos, temporada y jornada activa), con un minuto de caché."""
        now = time.monotonic()
        if self._competition_cache and now - self._competition_cache[0] < COMPETITION_TTL:
            return self._competition_cache[1]
        data = self._request(
            "GET", f"{API_BASE}/competitions/{COMPETITION}/data", params={"lang": "es", "score": 5}
        )["data"]
        self._competition_cache = (now, data)
        return data

    # ------------------------------------------------------------------ #
    # Jornadas
    # ------------------------------------------------------------------ #
    # OJO: rounds/league NO sirve para saber qué jornada toca: entre jornadas apunta a la ya
    # terminada y a veces a una "Jornada N (aplazada)". La fiable es activeEvents de la competición.
    def get_active_round(self) -> dict | None:
        """La jornada de LaLiga que se está jugando o la siguiente: {id, name, status, start, games}."""
        events = self._competition_data().get("activeEvents") or []
        return next((e for e in events if e.get("type") == "round"), None)

    def get_active_round_id(self) -> int | None:
        active = self.get_active_round()
        return int(active["id"]) if active else self._league_round_id()

    def get_recent_round_id(self, now_epoch: int | None = None) -> int | None:
        """La jornada cuyos puntos tiene sentido mirar: la activa si ya ha empezado; si no, la
        última terminada (sin contar las aplazadas). Para el resumen y el aprendizaje del lunes."""
        now_epoch = now_epoch or int(time.time())
        active = self.get_active_round()
        if active and (active.get("start") or 0) and active["start"] <= now_epoch:
            return int(active["id"])
        active_id = int(active["id"]) if active else None
        rounds = (self._competition_data().get("season") or {}).get("rounds") or []
        done = [int(r["id"]) for r in rounds
                if r.get("status") == "finished" and "aplazad" not in (r.get("name") or "").lower()
                and (active_id is None or int(r["id"]) < active_id)]
        return max(done) if done else self._league_round_id()

    def get_current_round_id(self) -> int | None:
        """Compatibilidad: la jornada activa (la que se juega o viene)."""
        return self.get_active_round_id()

    def get_round_number(self, round_id: int | None) -> int:
        """El número de jornada ("Jornada 11" → 11) de un id interno de Biwenger; 0 si no se sabe.
        Las reglas de la liga hablan de números de jornada, no de ids (4909…)."""
        if not round_id:
            return 0
        active = self.get_active_round() or {}
        rounds = list((self._competition_data().get("season") or {}).get("rounds") or []) + [active]
        name = next((r.get("name") or "" for r in rounds if str(r.get("id")) == str(round_id)), "")
        match = re.search(r"(\d+)", name)
        return int(match.group(1)) if match else 0

    def _league_round_id(self) -> int | None:
        data = self._request("GET", f"{API_BASE}/rounds/league")["data"]
        return (data.get("round") or {}).get("id")

    def get_offers(self) -> list[dict]:
        """Ofertas recibidas por tus jugadores (otros managers quieren comprarte)."""
        data = self._request("GET", f"{API_BASE}/user", params={"fields": "offers"})["data"]
        return data.get("offers", []) or []

    def get_round_fixtures(self) -> list[Fixture]:
        """Partidos de la jornada actual con la dificultad de cada equipo.

        Combina rounds/league (id de la jornada) + rounds/la-liga/{id} (games con
        la dificultad que calcula Biwenger). Base del pronóstico de la quiniela.
        """
        data = self.get_active_round()
        if data is None:
            round_id = self._league_round_id()
            if not round_id:
                return []
            data = self._request(
                "GET", f"{API_BASE}/rounds/{COMPETITION}/{round_id}", params={"lang": "es", "score": 5}
            )["data"]

        def _diff(side: dict):
            d = (side or {}).get("difficulty") or {}
            return d.get("rating")

        def _components(side: dict) -> dict[str, float]:
            d = (side or {}).get("difficulty") or {}
            out: dict[str, float] = {}
            for key in ("standings", "homeAway", "form", "goalDiff"):
                val = d.get(key)
                if isinstance(val, list) and val:  # formato [valor, peso]
                    out[key] = float(val[0])
            return out

        fixtures: list[Fixture] = []
        for g in data.get("games", []) or []:
            home = g.get("home") or {}
            away = g.get("away") or {}
            fixtures.append(
                Fixture(
                    home=home.get("name", "?"),
                    away=away.get("name", "?"),
                    home_difficulty=_diff(home),
                    away_difficulty=_diff(away),
                    status=g.get("status", ""),
                    home_components=_components(home),
                    away_components=_components(away),
                    date=int(g.get("date", 0) or 0),
                )
            )
        return fixtures

    def get_my_live_events(self, my_player_ids: list[int]) -> list[LivePlayerEvent]:
        """Eventos (goles, asistencias, tarjetas...) de TUS jugadores en la jornada activa.

        Salen de rounds/la-liga/{id}: cada partido trae en home/away.reports a sus
        jugadores con sus eventos, y Biwenger los va rellenando mientras se juega. Una
        sola petición para toda la jornada. (El detalle de cada jugador no sirve: no
        incluye la jornada en curso hasta que termina.)
        """
        round_id = self.get_active_round_id()
        if not round_id:
            return []
        data = self._request(
            "GET", f"{API_BASE}/rounds/{COMPETITION}/{round_id}", params={"lang": "es", "score": 5}
        )["data"]
        mine = {int(pid) for pid in my_player_ids}
        events: list[LivePlayerEvent] = []
        for game in data.get("games", []) or []:
            for side in ("home", "away"):
                for report in (game.get(side) or {}).get("reports", []) or []:
                    player = (report.get("player") or {}) if isinstance(report, dict) else {}
                    if player.get("id") is None or int(player["id"]) not in mine:
                        continue
                    for ev in report.get("events", []) or []:
                        events.append(
                            LivePlayerEvent(
                                player_id=int(player["id"]), player_name=player.get("name", "?"),
                                event_type=int(ev.get("type", 0) or 0),
                                minute=int(ev.get("metadata", 0) or 0),
                                round_id=int(round_id),
                            )
                        )
        return events

    def has_live_games(self, now_epoch: int) -> bool:
        """¿Hay algún partido de la jornada en curso ahora mismo? (ventana ~2.5h)."""
        WINDOW = 2.5 * 3600
        for fx in self.get_round_fixtures():
            if fx.date and fx.date <= now_epoch <= fx.date + WINDOW:
                return True
        return False

    def get_round_player_points(self, round_id: int, player_ids: list[int]) -> dict[int, dict]:
        """Puntos (Biwenger) que hicieron unos jugadores en una jornada concreta."""
        out: dict[int, dict] = {}
        for pid in player_ids:
            try:
                data = self._request(
                    "GET", f"{API_BASE}/players/{COMPETITION}/{pid}",
                    params={"lang": "es", "fields": "id,name,reports(match(round),points)"},
                )["data"]
            except Exception as exc:  # noqa: BLE001
                logger.warning("Sin puntos de jornada para %s: %s", pid, exc)
                continue
            pts = None
            for report in data.get("reports", []) or []:
                if _report_round_id(report) == int(round_id):
                    p = report.get("points")
                    pts = p.get("1") if isinstance(p, dict) else p
                    break
            out[int(pid)] = {"name": data.get("name", "?"), "points": pts}
        return out

    def get_round_results(self, round_id: int) -> list[dict]:
        """Resultados reales de los partidos de una jornada ya jugada."""
        data = self._request(
            "GET", f"{API_BASE}/rounds/{COMPETITION}/{round_id}", params={"lang": "es", "score": 5}
        )["data"]
        results = []
        for g in data.get("games", []) or []:
            home, away = g.get("home") or {}, g.get("away") or {}
            hs, as_ = home.get("score"), away.get("score")
            if hs is None or as_ is None:
                continue
            pick = "1" if hs > as_ else ("2" if as_ > hs else "X")
            results.append({"home": home.get("name", "?"), "away": away.get("name", "?"),
                            "home_score": hs, "away_score": as_, "pick": pick})
        return results

    def get_standings(self) -> list[dict]:
        """Clasificación de la liga, por posición: {id, name, points, position, team_value,
        team_value_inc, me}. `me` marca tu equipo."""
        data = self._request(
            "GET", f"{API_BASE}/league", params={"include": "all", "fields": "*,standings"}
        )["data"]
        rows = []
        for s in data.get("standings", []) or []:
            rows.append({
                "id": s.get("id"), "name": s.get("name", "?"),
                "points": int(s.get("points") or 0), "position": int(s.get("position") or 0),
                "team_value": int(s.get("teamValue") or 0), "team_value_inc": int(s.get("teamValueInc") or 0),
                "me": str(s.get("id")) == str(self._team_id),
            })
        return sorted(rows, key=lambda r: r["position"] or 999)

    def get_league_managers(self) -> list[dict]:
        """Managers de la liga (id, nombre), excluyéndote a ti."""
        data = self._request(
            "GET", f"{API_BASE}/league", params={"include": "all", "fields": "*,standings"}
        )["data"]
        managers = []
        for s in data.get("standings", []) or []:
            if str(s.get("id")) != str(self._team_id):
                managers.append({"id": s["id"], "name": s.get("name", "?")})
        return managers

    def get_manager_clauses(self, user_id: int) -> dict[int, dict]:
        """Plantilla de un rival: player_id -> {clause, buy_price}.

        Solo lectura: sirve para RECOMENDAR cláusulas rentables. El agente
        nunca paga una cláusula por su cuenta (requiere tu confirmación).
        """
        data = self._request(
            "GET", f"{API_BASE}/user/{user_id}", params={"fields": "*,players(id,owner)"}
        )["data"]
        result: dict[int, dict] = {}
        for p in data.get("players", []) or []:
            owner = p.get("owner") or {}
            result[int(p["id"])] = {"clause": owner.get("clause"), "buy_price": owner.get("price")}
        return result

    def get_manager_lineup(self, user_id: int) -> dict | None:
        """Alineación (once + capitán) de un rival, en la medida en que Biwenger la
        exponga (normalmente cuando la jornada ha empezado o terminado).

        Best-effort: mismo patrón que la lectura de tu propio equipo, pero sobre
        `/user/{id}`. Devuelve None si Biwenger no la expone (entonces los chequeos
        que dependen de la alineación simplemente se omiten sin romper nada).
        """
        try:
            data = self._request(
                "GET", f"{API_BASE}/user/{user_id}",
                params={"fields": "*,lineup(type,captain,playersID)"},
            )["data"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Sin alineación del rival %s: %s", user_id, exc)
            return None
        lu = data.get("lineup") or {}
        ids = list(lu.get("playersID") or [])
        if not ids:
            return None
        return {"player_ids": [int(i) for i in ids],
                "captain_id": lu.get("captain"),
                "formation": lu.get("type", "")}

    def get_round_manager_scores(self, round_id: int) -> dict[str, float]:
        """Puntuación (orientativa) de cada manager en una jornada, reconstruida a
        partir de su alineación y los puntos de sus jugadores (capitán x2).

        Best-effort: depende de poder leer las alineaciones de los rivales, que
        Biwenger solo expone cuando la jornada ha empezado/terminado. Devuelve {}
        si no hay datos suficientes. Es ORIENTATIVO (no incluye bonus como el
        ariete): sirve para avisar de la zona de castigo, no como verdad oficial.
        """
        # Recoge (nombre, lineup) de todos los managers: tú + rivales.
        rosters: list[tuple[str, dict]] = []
        try:
            me = self.get_my_team()
            if me.lineup and me.lineup.player_ids:
                rosters.append((me.name, {"player_ids": [int(i) for i in me.lineup.player_ids],
                                          "captain_id": me.lineup.captain_id}))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Sin tu alineación para la zona de castigo: %s", exc)
        for m in self.get_league_managers():
            lu = self.get_manager_lineup(m["id"])
            if lu:
                rosters.append((m["name"], lu))

        if len(rosters) < 2:
            return {}

        all_ids = {pid for _, lu in rosters for pid in lu["player_ids"]}
        points = self.get_round_player_points(round_id, list(all_ids))
        if not any((points.get(pid) or {}).get("points") for pid in all_ids):
            return {}  # la jornada aún no tiene puntos (no se ha jugado)

        scores: dict[str, float] = {}
        for name, lu in rosters:
            total = 0.0
            cap = lu.get("captain_id")
            for pid in lu["player_ids"]:
                pts = (points.get(int(pid)) or {}).get("points") or 0
                total += pts * (2 if cap and int(pid) == int(cap) else 1)
            scores[name] = total
        return scores

    # ------------------------------------------------------------------ #
    # Escritura de estado (acciones) — respetan DRY_RUN
    # ------------------------------------------------------------------ #
    def place_bid(self, player_id: int, amount: int, seller_id: int | None = None) -> dict | None:
        """Puja por un jugador del mercado (POST /api/v2/offers).

        Formato confirmado leyendo el propio código de la app de Biwenger:
        type='purchase', requestedPlayers=[id], amount, y user=vendedor (o null
        para el mercado libre general). Las pujas del mercado son CANCELABLES
        antes del cierre, así que una puja de más se puede retirar.
        """
        payload = {
            "type": "purchase",
            "amount": int(amount),
            "requestedPlayers": [int(player_id)],
            "user": int(seller_id) if seller_id else None,
        }
        if settings.dry_run:
            logger.info("[DRY RUN] place_bid %s", payload)
            return None
        return self._request("POST", f"{API_BASE}/offers", json=payload)

    def set_lineup(self, player_ids: list[int], formation: str, captain_id: int | None = None) -> dict | None:
        payload = {
            "type": formation,
            "playersID": [int(p) for p in player_ids],
            "captain": captain_id,
        }
        if settings.dry_run:
            logger.info("[DRY RUN] set_lineup %s", payload)
            return None
        return self._request("POST", f"{API_BASE}/user", json={"lineup": payload})

    def accept_offer(self, offer_id: int) -> dict | None:
        if settings.dry_run:
            logger.info("[DRY RUN] accept_offer(%s)", offer_id)
            return None
        return self._request("POST", f"{API_BASE}/offers/{offer_id}", json={"status": "accepted"})

    def pay_clause(self, player_id: int, amount: int, owner_id: int | None = None) -> dict | None:
        """Paga la cláusula de un jugador de un rival (compra instantánea).

        Es una oferta de type='clause' (POST /api/v2/offers), según el código de
        la app. A diferencia de una puja normal, la cláusula es INMEDIATA e
        irreversible, así que el flujo pide confirmación explícita.
        """
        payload = {
            "type": "clause",
            "amount": int(amount),
            "requestedPlayers": [int(player_id)],
            "user": int(owner_id) if owner_id else None,
        }
        if settings.dry_run:
            logger.info("[DRY RUN] pay_clause %s", payload)
            return None
        return self._request("POST", f"{API_BASE}/offers", json=payload)

    def list_for_sale(self, player_id: int, price: int) -> dict | None:
        """Pone un jugador TUYO en venta en el mercado (POST /api/v2/market).

        `price` es el precio de venta que fijas. Poner en venta es reversible
        (puedes quitarlo con remove_from_sale antes del cierre).
        """
        payload = {"player": int(player_id), "price": int(price)}
        if settings.dry_run:
            logger.info("[DRY RUN] list_for_sale %s", payload)
            return None
        return self._request("POST", f"{API_BASE}/market", json=payload)

    def remove_from_sale(self, player_id: int) -> dict | None:
        """Quita a un jugador tuyo del mercado (DELETE /api/v2/market/{id})."""
        if settings.dry_run:
            logger.info("[DRY RUN] remove_from_sale(%s)", player_id)
            return None
        return self._request("DELETE", f"{API_BASE}/market/{int(player_id)}")

    def raise_clause(self, player_id: int, new_clause: int) -> dict | None:
        """Sube la cláusula de un jugador TUYO para blindarlo (cuesta dinero).

        Endpoint NO validado en vivo (según forma conocida: PUT sobre el jugador
        propio). Se ejecuta solo si DRY_RUN=false y siempre tras confirmación.
        """
        payload = {"player": int(player_id), "clause": int(new_clause)}
        if settings.dry_run:
            logger.info("[DRY RUN] raise_clause %s", payload)
            return None
        return self._request("PUT", f"{API_BASE}/user/players/{int(player_id)}", json=payload)


# ---------------------------------------------------------------------- #
# Mapeo JSON -> modelos
# ---------------------------------------------------------------------- #
def _active_difficulty(data: dict) -> dict[int, dict[int, float]]:
    """Dificultad de cada equipo en los partidos de la jornada activa: {game_id: {team_id: rating}}.

    Biwenger dejó de mandar la dificultad en `nextGames` (ahora solo trae los ids de los
    equipos); sigue viniendo en los partidos de `activeEvents`.
    """
    result: dict[int, dict[int, float]] = {}
    for event in data.get("activeEvents") or []:
        for game in event.get("games") or []:
            for side in ("home", "away"):
                s = game.get(side) or {}
                diff = s.get("difficulty")
                if s.get("id") is not None and isinstance(diff, dict) and diff.get("rating") is not None:
                    result.setdefault(game.get("id"), {})[int(s["id"])] = float(diff["rating"])
    return result


def _game_rating(game: dict, team_id: int, known: dict[int, dict[int, float]] | None) -> float | None:
    """La dificultad de `game` para `team_id`: la que venga en el partido o, si no, la de la jornada activa."""
    side = game.get("home") if (game.get("home") or {}).get("id") == team_id else game.get("away")
    diff = (side or {}).get("difficulty")
    if isinstance(diff, dict) and diff.get("rating") is not None:
        return float(diff["rating"])
    return (known or {}).get(game.get("id"), {}).get(team_id)


def _difficulty_by_team(teams: dict, known: dict[int, dict[int, float]] | None = None) -> dict[int, float]:
    """Mapa teamID -> dificultad (0-100) de su próximo partido, según Biwenger.

    Cada equipo trae `nextGames`; el primero es el próximo. Se toma la dificultad del
    lado que corresponde a este equipo (o la de la jornada activa, ver `_active_difficulty`).
    """
    result: dict[int, float] = {}
    for tid, tdata in (teams or {}).items():
        try:
            team_id = int(tid)
            games = tdata.get("nextGames") or []
            if not games:
                continue
            rating = _game_rating(games[0], team_id, known)
            if rating is not None:
                result[team_id] = rating
        except (ValueError, KeyError, TypeError):
            continue
    return result


def _team_name_by_id(teams: dict) -> dict[int, str]:
    """Mapa teamID -> nombre, para nombrar al rival en el calendario."""
    names: dict[int, str] = {}
    for tid, tdata in (teams or {}).items():
        try:
            names[int(tid)] = tdata.get("name") or str(tid)
        except (ValueError, TypeError):
            continue
    return names


def _team_fixtures(teams: dict, weeks: int,
                   known: dict[int, dict[int, float]] | None = None) -> dict[int, list[UpcomingFixture]]:
    """Convierte `nextGames` de cada equipo en una lista de próximos partidos.

    Para cada partido, determina de qué lado juega este equipo (home/away),
    saca al rival y la dificultad propia de ese partido. Igual que
    `_difficulty_by_team` pero recorriendo TODOS los próximos partidos, no solo
    el primero.
    """
    names = _team_name_by_id(teams)
    result: dict[int, list[UpcomingFixture]] = {}
    for tid, tdata in (teams or {}).items():
        try:
            team_id = int(tid)
        except (ValueError, TypeError):
            continue
        runs: list[UpcomingFixture] = []
        for game in (tdata.get("nextGames") or [])[:weeks]:
            if not isinstance(game, dict):
                continue
            side = "home" if (game.get("home") or {}).get("id") == team_id else "away"
            other = "away" if side == "home" else "home"
            opp = game.get(other) or {}
            rating = _game_rating(game, team_id, known)
            opp_name = opp.get("name") or names.get(opp.get("id"), "?")
            runs.append(
                UpcomingFixture(
                    opponent=opp_name,
                    is_home=(side == "home"),
                    difficulty=float(rating) if rating is not None else None,
                    date=int(game.get("date", 0) or 0),
                )
            )
        if runs:
            result[team_id] = runs
    return result


def _player_from_json(data: dict) -> Player:
    pos_raw = data.get("position", 0)
    try:
        position = Position(int(pos_raw))
    except (ValueError, KeyError):
        position = Position.MIDFIELDER

    return Player(
        id=int(data["id"]),
        name=data.get("name", "?"),
        position=position,
        price=int(data.get("price", 0)),
        price_increment=int(data.get("priceIncrement", 0) or 0),
        status=data.get("status", "ok") or "ok",
        fitness=_extract_fitness(data.get("fitness") or data.get("reports")),
        slug=data.get("slug"),
        team_id=data.get("teamID") or data.get("team"),
        price_history=_extract_prices(data.get("prices")),
    )


def _extract_prices(raw) -> list[tuple[int, int]]:
    """Normaliza el histórico `prices` = [[YYMMDD, precio], ...] a tuplas ordenadas."""
    if not raw or not isinstance(raw, list):
        return []
    out: list[tuple[int, int]] = []
    for item in raw:
        try:
            out.append((int(item[0]), int(item[1])))
        except (ValueError, TypeError, IndexError):
            continue
    out.sort(key=lambda t: t[0])
    return out


def _extract_fitness(raw) -> list[int]:
    """Extrae la lista de puntos por jornada de forma tolerante.

    La API puede devolver `fitness` (lista directa de puntos) o `reports`
    (lista de objetos con `points`). Se normaliza a lista de enteros.
    """
    if not raw:
        return []
    points: list[int] = []
    for item in raw:
        if isinstance(item, (int, float)):
            points.append(int(item))
        elif isinstance(item, dict):
            p = item.get("points")
            if isinstance(p, (int, float)):
                points.append(int(p))
            elif isinstance(p, dict):
                # reports puede traer puntos por sistema de puntuación; usa el "1" (Biwenger)
                val = p.get("1")
                if isinstance(val, (int, float)):
                    points.append(int(val))
    return points


def _report_round_id(report) -> int | None:
    """La jornada de un report del detalle de un jugador. Biwenger la da en match.round
    (antes venía en round) y los partidos que no jugó llegan como [] en vez de un objeto."""
    if not isinstance(report, dict):
        return None
    rnd = (report.get("match") or {}).get("round") or report.get("round") or {}
    return int(rnd["id"]) if isinstance(rnd, dict) and rnd.get("id") is not None else None


def _old_version(resp) -> bool:
    """Biwenger responde 400 «Old version» cuando la X-Version ya no vale."""
    return resp.status_code == 400 and "version" in (resp.text or "").lower()
