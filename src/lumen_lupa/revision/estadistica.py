"""Estadística exacta y sin dependencias (REVIEW_PROTOCOL.md §8 y §9; contrato T9, diseño 5).

- CDF binomial exacta: ``math.comb`` y aritmética entera; las probabilidades son fracciones, no flotantes.
- Cota superior de Clopper–Pearson de una cola: el p con P(X ≤ x | n, p) = 1 − confianza, por bisección
  sobre la CDF, que baja estrictamente con p. Con x = n la cota es 1.
- Máximo de falsos positivos de una muestra de n (§9): el mayor c cuya cota al 90% es menor que 0.30. Como
  la CDF baja estrictamente con p, cota(c) < 0.30 equivale a P(X ≤ c | n, 0.30) < 0.10, y eso se decide con
  enteros, sin redondeo. Para n = 25 da 4. Si ningún c cumple (n ≤ 6), no hay máximo y una muestra de ese
  tamaño no puede pasar.
- Máximo de indeterminados de una muestra de n (§9): ⌊n/3⌋ (8 de 25).
- Acuerdo entre revisores (§8): proporción de etiquetas iguales y kappa de Cohen,
  (p_o − p_e) / (1 − p_e), con p_e de las proporciones de cada revisor en las tres etiquetas. Sin pares, o
  con p_e = 1 (los dos usaron una sola etiqueta, la misma), la kappa no está definida.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from fractions import Fraction
from math import comb

CONFIANZA_CP = Fraction(9, 10)  # §9: una cola, 90%
COTA_MAXIMA_FP = Fraction(3, 10)  # §9: con 90% de confianza, menos del 30% del volumen marcado es erróneo
ITERACIONES_BISECCION = 64  # la cota queda a menos de 2**-64 por encima del valor exacto


def _validar(x: int, n: int) -> None:
    if not (isinstance(n, int) and isinstance(x, int) and 0 <= x <= n and n >= 1):
        raise ValueError(f"se necesita 0 ≤ x ≤ n y n ≥ 1; llegó x = {x!r}, n = {n!r}")


def _probabilidad(p: Fraction | int) -> Fraction:
    p = Fraction(p)
    if not 0 <= p <= 1:
        raise ValueError(f"una probabilidad va de 0 a 1; llegó {p}")
    return p


def _suma_binomial(x: int, n: int, a: int, b: int) -> int:
    """Σ_{k=0..x} comb(n, k) · a^k · (b − a)^(n − k): el numerador de P(X ≤ x | n, a/b) sobre b^n."""
    return sum(comb(n, k) * a**k * (b - a) ** (n - k) for k in range(x + 1))


def cdf_binomial(x: int, n: int, p: Fraction | int) -> Fraction:
    """P(X ≤ x) con X ~ Binomial(n, p), exacta."""
    _validar(x, n)
    p = _probabilidad(p)
    a, b = p.numerator, p.denominator
    return Fraction(_suma_binomial(x, n, a, b), b**n)


def cota_superior_cp(
    x: int, n: int, confianza: Fraction = CONFIANZA_CP, iteraciones: int = ITERACIONES_BISECCION
) -> Fraction:
    """Cota superior de Clopper–Pearson de una cola para x de n, por bisección sobre la CDF.

    Devuelve el extremo superior del último intervalo: el valor exacto está a menos de 2**-iteraciones abajo.
    """
    _validar(x, n)
    alfa = 1 - _probabilidad(confianza)
    if x == n:
        return Fraction(1)
    bajo, alto = Fraction(0), Fraction(1)  # CDF(bajo) > alfa ≥ CDF(alto)
    for _ in range(iteraciones):
        medio = (bajo + alto) / 2
        if cdf_binomial(x, n, medio) > alfa:
            bajo = medio
        else:
            alto = medio
    return alto


def max_falsos_positivos(
    n: int, cota: Fraction = COTA_MAXIMA_FP, confianza: Fraction = CONFIANZA_CP
) -> int | None:
    """El mayor c cuya cota de Clopper–Pearson (una cola, `confianza`) es menor que `cota`; None si no hay."""
    _validar(0, n)
    cota, alfa = _probabilidad(cota), 1 - _probabilidad(confianza)
    a, b = cota.numerator, cota.denominator
    limite = alfa * b**n  # P(X ≤ c | n, cota) < alfa  ⟺  suma entera < alfa · b^n
    maximo, acumulado = None, 0
    for c in range(n + 1):
        acumulado += comb(n, c) * a**c * (b - a) ** (n - c)
        if acumulado >= limite:
            break  # la CDF solo crece con c: ningún c mayor cumple
        maximo = c
    return maximo


def max_indeterminados(n: int) -> int:
    """⌊n/3⌋ (§9)."""
    _validar(0, n)
    return n // 3


def proporcion_de_acuerdo(pares: Sequence[tuple[str, str]]) -> Fraction | None:
    """Proporción de pares con la misma etiqueta; None si no hay pares."""
    if not pares:
        return None
    return Fraction(sum(a == b for a, b in pares), len(pares))


def kappa_cohen(pares: Sequence[tuple[str, str]], categorias: Sequence[str]) -> Fraction | None:
    """Kappa de Cohen de dos revisores sobre las mismas alertas; None si no está definida."""
    desconocidas = {e for par in pares for e in par} - set(categorias)
    if desconocidas:
        raise ValueError(f"etiquetas fuera de las categorías: {sorted(desconocidas)}")
    p_o = proporcion_de_acuerdo(pares)
    if p_o is None:
        return None
    total = len(pares)
    primero = Counter(a for a, _ in pares)
    segundo = Counter(b for _, b in pares)
    p_e = sum((Fraction(primero[c] * segundo[c], total * total) for c in categorias), Fraction(0))
    if p_e == 1:
        return None
    return (p_o - p_e) / (1 - p_e)
