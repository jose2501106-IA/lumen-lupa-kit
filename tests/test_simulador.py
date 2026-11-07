"""N5: el simulador genera cada escenario con su verdad y su forma (contrato T4).

Cubre los criterios 2, 3 y 4 de TERMINADO CUANDO. Los criterios 1 (determinismo) y 5 (ninguna
dirección real) viven en test_simulador_propiedades.py.
"""

from collections import defaultdict
from decimal import Decimal

import pytest
from conftest import con  # noqa: F401  (fixture de base en memoria)

from lumen_lupa.modelo import TABLAS, Entity, Funding, Label, Order, Transfer
from lumen_lupa.simulador import (
    ESCENARIOS,
    GENERADORES,
    SENALES_ESPERADAS,
    combinar,
    crear_tabla_verdad,
    insertar_mercado,
)

SEMILLA = 2026
ACTIVO = "TKN:SIM-EMISOR-TKN"
COTIZACION = "USDC:SIM-EMISOR-USDC"


def _swaps(m):
    return m.de_tabla("swaps")


def _neto_token(swaps, token):
    """Posición neta del `token` por cuenta: lo recibido menos lo entregado."""
    neto: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for s in swaps:
        if s.asset_in == token:  # el trader entrega el token; la contraparte lo recibe
            neto[s.trader] -= s.amount_in
            if s.counterparty:
                neto[s.counterparty] += s.amount_in
        if s.asset_out == token:  # el trader recibe el token; la contraparte lo entrega
            neto[s.trader] += s.amount_out
            if s.counterparty:
                neto[s.counterparty] -= s.amount_out
    return neto


# --- criterio 4: la tabla verdad cubre el 100 % de los swaps -----------------------------------


@pytest.mark.parametrize("esc", ESCENARIOS)
def test_cada_swap_tiene_exactamente_una_verdad(esc):
    m = GENERADORES[esc](semilla=SEMILLA)
    swaps = _swaps(m)
    ops_swap = [s.op_ref for s in swaps]
    ops_verdad = [v.op_ref for v in m.verdad]
    assert sorted(ops_swap) == sorted(ops_verdad)  # 1 a 1, sin huérfanos ni de más
    assert len(set(ops_verdad)) == len(ops_verdad)  # op_ref único


def test_verdad_cubre_los_swaps_de_un_mercado_combinado():
    m = combinar({e: 1 for e in ESCENARIOS}, semilla=SEMILLA)
    ops_swap = {s.op_ref for s in _swaps(m)}
    ops_verdad = {v.op_ref for v in m.verdad}
    assert ops_swap == ops_verdad


def test_toda_senal_esperada_es_del_catalogo():
    m = combinar({e: 2 for e in ESCENARIOS}, semilla=SEMILLA)
    assert {v.senal_esperada for v in m.verdad} <= set(SENALES_ESPERADAS)


# --- criterio 3: las filas entran a crear_base() sin violar restricciones -----------------------


def test_las_filas_entran_a_la_base_sin_violar_restricciones(con):  # noqa: F811
    m = combinar({e: 2 for e in ESCENARIOS}, semilla=SEMILLA)
    crear_tabla_verdad(con)
    n_modelo, n_verdad = insertar_mercado(con, m)
    assert n_modelo == len(m.filas)
    assert n_verdad == len(m.verdad)
    total = sum(con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLAS)
    assert total == n_modelo
    huerfanos = con.execute(
        "SELECT count(*) FROM swaps s LEFT JOIN verdad v ON s.op_ref = v.op_ref WHERE v.op_ref IS NULL"
    ).fetchone()[0]
    assert huerfanos == 0


def test_el_simulador_no_genera_entidades(con):  # noqa: F811
    # `entities` lo construye el detector (spec §5.1), no el simulador: aquí va la verdad aparte.
    m = combinar({e: 1 for e in ESCENARIOS}, semilla=SEMILLA)
    assert not [f for f in m.filas if isinstance(f, Entity)]


# --- criterio 2: cada escenario, su verdad y su forma -------------------------------------------


