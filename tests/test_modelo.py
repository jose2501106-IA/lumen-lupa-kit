"""N2: las siete tablas de la spec v0.2 §4 se crean con sus columnas y aceptan filas válidas."""

import datetime as dt
from decimal import Decimal

import duckdb
import pytest
from conftest import TS, un_swap, una_fila_de_cada_tabla, una_transferencia

from lumen_lupa.modelo import TABLAS, columnas, crear_base, insertar

# Copiado a mano de spec/2026-10-04_mvp-v0.md v0.2 §4: es el oráculo, no se deriva del código.
COLUMNAS_SPEC = {
    "swaps": [
        "chain",
        "ts",
        "ledger",
        "tx_ref",
        "op_ref",
        "venue",
        "pool_id",
        "trader",
        "counterparty",
        "asset_in",
        "amount_in",
        "asset_out",
        "amount_out",
        "usd_low",
        "usd_high",
    ],
    "fundings": [
        "chain",
        "ts",
        "tx_ref",
        "funder",
        "funded",
        "fee_source",
        "sponsor",
        "asset",
        "amount",
        "is_first",
    ],
    "transfers": [
        "chain",
        "ts",
        "ledger",
        "tx_ref",
        "op_ref",
        "sender",
        "receiver",
        "asset",
        "amount",
        "kind",
        "asset_sent",
        "amount_sent",
    ],
    "holders": ["chain", "day", "asset", "holders_count", "new_holders"],
    "orders": ["chain", "ts", "ledger", "tx_ref", "account", "market", "op_type", "amount", "price"],
    "labels": ["chain", "address", "label", "source"],
    "entities": ["chain", "entity_id", "address", "reason"],
}


def test_son_las_siete_tablas_de_la_spec():
    assert list(TABLAS) == list(COLUMNAS_SPEC)
    assert len(TABLAS) == 7


@pytest.mark.parametrize("tabla", list(COLUMNAS_SPEC))
def test_tabla_creada_con_sus_columnas(con, tabla):
    filas = con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position",
        [tabla],
    ).fetchall()
    assert [f[0] for f in filas] == COLUMNAS_SPEC[tabla]
    assert columnas(tabla) == COLUMNAS_SPEC[tabla]


def test_crear_base_en_archivo_es_idempotente(tmp_path):
    ruta = tmp_path / "lumen.duckdb"
    crear_base(ruta).close()
    con = crear_base(ruta)  # segunda vez: no falla ni duplica
    tablas = con.execute("SELECT table_name FROM information_schema.tables ORDER BY table_name").fetchall()
    con.close()
    assert ruta.exists()
    assert sorted(t[0] for t in tablas) == sorted(COLUMNAS_SPEC)


def test_una_fila_de_cada_tabla_entra_y_sale_igual(con):
    filas = una_fila_de_cada_tabla()
    assert insertar(con, filas) == 7
    leido = con.execute("SELECT * FROM swaps").fetchone()
    assert leido[0] == "prueba"
    assert leido[1] == TS
    assert leido[10] == Decimal("10.5")
    assert leido[12] == Decimal("21.0000001")
    for tabla in TABLAS:
        assert con.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0] == 1
    assert con.execute("SELECT day FROM holders").fetchone()[0] == dt.date(2026, 2, 22)


def test_la_base_rechaza_swap_con_el_mismo_activo(con):
    insertar(con, [un_swap()])
    with pytest.raises(duckdb.ConstraintException):
        con.execute("UPDATE swaps SET asset_out = asset_in")


def test_la_base_rechaza_monto_no_positivo(con):
    insertar(con, [un_swap()])
    with pytest.raises(duckdb.ConstraintException):
        con.execute("UPDATE swaps SET amount_in = 0")


@pytest.mark.parametrize(
    "cambios",
    [{"venue": "cex"}, {"usd_low": 3.0, "usd_high": 2.0}],
)
def test_swap_rechaza_valores_fuera_de_la_spec(cambios):
    with pytest.raises(ValueError):
        un_swap(**cambios)


# --- transfers (spec v0.2 §4) ----------------------------------------------------------------


@pytest.mark.parametrize("monto", ["0", "-1"])
def test_la_base_rechaza_transferencia_con_monto_no_positivo(con, monto):
    insertar(con, [una_transferencia()])
    with pytest.raises(duckdb.ConstraintException):
        con.execute(f"UPDATE transfers SET amount = {monto}")


def test_la_base_rechaza_transferencia_con_kind_fuera_de_la_lista(con):
    insertar(con, [una_transferencia()])
    with pytest.raises(duckdb.ConstraintException):
        con.execute("UPDATE transfers SET kind = 'trade'")


@pytest.mark.parametrize("kind", ["payment", "path_payment", "sac_transfer"])
def test_la_base_acepta_los_tres_kinds(con, kind):
    insertar(con, [una_transferencia(kind=kind)])
    assert con.execute("SELECT kind FROM transfers").fetchone()[0] == kind


@pytest.mark.parametrize(
    "cambios",
    [{"amount": Decimal("0")}, {"amount": Decimal("-3")}, {"kind": "swap"}],
)
def test_transfer_rechaza_valores_fuera_de_la_spec(cambios):
    with pytest.raises(ValueError):
        una_transferencia(**cambios)


def test_transfer_a_si_misma_se_permite(con):
    # La spec v0.2 la guarda; S5 la ignora (candidato a arbitraje).
    t = una_transferencia(sender="cuenta-a", receiver="cuenta-a")
    insertar(con, [t])
    assert con.execute("SELECT sender = receiver FROM transfers").fetchone()[0] is True


# --- transfers: asset_sent / amount_sent (D-14, spec v0.3 §4) --------------------------------


def test_transfer_guarda_asset_sent_y_amount_sent(con):
    t = una_transferencia(asset_sent="BBB:emisor-2", amount_sent=Decimal("3.5"))
    insertar(con, [t])
    fila = con.execute("SELECT asset_sent, amount_sent FROM transfers").fetchone()
    assert fila == ("BBB:emisor-2", Decimal("3.5000000"))


def test_transfer_asset_sent_y_amount_sent_son_anulables(con):
    # Son los últimos campos, tras kind, y por defecto None (Hubble puede no traerlos).
    t = una_transferencia()
    assert t.asset_sent is None and t.amount_sent is None
    insertar(con, [t])
    assert con.execute("SELECT amount_sent FROM transfers").fetchone()[0] is None


@pytest.mark.parametrize("monto", [Decimal("0"), Decimal("-2")])
def test_transfer_rechaza_amount_sent_no_positivo_en_python(monto):
    with pytest.raises(ValueError):
        una_transferencia(amount_sent=monto)


@pytest.mark.parametrize("monto", ["0", "-2"])
def test_la_base_rechaza_amount_sent_no_positivo(con, monto):
    insertar(con, [una_transferencia(amount_sent=Decimal("3.5"))])
    with pytest.raises(duckdb.ConstraintException):
        con.execute(f"UPDATE transfers SET amount_sent = {monto}")


def test_insertar_rechaza_objetos_que_no_son_del_modelo(con):
    with pytest.raises(TypeError):
        insertar(con, [object()])
