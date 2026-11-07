"""Datos de prueba compartidos. Las direcciones son ficticias y no existen en ninguna cadena."""

import datetime as dt
from decimal import Decimal

import pytest

from lumen_lupa.modelo import Entity, Funding, Holders, Label, Order, Swap, Transfer, crear_base

TS = dt.datetime(2026, 2, 22, 12, 0)  # UTC, sin zona (ver modelo.py)


@pytest.fixture
def con():
    c = crear_base(":memory:")
    yield c
    c.close()


def un_swap(**cambios) -> Swap:
    base = dict(
        chain="prueba",
        ts=TS,
        ledger=100,
        tx_ref="tx-1",
        op_ref="op-1",
        venue="sdex",
        pool_id=None,
        trader="cuenta-a",
        counterparty="cuenta-b",
        asset_in="AAA:emisor-1",
        amount_in=Decimal("10.5"),
        asset_out="BBB:emisor-2",
        amount_out=Decimal("21.0000001"),
        usd_low=1.0,
        usd_high=2.0,
    )
    base.update(cambios)
    return Swap(**base)


def una_transferencia(**cambios) -> Transfer:
    base = dict(
        chain="prueba",
        ts=TS,
        ledger=101,
        tx_ref="tx-4",
        op_ref="op-4",
        sender="cuenta-a",
        receiver="cuenta-b",
        asset="AAA:emisor-1",
        amount=Decimal("7.25"),
        kind="payment",
    )
    base.update(cambios)
    return Transfer(**base)


def una_fila_de_cada_tabla() -> list[object]:
    """Una fila por tabla, en el orden de TABLAS (spec v0.2 §4)."""
    return [
        un_swap(),
        Funding("prueba", TS, "tx-2", "cuenta-f", "cuenta-a", None, None, "native", Decimal("5"), True),
        una_transferencia(),
        Holders("prueba", TS.date(), "AAA:emisor-1", 10, 2),
        Order(
            "prueba",
            TS,
            100,
            "tx-3",
            "cuenta-a",
            "AAA:emisor-1/BBB:emisor-2",
            "manage_sell_offer",
            Decimal("3"),
            1.25,
        ),
        Label("prueba", "cuenta-f", "exchange", "fixture"),
        Entity("prueba", "e-1", "cuenta-a", "mismo firmante"),
    ]