def test_honesto_es_honesto_con_entidades_distintas_y_holders_que_crecen():
    m = GENERADORES["honesto"](semilla=SEMILLA)
    assert all(not v.manipulada and v.senal_esperada == "ninguna" for v in m.verdad)
    # cada trader es su propia entidad
    assert len({v.entidad_real for v in m.verdad}) == len(_swaps(m)) // 2 == 5
    # fondeados por orígenes distintos
    fondeos = [f for f in m.filas if isinstance(f, Funding)]
    assert len({f.funder for f in fondeos}) == len(fondeos)
    # holders crecen todos los días
    holders = sorted((f for f in m.filas if type(f).__name__ == "Holders"), key=lambda h: h.day)
    assert len(holders) >= 2
    assert all(b.holders_count > a.holders_count for a, b in zip(holders, holders[1:], strict=False))
    assert all(h.new_holders > 0 for h in holders)


def test_anillo_comparte_origen_y_tiene_posicion_neta_cero():
    m = GENERADORES["anillo_de_lavado"](semilla=SEMILLA)
    assert all(v.manipulada and v.senal_esperada == "S1" for v in m.verdad)
    assert len({v.entidad_real for v in m.verdad}) == 1  # una sola entidad real
    fondeos = [f for f in m.filas if isinstance(f, Funding)]
    assert 3 <= len(fondeos) <= 6  # 3–6 cuentas
    assert len({f.funder for f in fondeos}) == 1  # mismo origen de fondeo
    neto = _neto_token(_swaps(m), ACTIVO)
    assert all(v == Decimal(0) for v in neto.values())  # posición neta ≈ 0


def test_ciclo_forma_un_ciclo_que_regresa_al_origen_en_ventana_corta():
    m = GENERADORES["ciclo"](semilla=SEMILLA)
    swaps = _swaps(m)
    assert all(v.manipulada and v.senal_esperada == "S5" for v in m.verdad)
    # grafo "vendió token a": cada swap aporta una arista trader -> counterparty
    aristas = {s.trader: s.counterparty for s in swaps if s.asset_in == ACTIVO}
    assert len(aristas) == len(swaps) == 3
    # recorrer las aristas desde un nodo regresa a él tras exactamente 3 saltos (un solo ciclo)
    inicio = next(iter(aristas))
    nodo, visitados = inicio, []
    for _ in range(len(aristas)):
        visitados.append(nodo)
        nodo = aristas[nodo]
    assert nodo == inicio and len(set(visitados)) == len(aristas)
    # el token regresa al origen: posición neta por cuenta = 0
    assert all(v == Decimal(0) for v in _neto_token(swaps, ACTIVO).values())
    # ventana corta
    ledgers = [s.ledger for s in swaps]
    assert max(ledgers) - min(ledgers) <= 5


def test_bot_mecanico_tiene_montos_e_intervalos_constantes_y_no_es_manipulado():
    m = GENERADORES["bot_mecanico"](semilla=SEMILLA)
    swaps = sorted(_swaps(m), key=lambda s: s.ledger)
    assert all(not v.manipulada and v.senal_esperada == "ninguna" for v in m.verdad)
    assert len({s.amount_in for s in swaps}) == 1  # dispersión cero en montos
    deltas = {b.ledger - a.ledger for a, b in zip(swaps, swaps[1:], strict=False)}
    assert len(deltas) == 1  # intervalo constante


def test_arbitraje_es_un_path_payment_con_origen_destino_y_ganancia():
    m = GENERADORES["arbitraje"](semilla=SEMILLA)
    swaps = _swaps(m)
    assert all(not v.manipulada and v.senal_esperada == "arbitraje" for v in m.verdad)
    assert len({s.tx_ref for s in swaps}) == 1  # una sola transacción
    assert len({s.trader for s in swaps}) == 1  # origen = destino (mismo trader)
    entra = sum(s.amount_in for s in swaps if s.asset_in == COTIZACION)
    sale = sum(s.amount_out for s in swaps if s.asset_out == COTIZACION)
    assert sale - entra > 0  # ganancia neta en el activo de origen


