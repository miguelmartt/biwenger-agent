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
from datetime import datetime, timezone

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

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

logger = logging.getLogger(__name__)

API_BASE = "https://biwenger.as.com/api/v2"
CF_BASE = "https://cf.biwenger.com/api/v2"
COMPETITION = "la-liga"


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
        self._app_version = app_version or settings.biwenger_app_version or "631"
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
        return db_token or settings.biwenger_token or None

    def _sync_token(self) -> None:
        """Si el token de la BD ha cambiado (lo renovaste con /token), lo adopta."""
        active = self._active_token()
        if active and active != self._token:
            self._token = active
            self._manual_token = True
            self._session.headers.update(self._base_headers())

    def update_token(self, new_token: str) -> None:
        """Guarda un token nuevo (renovación en caliente desde Telegram)."""
        from data.db import set_setting
        set_setting("biwenger_token", new_token.strip())
        self._token = new_token.strip()
        self._manual_token = True
        self._session.headers.update(self._base_headers())

    # ------------------------------------------------------------------ #
    # Autenticación
    # ------------------------------------------------------------------ #
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    def login(self) -> None:
        resp = self._session.post(
            f"{API_BASE}/auth/login",
            json={"email": settings.biwenger_email, "password": settings.biwenger_password},
            headers={"X-Lang": "es", "X-Version": self._app_version},
            timeout=15,
        )
        if resp.status_code != 200:
            raise BiwengerAuthError(f"Login falló ({resp.status_code}): {resp.text[:200]}")

        self._token = resp.json().get("token")
        if not self._token:
            raise BiwengerAuthError("Login OK pero no se recibió token")

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

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(requests.RequestException),
    )
    def _request(self, method: str, url: str, **kwargs):
        self._sync_token()  # adopta el token si lo renovaste con /token (otra sesión)
        self._ensure_auth()
        resp = self._session.request(method, url, timeout=15, **kwargs)
        if resp.status_code == 400 and "version" in resp.text.lower():
            raise BiwengerVersionError(
                f"Biwenger rechaza X-Version={self._app_version}. "
                "Actualiza BIWENGER_APP_VERSION al número actual de la app."
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
        data = self._request(
            "GET",
            f"{API_BASE}/competitions/{COMPETITION}/data",
            params={"lang": "es", "score": 5},
        )["data"]

        difficulty_by_team = _difficulty_by_team(data.get("teams", {}))
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
        data = self._request(
            "GET",
            f"{API_BASE}/competitions/{COMPETITION}/data",
            params={"lang": "es", "score": 5},
        )["data"]
        return _team_fixtures(data.get("teams", {}), weeks)

    def get_offers(self) -> list[dict]:
        """Ofertas recibidas por tus jugadores (otros managers quieren comprarte)."""
        data = self._request("GET", f"{API_BASE}/user", params={"fields": "offers"})["data"]
        return data.get("offers", []) or []

    def get_round_fixtures(self) -> list[Fixture]:
        """Partidos de la jornada actual con la dificultad de cada equipo.

        Combina rounds/league (id de la jornada) + rounds/la-liga/{id} (games con
        la dificultad que calcula Biwenger). Base del pronóstico de la quiniela.
        """
        round_id = self.get_current_round_id()
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

    def get_current_round_id(self) -> int | None:
        data = self._request("GET", f"{API_BASE}/rounds/league")["data"]
        return (data.get("round") or {}).get("id")

    def get_my_live_events(self, my_player_ids: list[int]) -> list[LivePlayerEvent]:
        """Eventos (goles, asistencias, tarjetas) de TUS jugadores en la jornada actual.

        Lee el detalle de cada jugador tuyo y saca los eventos del report de la
        jornada en curso. Durante un partido, Biwenger los va rellenando en vivo.
        """
        round_id = self.get_current_round_id()
        if not round_id:
            return []
        events: list[LivePlayerEvent] = []
        for pid in my_player_ids:
            try:
                data = self._request(
                    "GET", f"{API_BASE}/players/{COMPETITION}/{pid}",
                    params={"lang": "es", "fields": "id,name,reports(round,events)"},
                )["data"]
            except Exception as exc:  # noqa: BLE001
                logger.warning("Sin eventos en vivo para %s: %s", pid, exc)
                continue
            name = data.get("name", "?")
            for report in data.get("reports", []) or []:
                if (report.get("round") or {}).get("id") != round_id:
                    continue
                for ev in report.get("events", []) or []:
                    events.append(
                        LivePlayerEvent(
                            player_id=int(pid), player_name=name,
                            event_type=int(ev.get("type", 0)),
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
                    params={"lang": "es", "fields": "id,name,reports(round,points)"},
                )["data"]
            except Exception as exc:  # noqa: BLE001
                logger.warning("Sin puntos de jornada para %s: %s", pid, exc)
                continue
            pts = None
            for report in data.get("reports", []) or []:
                if (report.get("round") or {}).get("id") == round_id:
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
def _difficulty_by_team(teams: dict) -> dict[int, float]:
    """Mapa teamID -> dificultad (0-100) de su próximo partido, según Biwenger.

    Cada equipo trae `nextGames`; el primero es el próximo. Dentro, el partido
    tiene `home` y `away`, y cada lado su propia `difficulty.rating`. Se toma
    la del lado que corresponde a este equipo.
    """
    result: dict[int, float] = {}
    for tid, tdata in (teams or {}).items():
        try:
            team_id = int(tid)
            games = tdata.get("nextGames") or []
            if not games:
                continue
            game = games[0]
            for side in ("home", "away"):
                s = game.get(side) or {}
                if s.get("id") == team_id and isinstance(s.get("difficulty"), dict):
                    rating = s["difficulty"].get("rating")
                    if rating is not None:
                        result[team_id] = float(rating)
                    break
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


def _team_fixtures(teams: dict, weeks: int) -> dict[int, list[UpcomingFixture]]:
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
            me = game.get(side) or {}
            opp = game.get(other) or {}
            rating = None
            diff = me.get("difficulty")
            if isinstance(diff, dict):
                rating = diff.get("rating")
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
