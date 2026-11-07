"""Modelo de datos normalizado (spec v0.2 §4).

Siete tablas en DuckDB: swaps, fundings, transfers, holders, orders, labels y entities. Las columnas son
exactamente las de la spec; los tipos de SQL son decisión de implementación y viven en un solo
lugar: el `metadata["sql"]` de cada campo de las clases de abajo. El DDL se deriva de ahí, así
que las clases de Python y las tablas no pueden separarse.

Las señales, el puntaje y el agente solo leen este modelo; no saben de qué cadena viene el dato.
"""

from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import operator
import os
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

# Tiempos: TIMESTAMP en UTC, sin zona (como `closed_at` de Hubble). TIMESTAMPTZ obligaría a instalar
# pytz para leerlo desde Python, y pytz no está en el stack.
# Montos de tokens: Stellar usa enteros de 64 bits con 7 decimales; DECIMAL(38,7) los guarda sin pérdida.
MONTO = "DECIMAL(38,7)"

VENUES = ("sdex", "pool", "amm")

# Tipos de transferencia de la spec v0.2 §4 (tabla `transfers`).
KINDS_TRANSFER = ("payment", "path_payment", "sac_transfer")


def _col(sql: str, default: Any = dataclasses.MISSING) -> Any:
    return field(default=default, metadata={"sql": sql})


@dataclass(frozen=True, slots=True)
class Swap:
    chain: str = _col("VARCHAR NOT NULL")
    ts: dt.datetime = _col("TIMESTAMP NOT NULL")
    ledger: int = _col("BIGINT NOT NULL")
    tx_ref: str = _col("VARCHAR NOT NULL")
    op_ref: str = _col("VARCHAR NOT NULL")
    venue: str = _col("VARCHAR NOT NULL")
    pool_id: str | None = _col("VARCHAR")
    trader: str = _col("VARCHAR NOT NULL")
    counterparty: str | None = _col("VARCHAR")  # null si es pool
    asset_in: str = _col("VARCHAR NOT NULL")
    amount_in: Decimal = _col(f"{MONTO} NOT NULL")
    asset_out: str = _col("VARCHAR NOT NULL")
    amount_out: Decimal = _col(f"{MONTO} NOT NULL")
    usd_low: float | None = _col("DOUBLE", None)
    usd_high: float | None = _col("DOUBLE", None)

    def __post_init__(self) -> None:
        if self.venue not in VENUES:
            raise ValueError(f"venue debe ser uno de {VENUES}, no {self.venue!r}")
        if not self.amount_in > 0 or not self.amount_out > 0:
            raise ValueError("los montos de un swap deben ser positivos")
        if self.asset_in == self.asset_out:
            raise ValueError("asset_in y asset_out deben ser distintos")
        if self.usd_low is not None and self.usd_high is not None and self.usd_low > self.usd_high:
            raise ValueError("usd_low no puede ser mayor que usd_high")


@dataclass(frozen=True, slots=True)
class Funding:
    chain: str = _col("VARCHAR NOT NULL")
    ts: dt.datetime = _col("TIMESTAMP NOT NULL")
    tx_ref: str = _col("VARCHAR NOT NULL")
    funder: str = _col("VARCHAR NOT NULL")
    funded: str = _col("VARCHAR NOT NULL")
    fee_source: str | None = _col("VARCHAR")
    sponsor: str | None = _col("VARCHAR")
    asset: str = _col("VARCHAR NOT NULL")
    amount: Decimal = _col(f"{MONTO} NOT NULL")
    is_first: bool = _col("BOOLEAN NOT NULL")