def test_manipulacion_de_precio_salta_cien_veces_tras_retirar_la_oferta():
    m = GENERADORES["manipulacion_de_precio"](semilla=SEMILLA)
    assert all(v.manipulada and v.senal_esperada == "S7" for v in m.verdad)
    ordenes = sorted((f for f in m.filas if isinstance(f, Order)), key=lambda o: o.ledger)
    precios = [o.price for o in ordenes]
    salto = max(precios) / min(precios)
    assert 90 < salto < 110  # ~100x (YieldBlox: ~1.06 -> ~107)
    # hay un retiro (cantidad 0) antes de la oferta anormal
    retiro = next(o for o in ordenes if o.amount == Decimal(0))
    anormal = max(ordenes, key=lambda o: o.price)
    assert retiro.ledger < anormal.ledger
    # ventana corta sobre órdenes + swap
    ledgers = [o.ledger for o in ordenes] + [s.ledger for s in _swaps(m)]
    assert max(ledgers) - min(ledgers) <= 10


def test_trampa_exchange_es_honesta_pese_al_origen_comun():
    m = GENERADORES["trampa_exchange"](semilla=SEMILLA)
    assert all(not v.manipulada and v.senal_esperada == "ninguna" for v in m.verdad)
    fondeos = [f for f in m.filas if isinstance(f, Funding)]
    assert len({f.funder for f in fondeos}) == 1  # un origen común (el exchange)...
    etiquetas = [f for f in m.filas if isinstance(f, Label)]
    assert any(e.label == "exchange" for e in etiquetas)
    # ...pero cada cuenta es su propia entidad (no deben unirse): sin falso positivo de S2
    assert len({v.entidad_real for v in m.verdad}) == len(fondeos) == 8


def test_trampa_market_maker_es_honesto_con_posicion_que_cambia():
    m = GENERADORES["trampa_market_maker"](semilla=SEMILLA)
    swaps = _swaps(m)
    assert all(not v.manipulada and v.senal_esperada == "ninguna" for v in m.verdad)
    etiquetas = [f for f in m.filas if isinstance(f, Label)]
    assert any(e.label == "market_maker" for e in etiquetas)
    assert any(s.asset_out == ACTIVO for s in swaps)  # compra token
    assert any(s.asset_in == ACTIVO for s in swaps)  # vende token
    neto = _neto_token(swaps, ACTIVO)
    assert any(v != Decimal(0) for v in neto.values())  # la posición sí cambia


# --- contrato T5: escenarios con transferencias (spec v0.2 §4 y §5.2) ---------------------------


def _transfers(m):
    return [f for f in m.filas if isinstance(f, Transfer)]


def _regresa_al_origen(transfers):
    """¿Existe un camino de transferencias (sender -> receiver) que vuelva a su nodo de inicio?

    Se ignoran las transferencias a sí mismo (S5 las ignora, spec v0.2 §4).
    """
    sucesores: dict[str, set[str]] = defaultdict(set)
    for t in transfers:
        if t.sender != t.receiver:
            sucesores[t.sender].add(t.receiver)
    for inicio in list(sucesores):
        pendientes, vistos = list(sucesores[inicio]), set()
        while pendientes:
            nodo = pendientes.pop()
            if nodo == inicio:
                return True
            if nodo not in vistos:
                vistos.add(nodo)
                pendientes.extend(sucesores.get(nodo, ()))
    return False


def test_regresa_al_origen_detecta_un_ciclo_y_no_inventa_uno():
    # Prueba del ayudante: sin esto, una trampa "sin ciclo" podría pasar por un ayudante roto.
    from conftest import una_transferencia

    ida = una_transferencia(sender="a", receiver="b")
    vuelta = una_transferencia(sender="b", receiver="a")
    lejos = una_transferencia(sender="b", receiver="c")
    a_si_misma = una_transferencia(sender="a", receiver="a")
    assert _regresa_al_origen([ida, lejos, vuelta])
    assert not _regresa_al_origen([ida, lejos])
    assert not _regresa_al_origen([a_si_misma])


def test_ciclo_por_transferencias_tiene_verdad_s5_de_una_sola_entidad():
    m = GENERADORES["ciclo_por_transferencias"](semilla=SEMILLA)
    swaps = _swaps(m)
    assert len(swaps) == 2
    assert all(v.manipulada and v.senal_esperada == "S5" for v in m.verdad)
    assert len({v.entidad_real for v in m.verdad}) == 1


