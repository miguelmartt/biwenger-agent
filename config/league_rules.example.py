"""Reglas INTERNAS de tu liga para el detector de infracciones.

Este fichero es una PLANTILLA pública (sin datos reales). Copia este archivo a
`config/league_rules.py` y ajusta los valores a tu reglamento. `league_rules.py`
está en `.gitignore`, así que tus reglas concretas NO se suben a GitHub.

Si `config/league_rules.py` no existe, el detector de infracciones queda apagado
(el resto del agente funciona igual).
"""

# Activa/desactiva por completo el chequeo de reglas.
RULE_CHECK_ENABLED = True

# Capitán: valor de mercado máximo permitido (€). None = sin límite.
CAPTAIN_MAX_VALUE = 7_500_000

# Máximo de jugadores del mismo club de LaLiga en el once titular.
MAX_PLAYERS_PER_CLUB = 3

# Chequeo de cláusulas por debajo del mínimo. En Biwenger la cláusula base la fija
# la configuración de la liga (no cada manager), así que suele marcar a casi todos
# por un tema de config, no por trampas. Ponlo en True solo si quieres ese aviso.
CLAUSE_CHECK_ENABLED = False

# Tramos de cláusula MÍNIMA por valor de mercado (VM) del jugador:
# lista de (límite_superior_VM, multiplicador). Se aplica el primer tramo cuyo
# límite superior sea >= al VM del jugador.
CLAUSE_MIN_TIERS = [
    (2_000_000, 2.5),
    (10_000_000, 2.0),
    (float("inf"), 1.5),
]

# Las cláusulas empiezan a exigirse DESPUÉS de esta jornada (0 = desde el principio).
CLAUSE_RULES_FROM_ROUND = 2

# "Solo casos gordos": muchas cláusulas quedan por debajo del mínimo de forma
# sistemática (el valor sube con el tiempo). Para no soltar un tocho, solo se
# avisa de las flagrantes: hueco respecto al mínimo >= CLAUSE_MIN_GAP €, y como
# mucho CLAUSE_MAX_PER_MANAGER por rival. Pon CLAUSE_MIN_GAP = 0 para avisar de todas.
CLAUSE_MIN_GAP = 3_000_000
CLAUSE_MAX_PER_MANAGER = 3

# Zona de castigo: multa en € por PUESTO de la clasificación de la jornada.
# Ej. liga de 10: los 3 últimos pagan. En empates dentro de la zona, la suma de
# las multas de los puestos afectados se reparte a partes iguales. Es ORIENTATIVO
# (reconstruido; confírmalo en la app antes de cobrar de verdad).
PUNISHMENT_ENABLED = True
PUNISHMENT_BY_POSITION = {8: 1, 9: 2, 10: 3}
# Reinicio de 2ª vuelta y margen sin sanciones tras él (0 para desactivar).
RESET_ROUND = 19
PUNISHMENT_MARGIN_AFTER_RESET = 3
