# Despliegue en un VPS (modo seguro)

Guía para dejar el agente corriendo 24/7 en tu VPS, en **modo solo lectura**
(`DRY_RUN=true`): lee tu liga, te avisa por Telegram con la alineación óptima
y el informe de economía, y guarda histórico de precios. **No puja ni cambia
nada en tu cuenta.** Las pujas reales son una fase posterior.

Todos los comandos se ejecutan **por SSH en el VPS** (`ssh root@TU_VPS_IP`).
El agente va aislado en su propio contenedor Docker, sin tocar el resto de tus
servicios, y sin abrir ningún puerto (solo hace peticiones salientes → no hay
que tocar el firewall).

---

## Paso 1 — Crear el bot de Telegram (una sola vez)

1. En Telegram, habla con **@BotFather** → `/newbot` → dale un nombre. Te dará un **token**
   (algo como `123456789:AAE...`). Guárdalo.
2. Escribe un mensaje cualquiera a tu bot nuevo (para abrir la conversación).
3. Para sacar tu **chat_id**: abre en el navegador
   `https://api.telegram.org/bot<TU_TOKEN>/getUpdates` y busca `"chat":{"id":XXXXX`.
   Ese número es tu `TELEGRAM_CHAT_ID`.

## Paso 2 — Subir el proyecto al VPS

Descomprime `biwenger-agent.zip` y súbelo a `/root/biwenger-agent`. Desde tu equipo:

```bash
scp biwenger-agent.zip root@TU_VPS_IP:/root/
```

Y ya en el VPS:

```bash
cd /root && unzip biwenger-agent.zip && cd biwenger-agent
```

## Paso 3 — Configurar el `.env`

```bash
cp .env.example .env
nano .env
```

Rellena:

- `BIWENGER_EMAIL` y `BIWENGER_PASSWORD` — tus credenciales de Biwenger.
- `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` — los del paso 1.
- Deja `DRY_RUN=true` (modo seguro) y los `BIWENGER_LEAGUE_ID` / `BIWENGER_USER_ID`
  que ya vienen puestos.

> El `.env` con tu contraseña vive solo en el VPS. No lo subas a git ni lo compartas.

## Paso 4 — Prueba de humo (un ciclo manual)

Antes de dejarlo corriendo, un ciclo único para confirmar que hace login, lee tu
liga y te llega el mensaje de Telegram:

```bash
docker compose build
docker compose run --rm app python main.py --once
```

Deberías ver en el log el login OK y recibir en Telegram la alineación sugerida y
el informe de economía. Si ves un error **"Old version"**, edita `.env` y sube
`BIWENGER_APP_VERSION` al número actual (se ve en la URL del bundle de la web:
`cdn.biwenger.com/app/vXXX/...`).

## Paso 5 — Arrancar en 24/7

```bash
docker compose up -d
docker compose logs -f    # ver que arranca bien; Ctrl+C para salir del log
```

Queda con `restart: unless-stopped`, así que sobrevive a
reinicios del VPS.

---

## Operación

```bash
docker compose logs -f              # ver logs en vivo
docker compose restart              # reiniciar tras cambiar el .env
docker compose down                 # parar el agente
docker compose up -d --build        # actualizar tras cambiar el código
```

**Backup del histórico:** la BD SQLite vive en el volumen `biwenger_data`. Si quieres
incluirla en tus copias de seguridad, el archivo está dentro del volumen Docker
(`/var/lib/docker/volumes/biwenger-agent_biwenger_data/_data/biwenger.db`).

## Cuándo pasar a pujas reales

Cuando el agente lleve unos días avisándote bien y confíes en sus sugerencias,
validamos las acciones de escritura con una puja pequeña real y, si va fino,
cambiamos `DRY_RUN=false`. Hasta entonces, el agente no toca tu cuenta.
