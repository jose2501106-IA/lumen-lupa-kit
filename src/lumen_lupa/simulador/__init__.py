"""Simulador de mercados sintéticos con casos etiquetados (contrato T4, spec §7).

Genera filas del modelo normalizado (`lumen_lupa.modelo`) para una cadena ficticia
(`chain="sintetica"`) y, aparte, una tabla `verdad` con la etiqueta de cada swap: si es
honesto o manipulado, qué señal debería dispararse y a qué entidad real pertenece. Las señales
y el puntaje nunca leen `verdad`; solo sirve para medir precisión y exhaustividad.

Todo es determinista: la misma semilla produce exactamente las mismas filas. No hay dependencias
nuevas (solo `random` de la biblioteca estándar).
"""

from __future__ import annotations

from lumen_lupa.simulador.escenarios import (
    anillo_de_lavado,
    arbitraje,
    bot_mecanico,
    ciclo,
    ciclo_por_transferencias,
    honesto,
    manipulacion_de_precio,
    trampa_exchange,
    trampa_market_maker,
    trampa_pagos_honestos,
)
from lumen_lupa.simulador.mercado import combinar
from lumen_lupa.simulador.nucleo import (
    ACTIVO_POR_DEFECTO,
    COTIZACION_POR_DEFECTO,
    CUENTA_STELLAR,
    ESCENARIOS,
    SENALES_ESPERADAS,
    Mercado,
    Verdad,
    crear_tabla_verdad,
    insertar_mercado,
    insertar_verdad,
)

# Un escenario -> la función que lo genera por sí solo. El orden es el de ESCENARIOS (T4 y luego T5).
GENERADORES = {
    "honesto": honesto,
    "anillo_de_lavado": anillo_de_lavado,
    "ciclo": ciclo,
    "bot_mecanico": bot_mecanico,
    "arbitraje": arbitraje,
    "manipulacion_de_precio": manipulacion_de_precio,
    "trampa_exchange": trampa_exchange,
    "trampa_market_maker": trampa_market_maker,
    "ciclo_por_transferencias": ciclo_por_transferencias,
    "trampa_pagos_honestos": trampa_pagos_honestos,
}

__all__ = [
    "ACTIVO_POR_DEFECTO",
    "COTIZACION_POR_DEFECTO",
    "CUENTA_STELLAR",
    "ESCENARIOS",
    "GENERADORES",
    "SENALES_ESPERADAS",
    "Mercado",
    "Verdad",
    "anillo_de_lavado",
    "arbitraje",
    "bot_mecanico",
    "ciclo",
    "ciclo_por_transferencias",
    "combinar",
    "crear_tabla_verdad",
    "honesto",
    "insertar_mercado",
    "insertar_verdad",
    "manipulacion_de_precio",
    "trampa_exchange",
    "trampa_market_maker",
    "trampa_pagos_honestos",
]
