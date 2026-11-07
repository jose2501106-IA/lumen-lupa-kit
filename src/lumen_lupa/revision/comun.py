"""Piezas comunes de `lumen_lupa.revision` (REVIEW_PROTOCOL.md, versión 1.0; contrato T9).

Solo biblioteca estándar: el error con mensaje claro, la lectura estricta de JSON, el JSON canónico de las
huellas, sumas exactas de decimales y decimales en texto sin redondeo.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from decimal import MAX_EMAX, MAX_PREC, MIN_EMIN, Context, Decimal, Inexact
from fractions import Fraction
from pathlib import Path

# Versión de REVIEW_PROTOCOL.md que implementa este paquete. La muestra y el resultado la llevan escrita.
PROTOCOLO = "1.0"

# Contexto para sumar pesos sin redondeo: la suma de decimales finitos es exacta con esta precisión, y si
# algo se redondeara, Inexact detiene el cálculo en vez de seguir con un número distinto.
_EXACTO = Context(prec=MAX_PREC, Emax=MAX_EMAX, Emin=MIN_EMIN, traps=[Inexact])


class ErrorRevision(ValueError):
    """Entrada faltante o inválida. El mensaje dice qué está mal y, cuando se puede, cómo corregirlo."""


def sha256_hex(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def json_canonico(valor: object) -> bytes:
    """JSON canónico en UTF-8: claves ordenadas, separadores sin espacios, sin NaN ni infinitos."""
    texto = json.dumps(valor, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return texto.encode("utf-8")


def _sin_claves_repetidas(pares: list[tuple[str, object]]) -> dict[str, object]:
    objeto: dict[str, object] = {}
    for clave, valor in pares:
        if clave in objeto:
            raise ErrorRevision(f"la clave {clave!r} aparece dos veces en el mismo objeto; es ambiguo")
        objeto[clave] = valor
    return objeto


def _rechazar_constante(nombre: str) -> object:
    raise ErrorRevision(f"{nombre} no es JSON estándar")


def leer_json(ruta: Path, que: str) -> object:
    """Contenido de un archivo JSON en UTF-8, sin claves repetidas ni NaN o Infinity."""
    try:
        crudo = Path(ruta).read_bytes()
    except OSError as e:
        raise ErrorRevision(f"no pude leer {que} ({ruta}): {e.strerror}") from e
    try:
        return json.loads(
            crudo.decode("utf-8-sig"),
            object_pairs_hook=_sin_claves_repetidas,
            parse_constant=_rechazar_constante,
        )
    except UnicodeDecodeError as e:
        raise ErrorRevision(f"{que} ({ruta}) no está en UTF-8") from e
    except json.JSONDecodeError as e:
        raise ErrorRevision(f"{que} ({ruta}) no es JSON válido: {e}") from e
    except ErrorRevision as e:
        raise ErrorRevision(f"{que} ({ruta}): {e}") from e


def exigir_claves(objeto: object, claves: Sequence[str], que: str) -> dict[str, object]:
    """Un objeto JSON con exactamente estas claves, ni de más ni de menos."""
    if not isinstance(objeto, dict):
        raise ErrorRevision(f"{que} debe ser un objeto JSON")
    faltan = [c for c in claves if c not in objeto]
    if faltan:
        raise ErrorRevision(f"{que}: faltan las claves {', '.join(faltan)}")
    sobran = sorted(str(c) for c in objeto if c not in claves)
    if sobran:
        raise ErrorRevision(
            f"{que}: sobran las claves {', '.join(sobran)} (solo se aceptan {', '.join(claves)})"
        )
    return objeto


def entero(valor: object, que: str, minimo: int = 0) -> int:
    """Un entero JSON (no booleano) mayor o igual que `minimo`."""
    if type(valor) is not int or valor < minimo:
        raise ErrorRevision(f"{que} debe ser un entero ≥ {minimo}; llegó {valor!r}")
    return valor


def suma_exacta(valores: Iterable[Decimal]) -> Decimal:
    """Suma de decimales sin redondeo."""
    total = Decimal(0)
    for valor in valores:
        total = _EXACTO.add(total, valor)
    return total


def texto_decimal(valor: Decimal) -> str:
    """Decimal en notación fija, sin ceros sobrantes y sin redondear: "0.50" → "0.5"; "1E-7" → "0.0000001"."""
    texto = format(valor, "f")
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto


def texto_fraccion(valor: Fraction, cifras: int = 20) -> str:
    """Una fracción como decimal en texto, con `cifras` cifras significativas (redondeo al par)."""
    cociente = Context(prec=cifras).divide(Decimal(valor.numerator), Decimal(valor.denominator))
    return texto_decimal(cociente)


def lista_corta(ids: Sequence[str], maximo: int = 10) -> str:
    """Los primeros ids separados por comas y, si hay más, cuántos faltan por mostrar."""
    texto = ", ".join(ids[:maximo])
    return texto + (f" y {len(ids) - maximo} más" if len(ids) > maximo else "")
