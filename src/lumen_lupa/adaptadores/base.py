"""Interfaz de un adaptador: una cadena → modelo normalizado (spec v0.2 §3 y §4).

Cada cadena (Stellar con Hubble, después Sui) implementa esta interfaz. Las señales no ven al
adaptador: solo leen las tablas que `cargar` deja en DuckDB. La tabla `entities` no viene del
adaptador; se construye en el paso de entidades (spec §5.1).

Todavía no hay implementación de Stellar (T1 y semana 3).
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import ClassVar

import duckdb

from lumen_lupa.modelo import Funding, Holders, Label, Order, Swap, Transfer, insertar


class Adaptador(ABC):
    """Traduce los datos de una cadena a filas del modelo normalizado."""

    chain: ClassVar[str]

    @abstractmethod
    def swaps(self, asset: str, desde: dt.date, hasta: dt.date) -> Iterable[Swap]:
        """Trades donde aparece `asset` en el intervalo [desde, hasta]."""

    @abstractmethod
    def fundings(self, cuentas: Iterable[str]) -> Iterable[Funding]:
        """Fondeos de las cuentas dadas."""

    @abstractmethod
    def transfers(
        self, cuentas: Iterable[str], activos: Iterable[str], desde: dt.date, hasta: dt.date
    ) -> Iterable[Transfer]:
        """Transferencias (spec v0.3 §4) donde una de `cuentas` es emisor o receptor y el activo
        es uno de `activos` (el activo analizado y sus cotizaciones; D-14)."""

    @abstractmethod
    def holders(self, asset: str, desde: dt.date, hasta: dt.date) -> Iterable[Holders]:
        """Holders por día (balance > 0) de `asset`."""

    @abstractmethod
    def orders(self, market: str, desde: dt.date, hasta: dt.date) -> Iterable[Order]:
        """Órdenes del mercado en el intervalo."""

    @abstractmethod
    def labels(self, direcciones: Iterable[str]) -> Iterable[Label]:
        """Etiquetas conocidas de las direcciones dadas."""

    def cargar(
        self,
        con: duckdb.DuckDBPyConnection,
        asset: str,
        market: str,
        desde: dt.date,
        hasta: dt.date,
    ) -> dict[str, int]:
        """Carga swaps, fundings, transfers, holders, orders y labels de `asset` en la base.

        Las cuentas a fondear, etiquetar y de las que se bajan transferencias son las que aparecen
        en los swaps. Los activos de las transferencias son los del mercado ("ACTIVO/COTIZACION";
        D-14). Rechaza filas de otra cadena. Devuelve cuántas filas cargó por tabla.
        """
        swaps = list(self.swaps(asset, desde, hasta))
        cuentas = sorted({s.trader for s in swaps} | {s.counterparty for s in swaps if s.counterparty})
        activos = market.split("/")
        lotes = {
            "swaps": swaps,
            "fundings": list(self.fundings(cuentas)),
            "transfers": list(self.transfers(cuentas, activos, desde, hasta)),
            "holders": list(self.holders(asset, desde, hasta)),
            "orders": list(self.orders(market, desde, hasta)),
            "labels": list(self.labels(cuentas)),
        }
        for tabla, filas in lotes.items():
            ajenas = [f for f in filas if f.chain != self.chain]
            if ajenas:
                raise ValueError(f"{tabla}: {len(ajenas)} fila(s) no son de la cadena {self.chain!r}")
        return {tabla: insertar(con, filas) for tabla, filas in lotes.items()}
