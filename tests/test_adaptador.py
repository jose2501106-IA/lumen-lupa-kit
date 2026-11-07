"""N2: la interfaz Adaptador carga filas de su cadena al modelo y rechaza las ajenas."""

from decimal import Decimal

import pytest
from conftest import una_fila_de_cada_tabla

from lumen_lupa.adaptadores import Adaptador
from lumen_lupa.modelo import Funding, Holders, Label, Order, Swap, Transfer


class AdaptadorDePrueba(Adaptador):
    chain = "prueba"

    def __init__(self, filas):
        self.filas = filas

    def _de(self, clase):
        return [f for f in self.filas if isinstance(f, clase)]

    def swaps(self, asset, desde, hasta):
        return self._de(Swap)

    def fundings(self, cuentas):
        return self._de(Funding)

    def transfers(self, cuentas, activos, desde, hasta):
        return self._de(Transfer)

    def holders(self, asset, desde, hasta):
        return self._de(Holders)

    def orders(self, market, desde, hasta):
        return self._de(Order)

    def labels(self, direcciones):
        return self._de(Label)


def test_adaptador_es_abstracto():
    with pytest.raises(TypeError):
        Adaptador()


def test_cargar_deja_las_filas_en_el_modelo(con):
    from conftest import TS

    adaptador = AdaptadorDePrueba(una_fila_de_cada_tabla())
    cargadas = adaptador.cargar(con, "AAA:emisor-1", "AAA:emisor-1/BBB:emisor-2", TS.date(), TS.date())
    assert cargadas == {
        "swaps": 1,
        "fundings": 1,
        "transfers": 1,
        "holders": 1,
        "orders": 1,
        "labels": 1,
    }
    fila = con.execute("SELECT sender, receiver, amount, kind FROM transfers").fetchone()
    assert fila == ("cuenta-a", "cuenta-b", Decimal("7.25"), "payment")
    # entities no viene del adaptador (spec §5.1)
    assert con.execute("SELECT count(*) FROM entities").fetchone()[0] == 0


def test_cargar_rechaza_filas_de_otra_cadena_sin_escribir_nada(con):
    from conftest import TS

    filas = [f for f in una_fila_de_cada_tabla() if not isinstance(f, Label)]
    filas.append(Label("otra", "cuenta-f", "exchange", "fixture"))
    with pytest.raises(ValueError, match="otra|prueba"):
        AdaptadorDePrueba(filas).cargar(con, "AAA:emisor-1", "m", TS.date(), TS.date())
    assert con.execute("SELECT count(*) FROM swaps").fetchone()[0] == 0


def test_cargar_rechaza_transferencias_de_otra_cadena_sin_escribir_nada(con):
    from conftest import TS, una_transferencia

    filas = [f for f in una_fila_de_cada_tabla() if not isinstance(f, Transfer)]
    filas.append(una_transferencia(chain="otra"))
    with pytest.raises(ValueError, match="transfers"):
        AdaptadorDePrueba(filas).cargar(con, "AAA:emisor-1", "m", TS.date(), TS.date())
    for tabla in ("swaps", "transfers"):
        assert con.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0] == 0


def test_cargar_pide_transferencias_de_las_cuentas_de_los_swaps(con):
    from conftest import TS

    pedidas = []

    class Espia(AdaptadorDePrueba):
        def transfers(self, cuentas, activos, desde, hasta):
            pedidas.append((list(cuentas), list(activos), desde, hasta))
            return super().transfers(cuentas, activos, desde, hasta)

    Espia(una_fila_de_cada_tabla()).cargar(
        con, "AAA:emisor-1", "AAA:emisor-1/BBB:emisor-2", TS.date(), TS.date()
    )
    # cargar pasa las cuentas de los swaps y los activos del mercado (ACTIVO/COTIZACION, D-14).
    assert pedidas == [(["cuenta-a", "cuenta-b"], ["AAA:emisor-1", "BBB:emisor-2"], TS.date(), TS.date())]
