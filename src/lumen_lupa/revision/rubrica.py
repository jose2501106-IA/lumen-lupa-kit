"""Rúbrica de la revisión (REVIEW_PROTOCOL.md §7 y §8; contrato T9): respuestas, etiqueta derivada y CSV.

Respuestas: q1 ∈ {yes, no, unclear}; q2, q3 y q4 ∈ {yes, no}. También se aceptan sí/si, no e incierto, sin
importar mayúsculas ni espacios alrededor; se guardan en la forma del protocolo (yes, no, unclear).

La etiqueta sale de las cuatro respuestas, en este orden (§7):

1. ``falso_positivo`` si q1 = no, q3 = yes o q4 = yes;
2. ``consistente`` si q1 = yes, q2 = yes, q3 = no y q4 = no;
3. ``indeterminado`` en cualquier otro caso.

CSV de un revisor (UTF-8, con o sin BOM, con encabezado): ``alerta_id, q1, q2, q3, q4, evidencia, notas``,
en cualquier orden y sin columnas de más. Una fila por alerta; un id repetido es un error. ``evidencia`` y
``notas`` son texto libre: se exige la columna, pero su contenido no se copia al resultado (puede tener
cuentas o enlaces que el §11 deja privados).

CSV de resoluciones (§8; mismo formato): ``alerta_id, q1, q2, q3, q4, resolucion``, con las respuestas ya
resueltas de un desacuerdo y, en ``resolucion``, una línea que explica cómo se resolvió. Ese texto sí va al
resultado, porque el §8 pide listar cada desacuerdo con su resolución; por eso no puede ir vacío ni llevar
nada con forma de cuenta, contrato o llave de Stellar.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from lumen_lupa.revision.alertas import validar_id
from lumen_lupa.revision.comun import ErrorRevision

COLUMNAS: tuple[str, ...] = ("alerta_id", "q1", "q2", "q3", "q4", "evidencia", "notas")
COLUMNAS_RESOLUCIONES: tuple[str, ...] = ("alerta_id", "q1", "q2", "q3", "q4", "resolucion")
PREGUNTAS: tuple[str, ...] = ("q1", "q2", "q3", "q4")
SI, NO, INCIERTO = "yes", "no", "unclear"
CONSISTENTE, FALSO_POSITIVO, INDETERMINADO = "consistente", "falso_positivo", "indeterminado"
ETIQUETAS: tuple[str, ...] = (CONSISTENTE, FALSO_POSITIVO, INDETERMINADO)

_SINONIMOS = {"yes": SI, "sí": SI, "si": SI, "no": NO, "unclear": INCIERTO, "incierto": INCIERTO}
# Forma de una cuenta (G…), un contrato (C…), una llave secreta (S…) o una cuenta multiplexada (M…).
_FORMA_STELLAR = re.compile(r"\b(?:[GCS][A-Z2-7]{55}|M[A-Z2-7]{68})\b")


def normalizar_respuesta(valor: str, pregunta: str) -> str:
    """La respuesta en la forma del protocolo; error si no es válida para esa pregunta."""
    respuesta = _SINONIMOS.get(unicodedata.normalize("NFC", valor).strip().casefold())
    permitidas = (SI, NO, INCIERTO) if pregunta == "q1" else (SI, NO)
    if respuesta not in permitidas:
        opciones = "yes, no o unclear (o sí, no, incierto)" if pregunta == "q1" else "yes o no (o sí, no)"
        raise ErrorRevision(f"{pregunta} = {valor!r} no es una respuesta válida; usa {opciones}")
    return respuesta


def etiqueta(q1: str, q2: str, q3: str, q4: str) -> str:
    """Etiqueta del §7 a partir de las cuatro respuestas, ya normalizadas."""
    if q1 == NO or q3 == SI or q4 == SI:
        return FALSO_POSITIVO
    if q1 == SI and q2 == SI and q3 == NO and q4 == NO:
        return CONSISTENTE
    return INDETERMINADO


@dataclass(frozen=True, slots=True)
class Respuesta:
    """Las cuatro respuestas de un revisor a una alerta (ya normalizadas) y su línea en el CSV."""

    alerta_id: str
    q1: str
    q2: str
    q3: str
    q4: str
    linea: int

    @property
    def etiqueta(self) -> str:
        return etiqueta(self.q1, self.q2, self.q3, self.q4)

    def como_dict(self) -> dict[str, str]:
        return {"q1": self.q1, "q2": self.q2, "q3": self.q3, "q4": self.q4, "label": self.etiqueta}


@dataclass(frozen=True, slots=True)
class Resolucion:
    """Las respuestas ya resueltas de un desacuerdo (§8) y la línea que explica la resolución."""

    respuesta: Respuesta
    texto: str

    @property
    def alerta_id(self) -> str:
        return self.respuesta.alerta_id

    @property
    def etiqueta(self) -> str:
        return self.respuesta.etiqueta


def _leer_csv(ruta: Path, que: str, columnas: tuple[str, ...]) -> list[tuple[int, str, dict[str, str]]]:
    """Filas no vacías de un CSV con exactamente estas columnas: (línea, dónde para los errores, celdas)."""
    try:
        texto = Path(ruta).read_bytes().decode("utf-8-sig")
    except OSError as e:
        raise ErrorRevision(f"no pude leer {que} ({ruta}): {e.strerror}") from e
    except UnicodeDecodeError as e:
        raise ErrorRevision(f"{que} ({ruta}) no está en UTF-8") from e
    lector = csv.reader(io.StringIO(texto, newline=""))
    filas = []
    try:
        encabezado = [c.strip() for c in next(lector, [])]
        if not encabezado:
            raise ErrorRevision(f"{que} ({ruta}) está vacío: falta el encabezado {', '.join(columnas)}")
        if sorted(encabezado) != sorted(columnas):
            raise ErrorRevision(
                f"{que} ({ruta}): el encabezado debe tener exactamente las columnas {', '.join(columnas)}; "
                f"tiene {', '.join(encabezado)}"
            )
        for fila in lector:
            if not any(celda.strip() for celda in fila):
                continue  # renglón vacío
            donde = f"{que} ({ruta}), línea {lector.line_num}"
            if len(fila) != len(encabezado):
                raise ErrorRevision(f"{donde}: tiene {len(fila)} columnas y el encabezado {len(encabezado)}")
            filas.append((lector.line_num, donde, dict(zip(encabezado, fila, strict=True))))
    except csv.Error as e:
        raise ErrorRevision(f"{que} ({ruta}) no es un CSV válido: {e}") from e
    return filas


def _respuesta(linea: int, donde: str, celdas: dict[str, str], vistas: dict[str, int]) -> Respuesta:
    """La respuesta de una fila; `vistas` (id -> línea) detecta ids repetidos en el mismo archivo."""
    id_alerta = validar_id(celdas["alerta_id"].strip(), donde)
    if id_alerta in vistas:
        raise ErrorRevision(
            f"{donde}: {id_alerta} ya tiene respuesta en la línea {vistas[id_alerta]}; "
            "cada alerta lleva una sola"
        )
    vistas[id_alerta] = linea
    try:
        q1, q2, q3, q4 = (normalizar_respuesta(celdas[p], p) for p in PREGUNTAS)
    except ErrorRevision as e:
        raise ErrorRevision(f"{donde} ({id_alerta}): {e}") from e
    return Respuesta(id_alerta, q1, q2, q3, q4, linea)


def leer_respuestas(ruta: Path, que: str) -> dict[str, Respuesta]:
    """Respuestas de un revisor por id, en el orden del archivo. `que` nombra el archivo en los errores."""
    vistas: dict[str, int] = {}
    respuestas = (
        _respuesta(linea, donde, celdas, vistas) for linea, donde, celdas in _leer_csv(ruta, que, COLUMNAS)
    )
    return {r.alerta_id: r for r in respuestas}


def leer_resoluciones(ruta: Path, que: str) -> dict[str, Resolucion]:
    """Resoluciones de los desacuerdos (§8) por id, en el orden del archivo."""
    vistas: dict[str, int] = {}
    resoluciones: dict[str, Resolucion] = {}
    for linea, donde, celdas in _leer_csv(ruta, que, COLUMNAS_RESOLUCIONES):
        respuesta = _respuesta(linea, donde, celdas, vistas)
        texto = " ".join(celdas["resolucion"].split())
        if not texto:
            raise ErrorRevision(f"{donde} ({respuesta.alerta_id}): falta el texto de la columna resolucion")
        if _FORMA_STELLAR.search(texto):
            raise ErrorRevision(
                f"{donde} ({respuesta.alerta_id}): la resolución tiene algo con forma de cuenta, contrato o "
                "llave de Stellar; ese texto va al resultado y el §11 deja privadas las cuentas"
            )
        resoluciones[respuesta.alerta_id] = Resolucion(respuesta, texto)
    return resoluciones
