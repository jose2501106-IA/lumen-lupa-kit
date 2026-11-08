"""Adaptador de Stellar (Hubble) — parte clásica: swaps, transfers y fundings (T3b, spec v0.3 §4).

Dos capas separadas (contrato T3b):
  - DESCARGA (BigQuery → data/estudio/*.jsonl): la hace `scripts/bq.py` con las consultas de
    `queries/estudio/`. No vive aquí.
  - TRADUCCIÓN (archivos locales → filas del modelo normalizado): funciones PURAS, sin red, abajo.
    Se prueban con fixtures (`tests/fixtures/stellar/`), sin tocar BigQuery.

Aquarius (evento `trade`), `orders`, `holders` y `labels` quedan para T3c: aquí devuelven listas
vacías con el aviso "pendiente T3c" (nada silencioso).
"""

from __future__ import annotations

import datetime as dt
import glob
import json
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import ClassVar

import duckdb

from lumen_lupa.adaptadores.base import Adaptador
from lumen_lupa.modelo import Funding, Holders, Label, Order, Swap, Transfer, insertar_masivo

CHAIN = "stellar"
STROOP = Decimal("0.0000001")  # 7 decimales: la unidad mínima de Stellar

# Ventana del estudio (D-15): fechas fijas, UTC. Ajústala para tus datos.
VENTANA_DESDE = dt.date(2026, 9, 21)
VENTANA_HASTA = dt.date(2026, 10, 4)

_USDC = "USDC:GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN"
_XLM = "XLM:nativo"

# Un archivo .duckdb por mercado: (nombre de archivo, activo analizado, mercado ACTIVO/COTIZACION).
# El kit trae solo el mercado de control (USDC de Circle / XLM nativo); agrega aquí los tuyos.
MERCADOS: tuple[tuple[str, str, str], ...] = (("control.duckdb", _USDC, f"{_USDC}/{_XLM}"),)

# asset_id (columna de clustering de history_trades) -> "CODIGO:EMISOR" de los activos del kit: los
# dos lados del mercado de control. Origen: history_trades de Hubble (2026-10-01). Agrega los tuyos.
# Los trades solo se bajaron con asset_id (sin code/issuer) para abaratar; aquí se re-mapea.
ASSET_POR_ID: dict[int, str] = {
    -5706705804583548011: "XLM:nativo",
    -4025621231271331684: "USDC:GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN",
}

# type de history_operations -> kind de transfers (spec v0.3 §4; D-14: los dos path payments = path_payment).
_KIND_POR_TYPE = {1: "payment", 2: "path_payment", 13: "path_payment"}
TIPOS_TRANSFER = frozenset(_KIND_POR_TYPE)
TIPO_FUNDING = 0  # create_account


# --- Primitivas puras -----------------------------------------------------------------------------


def d7(x) -> Decimal:
    """FLOAT64/num -> Decimal con 7 decimales explícitos (stroops). Vía str para no arrastrar ruido."""
    return Decimal(str(x)).quantize(STROOP)


def ledger_de_toid(history_operation_id: int) -> int:
    """ledger = TOID >> 32 (los 32 bits altos del TOID; spec v0.3 §4)."""
    return history_operation_id >> 32


def tx_ref_de_toid(history_operation_id: int) -> str:
    """tx_ref = TOID de la transacción = el TOID de la operación sin los 12 bits de operación.

    Equivale a `history_operations.transaction_id` (verificado en los datos). Se devuelve como texto.
    """
    return str((history_operation_id >> 12) << 12)


def _ts(iso: str) -> dt.datetime:
    """closed_at/ledger_closed_at ISO (UTC, +00:00) -> datetime naive UTC (como el modelo)."""
    return dt.datetime.fromisoformat(iso).replace(tzinfo=None)


def _asset_de_campos(code, issuer, type_) -> str:
    """Activo "CODIGO:EMISOR"; XLM nativo = "XLM:nativo" (spec v0.3 §4)."""
    if type_ == "native":
        return "XLM:nativo"
    return f"{code}:{issuer}"


