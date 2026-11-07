"""Núcleo del simulador: etiquetas de verdad, generador determinista y utilidades.

No depende de los escenarios (para no crear ciclos de importación). Los escenarios y `combinar`
usan `_Gen` para producir filas con referencias únicas y tiempos reproducibles.
"""

from __future__ import annotations

import datetime as dt
import random
import re
from dataclasses import dataclass, field
from decimal import Decimal

import duckdb

from lumen_lupa.modelo import insertar

# Los 8 escenarios del contrato T4 y, al final, los 2 del contrato T5, en orden. Los nuevos van al
# final para que `combinar` siga produciendo las mismas filas para los escenarios de T4.
ESCENARIOS: tuple[str, ...] = (
    "honesto",
    "anillo_de_lavado",
    "ciclo",
    "bot_mecanico",
    "arbitraje",
    "manipulacion_de_precio",
    "trampa_exchange",
    "trampa_market_maker",
    "ciclo_por_transferencias",
    "trampa_pagos_honestos",
)

# Valores que puede tomar `verdad.senal_esperada` (contrato T4). S6 no está: marca un bot, no
# resta volumen (spec §5.2), así que un bot mecánico espera `ninguna`.
SENALES_ESPERADAS: tuple[str, ...] = ("S1", "S5", "S7", "arbitraje", "ninguna")

# Prefijo de toda dirección ficticia. Nunca se usa el formato real de Stellar (G + 55 base32).
PREFIJO_CUENTA = "SIM"
# Forma de una cuenta real de Stellar; ninguna dirección generada debe coincidir (contrato, crit. 5).
CUENTA_STELLAR = re.compile(r"^G[A-Z2-7]{55}$")

CHAIN = "sintetica"
ACTIVO_POR_DEFECTO = "TKN:SIM-EMISOR-TKN"
COTIZACION_POR_DEFECTO = "USDC:SIM-EMISOR-USDC"
NATIVO = "XLM:nativo"

# Un ledger de Stellar cierra cada ~5 s; el tiempo se deriva del número de ledger para que sea
# monótono y reproducible, sin leer el reloj.
INICIO_POR_DEFECTO = dt.datetime(2026, 1, 1, 0, 0, 0)
SEGUNDOS_POR_LEDGER = 5
_SIETE = Decimal("0.0000001")  # 7 decimales: la resolución de un stroop


@dataclass(frozen=True, slots=True)
class Verdad:
    """La verdad de un swap: tabla aparte del modelo normalizado. Las señales no la leen."""

    chain: str
    op_ref: str  # la operación del swap que describe (clave)
    escenario: str
    manipulada: bool
    senal_esperada: str
    entidad_real: str

    def __post_init__(self) -> None:
        if self.escenario not in ESCENARIOS:
            raise ValueError(f"escenario desconocido: {self.escenario!r}")
        if self.senal_esperada not in SENALES_ESPERADAS:
            raise ValueError(
                f"senal_esperada debe ser una de {SENALES_ESPERADAS}, no {self.senal_esperada!r}"
            )


@dataclass(frozen=True, slots=True)
class Mercado:
    """Lo que devuelve cada escenario y `combinar`: filas del modelo + su verdad."""

    filas: list[object] = field(default_factory=list)
    verdad: list[Verdad] = field(default_factory=list)

    def de_tabla(self, tabla: str) -> list[object]:
        """Las filas de una tabla concreta del modelo (p. ej. 'swaps'), en orden."""
        from lumen_lupa.modelo import TABLAS

        clase = TABLAS[tabla]
        return [f for f in self.filas if isinstance(f, clase)]


class _Gen:
    """Fuente única de aleatoriedad, tiempo y referencias de un mercado.

    Compartir una instancia entre escenarios garantiza referencias (`tx_ref`, `op_ref`) y ledgers
    únicos y una secuencia aleatoria reproducible para una semilla dada.
    """

    def __init__(
        self,
        semilla: int,
        chain: str,
        activo: str,
        cotizacion: str,
        inicio: dt.datetime,
    ) -> None:
        self.rng = random.Random(semilla)
        self.chain = chain
        self.activo = activo
        self.cotizacion = cotizacion
        self.inicio = inicio
        self._ledger = 0
        self._tx = 0
        self._op = 0

    def ledger(self, salto: int = 1) -> int:
        self._ledger += salto
        return self._ledger

    def ts(self, ledger: int) -> dt.datetime:
        return self.inicio + dt.timedelta(seconds=SEGUNDOS_POR_LEDGER * ledger)

    def tx(self, esc: str) -> str:
        self._tx += 1
        return f"SIMTX-{esc}-{self._tx:06d}"

    def op(self, esc: str) -> str:
        self._op += 1
        return f"SIMOP-{esc}-{self._op:06d}"

    def cuenta(self, esc: str, inst: str, nombre: str) -> str:
        """Dirección ficticia estable y única por (escenario, instancia, nombre)."""
        return f"{PREFIJO_CUENTA}-{esc}-{inst}-{nombre}"

    def pool(self, esc: str, inst: str, nombre: str = "pool") -> str:
        return f"{PREFIJO_CUENTA}-{esc}-{inst}-{nombre}"

    def monto(self, lo: int, hi: int) -> Decimal:
        """Un monto positivo en [lo, hi] (unidades enteras) con resolución de stroop."""
        stroops = self.rng.randint(max(1, lo) * 10_000_000, hi * 10_000_000)
        return (Decimal(stroops) / 10_000_000).quantize(_SIETE)


def crear_tabla_verdad(con: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """Crea (si no existe) la tabla `verdad`, aparte de las siete del modelo."""
    senales = ", ".join(f"'{s}'" for s in SENALES_ESPERADAS)
    con.execute(
        "CREATE TABLE IF NOT EXISTS verdad (\n"
        "    chain VARCHAR NOT NULL,\n"
        "    op_ref VARCHAR NOT NULL,\n"
        "    escenario VARCHAR NOT NULL,\n"
        "    manipulada BOOLEAN NOT NULL,\n"
        "    senal_esperada VARCHAR NOT NULL,\n"
        "    entidad_real VARCHAR NOT NULL,\n"
        f"    CHECK (senal_esperada IN ({senales}))\n"
        ")"
    )
    return con


def insertar_verdad(con: duckdb.DuckDBPyConnection, verdades: list[Verdad]) -> int:
    for v in verdades:
        con.execute(
            "INSERT INTO verdad VALUES (?, ?, ?, ?, ?, ?)",
            [v.chain, v.op_ref, v.escenario, v.manipulada, v.senal_esperada, v.entidad_real],
        )
    return len(verdades)


def insertar_mercado(con: duckdb.DuckDBPyConnection, mercado: Mercado) -> tuple[int, int]:
    """Inserta las filas del modelo y las de verdad. Devuelve (filas_modelo, filas_verdad)."""
    n_modelo = insertar(con, mercado.filas)
    n_verdad = insertar_verdad(con, mercado.verdad)
    return n_modelo, n_verdad
