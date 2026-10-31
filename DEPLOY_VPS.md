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
   Ese número es tu `TELEGRAM_CHAT_ID`. (Esa URL lleva tu token: no la pegues en ningún sitio.)

El bot solo atiende a ese chat privado: ignora grupos, a cualquier otra persona y los
mensajes editados.

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
- Deja `DRY_RUN=true` (modo seguro, ya viene así). `BIWENGER_LEAGUE_ID` y
  `BIWENGER_USER_ID` pueden quedarse vacíos: se resuelven solos tras el login.

> El `.env` con tu contraseña vive solo en el VPS. No lo subas a git ni lo compartas.
> Déjalo legible solo para root: `chmod 600 .env`.

## Paso 4 — Prueba de humo (un ciclo manual)

Antes de dejarlo corriendo, un ciclo único para confirmar que hace login, lee tu
liga y te llega el mensaje de Telegram:

```bash
docker compose build
docker compose run --rm app python main.py --once
```

Deberías ver en el log el login OK y recibir en Telegram la alineación sugerida y
el informe de economía. Si Biwenger ha cambiado de versión ("Old version"), el agente
lee la nueva de la web y sigue solo; solo si no pudiera leerla tendrías que subir
`BIWENGER_APP_VERSION` en `.env` al número actual (se ve en la URL del bundle de la web:
`cdn.biwenger.com/app/vXXX/...`).

## Paso 5 — Arrancar en 24/7

```bash
docker compose up -d
docker compose logs -f    # ver que arranca bien; Ctrl+C para salir del log
```

Queda con `restart: unless-stopped`, así que sobrevive a
reinicios del VPS.

El contenedor va endurecido (`docker-compose.yml`): corre como el usuario `app`
(uid/gid 10001), con el sistema de archivos de solo lectura (solo escribe en `/data`, el
volumen de la BD, y en un `/tmp` en memoria), sin capacidades de root,
`no-new-privileges`, 768 MB de memoria como máximo y 128 procesos. Si algún día lo ves
reiniciarse por falta de memoria (`docker compose ps`, `docker stats`), sube `mem_limit`.

---

## Actualizar desde una versión anterior (una sola vez)

Las versiones anteriores corrían como root y el volumen `biwenger_data` es de root. Ahora
el bot corre como el usuario 10001, así que, **una sola vez**, al actualizar hay que pasarle
el volumen. Por SSH en el VPS, en `/root/biwenger-agent`:

```bash
# 1. Trae el código nuevo (como lo hagas: git pull, o sube y descomprime el zip).
# 2. Construye la imagen nueva y para el bot.
docker compose build
docker compose down

# 3. Comprueba el nombre del volumen (normalmente biwenger-agent_biwenger_data).
docker volume ls | grep biwenger_data

# 4. Pasa el volumen al usuario del bot y ciérralo a los demás (con la propia imagen del bot,
#    sin descargar nada).
docker run --rm --user root -v biwenger-agent_biwenger_data:/data biwenger-agent:latest \
  sh -c 'chown -R 10001:10001 /data && chmod -R u=rwX,go= /data'

# 5. Revisa el .env: DRY_RUN y, si quieres, AUTO_CLAUSE_MAX_PER_DAY (ver .env.example).
# 6. Arranca y mira que va bien.
docker compose up -d
docker compose logs -f
```

Si te saltas el paso 4, el bot no puede escribir en la BD y falla al arrancar
(`unable to open database file` o `attempt to write a readonly database`): haz el paso 4 y
`docker compose up -d` otra vez. Una instalación nueva no lo necesita: Docker crea el volumen
ya con el dueño bueno.

Tras actualizar:

- Los botones de confirmar de mensajes viejos ya no hacen nada (ahora cada confirmación es
  de un solo uso y caduca a los 10 minutos): pide la acción otra vez.
- Las auto-pujas que marcaste con la versión anterior se quitan solas (te avisa el bot),
  porque no estaban atadas a un mercado concreto. Márcalas otra vez si aún las quieres.
- La BD recibe sola las tablas y columnas nuevas al arrancar.

## Si tus logs se han compartido: rota el token del bot

`docker logs` guardaba en claro el token del bot de Telegram (iba dentro de la URL de
algunos errores). Ahora se tapa con `***`, pero si alguna vez has copiado, pegado o subido
esos logs, ese token hay que darlo por perdido:

1. En Telegram, **@BotFather** → `/revoke` → elige tu bot. El token viejo deja de valer y te
   da uno nuevo.
2. Pon el nuevo en `TELEGRAM_BOT_TOKEN` del `.env`.
3. `docker compose up -d --force-recreate`. Recrear el contenedor también borra su log
   viejo (el que tenía el token).

Si en esos logs aparece la contraseña o el token de Biwenger, cámbialos también (contraseña
nueva en Biwenger; o un token nuevo con `/token`).

---

## Operación

```bash
docker compose logs -f              # ver logs en vivo
docker compose restart              # reiniciar tras cambiar el .env
docker compose down                 # parar el agente
docker compose up -d --build        # actualizar tras cambiar el código
```

Los logs rotan solos: como mucho 3 archivos de 10 MB (`logging` en `docker-compose.yml`).

**Backup del histórico:** la BD SQLite vive en el volumen `biwenger_data`. Si quieres
incluirla en tus copias de seguridad, el archivo está dentro del volumen Docker
(`/var/lib/docker/volumes/biwenger-agent_biwenger_data/_data/biwenger.db`). Ojo: guarda en
claro el token de Biwenger que renuevas con `/token`, así que trata la copia como un secreto
(el archivo ya es solo de su dueño, 0600).

## Cuándo pasar a pujas reales

Cuando el agente lleve unos días avisándote bien y confíes en sus sugerencias,
validamos las acciones de escritura con una puja pequeña real y, si va fino,
cambiamos `DRY_RUN=false`. Hasta entonces, el agente no toca tu cuenta.
