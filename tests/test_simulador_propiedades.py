"""N4 sobre el simulador: determinismo byte por byte (crit. 1) y direcciones ficticias (crit. 5)."""

import dataclasses

from hypothesis import given, settings
from hypothesis import strategies as st

from lumen_lupa.simulador import CUENTA_STELLAR, ESCENARIOS, GENERADORES, combinar
from lumen_lupa.simulador.nucleo import PREFIJO_CUENTA

SEMILLAS = st.integers(min_value=0, max_value=2**32 - 1)
# Una composición arbitraria: 0–3 instancias de cada escenario.
COMPOSICIONES = st.fixed_dictionaries({e: st.integers(min_value=0, max_value=3) for e in ESCENARIOS})

# Campos de las filas del modelo que contienen una dirección.
_CAMPOS_DIRECCION = (
    "trader",
    "counterparty",
    "funder",
    "funded",
    "fee_source",
    "sponsor",
    "account",
    "address",
    "pool_id",
    "sender",
    "receiver",
)


def _direcciones(m):
    dirs = set()
    for f in m.filas:
        for campo in _CAMPOS_DIRECCION:
            v = getattr(f, campo, None)
            if isinstance(v, str):
                dirs.add(v)
    return dirs


# --- criterio 1: misma semilla -> mismas filas, byte por byte -----------------------------------


@settings(max_examples=40)
@given(COMPOSICIONES, SEMILLAS)
def test_misma_semilla_mismas_filas(composicion, semilla):
    a = combinar(composicion, semilla=semilla)
    b = combinar(composicion, semilla=semilla)
    assert a.filas == b.filas
    assert a.verdad == b.verdad


@settings(max_examples=40)
@given(st.sampled_from(ESCENARIOS), SEMILLAS)
def test_cada_escenario_es_reproducible(esc, semilla):
    assert GENERADORES[esc](semilla=semilla).filas == GENERADORES[esc](semilla=semilla).filas


def test_dos_semillas_distintas_dan_mercados_distintos():
    # No es obligatorio, pero una semilla que no cambia nada sería un error silencioso.
    comp = {e: 1 for e in ESCENARIOS}
    assert combinar(comp, semilla=1).filas != combinar(comp, semilla=2).filas


# --- criterio 5: ninguna dirección con formato de cuenta real de Stellar ------------------------


@settings(max_examples=40)
@given(COMPOSICIONES, SEMILLAS)
def test_ninguna_direccion_tiene_formato_real_de_stellar(composicion, semilla):
    for d in _direcciones(combinar(composicion, semilla=semilla)):
        assert not CUENTA_STELLAR.match(d), d
        assert d.startswith(PREFIJO_CUENTA)


def test_el_prefijo_sim_no_es_una_cuenta_real():
    # G + 55 base32 es una cuenta real; "SIM-…" nunca lo es.
    assert not CUENTA_STELLAR.match("SIM-honesto-0-t0")
    assert CUENTA_STELLAR.match("G" + "A" * 55)


@st.composite
def _filas_de_cualquier_escenario(draw):
    esc = draw(st.sampled_from(ESCENARIOS))
    return GENERADORES[esc](semilla=draw(SEMILLAS))


@settings(max_examples=30)
@given(_filas_de_cualquier_escenario())
def test_toda_fila_es_del_modelo_normalizado(m):
    # dataclasses del modelo: tienen el campo `chain`. La verdad va aparte, no en `.filas`.
    assert all(any(f.name == "chain" for f in dataclasses.fields(fila)) for fila in m.filas)
