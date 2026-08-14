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
pip install -r requirements.txt
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

Un único mensaje diario con botones (nada de spam). Desde ahí o escribiendo el
comando:

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

En cada sincronización el agente detecta fichajes/ventas NUEVOS de los rivales y
te avisa una sola vez por movimiento (con deduplicación en BD, sin repetir). La
primera vez solo aprende el estado, sin soltar un aviso gigante.

También avisa (deduplicado, solo caídas urgentes) cuando a un jugador tuyo le
conviene salir por precio antes de que baje — el resto lo consultas tú con
`/vender`.

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