def test_ciclo_por_transferencias_regresa_el_valor_a_la_cuenta_de_origen():
    m = GENERADORES["ciclo_por_transferencias"](semilla=SEMILLA)
    compra, venta = sorted(_swaps(m), key=lambda s: s.ledger)
    ida, vuelta = sorted(_transfers(m), key=lambda t: t.ledger)
    a, b = compra.trader, venta.trader
    assert a != b
    # A compra X al pool con la cotización (sin contraparte: es un pool)
    assert compra.counterparty is None and venta.counterparty is None
    assert (compra.asset_in, compra.asset_out) == (COTIZACION, ACTIVO)
    # A -> B por X: exactamente lo que A compró
    assert (ida.sender, ida.receiver, ida.asset, ida.amount) == (a, b, ACTIVO, compra.amount_out)
    # B vende ese mismo X al pool
    assert (venta.asset_in, venta.amount_in, venta.asset_out) == (ACTIVO, ida.amount, COTIZACION)
    # B -> A por la cotización: exactamente lo que B recibió
    assert (vuelta.sender, vuelta.receiver, vuelta.asset) == (b, a, COTIZACION)
    assert vuelta.amount == venta.amount_out
    # el valor regresa a A, menos comisiones (pérdida pequeña y positiva)
    perdida = compra.amount_in - vuelta.amount
    assert Decimal(0) < perdida < compra.amount_in * Decimal("0.01")
    assert _regresa_al_origen(_transfers(m))
    # orden y ventana corta: compra -> ida -> venta -> vuelta
    ledgers = [compra.ledger, ida.ledger, venta.ledger, vuelta.ledger]
    assert ledgers == sorted(ledgers) and ledgers[-1] - ledgers[0] <= 5


def test_ciclo_por_transferencias_no_comparte_origen_de_fondeo():
    m = GENERADORES["ciclo_por_transferencias"](semilla=SEMILLA)
    fondeo = {f.funded: f.funder for f in m.filas if isinstance(f, Funding)}
    a, b = (s.trader for s in sorted(_swaps(m), key=lambda s: s.ledger))
    assert a in fondeo and b in fondeo
    assert fondeo[a] != fondeo[b]


def test_trampa_pagos_honestos_es_honesta_con_entidades_distintas():
    m = GENERADORES["trampa_pagos_honestos"](semilla=SEMILLA)
    assert all(not v.manipulada and v.senal_esperada == "ninguna" for v in m.verdad)
    swaps = _swaps(m)
    assert len({v.entidad_real for v in m.verdad}) == len({s.trader for s in swaps})
    transfers = _transfers(m)
    assert 3 <= len(transfers) <= 5
    pagador = {t.sender for t in transfers}
    assert len(pagador) == 1  # una sola cuenta paga...
    assert len({t.receiver for t in transfers}) == len(transfers)  # ...a proveedores distintos
    fondeos = [f for f in m.filas if isinstance(f, Funding)]
    assert len({f.funder for f in fondeos}) == len(fondeos)  # orígenes distintos
    # cada proveedor opera por su cuenta después de recibir el pago
    for t in transfers:
        assert any(s.trader == t.receiver and s.ledger > t.ledger for s in swaps)


def test_trampa_pagos_honestos_no_tiene_camino_que_regrese_al_origen():
    m = GENERADORES["trampa_pagos_honestos"](semilla=SEMILLA)
    transfers = _transfers(m)
    assert not _regresa_al_origen(transfers)
    pagador = next(iter({t.sender for t in transfers}))
    assert all(t.receiver != pagador for t in transfers)  # nadie le devuelve nada


@pytest.mark.parametrize("semilla", range(20))
def test_los_escenarios_de_t5_mantienen_su_forma_con_cualquier_semilla(semilla):
    ciclo = GENERADORES["ciclo_por_transferencias"](semilla=semilla)
    trampa = GENERADORES["trampa_pagos_honestos"](semilla=semilla)
    assert _regresa_al_origen(_transfers(ciclo))
    assert not _regresa_al_origen(_transfers(trampa))


def test_las_transferencias_usan_direcciones_sim_y_cadena_sintetica():
    m = combinar({e: 1 for e in ESCENARIOS}, semilla=SEMILLA)
    transfers = _transfers(m)
    assert transfers
    assert all(t.chain == "sintetica" for t in transfers)
    assert all(t.sender.startswith("SIM-") and t.receiver.startswith("SIM-") for t in transfers)


def test_combinar_rechaza_un_escenario_desconocido():
    with pytest.raises(ValueError):
        combinar({"inexistente": 1})