@dataclass(frozen=True, slots=True)
class Transfer:
    """Pago entre cuentas (spec v0.3 §4). `asset` y `amount` son lo que recibe `receiver`.

    `asset_sent` y `amount_sent` son lo que sale del emisor (en `payment` son iguales a
    `asset`/`amount`; anulables si Hubble no los trae; D-14). Van después de `kind` (spec v0.3).
    `sender == receiver` se permite: la spec lo guarda y S5 lo ignora (candidato a arbitraje).
    """

    chain: str = _col("VARCHAR NOT NULL")
    ts: dt.datetime = _col("TIMESTAMP NOT NULL")
    ledger: int = _col("BIGINT NOT NULL")
    tx_ref: str = _col("VARCHAR NOT NULL")
    op_ref: str = _col("VARCHAR NOT NULL")
    sender: str = _col("VARCHAR NOT NULL")
    receiver: str = _col("VARCHAR NOT NULL")
    asset: str = _col("VARCHAR NOT NULL")
    amount: Decimal = _col(f"{MONTO} NOT NULL")
    kind: str = _col("VARCHAR NOT NULL")
    asset_sent: str | None = _col("VARCHAR", None)
    amount_sent: Decimal | None = _col(f"{MONTO}", None)

    def __post_init__(self) -> None:
        if not self.amount > 0:
            raise ValueError("el monto de una transferencia debe ser positivo")
        if self.kind not in KINDS_TRANSFER:
            raise ValueError(f"kind debe ser uno de {KINDS_TRANSFER}, no {self.kind!r}")
        if self.amount_sent is not None and not self.amount_sent > 0:
            raise ValueError("amount_sent, si está, debe ser positivo")


@dataclass(frozen=True, slots=True)
class Holders:
    chain: str = _col("VARCHAR NOT NULL")
    day: dt.date = _col("DATE NOT NULL")
    asset: str = _col("VARCHAR NOT NULL")
    holders_count: int = _col("BIGINT NOT NULL")
    new_holders: int = _col("BIGINT NOT NULL")


@dataclass(frozen=True, slots=True)
class Order:
    chain: str = _col("VARCHAR NOT NULL")
    ts: dt.datetime = _col("TIMESTAMP NOT NULL")
    ledger: int = _col("BIGINT NOT NULL")
    tx_ref: str = _col("VARCHAR NOT NULL")
    account: str = _col("VARCHAR NOT NULL")
    market: str = _col("VARCHAR NOT NULL")
    op_type: str = _col("VARCHAR NOT NULL")
    amount: Decimal = _col(f"{MONTO} NOT NULL")
    price: float = _col("DOUBLE NOT NULL")


@dataclass(frozen=True, slots=True)
class Label:
    chain: str = _col("VARCHAR NOT NULL")
    address: str = _col("VARCHAR NOT NULL")
    label: str = _col("VARCHAR NOT NULL")
    source: str = _col("VARCHAR NOT NULL")


@dataclass(frozen=True, slots=True)
class Entity:
    chain: str = _col("VARCHAR NOT NULL")
    entity_id: str = _col("VARCHAR NOT NULL")
    address: str = _col("VARCHAR NOT NULL")
    reason: str = _col("VARCHAR NOT NULL")


# Tabla → clase de fila. El orden es el de la spec v0.2 §4.
TABLAS: dict[str, type] = {
    "swaps": Swap,
    "fundings": Funding,
    "transfers": Transfer,
    "holders": Holders,
    "orders": Order,
    "labels": Label,
    "entities": Entity,
}

# Restricciones que la base también hace cumplir, además de la validación de Python.
_RESTRICCIONES: dict[str, tuple[str, ...]] = {
    "transfers": (
        "CHECK (amount > 0)",
        "CHECK (kind IN (" + ", ".join(f"'{k}'" for k in KINDS_TRANSFER) + "))",
        "CHECK (amount_sent IS NULL OR amount_sent > 0)",
    ),
    "swaps": (
        "CHECK (venue IN ('sdex', 'pool', 'amm'))",
        "CHECK (amount_in > 0 AND amount_out > 0)",
        "CHECK (asset_in <> asset_out)",
        "CHECK (usd_low IS NULL OR usd_high IS NULL OR usd_low <= usd_high)",
    ),
}


def columnas(tabla: str) -> list[str]:
    """Nombres de columna de una tabla, en orden."""
    return [f.name for f in dataclasses.fields(TABLAS[tabla])]


