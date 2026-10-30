# Biwenger Agent

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)
![Tests](https://img.shields.io/badge/tests-65%20passing-brightgreen)
![Telegram](https://img.shields.io/badge/Telegram-bot-26A5E4?logo=telegram&logoColor=white)

[English](./README.md) · **🌐 Español**

Agente personal para optimizar tu liga de fantasy [Biwenger](https://biwenger.as.com):
alineación óptima semanal con capitán inteligente, pujas y "sniping" de última
hora, gestión de economía/mercado, planificador de calendario, diferenciales y
espía de rivales — todo controlado desde un bot de Telegram con confirmación por
botón. Incluye un predictor de puntos que se **auto-calibra** aprendiendo de sus
propios aciertos.

> **Filosofía:** el agente *recomienda*, tú *decides*. Ninguna acción real (fichar,
> pujar, cláusula, vender) se ejecuta sin tu confirmación explícita. Las pocas
> automatizaciones opcionales están **apagadas por defecto**. Arranca en modo
> seguro (`DRY_RUN=true`), que solo simula las acciones de escritura.

Ver [`ARCHITECTURE.md`](./ARCHITECTURE.md) para el diseño completo y
[`DEPLOY_VPS.md`](./DEPLOY_VPS.md) para desplegarlo 24/7 con Docker.

## Sobre la API de Biwenger

Biwenger **no** tiene API pública, así que `core/client.py` está construido a
partir de ingeniería inversa de las peticiones de la web (headers, payloads),
apoyándose también en proyectos de la comunidad como
[pablopb3/biwenger-api](https://github.com/pablopb3/biwenger-api) y
[biwenger-java-api](https://github.com/jbujalance/biwenger-java-api). Como la API
no oficial puede cambiar sin aviso, si algo deja de funcionar revisa los
endpoints con las devtools del navegador (pestaña Network). Todo el acceso HTTP
está aislado en un único fichero (`core/client.py`) precisamente para que ese sea
el único sitio a tocar.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock   # las versiones exactas que usa la imagen de Docker
cp .env.example .env   # rellena tus credenciales y IDs de liga
python main.py --once  # ejecuta un ciclo manual (sin automatizar pujas todavía)
```

## Módulos

- `core/` — cliente HTTP de Biwenger (login, plantilla, mercado, pujas) y modelos de datos.
- `lineup/` — predicción de puntos por jugador + solver de alineación óptima (PuLP).
- `bidding/` — cálculo de puja ideal/máxima + motor de "sniping" en el cierre de mercado.
- `economy/` — análisis de tendencias, sugerencias de compra/venta, simulación de presupuesto.
- `bot/` — notificaciones e interacción vía Telegram.
- `scheduler/` — orquestación de todos los jobs periódicos (APScheduler).
- `data/` — modelos de base de datos (SQLAlchemy) para guardar histórico de precios/puntos.

## Comandos del bot (Telegram)

Cada mañana a las 9:00 llega **un parte** con lo importante ya dentro: tu puesto en la
liga y la distancia al líder, cuándo empieza la jornada, tu saldo y el valor de la
plantilla, lesiones, ofertas, a quién conviene vender ya y el capitán sugerido. Trae
solo dos botones (alineación y menú). El menú tiene seis (parte, alineación, mercado,
liga, jornada y «más»), y al navegar se edita el mismo mensaje en vez de amontonar otros.

- `/parte` — el parte del día, ahora.
- `/liga` — la clasificación, con la distancia a los de arriba y abajo.
- `/jornada` — los partidos con su hora y qué jugadores tuyos juegan en cada uno.
- `/menu` — todo con botones.
- `/alineacion` — el once óptimo de la jornada, con **capitán inteligente**: combina puntos esperados (ya incluye la dificultad del partido) con un pequeño bonus si es diferencial (casi nadie más lo tiene → capitanearlo renta más para subir puestos).
- `/mercado` — chollos, tendencias de precio y cláusulas (con botones para fichar/vender).
- `/optimizar` — la mejor jugada: vende X y ficha Z para subir puntos, **mirando las próximas jornadas** (premia a quien tiene calendario fácil por delante).
- `/vender` — timing de ventas: a quién soltar YA porque su precio ha hecho techo o baja, y/o le viene una racha de calendario dura (con botón de vender).
- `/calendario` — dificultad de las próximas 5 jornadas de tu equipo: separa rachas fáciles (a quién capitanear/mantener) de rachas duras (a quién rotar o vender antes de que baje).
- `/diferenciales` — tus jugadores buenos que casi nadie más tiene en la liga (tu ventaja) y "gemas libres": jugadores buenos que NADIE tiene y están en el mercado libre.
- `/quiniela` — pronóstico 1X2 de la jornada (y se guarda para evaluarlo luego).
- `/resumen` — cómo fue tu equipo en la última jornada + aprendizaje del modelo.
- `/objetivos` — tus auto-pujas (sniping) programadas.
- `/auto` — activa/desactiva las automatizaciones (ver abajo).
- `/aprendizaje` — estado del auto-calibrado del predictor.
- `/token <valor>` — renueva el token de Biwenger en caliente, sin tocar el servidor.
- `/equipo`, `/help`.

### Automatizaciones con límites (`/auto`)

Dos interruptores, **ambos apagados por defecto**. El agente nunca actúa sin que
los enciendas tú:

- **Alineación automática** (riesgo cero): pone tu once óptimo una vez por jornada,
  ~4h antes del primer partido. Es reversible: puedes cambiarla antes del cierre.
- **Subir cláusula automática**: blinda a tus cracks vulnerables (cláusula baja)
  subiéndola una sola vez por jugador. Gasta saldo y usa un endpoint de escritura
  aún no confirmado del todo — vigílalo la primera vez que lo actives.

### Espía de rivales (proactivo)

Una vez al día (15:00) el agente te manda un **único resumen** con todos los
fichajes/ventas de tus rivales de ese día, agrupados en un solo mensaje (con
deduplicación en BD, sin repetir) — en vez de gotear un mensaje por movimiento.
La primera vez solo aprende el estado, sin soltar un aviso gigante.

A quién conviene vender antes de que baje de precio va en el parte de la mañana (solo
las caídas urgentes); el análisis completo está en `/vender`.

**Chequeo de reglas de liga (opcional).** Si creas `config/league_rules.py` (mira
`config/league_rules.example.py`; está en .gitignore, así que tus reglas quedan
privadas), el agente marca a los rivales que incumplan las reglas acordadas de tu
liga — cláusulas por debajo del mínimo, capitán por encima de un tope de valor,
demasiados jugadores del mismo club — dentro de ese mismo resumen diario. También
puedes pedirlo cuando quieras con `/resumendiario`.

## Se mantiene en pie solo

La API no oficial cambia sin avisar, así que el agente está hecho para aguantarlo:

- **Versión de la app.** Cuando Biwenger saca app nueva rechaza la `X-Version` vieja con
  "Old version". El agente lee el número nuevo de biwenger.as.com, se queda con él (lo
  guarda en la base de datos) y te avisa por Telegram. `BIWENGER_APP_VERSION` del `.env`
  es solo el valor de partida.
- **Jornadas.** La jornada que toca sale de `activeEvents` de la competición, no de
  `rounds/league` (que entre jornadas apunta a la ya terminada o a una aplazada).
- **Avisos de fallo.** Si un trabajo programado deja de funcionar te llega un mensaje
  cuando se rompe y otro cuando vuelve, nunca uno por ejecución. Si caduca el token, un
  único aviso para todos.
- **Dependencias fijadas.** La imagen instala `requirements.lock` (versiones exactas
  probadas juntas), así que una librería que se actualiza sola no lo puede romper.
  `.dockerignore` deja el `.env` y la base de datos fuera de la imagen.

Los tests, en un contenedor de usar y tirar, nunca contra tu base de datos real (la vacían):

```bash
docker build -t biwenger-agent:test .
docker run --rm biwenger-agent:test sh -c "pip install -q pytest && python -m pytest -q"
```

## Aviso importante

Automatizar tu cuenta puede entrar en conflicto con los términos de uso de
Biwenger. El riesgo real es que te baneen la cuenta, no uno legal, pero:

- Empieza en modo **dry-run** (solo notifica, no actúa) hasta que confíes en la lógica.
- Añade jitter aleatorio a los tiempos de las pujas automáticas para no parecer tráfico de bot perfecto.
- No hagas polling agresivo del mercado; una vez cada pocos minutos es más que suficiente salvo en los últimos minutos antes del cierre.

Este proyecto es una herramienta personal y educativa, sin relación con Biwenger
ni con AS. "Biwenger" es marca de sus respectivos propietarios.

## Licencia

[MIT](./LICENSE) © 2026 VerticeDev. Úsalo, modifícalo y compártelo libremente;
se agradece atribución. Sin garantías (ver la licencia).
