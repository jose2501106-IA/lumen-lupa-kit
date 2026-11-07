"""Sorteo de las alertas a revisar y de las del segundo revisor (REVIEW_PROTOCOL.md §5, §6 y §8; contrato T9).

- Semilla (§5): el hash de un ledger, 64 caracteres hexadecimales en cualquier caso; se usa y se guarda en
  minúsculas.
- Por señal (§6): censo si la población es ≤ n (n = 25 por omisión); si no, muestra de n por peso y sin
  reemplazo (Efraimidis y Spirakis, 2006). Para cada alerta,
  ``u = (int(sha256(semilla + ":" + id)) + 0.5) / 2**256`` y ``llave = ln(u) / peso``, con aritmética decimal
  de 50 dígitos (redondeo al par, el de Python por omisión; el contexto se fija aquí para no depender del
  ambiente). Se toman las n llaves más grandes; los empates se rompen por id, de menor a mayor.
- Orden: las alertas a revisar van en el orden del sorteo (llave de mayor a menor, empates por id). En censo
  se revisan todas y van en ese mismo orden.
- Segundo revisor (§8): por señal, ⌈k/3⌉ de las k revisadas: las de menor
  ``sha256(semilla + ":segundo:" + id)`` en hexadecimal, en ese orden.

La muestra se escribe en JSON con la semilla, n, la huella de la lista de alertas, la semana y, por señal,
el modo (``censo`` o ``muestra``), la población, los ids a revisar y los del segundo revisor.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from pathlib import Path

from lumen_lupa.revision.alertas import SENALES, Alerta, ListaAlertas, huella, validar_id, validar_semana
from lumen_lupa.revision.comun import PROTOCOLO, ErrorRevision, entero, exigir_claves, leer_json

N_MUESTRA = 25  # §6: censo hasta 25 alertas por señal; con más, muestra de 25
DIGITOS = 50  # §6: aritmética decimal de 50 dígitos
DIVISOR_SEGUNDO = 3  # §8: al menos un tercio de las revisadas, redondeado hacia arriba
CENSO, MUESTRA = "censo", "muestra"
VERSION_MUESTRA = 1

_HEX64 = re.compile(r"[0-9a-fA-F]{64}")
_HEX64_MINUSCULAS = re.compile(r"[0-9a-f]{64}")
_DOS_A_LA_256 = Decimal(2**256)
_CLAVES_MUESTRA = ("version", "protocolo", "semilla", "n", "huella", "semana", "senales")
_CLAVES_SENAL = ("modo", "poblacion", "revisar", "segundo_revisor")


@dataclass(frozen=True, slots=True)
class SorteoSenal:
    senal: str
    modo: str
    poblacion: int
    revisar: tuple[str, ...]
    segundo_revisor: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Muestra:
    semilla: str
    n: int
    huella: str
    desde: str
    hasta: str
    senales: tuple[SorteoSenal, ...]

    def ids_a_revisar(self) -> list[str]:
        return [i for s in self.senales for i in s.revisar]

    def ids_segundo_revisor(self) -> list[str]:
        return [i for s in self.senales for i in s.segundo_revisor]

    def como_dict(self) -> dict[str, object]:
        return {
            "version": VERSION_MUESTRA,
            "protocolo": PROTOCOLO,
            "semilla": self.semilla,
            "n": self.n,
            "huella": self.huella,
            "semana": {"desde": self.desde, "hasta": self.hasta},
            "senales": {
                s.senal: {
                    "modo": s.modo,
                    "poblacion": s.poblacion,
                    "revisar": list(s.revisar),
                    "segundo_revisor": list(s.segundo_revisor),
                }
                for s in self.senales
            },
        }


def validar_semilla(valor: object) -> str:
    """La semilla en minúsculas; debe tener 64 caracteres hexadecimales (el hash de un ledger, §5)."""
    if not isinstance(valor, str) or not _HEX64.fullmatch(valor):
        raise ErrorRevision(
            "la semilla debe tener exactamente 64 caracteres hexadecimales (el hash del ledger del §5); "
            f"llegó {valor!r}"
        )
    return valor.lower()


def validar_n(valor: object) -> int:
    return entero(valor, "n (tamaño de la muestra)", minimo=1)


def _contexto() -> Context:
    return Context(prec=DIGITOS, rounding=ROUND_HALF_EVEN)


def valor_u(semilla: str, id_alerta: str) -> Decimal:
    """u = (int(sha256(semilla + ":" + id)) + 0.5) / 2**256, con 50 dígitos (§6)."""
    entero_hash = int.from_bytes(hashlib.sha256(f"{semilla}:{id_alerta}".encode()).digest(), "big")
    with localcontext(_contexto()):
        return (Decimal(entero_hash) + Decimal("0.5")) / _DOS_A_LA_256


def llave(semilla: str, id_alerta: str, peso: Decimal) -> Decimal:
    """llave = ln(u) / peso, con 50 dígitos (§6). Entre más peso, más cerca de 0 y más probable salir."""
    with localcontext(_contexto()):
        return valor_u(semilla, id_alerta).ln() / peso


def orden_del_sorteo(llaves: Mapping[str, Decimal]) -> list[str]:
    """Ids de la llave más grande a la más chica; los empates, por id de menor a mayor.

    `copy_negate` no redondea; el signo menos sí lo haría (a la precisión del contexto del momento).
    """
    return sorted(llaves, key=lambda i: (llaves[i].copy_negate(), i))


def sortear(semilla: str, alertas: Sequence[Alerta], n: int = N_MUESTRA) -> tuple[str, list[str]]:
    """(modo, ids a revisar en el orden del sorteo) de las alertas de una señal."""
    orden = orden_del_sorteo({a.id: llave(semilla, a.id, a.peso) for a in alertas})
    if len(alertas) <= n:
        return CENSO, orden
    return MUESTRA, orden[:n]


def cuantas_segundo_revisor(revisadas: int) -> int:
    """⌈k/3⌉ (§8): 9 de 25."""
    return -(-revisadas // DIVISOR_SEGUNDO)


def segundo_revisor(semilla: str, revisar: Sequence[str]) -> list[str]:
    """Las ⌈k/3⌉ revisadas con el menor sha256(semilla + ":segundo:" + id) en hexadecimal (§8)."""

    def resumen(id_alerta: str) -> str:
        return hashlib.sha256(f"{semilla}:segundo:{id_alerta}".encode()).hexdigest()

    return sorted(revisar, key=lambda i: (resumen(i), i))[: cuantas_segundo_revisor(len(revisar))]


def armar_muestra(lista: ListaAlertas, semilla: str, n: int = N_MUESTRA) -> Muestra:
    """La muestra de una lista de alertas: por cada señal con alertas, su sorteo y su segundo revisor."""
    semilla = validar_semilla(semilla)
    n = validar_n(n)
    senales = []
    for senal in SENALES:
        alertas = lista.de_senal(senal)
        if not alertas:
            continue
        modo, revisar = sortear(semilla, alertas, n)
        senales.append(
            SorteoSenal(senal, modo, len(alertas), tuple(revisar), tuple(segundo_revisor(semilla, revisar)))
        )
    return Muestra(semilla, n, huella(lista), lista.desde, lista.hasta, tuple(senales))


def texto_muestra(muestra: Muestra) -> str:
    return json.dumps(muestra.como_dict(), indent=2, ensure_ascii=False) + "\n"


def _ids(valor: object, que: str) -> tuple[str, ...]:
    if not isinstance(valor, list):
        raise ErrorRevision(f"{que} debe ser una lista de ids")
    ids = tuple(validar_id(i, que) for i in valor)
    repetidos = sorted({i for i in ids if ids.count(i) > 1})
    if repetidos:
        raise ErrorRevision(f"{que}: ids repetidos {', '.join(repetidos)}")
    return ids


def _validar_senal(senal: str, valor: object, semilla: str, n: int) -> SorteoSenal:
    que = f"la muestra, señal {senal}"
    datos = exigir_claves(valor, _CLAVES_SENAL, que)
    modo = datos["modo"]
    if modo not in (CENSO, MUESTRA):
        raise ErrorRevision(f"{que}: modo desconocido {modo!r}; se aceptan {CENSO} y {MUESTRA}")
    poblacion = entero(datos["poblacion"], f"{que}: poblacion", minimo=1)
    revisar = _ids(datos["revisar"], f"{que}: revisar")
    segundo = _ids(datos["segundo_revisor"], f"{que}: segundo_revisor")
    esperado = CENSO if poblacion <= n else MUESTRA
    if modo != esperado:
        raise ErrorRevision(f"{que}: con población {poblacion} y n = {n} el modo es {esperado}, no {modo}")
    if len(revisar) != (poblacion if modo == CENSO else n):
        raise ErrorRevision(
            f"{que}: en {modo} se revisan {poblacion if modo == CENSO else n} alertas y la lista tiene "
            f"{len(revisar)}"
        )
    if list(segundo) != segundo_revisor(semilla, revisar):
        raise ErrorRevision(f"{que}: los ids del segundo revisor no son los que salen de la semilla (§8)")
    return SorteoSenal(senal, modo, poblacion, revisar, segundo)


def validar_muestra(datos: object) -> Muestra:
    """Valida una muestra escrita por `muestra`: forma, modos, tamaños y segundo revisor según la semilla."""
    muestra = exigir_claves(datos, _CLAVES_MUESTRA, "la muestra")
    version = muestra["version"]
    if type(version) is not int or version != VERSION_MUESTRA:
        raise ErrorRevision(f"la muestra: version debe ser {VERSION_MUESTRA}; llegó {version!r}")
    if muestra["protocolo"] != PROTOCOLO:
        raise ErrorRevision(
            f"la muestra es del protocolo {muestra['protocolo']!r}; esta herramienta ejecuta la versión "
            f"{PROTOCOLO} de REVIEW_PROTOCOL.md"
        )
    semilla = validar_semilla(muestra["semilla"])
    n = validar_n(muestra["n"])
    huella_lista = muestra["huella"]
    if not isinstance(huella_lista, str) or not _HEX64_MINUSCULAS.fullmatch(huella_lista):
        raise ErrorRevision(
            "la muestra: huella debe ser un sha256 en hexadecimal (64 caracteres en minúsculas)"
        )
    desde, hasta = validar_semana(muestra["semana"], "la muestra, semana")
    crudas = muestra["senales"]
    if not isinstance(crudas, dict):
        raise ErrorRevision("la muestra: `senales` debe ser un objeto JSON")
    desconocidas = sorted(s for s in crudas if s not in SENALES)
    if desconocidas:
        raise ErrorRevision(f"la muestra: señales desconocidas {', '.join(desconocidas)}")
    senales = tuple(_validar_senal(s, crudas[s], semilla, n) for s in SENALES if s in crudas)
    todas = [i for s in senales for i in s.revisar]
    repetidos = sorted({i for i in todas if todas.count(i) > 1})
    if repetidos:
        raise ErrorRevision(f"la muestra: ids en más de una señal: {', '.join(repetidos)}")
    return Muestra(semilla, n, huella_lista, desde, hasta, senales)


def leer_muestra(ruta: Path) -> Muestra:
    return validar_muestra(leer_json(ruta, "la muestra"))