def _base_de_muxed(base, muxed) -> str:
    """Cuenta base (G...). Hubble ya entrega la base en el campo sin sufijo; el muxed (M...) va aparte.

    En los datos de la ventana no hubo casos con muxed sin base (medido en T3b).
    """
    if base:
        return base
    raise ValueError(f"operación con cuenta muxed sin base: {muxed!r}")


# --- Traducción (funciones puras) -----------------------------------------------------------------


def traducir_swap(t: dict) -> Swap:
    """Una fila de history_trades -> Swap, desde el taker (spec v0.3 §4).

    trader = buying_account_address; asset_in/amount_in = buying_* (lo que entrega el taker);
    asset_out/amount_out = selling_* (lo que recibe). trade_type 1 = sdex (counterparty = vendedor);
    2 = pool (counterparty NULL, pool_id = selling_liquidity_pool_id).
    """
    op_id = t["history_operation_id"]
    es_pool = t["trade_type"] == 2
    return Swap(
        chain=CHAIN,
        ts=_ts(t["ledger_closed_at"]),
        ledger=ledger_de_toid(op_id),
        tx_ref=tx_ref_de_toid(op_id),
        op_ref=f"{op_id}:{t['order']}",
        venue="pool" if es_pool else "sdex",
        pool_id=t["selling_liquidity_pool_id"] if es_pool else None,
        trader=t["buying_account_address"],
        counterparty=None if es_pool else t["selling_account_address"],
        asset_in=ASSET_POR_ID[t["buying_asset_id"]],
        amount_in=d7(t["buying_amount"]),
        asset_out=ASSET_POR_ID[t["selling_asset_id"]],
        amount_out=d7(t["selling_amount"]),
    )


def traducir_transfer(o: dict) -> Transfer:
    """Una fila de history_operations (type 1/2/13) -> Transfer (spec v0.3 §4, D-14).

    `asset`/`amount` = lo que recibe el destino; `asset_sent`/`amount_sent` = lo que sale del emisor
    (en payment son iguales; en path payments salen de source_*). muxed -> cuenta base.
    Supone transacción exitosa (lo filtra el llamador).
    """
    op_id = o["history_operation_id"]
    asset = _asset_de_campos(o["asset_code"], o["asset_issuer"], o["asset_type"])
    amount = d7(o["amount"])
    if o["type"] == 1:  # payment: lo que sale es igual a lo que entra
        asset_sent, amount_sent = asset, amount
    else:  # path payment: lo que sale es source_*
        asset_sent = _asset_de_campos(
            o["source_asset_code"], o["source_asset_issuer"], o["source_asset_type"]
        )
        amount_sent = d7(o["source_amount"]) if o.get("source_amount") is not None else None
    return Transfer(
        chain=CHAIN,
        ts=_ts(o["closed_at"]),
        ledger=o["ledger_sequence"],
        tx_ref=tx_ref_de_toid(op_id),
        op_ref=str(op_id),
        sender=_base_de_muxed(o["det_from"], o.get("from_muxed")),
        receiver=_base_de_muxed(o["det_to"], o.get("to_muxed")),
        asset=asset,
        amount=amount,
        kind=_KIND_POR_TYPE[o["type"]],
        asset_sent=asset_sent,
        amount_sent=amount_sent,
    )


def traducir_funding(o: dict) -> Funding:
    """Una fila de history_operations (type 0 create_account) -> Funding (spec v0.3 §4).

    funder fondea a `account` con `starting_balance` XLM; es su primer fondeo (is_first).
    fee_source = COALESCE(fee_account, source_account) (decisión de costo T3b; ver nota).
    Supone transacción exitosa (lo filtra el llamador).
    """
    op_id = o["history_operation_id"]
    fee_source = o.get("fee_account") or o.get("source_account")
    return Funding(
        chain=CHAIN,
        ts=_ts(o["closed_at"]),
        tx_ref=tx_ref_de_toid(op_id),
        funder=_base_de_muxed(o["funder"], o.get("funder_muxed")),
        funded=o["account"],
        fee_source=fee_source,
        sponsor=o.get("sponsor"),
        asset="XLM:nativo",
        amount=d7(o["starting_balance"]),
        is_first=True,
    )


