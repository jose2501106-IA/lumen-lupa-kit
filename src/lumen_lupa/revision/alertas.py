"""Lista de alertas de una semana: lectura estricta y huella (REVIEW_PROTOCOL.md §3 y §4; contrato T9).

Formato (JSON en UTF-8; lo produce el detector, que es privado; aquí solo se lee):

    {"version": 1, "semana": {"desde": "2026-09-28", "hasta": "2026-10-04"},
     "alertas": [{"id": "S1-B-003", "senal": "S1", "mercado": "B", "peso": "0.0123"}, ...]}

- ``id``: texto único, sin espacios ni caracteres de control.
- ``senal``: ``S1`` o ``S5``. ``mercado``: una letra mayúscula (A–Z).
- ``peso``: decimal en texto, en notación fija, con 0 < peso ≤ 1: el volumen marcado de la alerta entre el
  volumen reportado de su mercado en la semana (§3).
- ``semana``: fechas ``AAAA-MM-DD``, con ``desde`` ≤ ``hasta``.

Cualquier otra clave es un error: la huella cubre exactamente estos campos y ningún otro dato pasa de aquí.

Huella (§4): sha256 del JSON canónico de la lista: las alertas ordenadas por id (por puntos de código),
claves ordenadas, separadores sin espacios, cada peso como decimal sin ceros sobrantes ("0.50" → "0.5")
y UTF-8. Reordenar las alertas o escribir un peso de otra forma no cambia la huella; cambiar un valor sí.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from lumen_lupa.revision.comun import (
    ErrorRevision,
    exigir_claves,
    json_canonico,
    leer_json,
    sha256_hex,
    texto_decimal,
)

VERSION = 1
SENALES: tuple[str, ...] = ("S1", "S5")

_CLAVES_LISTA = ("version", "semana", "alertas")
_CLAVES_SEMANA = ("desde", "hasta")
_CLAVES_ALERTA = ("id", "senal", "mercado", "peso")
_FECHA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_MERCADO = re.compile(r"[A-Z]")
_DECIMAL = re.compile(r"[0-9]+(?:\.[0-9]+)?")


@dataclass(frozen=True, slots=True)
class Alerta:
    id: str
    senal: str
    mercado: str
    peso: Decimal


@dataclass(frozen=True, slots=True)
class ListaAlertas:
    """Una lista de alertas ya validada, con las alertas ordenadas por id."""

    desde: str
    hasta: str
    alertas: tuple[Alerta, ...]

    def de_senal(self, senal: str) -> tuple[Alerta, ...]:
        return tuple(a for a in self.alertas if a.senal == senal)

    def pesos(self) -> dict[str, Decimal]:
        return {a.id: a.peso for a in self.alertas}


def validar_id(valor: object, que: str) -> str:
    """Un id de alerta: texto no vacío, sin espacios ni caracteres de control."""
    if not isinstance(valor, str) or not valor:
        raise ErrorRevision(f"{que}: el id debe ser un texto no vacío; llegó {valor!r}")
    if any(c.isspace() for c in valor) or not valor.isprintable():
        raise ErrorRevision(f"{que}: el id {valor!r} no puede tener espacios ni caracteres de control")
    return valor


def leer_peso(valor: object, que: str) -> Decimal:
    """Un peso: decimal en texto, en notación fija, con 0 < peso ≤ 1."""
    if not isinstance(valor, str) or not _DECIMAL.fullmatch(valor):
        raise ErrorRevision(
            f'{que}: el peso debe ser un decimal en texto y en notación fija, por ejemplo "0.0123"; '
            f"llegó {valor!r}"
        )
    peso = Decimal(valor)
    if not 0 < peso <= 1:
        raise ErrorRevision(f"{que}: el peso debe cumplir 0 < peso ≤ 1; llegó {valor}")
    return peso


def validar_semana(valor: object, que: str) -> tuple[str, str]:
    """(desde, hasta) de una semana: fechas AAAA-MM-DD válidas, con desde ≤ hasta."""
    semana = exigir_claves(valor, _CLAVES_SEMANA, que)
    for clave in _CLAVES_SEMANA:
        texto = semana[clave]
        valida = isinstance(texto, str) and _FECHA.fullmatch(texto) is not None
        if valida:
            try:
                dt.date.fromisoformat(texto)
            except ValueError:
                valida = False
        if not valida:
            raise ErrorRevision(f"{que}: {clave} debe ser una fecha AAAA-MM-DD; llegó {texto!r}")
    desde, hasta = semana["desde"], semana["hasta"]
    if desde > hasta:  # en AAAA-MM-DD, el orden del texto es el de las fechas
        raise ErrorRevision(f"{que}: desde ({desde}) es posterior a hasta ({hasta})")
    return desde, hasta


def validar_lista(datos: object) -> ListaAlertas:
    """Valida el contenido de una lista de alertas (ver el formato en el docstring del módulo)."""
    lista = exigir_claves(datos, _CLAVES_LISTA, "la lista de alertas")
    version = lista["version"]
    if type(version) is not int or version != VERSION:
        raise ErrorRevision(f"la lista de alertas: version debe ser {VERSION}; llegó {version!r}")
    desde, hasta = validar_semana(lista["semana"], "la lista de alertas, semana")
    crudas = lista["alertas"]
    if not isinstance(crudas, list):
        raise ErrorRevision("la lista de alertas: `alertas` debe ser una lista")
    alertas: list[Alerta] = []
    vistas: dict[str, int] = {}
    for i, cruda in enumerate(crudas, start=1):
        alerta = exigir_claves(cruda, _CLAVES_ALERTA, f"alerta #{i}")
        id_alerta = validar_id(alerta["id"], f"alerta #{i}")
        if id_alerta in vistas:
            raise ErrorRevision(
                f"id repetido {id_alerta} (alertas #{vistas[id_alerta]} y #{i}); cada id debe ser único"
            )
        vistas[id_alerta] = i
        que = f"alerta #{i} ({id_alerta})"
        senal = alerta["senal"]
        if not isinstance(senal, str) or senal not in SENALES:
            raise ErrorRevision(f"{que}: señal desconocida {senal!r}; se aceptan {', '.join(SENALES)}")
        mercado = alerta["mercado"]
        if not isinstance(mercado, str) or not _MERCADO.fullmatch(mercado):
            raise ErrorRevision(f"{que}: el mercado debe ser una letra mayúscula (A–Z); llegó {mercado!r}")
        alertas.append(Alerta(id_alerta, senal, mercado, leer_peso(alerta["peso"], que)))
    alertas.sort(key=lambda a: a.id)
    return ListaAlertas(desde, hasta, tuple(alertas))


def leer_alertas(ruta: Path) -> ListaAlertas:
    return validar_lista(leer_json(ruta, "la lista de alertas"))


def forma_canonica(lista: ListaAlertas) -> dict[str, object]:
    """Lo que entra a la huella: la lista con las alertas por id y cada peso sin ceros sobrantes."""
    return {
        "version": VERSION,
        "semana": {"desde": lista.desde, "hasta": lista.hasta},
        "alertas": [
            {"id": a.id, "senal": a.senal, "mercado": a.mercado, "peso": texto_decimal(a.peso)}
            for a in lista.alertas
        ],
    }


def huella(lista: ListaAlertas) -> str:
    """sha256 (hexadecimal) del JSON canónico de la lista (§4)."""
    return sha256_hex(json_canonico(forma_canonica(lista)))
