# Arquitectura

## Visión general

```
                     ┌─────────────────────┐
                     │   scheduler/jobs.py   │   (APScheduler, corre 24/7 en el VPS)
                     └──────────┬───────────┘
          ┌────────────────────┼─────────────────────┐
          ▼                    ▼                      ▼
   ┌─────────────┐      ┌─────────────┐        ┌──────────────┐
   │  lineup/     │      │  bidding/    │        │  economy/     │
   │  optimizer   │      │  sniper      │        │  analyzer     │
   └──────┬───────┘      └──────┬───────┘        └──────┬────────┘
          │                     │                        │
          └──────────┬──────────┴────────────┬───────────┘
                      ▼                       ▼
              ┌───────────────┐       ┌───────────────┐
              │ core/client.py │◄─────►│  Biwenger.com  │
              └───────┬────────┘       └───────────────┘
                      │
                      ▼
              ┌───────────────┐        ┌────────────────┐
              │  data/db.py    │◄──────►│  bot/telegram   │ (notificaciones)
              │  (Postgres)    │        └────────────────┘
              └───────────────┘
```

Todo pasa por `core/client.py`, que es la única pieza que habla HTTP con
Biwenger. Los tres módulos de negocio (lineup, bidding, economy) son puro
Python/lógica y no saben nada de HTTP — así, si Biwenger cambia su API, solo
tocas un archivo.

## Módulos

### `core/` — Cliente Biwenger

- `client.py`: login (guarda el token Bearer), `get_my_team()`,
  `get_my_money()`, `get_market()`, `place_bid()`, `set_lineup()`,
  `get_received_offers()`, `accept_offer()`.
- `models.py`: dataclasses `Player`, `TeamSlot`, `MarketPlayer`, `Bid`.

Nota sobre autenticación: Biwenger usa un login por email/contraseña que
devuelve un token, y las peticiones posteriores necesitan además headers de
contexto (usuario, liga, versión de la app) — estos tres IDs se sacan
mirando las peticiones de red en el navegador la primera vez y se guardan en
`.env`. No hay refresco automático de esto todavía: si Biwenger invalida el
token, `client.py` debe relanzar login.

### `lineup/` — Alineación óptima

- `predictor.py`: da una puntuación esperada por jugador para la próxima
  jornada. V1 = media ponderada de las últimas N jornadas (más peso a las
  recientes) + penalización si hay duda de lesión/sanción. Está pensado para
  poder sustituirse después por un modelo de ML sin tocar el resto del
  sistema (respeta la misma interfaz `predict(player_id) -> float`).
- `optimizer.py`: problema de programación lineal entera con PuLP.
  Variables binarias `x_i` (titular sí/no) por jugador de tu plantilla,
  maximiza `sum(x_i * puntos_esperados_i)` sujeto a: exactamente 11
  titulares, cupos mínimos/máximos por posición según la formación elegida
  (o probando varias formaciones y quedándote con la de mayor suma), y solo
  jugadores disponibles (no lesionados/sancionados si se marca así).

### `bidding/` — Pujas automáticas

- `valuation.py`: calcula la "puja ideal" y la "puja máxima" para un
  jugador objetivo combinando: precio de mercado actual, tendencia de
  precio de los últimos días, hueco real en tu plantilla (¿lo necesitas o
  es capricho?), y presupuesto disponible tras reservar un colchón mínimo
  configurable.
- `sniper.py`: dado un jugador objetivo y un `max_bid`, programa el envío
  de la puja para pocos segundos antes del cierre del mercado (con jitter
  aleatorio ±unos segundos) para minimizar el tiempo que otros managers
  tienen para contrapujar. Corre como job de APScheduler disparado por el
  scheduler central, no como proceso aparte, para que todo comparta el
  mismo log y estado.

### `economy/` — Gestión de mercado y presupuesto

- `analyzer.py`: cada día genera un informe: jugadores de tu plantilla en
  tendencia bajista (candidatos a vender ya), jugadores del mercado en
  tendencia alcista fuera de tu presupuesto actual (para avisarte con
  tiempo), y una simulación simple de "si vendo X compro Y" con el nuevo
  balance resultante.

### `bot/` — Interfaz de notificaciones

Bot de Telegram (reutilizando el patrón de
[Poppeyye/biwenger_bot](https://github.com/Poppeyye/biwenger_bot)): manda el
resumen diario de economía, avisa cuando el sniper ha pujado y si ha ganado
o perdido la puja, y permite comandos manuales (`/alineacion`, `/mercado`,
`/pujar <jugador> <cantidad>`) para cuando prefieras decidir tú en vez de
dejarlo en automático.

### `scheduler/` — Orquestación

Un único proceso APScheduler con los jobs:

| Job | Frecuencia |
|---|---|
| Sincronizar plantilla/mercado/precios a la BD | cada 30–60 min |
| Sugerir alineación óptima | día antes del cierre de alineaciones |
| Informe de economía | diario |
| Motor de pujas (sniper) | pocos segundos antes de cada cierre de mercado |

### `data/` — Persistencia

Postgres (o SQLite para desarrollo) vía SQLAlchemy. Guardamos snapshots de
precios y puntos por jornada porque Biwenger no expone histórico largo vía
API — si no lo guardamos nosotros mismos desde el primer día, lo perdemos.

## Despliegue (VPS / Raspberry Pi 24/7)

`docker-compose.yml` levanta dos servicios: `app` (el proceso Python con el
scheduler) y `db` (Postgres). Para pujar con precisión de segundos en el
cierre de mercado necesitas que el proceso esté vivo permanentemente, de ahí
la preferencia por VPS/Raspberry frente a cron serverless (que suele tener
menos precisión en el disparo exacto).

Recomendación operativa: `restart: unless-stopped` en docker-compose +
alerta por Telegram si el proceso se cae (heartbeat job que avisa si no ha
corrido en más de X minutos).

## Roadmap sugerido

1. **Fase 0** — `core/client.py` funcionando de verdad contra tu cuenta
   (login + leer plantilla/mercado). Sin escribir nada todavía.
2. **Fase 1** — `lineup/` completo: alineación óptima sugerida cada semana
   (todavía manual: tú la aplicas a mano).
3. **Fase 2** — `economy/analyzer.py` + bot de Telegram con el informe
   diario. Sigue sin tocar nada automáticamente.
4. **Fase 3** — Automatizar `set_lineup()` (bajo riesgo, no toca dinero).
5. **Fase 4** — `bidding/sniper.py` en modo dry-run (calcula y notifica qué
   pujaría, pero no puja de verdad).
6. **Fase 5** — Activar pujas automáticas reales, empezando con un
   presupuesto de prueba pequeño y límites conservadores.