# --- Adaptador ------------------------------------------------------------------------------------


class StellarHubble(Adaptador):
    """Lee los .jsonl descargados de Hubble y traduce al modelo normalizado. chain = "stellar".

    Un objeto por mercado ("ACTIVO/COTIZACION"): `swaps` devuelve los trades del par; `transfers`
    y `fundings`, los de las cuentas del mercado. holders/orders/labels: pendiente T3c (lista vacía).
    """

    chain: ClassVar[str] = CHAIN
    PENDIENTE_T3C: ClassVar[tuple[str, ...]] = ("holders", "orders", "labels")

    def __init__(self, dir_datos: str | Path, mercado: str):
        self.dir_datos = Path(dir_datos)
        self.mercado = mercado
        # asset_id de los dos lados del par (para filtrar los trades de ESTE mercado).
        activos = mercado.split("/")
        por_nombre = {v: k for k, v in ASSET_POR_ID.items()}
        self._ids_mercado = frozenset(por_nombre[a] for a in activos)
        self.avisos: list[str] = []
        self.swaps_omitidos = 0  # trades con un monto que redondea a 0 (dust); no son Swap válidos

    def _leer(self, patron: str) -> Iterable[dict]:
        for ruta in sorted(glob.glob(str(self.dir_datos / patron))):
            with open(ruta, encoding="utf-8") as fh:
                for linea in fh:
                    linea = linea.strip()
                    if linea:
                        yield json.loads(linea)

    def _en_ventana(self, iso: str, desde: dt.date, hasta: dt.date) -> bool:
        fecha = _ts(iso).date()
        return desde <= fecha <= hasta

    def swaps(self, asset: str, desde: dt.date, hasta: dt.date) -> Iterable[Swap]:
        for t in self._leer("02_trades_*.jsonl"):
            if {t["selling_asset_id"], t["buying_asset_id"]} != self._ids_mercado:
                continue
            if not self._en_ventana(t["ledger_closed_at"], desde, hasta):
                continue
            # Trades dust: un lado redondea a 0 stroops → no son Swap válidos (monto > 0). Se omiten
            # contándolos (nada silencioso): aparecen en el resumen de carga como swaps_omitidos.
            if d7(t["selling_amount"]) == 0 or d7(t["buying_amount"]) == 0:
                self.swaps_omitidos += 1
                continue
            yield traducir_swap(t)

    def transfers(
        self, cuentas: Iterable[str], activos: Iterable[str], desde: dt.date, hasta: dt.date
    ) -> Iterable[Transfer]:
        cuentas = set(cuentas)
        activos = set(activos)
        for o in self._leer("03_operaciones_*.jsonl"):
            if o["type"] not in TIPOS_TRANSFER or o.get("successful") is not True:
                continue
            if not self._en_ventana(o["closed_at"], desde, hasta):
                continue
            t = traducir_transfer(o)
            # Cuenta del estudio como emisor o receptor (D-14) y activo = X o una cotización
            # (en cualquiera de los dos lados, para no perder path payments que mueven X).
            if t.sender not in cuentas and t.receiver not in cuentas:
                continue
            if t.asset not in activos and t.asset_sent not in activos:
                continue
            yield t

    def fundings(self, cuentas: Iterable[str]) -> Iterable[Funding]:
        cuentas = set(cuentas)
        for o in self._leer("03_operaciones_*.jsonl"):
            if o["type"] != TIPO_FUNDING or o.get("successful") is not True:
                continue
            if o["account"] not in cuentas:
                continue
            yield traducir_funding(o)

    def holders(self, asset: str, desde: dt.date, hasta: dt.date) -> Iterable[Holders]:
        self.avisos.append("holders: pendiente T3c")
        return []

    def orders(self, market: str, desde: dt.date, hasta: dt.date) -> Iterable[Order]:
        self.avisos.append("orders: pendiente T3c")
        return []

    def labels(self, direcciones: Iterable[str]) -> Iterable[Label]:
        self.avisos.append("labels: pendiente T3c")
        return []

    def cargar(
        self,
        con: duckdb.DuckDBPyConnection,
        asset: str,
        market: str,
        desde: dt.date,
        hasta: dt.date,
    ) -> dict:
        """Carga swaps, transfers y fundings de ESTE mercado en la base y devuelve un resumen.

        El resumen trae filas por tabla, swaps por venue y el aviso "pendiente T3c" (nada silencioso).
        """
        self.avisos = []
        self.swaps_omitidos = 0
        swaps = list(self.swaps(asset, desde, hasta))
        cuentas = sorted({s.trader for s in swaps} | {s.counterparty for s in swaps if s.counterparty})
        activos = market.split("/")
        lotes: dict[str, list] = {
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
        conteos = {tabla: insertar_masivo(con, filas) for tabla, filas in lotes.items()}
        por_venue: dict[str, int] = {}
        for s in swaps:
            por_venue[s.venue] = por_venue.get(s.venue, 0) + 1
        return {
            "mercado": market,
            "conteos": conteos,
            "swaps_por_venue": por_venue,
            "swaps_omitidos": self.swaps_omitidos,
            "pendiente_t3c": list(self.PENDIENTE_T3C),
            "avisos": list(self.avisos),
        }


# --- Comando de carga (paso 10) -------------------------------------------------------------------


def cargar_mercados(dir_datos: str | Path, dir_salida: str | Path | None = None) -> list[dict]:
    """Crea un .duckdb por mercado en `dir_salida` (por defecto `dir_datos`) y devuelve los resúmenes."""
    from lumen_lupa.modelo import crear_base

    dir_datos = Path(dir_datos)
    dir_salida = Path(dir_salida) if dir_salida else dir_datos
    dir_salida.mkdir(parents=True, exist_ok=True)
    resumenes = []
    for archivo, activo, mercado in MERCADOS:
        destino = dir_salida / archivo
        if destino.exists():
            destino.unlink()  # recrear desde cero (idempotente)
        con = crear_base(destino)
        try:
            adaptador = StellarHubble(dir_datos, mercado)
            resumen = adaptador.cargar(con, activo, mercado, VENTANA_DESDE, VENTANA_HASTA)
        finally:
            con.close()
        resumen["archivo"] = str(destino)
        resumenes.append(resumen)
    return resumenes


def _imprimir_resumen(r: dict) -> None:
    print(f"\n== {r['archivo']} ({r['mercado']}) ==")
    for tabla, n in r["conteos"].items():
        extra = ""
        if tabla == "swaps" and r["swaps_por_venue"]:
            extra = "  (" + ", ".join(f"{v}={c}" for v, c in sorted(r["swaps_por_venue"].items())) + ")"
        if tabla in r["pendiente_t3c"]:
            extra = "  [pendiente T3c]"
        print(f"  {tabla:<10} {n}{extra}")
    if r.get("swaps_omitidos"):
        print(f"  (swaps_omitidos: {r['swaps_omitidos']} trades dust con un lado = 0 stroops)")


def main(argv: list[str] | None = None) -> int:
    import argparse

    raiz = Path(__file__).resolve().parents[3]
    p = argparse.ArgumentParser(description="Carga los mercados de estudio de Stellar en DuckDB (T3b).")
    p.add_argument("comando", choices=["cargar"], help="acción")
    p.add_argument("--dir-datos", default=str(raiz / "data" / "estudio"), help="carpeta de los .jsonl")
    p.add_argument("--dir-salida", default=None, help="carpeta de los .duckdb (por defecto, la de datos)")
    args = p.parse_args(argv)

    resumenes = cargar_mercados(args.dir_datos, args.dir_salida)
    for r in resumenes:
        _imprimir_resumen(r)
    print("\nAquarius (evento trade), orders, holders y labels: pendiente T3c.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
