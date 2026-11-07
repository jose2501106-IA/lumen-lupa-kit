"""N4: propiedades de un swap normalizado, para cualquier entrada (hypothesis)."""

from decimal import Decimal

import pytest
from conftest import un_swap
from hypothesis import given, settings
from hypothesis import strategies as st

from lumen_lupa.modelo import crear_base, insertar

# Rango que cabe en un int64 de stroops (7 decimales), como guarda Stellar los montos.
MONTOS = st.decimals(
    min_value=Decimal("0.0000001"),
    max_value=Decimal("922337203685.4775807"),
    places=7,
    allow_nan=False,
    allow_infinity=False,
)
NO_POSITIVOS = st.decimals(max_value=Decimal("0"), places=7, allow_nan=False, allow_infinity=False)
ACTIVOS = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:-", min_size=1, max_size=20)


@st.composite
def swaps_validos(draw):
    asset_in = draw(ACTIVOS)
    asset_out = draw(ACTIVOS.filter(lambda a: a != asset_in))
    return un_swap(
        asset_in=asset_in,
        asset_out=asset_out,
        amount_in=draw(MONTOS),
        amount_out=draw(MONTOS),
        venue=draw(st.sampled_from(["sdex", "pool", "amm"])),
    )


@given(swaps_validos())
def test_swap_normalizado_conserva_montos_positivos_y_activos_distintos(swap):
    assert swap.amount_in > 0
    assert swap.amount_out > 0
    assert swap.asset_in != swap.asset_out


@settings(max_examples=50)
@given(swaps_validos())
def test_swap_sale_de_duckdb_con_los_mismos_montos_y_activos(swap):
    con = crear_base(":memory:")
    insertar(con, [swap])
    fila = con.execute("SELECT asset_in, amount_in, asset_out, amount_out FROM swaps").fetchone()
    con.close()
    assert fila == (swap.asset_in, swap.amount_in, swap.asset_out, swap.amount_out)


@given(NO_POSITIVOS, MONTOS, st.booleans())
def test_swap_rechaza_cualquier_monto_no_positivo(malo, bueno, malo_es_entrada):
    cambios = (
        {"amount_in": malo, "amount_out": bueno}
        if malo_es_entrada
        else {"amount_in": bueno, "amount_out": malo}
    )
    with pytest.raises(ValueError):
        un_swap(**cambios)


@given(ACTIVOS, MONTOS, MONTOS)
def test_swap_rechaza_el_mismo_activo_de_entrada_y_salida(activo, a, b):
    with pytest.raises(ValueError):
        un_swap(asset_in=activo, asset_out=activo, amount_in=a, amount_out=b)
