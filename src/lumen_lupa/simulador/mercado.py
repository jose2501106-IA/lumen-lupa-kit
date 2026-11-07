"""`combinar`: un solo mercado sintético a partir de varios escenarios, con proporciones dadas."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping

from lumen_lupa.modelo import Holders
from lumen_lupa.simulador.escenarios import _CONSTRUCTORES
from lumen_lupa.simulador.nucleo import (
    ACTIVO_POR_DEFECTO,
    CHAIN,
    COTIZACION_POR_DEFECTO,
    ESCENARIOS,
    INICIO_POR_DEFECTO,
    Mercado,
    _Gen,
)


def _sin_holders_duplicados(filas: list[object]) -> list[object]:
    """Holders es una serie por token: si varias instancias repiten un (día, activo), se deja la primera."""
    vistos: set[tuple[str, dt.date, str]] = set()
    salida: list[object] = []
    for f in filas:
        if isinstance(f, Holders):
            clave = (f.chain, f.day, f.asset)
            if clave in vistos:
                continue
            vistos.add(clave)
        salida.append(f)
    return salida


def combinar(
    composicion: Mapping[str, int],
    semilla: int = 0,
    *,
    activo: str = ACTIVO_POR_DEFECTO,
    cotizacion: str = COTIZACION_POR_DEFECTO,
    inicio: dt.datetime = INICIO_POR_DEFECTO,
) -> Mercado:
    """Un mercado con `composicion[escenario]` instancias de cada escenario.

    Devuelve un `Mercado` con las filas del modelo (`.filas`) y las de verdad (`.verdad`). Las
    referencias (`tx_ref`/`op_ref`), ledgers y direcciones son únicas entre instancias, porque todas
    comparten un mismo `_Gen`. Misma semilla y misma composición -> mismas filas, byte por byte.
    """
    desconocidos = set(composicion) - set(ESCENARIOS)
    if desconocidos:
        raise ValueError(f"escenarios desconocidos: {sorted(desconocidos)}")
    if any(n < 0 for n in composicion.values()):
        raise ValueError("las cantidades de la composición no pueden ser negativas")

    gen = _Gen(semilla, CHAIN, activo, cotizacion, inicio)
    m = Mercado()
    # Orden fijo (el de ESCENARIOS) para que el resultado sea reproducible.
    for esc in ESCENARIOS:
        for instancia in range(composicion.get(esc, 0)):
            _CONSTRUCTORES[esc](gen, m, str(instancia))

    return Mercado(filas=_sin_holders_duplicados(m.filas), verdad=m.verdad)
