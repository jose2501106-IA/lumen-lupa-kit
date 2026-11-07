"""Los 8 escenarios del contrato T4 y los 2 del contrato T5. Cada uno es una función pública
reproducible por semilla.

Convención de un swap del modelo: `trader` entrega `asset_in`/`amount_in` y recibe
`asset_out`/`amount_out`; `counterparty` toma el otro lado (o `None` si es un pool). La "verdad"
de cada swap (honesto/manipulado, señal esperada, entidad real) va en una tabla aparte.
Las transferencias (`Transfer`, spec v0.2 §4) no llevan verdad propia: la verdad cubre los swaps.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from decimal import Decimal

from lumen_lupa.modelo import Funding, Holders, Label, Order, Swap, Transfer
from lumen_lupa.simulador.nucleo import (
    ACTIVO_POR_DEFECTO,
    CHAIN,
    COTIZACION_POR_DEFECTO,
    INICIO_POR_DEFECTO,
    NATIVO,
    Mercado,
    Verdad,
    _Gen,
)

# Activo intermedio para el ciclo de arbitraje (ficticio).
_INTERMEDIO = "MID:SIM-EMISOR-MID"

# Comisión ficticia de un pool del simulador (0.3 %): lo que pierde quien hace ida y vuelta.
_COMISION_POOL = Decimal("0.003")
_SIETE = Decimal("0.0000001")


# --- utilidades compartidas -------------------------------------------------------------------


def _swap(
    gen: _Gen,
    m: Mercado,
    esc: str,
    *,
    trader: str,
    cp: str | None,
    vende: str,
    cant_vende: Decimal,
    compra: str,
    cant_compra: Decimal,
    venue: str,
    pool_id: str | None,
    manipulada: bool,
    senal: str,
    entidad: str,
    ledger: int | None = None,
    tx: str | None = None,
) -> Swap:
    """Crea un swap y su fila de verdad (1 a 1: toda swap queda cubierta)."""
    if ledger is None:
        ledger = gen.ledger()
    if tx is None:
        tx = gen.tx(esc)
    op = gen.op(esc)
    s = Swap(
        chain=gen.chain,
        ts=gen.ts(ledger),
        ledger=ledger,
        tx_ref=tx,
        op_ref=op,
        venue=venue,
        pool_id=pool_id,
        trader=trader,
        counterparty=cp,
        asset_in=vende,
        amount_in=cant_vende,
        asset_out=compra,
        amount_out=cant_compra,
        usd_low=None,
        usd_high=None,
    )
    m.filas.append(s)
    m.verdad.append(
        Verdad(
            chain=gen.chain,
            op_ref=op,
            escenario=esc,
            manipulada=manipulada,
            senal_esperada=senal,
            entidad_real=entidad,
        )
    )
    return s


def _fondeo(gen: _Gen, m: Mercado, esc: str, funder: str, funded: str, monto: Decimal) -> None:
    m.filas.append(
        Funding(
            chain=gen.chain,
            ts=gen.ts(gen.ledger()),
            tx_ref=gen.tx(esc),
            funder=funder,
            funded=funded,
            fee_source=None,
            sponsor=None,
            asset=NATIVO,
            amount=monto,
            is_first=True,
        )
    )


def _transferencia(
    gen: _Gen,
    m: Mercado,
    esc: str,
    *,
    sender: str,
    receiver: str,
    asset: str,
    monto: Decimal,
    ledger: int | None = None,
) -> Transfer:
    """Un pago simple (`kind="payment"`) de `sender` a `receiver`, con su propia transacción."""
    if ledger is None:
        ledger = gen.ledger()
    t = Transfer(
        chain=gen.chain,
        ts=gen.ts(ledger),
        ledger=ledger,
        tx_ref=gen.tx(esc),
        op_ref=gen.op(esc),
        sender=sender,
        receiver=receiver,
        asset=asset,
        amount=monto,
        kind="payment",
        # En un payment, lo que sale del emisor es igual a lo que recibe el destino (spec v0.3 §4).
        asset_sent=asset,
        amount_sent=monto,
    )
    m.filas.append(t)
    return t


def _etiqueta(gen: _Gen, m: Mercado, address: str, etiqueta: str) -> None:
    m.filas.append(Label(chain=gen.chain, address=address, label=etiqueta, source="simulador"))


def _orden(
    gen: _Gen,
    m: Mercado,
    esc: str,
    *,
    cuenta: str,
    mercado: str,
    op_type: str,
    cantidad: Decimal,
    precio: float,
    ledger: int,
) -> None:
    m.filas.append(
        Order(
            chain=gen.chain,
            ts=gen.ts(ledger),
            ledger=ledger,
            tx_ref=gen.tx(esc),
            account=cuenta,
            market=mercado,
            op_type=op_type,
            amount=cantidad,
            price=precio,
        )
    )


def _holders_que_crecen(gen: _Gen, m: Mercado, dias: int, inicio: int, nuevos: int) -> None:
    """Serie diaria de holders con balance > 0 que aumenta todos los días (spec §4)."""
    cuenta = inicio
    dia0 = gen.inicio.date()
    for d in range(dias):
        m.filas.append(
            Holders(
                chain=gen.chain,
                day=dia0 + dt.timedelta(days=d),
                asset=gen.activo,
                holders_count=cuenta,
                new_holders=nuevos,
            )
        )
        cuenta += nuevos


# --- los escenarios (construyen sobre un _Gen compartido) -----------------------------------


def _honesto(gen: _Gen, m: Mercado, inst: str) -> None:
    """1. Traders independientes, fondeados por orígenes distintos, holders que crecen."""
    esc = "honesto"
    pool = gen.pool(esc, inst)
    for i in range(5):
        cuenta = gen.cuenta(esc, inst, f"t{i}")
        origen = gen.cuenta(esc, inst, f"origen{i}")  # un origen distinto por trader
        _fondeo(gen, m, esc, origen, cuenta, gen.monto(50, 500))
        entidad = f"real-{esc}-{inst}-t{i}"  # cada trader es su propia entidad
        # una venta y una compra, montos variados, contra el pool (sin contraparte)
        _swap(
            gen,
            m,
            esc,
            trader=cuenta,
            cp=None,
            vende=gen.activo,
            cant_vende=gen.monto(10, 100),
            compra=gen.cotizacion,
            cant_compra=gen.monto(10, 100),
            venue="amm",
            pool_id=pool,
            manipulada=False,
            senal="ninguna",
            entidad=entidad,
        )
        _swap(
            gen,
            m,
            esc,
            trader=cuenta,
            cp=None,
            vende=gen.cotizacion,
            cant_vende=gen.monto(10, 100),
            compra=gen.activo,
            cant_compra=gen.monto(10, 100),
            venue="amm",
            pool_id=pool,
            manipulada=False,
            senal="ninguna",
            entidad=entidad,
        )
    _holders_que_crecen(gen, m, dias=7, inicio=100, nuevos=5)


def _anillo_de_lavado(gen: _Gen, m: Mercado, inst: str) -> None:
    """2. S1: 3–6 cuentas de un mismo origen operan entre sí; posición neta ≈ 0 (ida y vuelta)."""
    esc = "anillo_de_lavado"
    k = gen.rng.randint(3, 6)
    funder = gen.cuenta(esc, inst, "origen")  # un solo origen de fondeo
    entidad = f"real-{esc}-{inst}"  # todas son la misma entidad real
    cuentas = [gen.cuenta(esc, inst, f"a{i}") for i in range(k)]
    for c in cuentas:
        _fondeo(gen, m, esc, funder, c, gen.monto(100, 200))
    # pares consecutivos hacen ida y vuelta con el MISMO monto de token: posición neta = 0
    for i in range(k):
        a, b = cuentas[i], cuentas[(i + 1) % k]
        tkn = gen.monto(10, 50)
        usdc = gen.monto(10, 50)
        _swap(
            gen,
            m,
            esc,
            trader=a,
            cp=b,
            vende=gen.activo,
            cant_vende=tkn,
            compra=gen.cotizacion,
            cant_compra=usdc,
            venue="sdex",
            pool_id=None,
            manipulada=True,
            senal="S1",
            entidad=entidad,
        )
        _swap(
            gen,
            m,
            esc,
            trader=b,
            cp=a,
            vende=gen.activo,
            cant_vende=tkn,
            compra=gen.cotizacion,
            cant_compra=usdc,
            venue="sdex",
            pool_id=None,
            manipulada=True,
            senal="S1",
            entidad=entidad,
        )


def _ciclo(gen: _Gen, m: Mercado, inst: str) -> None:
    """3. S5: A vende a B, B a C, C a A en el SDEX; el token regresa al origen, ventana corta."""
    esc = "ciclo"
    entidad = f"real-{esc}-{inst}"
    funder = gen.cuenta(esc, inst, "origen")
    cuentas = [gen.cuenta(esc, inst, f"n{i}") for i in range(3)]
    for c in cuentas:
        _fondeo(gen, m, esc, funder, c, gen.monto(100, 200))
    tkn = gen.monto(20, 60)  # mismo monto alrededor del anillo: regresa al origen
    for i in range(3):
        a, b = cuentas[i], cuentas[(i + 1) % 3]
        _swap(
            gen,
            m,
            esc,
            trader=a,
            cp=b,
            vende=gen.activo,
            cant_vende=tkn,
            compra=gen.cotizacion,
            cant_compra=gen.monto(20, 60),
            venue="sdex",
            pool_id=None,
            manipulada=True,
            senal="S5",
            entidad=entidad,
            ledger=gen.ledger(),
        )


def _bot_mecanico(gen: _Gen, m: Mercado, inst: str) -> None:
    """4. S6: una entidad con montos e intervalos casi constantes. NO es volumen falso."""
    esc = "bot_mecanico"
    entidad = f"real-{esc}-{inst}"
    cuenta = gen.cuenta(esc, inst, "bot")
    _fondeo(gen, m, esc, gen.cuenta(esc, inst, "origen"), cuenta, gen.monto(100, 200))
    pool = gen.pool(esc, inst)
    tkn = Decimal("25.0000000")  # monto constante -> dispersión cero
    usdc = Decimal("30.0000000")
    for _ in range(12):
        _swap(
            gen,
            m,
            esc,
            trader=cuenta,
            cp=None,
            vende=gen.activo,
            cant_vende=tkn,
            compra=gen.cotizacion,
            cant_compra=usdc,
            venue="amm",
            pool_id=pool,
            manipulada=False,
            senal="ninguna",
            entidad=entidad,
            ledger=gen.ledger(salto=10),  # intervalo constante
        )


def _arbitraje(gen: _Gen, m: Mercado, inst: str) -> None:
    """5. Path payment en una sola transacción, origen = destino, ganancia neta en el activo origen."""
    esc = "arbitraje"
    entidad = f"real-{esc}-{inst}"
    p = gen.cuenta(esc, inst, "arb")  # el mismo trader en todo el path: origen = destino
    tx = gen.tx(esc)  # una sola transacción
    ledger = gen.ledger()
    # USDC -> TKN -> MID -> USDC, terminando con más USDC del que entró (ganancia = 5)
    _swap(
        gen,
        m,
        esc,
        trader=p,
        cp=None,
        vende=gen.cotizacion,
        cant_vende=Decimal("100.0000000"),
        compra=gen.activo,
        cant_compra=Decimal("200.0000000"),
        venue="amm",
        pool_id=gen.pool(esc, inst, "p1"),
        manipulada=False,
        senal="arbitraje",
        entidad=entidad,
        ledger=ledger,
        tx=tx,
    )
    _swap(
        gen,
        m,
        esc,
        trader=p,
        cp=None,
        vende=gen.activo,
        cant_vende=Decimal("200.0000000"),
        compra=_INTERMEDIO,
        cant_compra=Decimal("50.0000000"),
        venue="amm",
        pool_id=gen.pool(esc, inst, "p2"),
        manipulada=False,
        senal="arbitraje",
        entidad=entidad,
        ledger=ledger,
        tx=tx,
    )
    _swap(
        gen,
        m,
        esc,
        trader=p,
        cp=None,
        vende=_INTERMEDIO,
        cant_vende=Decimal("50.0000000"),
        compra=gen.cotizacion,
        cant_compra=Decimal("105.0000000"),
        venue="sdex",
        pool_id=None,
        manipulada=False,
        senal="arbitraje",
        entidad=entidad,
        ledger=ledger,
        tx=tx,
    )


def _manipulacion_de_precio(gen: _Gen, m: Mercado, inst: str) -> None:
    """6. S7: mercado sin liquidez; una entidad retira su oferta, coloca una anormal, el precio salta ~100x.

    Inspirado en YieldBlox (22-feb-2026, USTRY/USDC de ~US$1.06 a ~US$107); datos ficticios.
    """
    esc = "manipulacion_de_precio"
    entidad = f"real-{esc}-{inst}"
    manipulador = gen.cuenta(esc, inst, "manip")
    mercado = f"{gen.activo}/{gen.cotizacion}"
    precio_normal = 1.06
    precio_anormal = 107.0  # ~100x
    # poca profundidad: dos ofertas pequeñas de cuentas honestas al precio normal
    for i in range(2):
        _orden(
            gen,
            m,
            esc,
            cuenta=gen.cuenta(esc, inst, f"h{i}"),
            mercado=mercado,
            op_type="manage_sell_offer",
            cantidad=Decimal("5.0000000"),
            precio=precio_normal,
            ledger=gen.ledger(),
        )
    # el manipulador coloca, retira (cantidad 0) y coloca una oferta anormal, en pocos ledgers
    _orden(
        gen,
        m,
        esc,
        cuenta=manipulador,
        mercado=mercado,
        op_type="manage_sell_offer",
        cantidad=Decimal("50.0000000"),
        precio=precio_normal,
        ledger=gen.ledger(),
    )
    _orden(
        gen,
        m,
        esc,
        cuenta=manipulador,
        mercado=mercado,
        op_type="manage_sell_offer",
        cantidad=Decimal("0E-7"),
        precio=precio_normal,
        ledger=gen.ledger(),  # retira su oferta
    )
    _orden(
        gen,
        m,
        esc,
        cuenta=manipulador,
        mercado=mercado,
        op_type="manage_sell_offer",
        cantidad=Decimal("1000.0000000"),
        precio=precio_anormal,
        ledger=gen.ledger(),
    )
    # un swap se ejecuta al precio saltado (~107 USDC por 1 TKN) -> queda cubierto por verdad
    _swap(
        gen,
        m,
        esc,
        trader=manipulador,
        cp=gen.cuenta(esc, inst, "h0"),
        vende=gen.cotizacion,
        cant_vende=Decimal("107.0000000"),
        compra=gen.activo,
        cant_compra=Decimal("1.0000000"),
        venue="sdex",
        pool_id=None,
        manipulada=True,
        senal="S7",
        entidad=entidad,
        ledger=gen.ledger(),
    )


def _trampa_exchange(gen: _Gen, m: Mercado, inst: str) -> None:
    """7. TRAMPA: un exchange fondea muchas cuentas que operan independientes. Verdad: honesto."""
    esc = "trampa_exchange"
    exch = gen.cuenta(esc, inst, "exchange")
    _etiqueta(gen, m, exch, "exchange")
    pool = gen.pool(esc, inst)
    for i in range(8):
        cuenta = gen.cuenta(esc, inst, f"u{i}")
        _fondeo(gen, m, esc, exch, cuenta, gen.monto(10, 100))  # mismo origen = el exchange
        _swap(
            gen,
            m,
            esc,
            trader=cuenta,
            cp=None,
            vende=gen.activo,
            cant_vende=gen.monto(10, 100),
            compra=gen.cotizacion,
            cant_compra=gen.monto(10, 100),
            venue="amm",
            pool_id=pool,
            manipulada=False,
            senal="ninguna",
            entidad=f"real-{esc}-{inst}-u{i}",  # entidad distinta
        )


def _trampa_market_maker(gen: _Gen, m: Mercado, inst: str) -> None:
    """8. TRAMPA: un market maker opera en ambos lados con posición que sí cambia. Verdad: honesto."""
    esc = "trampa_market_maker"
    entidad = f"real-{esc}-{inst}"
    mm = gen.cuenta(esc, inst, "mm")
    _etiqueta(gen, m, mm, "market_maker")
    _fondeo(gen, m, esc, gen.cuenta(esc, inst, "origen"), mm, gen.monto(1000, 2000))
    pool = gen.pool(esc, inst)
    for i in range(10):
        if i % 2 == 0:  # compra TKN (montos grandes)
            _swap(
                gen,
                m,
                esc,
                trader=mm,
                cp=None,
                vende=gen.cotizacion,
                cant_vende=gen.monto(80, 100),
                compra=gen.activo,
                cant_compra=gen.monto(80, 100),
                venue="amm",
                pool_id=pool,
                manipulada=False,
                senal="ninguna",
                entidad=entidad,
            )
        else:  # vende TKN (montos chicos) -> la posición neta crece, no es ≈ 0
            _swap(
                gen,
                m,
                esc,
                trader=mm,
                cp=None,
                vende=gen.activo,
                cant_vende=gen.monto(10, 20),
                compra=gen.cotizacion,
                cant_compra=gen.monto(10, 20),
                venue="amm",
                pool_id=pool,
                manipulada=False,
                senal="ninguna",
                entidad=entidad,
            )


def _ciclo_por_transferencias(gen: _Gen, m: Mercado, inst: str) -> None:
    """9. S5 (spec v0.2 §5.2): A compra X al pool, se lo transfiere a B, B lo vende al pool y le
    devuelve la cotización a A, en una ventana corta. El valor regresa a A (menos comisiones).

    A y B se fondean desde orígenes DISTINTOS: el fondeo común no basta para detectarlo.
    """
    esc = "ciclo_por_transferencias"
    entidad = f"real-{esc}-{inst}"  # A y B son la misma entidad real
    a = gen.cuenta(esc, inst, "A")
    b = gen.cuenta(esc, inst, "B")
    _fondeo(gen, m, esc, gen.cuenta(esc, inst, "origenA"), a, gen.monto(100, 200))
    _fondeo(gen, m, esc, gen.cuenta(esc, inst, "origenB"), b, gen.monto(100, 200))
    pool = gen.pool(esc, inst)
    cot_entra = gen.monto(50, 150)
    x = gen.monto(50, 150)
    # 1) A compra X al pool con la cotización
    _swap(
        gen,
        m,
        esc,
        trader=a,
        cp=None,
        vende=gen.cotizacion,
        cant_vende=cot_entra,
        compra=gen.activo,
        cant_compra=x,
        venue="amm",
        pool_id=pool,
        manipulada=True,
        senal="S5",
        entidad=entidad,
    )
    # 2) A transfiere X a B
    _transferencia(gen, m, esc, sender=a, receiver=b, asset=gen.activo, monto=x)
    # 3) B vende el mismo X al pool; recibe la cotización menos la comisión
    cot_sale = (cot_entra * (1 - 2 * _COMISION_POOL)).quantize(_SIETE)
    _swap(
        gen,
        m,
        esc,
        trader=b,
        cp=None,
        vende=gen.activo,
        cant_vende=x,
        compra=gen.cotizacion,
        cant_compra=cot_sale,
        venue="amm",
        pool_id=pool,
        manipulada=True,
        senal="S5",
        entidad=entidad,
    )
    # 4) B le devuelve la cotización a A: el valor regresa al origen
    _transferencia(gen, m, esc, sender=b, receiver=a, asset=gen.cotizacion, monto=cot_sale)


def _trampa_pagos_honestos(gen: _Gen, m: Mercado, inst: str) -> None:
    """10. TRAMPA: una cuenta paga a varios proveedores independientes que operan por su cuenta.
    El valor nunca regresa al pagador. Verdad: honesto.
    """
    esc = "trampa_pagos_honestos"
    pagador = gen.cuenta(esc, inst, "pagador")
    _fondeo(gen, m, esc, gen.cuenta(esc, inst, "origen-pagador"), pagador, gen.monto(500, 1000))
    pool = gen.pool(esc, inst)
    # el pagador compra X para poder pagar con él
    _swap(
        gen,
        m,
        esc,
        trader=pagador,
        cp=None,
        vende=gen.cotizacion,
        cant_vende=gen.monto(200, 400),
        compra=gen.activo,
        cant_compra=gen.monto(200, 400),
        venue="amm",
        pool_id=pool,
        manipulada=False,
        senal="ninguna",
        entidad=f"real-{esc}-{inst}-pagador",
    )
    k = gen.rng.randint(3, 5)
    for i in range(k):
        prov = gen.cuenta(esc, inst, f"proveedor{i}")
        _fondeo(gen, m, esc, gen.cuenta(esc, inst, f"origen{i}"), prov, gen.monto(50, 100))
        entidad = f"real-{esc}-{inst}-proveedor{i}"  # cada proveedor es su propia entidad
        if i % 2 == 0:  # le paga en X; el proveedor lo vende cuando quiere
            monto = gen.monto(20, 60)
            _transferencia(gen, m, esc, sender=pagador, receiver=prov, asset=gen.activo, monto=monto)
            vende, compra = gen.activo, gen.cotizacion
        else:  # le paga en la cotización; el proveedor compra X por su cuenta
            monto = gen.monto(20, 60)
            _transferencia(gen, m, esc, sender=pagador, receiver=prov, asset=gen.cotizacion, monto=monto)
            vende, compra = gen.cotizacion, gen.activo
        _swap(
            gen,
            m,
            esc,
            trader=prov,
            cp=None,
            vende=vende,
            cant_vende=monto,
            compra=compra,
            cant_compra=gen.monto(20, 60),
            venue="amm",
            pool_id=pool,
            manipulada=False,
            senal="ninguna",
            entidad=entidad,
            ledger=gen.ledger(salto=gen.rng.randint(20, 200)),  # más tarde, a su ritmo
        )


# --- envoltura pública: cada escenario por sí solo, reproducible por semilla ------------------

_Constructor = Callable[[_Gen, Mercado, str], None]


def _solo(
    esc: str,
    constructor: _Constructor,
    semilla: int,
    activo: str,
    cotizacion: str,
    inicio: dt.datetime,
) -> Mercado:
    gen = _Gen(semilla, CHAIN, activo, cotizacion, inicio)
    m = Mercado()
    constructor(gen, m, "0")
    return m


def honesto(
    semilla: int = 0,
    *,
    activo: str = ACTIVO_POR_DEFECTO,
    cotizacion: str = COTIZACION_POR_DEFECTO,
    inicio: dt.datetime = INICIO_POR_DEFECTO,
) -> Mercado:
    return _solo("honesto", _honesto, semilla, activo, cotizacion, inicio)


def anillo_de_lavado(
    semilla: int = 0,
    *,
    activo: str = ACTIVO_POR_DEFECTO,
    cotizacion: str = COTIZACION_POR_DEFECTO,
    inicio: dt.datetime = INICIO_POR_DEFECTO,
) -> Mercado:
    return _solo("anillo_de_lavado", _anillo_de_lavado, semilla, activo, cotizacion, inicio)


def ciclo(
    semilla: int = 0,
    *,
    activo: str = ACTIVO_POR_DEFECTO,
    cotizacion: str = COTIZACION_POR_DEFECTO,
    inicio: dt.datetime = INICIO_POR_DEFECTO,
) -> Mercado:
    return _solo("ciclo", _ciclo, semilla, activo, cotizacion, inicio)


def bot_mecanico(
    semilla: int = 0,
    *,
    activo: str = ACTIVO_POR_DEFECTO,
    cotizacion: str = COTIZACION_POR_DEFECTO,
    inicio: dt.datetime = INICIO_POR_DEFECTO,
) -> Mercado:
    return _solo("bot_mecanico", _bot_mecanico, semilla, activo, cotizacion, inicio)


def arbitraje(
    semilla: int = 0,
    *,
    activo: str = ACTIVO_POR_DEFECTO,
    cotizacion: str = COTIZACION_POR_DEFECTO,
    inicio: dt.datetime = INICIO_POR_DEFECTO,
) -> Mercado:
    return _solo("arbitraje", _arbitraje, semilla, activo, cotizacion, inicio)


def manipulacion_de_precio(
    semilla: int = 0,
    *,
    activo: str = ACTIVO_POR_DEFECTO,
    cotizacion: str = COTIZACION_POR_DEFECTO,
    inicio: dt.datetime = INICIO_POR_DEFECTO,
) -> Mercado:
    return _solo("manipulacion_de_precio", _manipulacion_de_precio, semilla, activo, cotizacion, inicio)


def trampa_exchange(
    semilla: int = 0,
    *,
    activo: str = ACTIVO_POR_DEFECTO,
    cotizacion: str = COTIZACION_POR_DEFECTO,
    inicio: dt.datetime = INICIO_POR_DEFECTO,
) -> Mercado:
    return _solo("trampa_exchange", _trampa_exchange, semilla, activo, cotizacion, inicio)


def trampa_market_maker(
    semilla: int = 0,
    *,
    activo: str = ACTIVO_POR_DEFECTO,
    cotizacion: str = COTIZACION_POR_DEFECTO,
    inicio: dt.datetime = INICIO_POR_DEFECTO,
) -> Mercado:
    return _solo("trampa_market_maker", _trampa_market_maker, semilla, activo, cotizacion, inicio)


def ciclo_por_transferencias(
    semilla: int = 0,
    *,
    activo: str = ACTIVO_POR_DEFECTO,
    cotizacion: str = COTIZACION_POR_DEFECTO,
    inicio: dt.datetime = INICIO_POR_DEFECTO,
) -> Mercado:
    return _solo("ciclo_por_transferencias", _ciclo_por_transferencias, semilla, activo, cotizacion, inicio)


def trampa_pagos_honestos(
    semilla: int = 0,
    *,
    activo: str = ACTIVO_POR_DEFECTO,
    cotizacion: str = COTIZACION_POR_DEFECTO,
    inicio: dt.datetime = INICIO_POR_DEFECTO,
) -> Mercado:
    return _solo("trampa_pagos_honestos", _trampa_pagos_honestos, semilla, activo, cotizacion, inicio)


# El constructor interno de cada escenario, para que `combinar` los corra sobre un _Gen compartido.
_CONSTRUCTORES: dict[str, _Constructor] = {
    "honesto": _honesto,
    "anillo_de_lavado": _anillo_de_lavado,
    "ciclo": _ciclo,
    "bot_mecanico": _bot_mecanico,
    "arbitraje": _arbitraje,
    "manipulacion_de_precio": _manipulacion_de_precio,
    "trampa_exchange": _trampa_exchange,
    "trampa_market_maker": _trampa_market_maker,
    "ciclo_por_transferencias": _ciclo_por_transferencias,
    "trampa_pagos_honestos": _trampa_pagos_honestos,
}