def ddl(tabla: str) -> str:
    """CREATE TABLE de DuckDB para una tabla del modelo."""
    partes = [f"{f.name} {f.metadata['sql']}" for f in dataclasses.fields(TABLAS[tabla])]
    partes += list(_RESTRICCIONES.get(tabla, ()))
    return f"CREATE TABLE IF NOT EXISTS {tabla} (\n    " + ",\n    ".join(partes) + "\n)"


def crear_base(ruta: str | Path) -> duckdb.DuckDBPyConnection:
    """Crea (si no existen) las siete tablas en `ruta` y devuelve la conexión abierta.

    `ruta` puede ser ":memory:" para una base en memoria.
    """
    con = duckdb.connect(str(ruta))
    for tabla in TABLAS:
        con.execute(ddl(tabla))
    return con


def tabla_de(fila: object) -> str:
    """Nombre de la tabla a la que pertenece una fila del modelo."""
    for tabla, clase in TABLAS.items():
        if isinstance(fila, clase):
            return tabla
    raise TypeError(f"{type(fila).__name__} no es una fila del modelo normalizado")


_LOTE_INSERT = 1000  # filas por sentencia INSERT (VALUES multi-fila)


def insertar(con: duckdb.DuckDBPyConnection, filas: list[object], lote: int = _LOTE_INSERT) -> int:
    """Inserta filas del modelo (de cualquier tabla) y devuelve cuántas insertó.

    Agrupa por tabla e inserta con `VALUES` multi-fila en lotes. Mismo resultado que fila por fila
    —las restricciones CHECK se siguen aplicando— pero mucho más rápido: `executemany` de DuckDB
    corre una sentencia por fila (~1 ms/fila), inviable para millones de filas.
    """
    por_tabla: dict[str, list[tuple]] = {}
    for fila in filas:
        por_tabla.setdefault(tabla_de(fila), []).append(dataclasses.astuple(fila))
    total = 0
    for tabla, tuplas in por_tabla.items():
        cols = columnas(tabla)
        prefijo = f"INSERT INTO {tabla} ({', '.join(cols)}) VALUES "
        marca_fila = "(" + ", ".join("?" for _ in cols) + ")"
        for i in range(0, len(tuplas), lote):
            bloque = tuplas[i : i + lote]
            sql = prefijo + ", ".join(marca_fila for _ in bloque)
            con.execute(sql, [v for t in bloque for v in t])
        total += len(tuplas)
    return total


def _csv_valor(v: Any) -> str:
    """Formatea un valor del modelo para CSV que DuckDB COPY parsea según el tipo de la columna."""
    if v is None:
        return ""  # NULL '' en el COPY (ninguna columna de texto del modelo guarda cadena vacía)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, dt.datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, dt.date):
        return v.isoformat()
    return str(v)


def insertar_masivo(con: duckdb.DuckDBPyConnection, filas: list[object]) -> int:
    """Como `insertar`, pero vía CSV temporal + `COPY` (rápido para millones de filas).

    DuckDB es columnar: un INSERT por fila (o `executemany`) cuesta ~ms/fila, inviable a millones;
    `COPY` desde archivo carga en bloque. Las restricciones CHECK se siguen aplicando en el COPY.
    El CSV temporal es efímero (se borra siempre) y no contiene nada que no esté ya en la base.
    """
    por_tabla: dict[str, list[object]] = {}
    for fila in filas:
        por_tabla.setdefault(tabla_de(fila), []).append(fila)
    total = 0
    for tabla, grupo in por_tabla.items():
        cols = columnas(tabla)
        getter = operator.attrgetter(*cols)
        una_col = len(cols) == 1
        fd, ruta = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        try:
            with open(ruta, "w", newline="", encoding="utf-8") as fh:
                escritor = csv.writer(fh)
                escritor.writerow(cols)
                for f in grupo:
                    vals = (getter(f),) if una_col else getter(f)
                    escritor.writerow([_csv_valor(v) for v in vals])
            ruta_sql = ruta.replace("'", "''")
            con.execute(f"COPY {tabla} FROM '{ruta_sql}' (FORMAT CSV, HEADER, NULL '')")
        finally:
            os.unlink(ruta)
        total += len(grupo)
    return total
