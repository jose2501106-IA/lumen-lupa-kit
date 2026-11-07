"""Herramienta pública del protocolo de revisión con datos reales (REVIEW_PROTOCOL.md, versión 1.0).

Contrato T9. Ejecuta el protocolo tal como está escrito, solo con la biblioteca estándar y sin red:

- ``huella``: sha256 de la lista de alertas que se congela (§4).
- ``muestra``: por señal, censo o sorteo por peso con la semilla externa (§5 y §6), y las alertas que
  también contesta el segundo revisor (§8).
- ``puntaje``: etiqueta de cada alerta a partir de las cuatro respuestas (§7), acuerdo entre revisores con
  las respuestas tal como se dieron y puntaje con las respuestas resueltas de cada desacuerdo (§8), regla de
  aprobación por señal (§9) y veredicto del entregable 1 (§1).

Uso: ``python -m lumen_lupa.revision {huella,muestra,puntaje} --help``.
"""

from lumen_lupa.revision.alertas import Alerta, ListaAlertas, huella, leer_alertas, validar_lista
from lumen_lupa.revision.comun import PROTOCOLO, ErrorRevision
from lumen_lupa.revision.muestreo import N_MUESTRA, Muestra, armar_muestra, leer_muestra, validar_muestra
from lumen_lupa.revision.puntaje import Resultado, puntuar
from lumen_lupa.revision.rubrica import Resolucion, Respuesta, etiqueta, leer_resoluciones, leer_respuestas

__all__ = [
    "N_MUESTRA",
    "PROTOCOLO",
    "Alerta",
    "ErrorRevision",
    "ListaAlertas",
    "Muestra",
    "Resolucion",
    "Respuesta",
    "Resultado",
    "armar_muestra",
    "etiqueta",
    "huella",
    "leer_alertas",
    "leer_muestra",
    "leer_resoluciones",
    "leer_respuestas",
    "puntuar",
    "validar_lista",
    "validar_muestra",
]
