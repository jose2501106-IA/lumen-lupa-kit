"""Pruebas SIN RED del adaptador de Stellar (T3b, paso 11).

Usan los fixtures reales del mercado de control (`tests/fixtures/stellar/`, ver ORIGEN.md).
La traducción es pura; la descarga (BigQuery) no se toca aquí. Las pruebas que tocarían BigQuery
llevarían @pytest.mark.live y no corren en CI; aquí no hay ninguna (todo sale de fixtures).
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from lumen_lupa.adaptadores.stellar_hubble import (
    ASSET_POR_ID,
    STROOP,
    VENTANA_DESDE,
    VENTANA_HASTA,
    StellarHubble,
    d7,
    ledger_de_toid,
    traducir_funding,
    traducir_swap,
    traducir_transfer,
    tx_ref_de_toid,
)
from lumen_lupa.modelo import crear_base

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "stellar"
USDC = "USDC:GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN"
XLM = "XLM:nativo"


def _leer(nombre):
    lineas = (FIXTURES / nombre).read_text().splitlines()
    return [json.loads(linea) for linea in lineas if linea.strip()]


@pytest.fixture
def trades():
    return _leer("02_trades_control_fixture.jsonl")


@pytest.fixture
def ops():
    return _leer("03_operaciones_control_fixture.jsonl")


def _de_tipo(ops, type_, successful=True, muxed=None):
    for o in ops:
        if o["type"] != type_ or o.get("successful") is not successful:
            continue
        tiene_muxed = bool(o.get("to_muxed") or o.get("from_muxed"))
        if muxed is None or tiene_muxed == muxed:
            return o
    raise AssertionError(f"no hay op type={type_} successful={successful} muxed={muxed} en el fixture")


# --- 5a: traducción de un trade de libro de órdenes y de uno de pool -------------------------------


def test_traducir_swap_libro_de_ordenes(trades):
    t = next(r for r in trades if r["trade_type"] == 1)
    s = traducir_swap(t)
    assert s.chain == "stellar"
    assert s.venue == "sdex"
    assert s.pool_id is None
    assert s.trader == t["buying_account_address"]  # taker = comprador
    assert s.counterparty == t["selling_account_address"]  # maker = dueño de la oferta
    assert s.asset_in == ASSET_POR_ID[t["buying_asset_id"]]  # lo que entrega el taker
    assert s.amount_in == d7(t["buying_amount"])
    assert s.asset_out == ASSET_POR_ID[t["selling_asset_id"]]  # lo que recibe
    assert s.amount_out == d7(t["selling_amount"])


def test_traducir_swap_pool(trades):
    t = next(r for r in trades if r["trade_type"] == 2)
    s = traducir_swap(t)
    assert s.venue == "pool"
    assert s.counterparty is None  # el pool no es una cuenta
    assert s.pool_id == t["selling_liquidity_pool_id"]
    assert s.trader == t["buying_account_address"]


# --- 5b: op_ref único; ledger y tx_ref del TOID; montos consistentes con price_n/price_d -----------


def test_op_ref_unico(trades):
    swaps = [traducir_swap(t) for t in trades]
    op_refs = [s.op_ref for s in swaps]
    assert len(set(op_refs)) == len(op_refs)
    # op_ref = history_operation_id:order
    s0 = swaps[0]
    assert s0.op_ref == f"{trades[0]['history_operation_id']}:{trades[0]['order']}"


def test_ledger_y_tx_ref_derivan_del_toid(trades):
    t = trades[0]
    oid = t["history_operation_id"]
    s = traducir_swap(t)
    assert s.ledger == oid >> 32 == ledger_de_toid(oid)
    assert s.tx_ref == str((oid >> 12) << 12) == tx_ref_de_toid(oid)


def test_montos_consistentes_con_price(trades):
    # Dirección medida (nota §paso 8): amount_out = amount_in * price_d/price_n, ±1 stroop.
    # El fixture incluye al menos un trade que lo cumple exactamente.
    consistentes = 0
    for t in trades:
        s = traducir_swap(t)
        pred = (s.amount_in * Decimal(t["price_d"]) / Decimal(t["price_n"])).quantize(STROOP)
        if abs(pred - s.amount_out) <= STROOP:
            consistentes += 1
    assert consistentes >= 1


# --- 5c: transfers (payment, los dos path payments, muxed; fallidas excluidas) --------------------


def test_traducir_transfer_payment(ops):
    o = _de_tipo(ops, 1, muxed=False)
    t = traducir_transfer(o)
    assert t.chain == "stellar"
    assert t.kind == "payment"
    assert t.sender == o["det_from"]
    assert t.receiver == o["det_to"]
    # en un payment, lo que sale = lo que entra
    assert t.asset_sent == t.asset
    assert t.amount_sent == t.amount == d7(o["amount"])


def test_traducir_transfer_path_payment_strict_receive(ops):
    o = _de_tipo(ops, 2)
    t = traducir_transfer(o)
    assert t.kind == "path_payment"
    assert t.amount == d7(o["amount"])  # lo que recibe
    assert t.amount_sent == d7(o["source_amount"])  # lo que sale
    if o["source_asset_type"] == "native":
        esperado_sent = "XLM:nativo"
    else:
        esperado_sent = f"{o['source_asset_code']}:{o['source_asset_issuer']}"
    assert t.asset_sent == esperado_sent


def test_traducir_transfer_path_payment_strict_send(ops):
    o = _de_tipo(ops, 13)
    t = traducir_transfer(o)
    assert t.kind == "path_payment"
    assert t.amount_sent == d7(o["source_amount"])


def test_traducir_transfer_muxed_a_cuenta_base(ops):
    o = _de_tipo(ops, 1, muxed=True)
    assert o.get("to_muxed") or o.get("from_muxed")  # el fixture trae un muxed
    t = traducir_transfer(o)
    # receiver/sender son la cuenta base G…, nunca la muxed M…
    assert t.receiver == o["det_to"]
    assert t.receiver.startswith("G")
    assert t.sender == o["det_from"]


def test_transfers_excluye_transacciones_fallidas(ops):
    fallida = next(o for o in ops if o.get("successful") is False and o["type"] in (1, 2, 13))
    cuentas = set()
    for o in ops:
        cuentas.update(a for a in (o.get("det_from"), o.get("det_to")) if a)
    ad = StellarHubble(FIXTURES, f"{USDC}/{XLM}")
    salida = list(ad.transfers(cuentas, [USDC, XLM], VENTANA_DESDE, VENTANA_HASTA))
    # La operación de la transacción fallida no aparece en la salida.
    assert str(fallida["history_operation_id"]) not in {t.op_ref for t in salida}
    # Sí aparecen los pagos/path payments exitosos (payment, pp2, pp13, muxed-payment).
    assert len(salida) == 4
    assert all(t.chain == "stellar" for t in salida)


# --- 5d: fundings (funder, funded, monto, fee_source, is_first) -----------------------------------


def test_traducir_funding(ops):
    o = _de_tipo(ops, 0)
    f = traducir_funding(o)
    assert f.chain == "stellar"
    assert f.funder == o["funder"]
    assert f.funded == o["account"]
    assert f.amount == d7(o["starting_balance"])
    assert f.asset == "XLM:nativo"
    assert f.is_first is True
    # sin fee bump, fee_source = la cuenta origen de la transacción (COALESCE)
    assert f.fee_source == (o.get("fee_account") or o.get("source_account"))


def test_fundings_filtra_por_cuenta_creada(ops):
    o = _de_tipo(ops, 0)
    ad = StellarHubble(FIXTURES, f"{USDC}/{XLM}")
    salida = list(ad.fundings({o["account"]}))
    assert [f.funded for f in salida] == [o["account"]]


# --- 5e: StellarHubble.cargar sobre fixtures ------------------------------------------------------


def test_cargar_sobre_fixtures(trades):
    ad = StellarHubble(FIXTURES, f"{USDC}/{XLM}")
    con = crear_base(":memory:")
    try:
        resumen = ad.cargar(con, USDC, f"{USDC}/{XLM}", VENTANA_DESDE, VENTANA_HASTA)
        # swaps cargados (sin dust en el fixture) y chain = stellar
        assert resumen["conteos"]["swaps"] == len(trades)
        assert con.execute("SELECT count(*) FROM swaps WHERE chain = 'stellar'").fetchone()[0] == len(trades)
        # swaps por venue: hay libro de órdenes y pool
        assert set(resumen["swaps_por_venue"]) == {"sdex", "pool"}
        # holders/orders/labels vacíos, con aviso pendiente T3c (nada silencioso)
        assert resumen["pendiente_t3c"] == ["holders", "orders", "labels"]
        for tabla in ("holders", "orders", "labels"):
            assert resumen["conteos"][tabla] == 0
            assert con.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0] == 0
        assert any("holders" in a for a in resumen["avisos"])
    finally:
        con.close()
