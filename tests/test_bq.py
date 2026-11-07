"""Pruebas sin red del ejecutor scripts/bq.py (contratos T1 y T3a).

Un cliente falso imita bigquery.Client y registra los job_config que recibe.
El resultado falso imita el RowIterator: expone total_rows y anota si fue iterado,
para probar que el aborto por --max-filas ocurre ANTES de materializar las filas.
No tocan la red ni credenciales: corren por defecto (sin el marcador `live`).
"""

import importlib.util
import json
from pathlib import Path

import pytest

GIB = 1024**3

_RUTA = Path(__file__).resolve().parent.parent / "scripts" / "bq.py"
_spec = importlib.util.spec_from_file_location("bq", _RUTA)
bq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bq)


class _ResultadoFalso:
    """Imita job.result(): iterable con total_rows; anota si alguien lo recorrió."""

    def __init__(self, filas, total_rows=None):
        self._filas = list(filas)
        self.total_rows = len(self._filas) if total_rows is None else total_rows
        self.iterado = False

    def __iter__(self):
        self.iterado = True
        return iter(self._filas)


class _JobFalso:
    def __init__(self, total_bytes_processed=0, total_bytes_billed=0, filas=None, total_rows=None):
        self.total_bytes_processed = total_bytes_processed
        self.total_bytes_billed = total_bytes_billed
        self.resultado = _ResultadoFalso(filas or [], total_rows=total_rows)

    def result(self):
        return self.resultado


class ClienteFalso:
    """Imita bigquery.Client sin red; guarda cada job_config y el último resultado."""

    def __init__(self, bytes_dry=0, bytes_facturados=0, filas=None, total_rows=None):
        self.bytes_dry = bytes_dry
        self.bytes_facturados = bytes_facturados
        self.filas = filas or []
        self.total_rows = total_rows
        self.configs = []
        self.ultimo_resultado = None

    def query(self, sql, job_config=None):
        self.configs.append(job_config)
        if getattr(job_config, "dry_run", False):
            return _JobFalso(total_bytes_processed=self.bytes_dry)
        job = _JobFalso(
            total_bytes_billed=self.bytes_facturados, filas=self.filas, total_rows=self.total_rows
        )
        self.ultimo_resultado = job.resultado
        return job


def test_rechaza_dry_run_mayor_a_10_gib_sin_aprobado(tmp_path):
    cliente = ClienteFalso(bytes_dry=11 * GIB)
    with pytest.raises(bq.LimiteExcedido):
        bq.ejecutar(
            cliente,
            "q",
            "SELECT 1",
            aprobado=False,
            dir_datos=tmp_path / "data",
            registro=tmp_path / "registro.csv",
        )
    # Rechazada antes de ejecutar: solo corrió el dry run, nunca la consulta real.
    assert all(getattr(c, "dry_run", False) for c in cliente.configs)
    assert not (tmp_path / "registro.csv").exists()


def test_con_aprobado_ejecuta_aunque_pase_de_10_gib(tmp_path):
    cliente = ClienteFalso(bytes_dry=11 * GIB, bytes_facturados=GIB, filas=[{"a": 1}])
    res = bq.ejecutar(
        cliente,
        "q",
        "SELECT 1",
        aprobado=True,
        dir_datos=tmp_path / "data",
        registro=tmp_path / "registro.csv",
    )
    assert res["filas"] == 1


def test_siempre_fija_maximum_bytes_billed(tmp_path):
    cliente = ClienteFalso(bytes_dry=GIB, bytes_facturados=GIB, filas=[{"a": 1}])
    bq.ejecutar(
        cliente,
        "q",
        "SELECT 1",
        dir_datos=tmp_path / "data",
        registro=tmp_path / "registro.csv",
    )
    reales = [c for c in cliente.configs if not getattr(c, "dry_run", False)]
    assert len(reales) == 1
    assert reales[0].maximum_bytes_billed == bq.TOPE_FACTURADO
    assert bq.TOPE_FACTURADO == 20 * GIB


def test_dry_run_pide_sin_cache_y_devuelve_bytes():
    cliente = ClienteFalso(bytes_dry=5 * GIB)
    assert bq.tamano_dry_run(cliente, "SELECT 1") == 5 * GIB
    assert cliente.configs[0].dry_run is True
    assert cliente.configs[0].use_query_cache is False


def test_guarda_filas_y_una_fila_de_registro(tmp_path):
    cliente = ClienteFalso(bytes_dry=GIB, bytes_facturados=2 * GIB, filas=[{"a": 1}, {"a": 2}])
    registro = tmp_path / "queries" / "registro.csv"
    bq.ejecutar(cliente, "trades", "SELECT 1", dir_datos=tmp_path / "data", registro=registro)
    assert (tmp_path / "data" / "trades.jsonl").exists()
    lineas = registro.read_text(encoding="utf-8").splitlines()
    assert lineas[0] == "consulta,bytes_dry_run,bytes_facturados,filas,parametros,nota,fecha"
    assert lineas[1].startswith("trades,")
    assert lineas[1].split(",")[3] == "2"  # filas


def test_aborta_si_pasa_de_max_filas_antes_de_iterar(tmp_path):
    # total_rows (300k) supera --max-filas (200k): debe abortar sin tocar las filas.
    cliente = ClienteFalso(bytes_dry=GIB, bytes_facturados=GIB, filas=[{"a": 1}], total_rows=300_000)
    with pytest.raises(bq.ResultadoDemasiadoGrande):
        bq.ejecutar(
            cliente,
            "q",
            "SELECT 1",
            dir_datos=tmp_path / "data",
            registro=tmp_path / "registro.csv",
            max_filas=200_000,
        )
    # Abortó ANTES de iterar y no escribió el .jsonl...
    assert cliente.ultimo_resultado.iterado is False
    assert not (tmp_path / "data" / "q.jsonl").exists()
    # ...pero SÍ dejó su fila en el registro con la nota "abortada" (bytes ya facturados).
    lineas = (tmp_path / "registro.csv").read_text(encoding="utf-8").splitlines()
    assert lineas[0] == "consulta,bytes_dry_run,bytes_facturados,filas,parametros,nota,fecha"
    fila = lineas[1].split(",")
    assert fila[0] == "q"
    assert fila[3] == "0"  # filas escritas
    assert "abortada" in fila[5]  # nota


def test_parametros_desde_json_arreglo_y_escalar(tmp_path):
    ruta = tmp_path / "p.json"
    ruta.write_text(
        json.dumps(
            [
                {"nombre": "cuentas", "tipo": "STRING", "valores": ["GAAA", "GBBB", "GCCC"]},
                {"nombre": "min_ops", "tipo": "INT64", "valor": 5},
            ]
        ),
        encoding="utf-8",
    )
    params = bq.parametros_desde_json(ruta)
    assert params[0].name == "cuentas"
    assert list(params[0].values) == ["GAAA", "GBBB", "GCCC"]
    assert params[1].name == "min_ops"
    assert params[1].value == 5


def test_descripcion_parametros_cuenta_valores_sin_revelarlos():
    from google.cloud import bigquery

    params = [
        bigquery.ArrayQueryParameter("cuentas", "STRING", ["GSECRETA1", "GSECRETA2", "GSECRETA3"]),
        bigquery.ScalarQueryParameter("min_ops", "INT64", 5),
    ]
    desc = bq.descripcion_parametros(params)
    assert desc == "cuentas=3;min_ops=1"
    # Nunca aparece un valor de cuenta en la descripción que va al registro.
    assert "GSECRETA" not in desc


def test_anotar_registro_migra_la_cabecera_vieja_de_5_columnas(tmp_path):
    # Un registro de T1/T3a (5 columnas) no debe quedar desalineado al anexarle filas de 7.
    registro = tmp_path / "registro.csv"
    registro.write_text(
        "consulta,bytes_dry_run,bytes_facturados,filas,fecha\nvieja,1,2,3,2026-10-04\n",
        encoding="utf-8",
    )
    bq._anotar_registro(registro, "nueva", 10, 20, 30, "cuentas=5", "")
    lineas = registro.read_text(encoding="utf-8").splitlines()
    assert lineas[0] == ",".join(bq.CABECERA_REGISTRO)
    # La fila vieja gana parametros/nota vacíos ANTES de la fecha.
    assert lineas[1] == "vieja,1,2,3,,,2026-10-04"
    # La fila nueva queda alineada: parametros en su columna y fecha al final.
    import csv as csv_mod

    filas = list(csv_mod.DictReader(lineas))
    assert filas[1]["parametros"] == "cuentas=5"
    assert filas[1]["fecha"].startswith("20")


def test_solo_dry_run_tambien_deja_fila_en_el_registro(tmp_path, monkeypatch):
    # Criterio 2c del contrato T3b: una fila por consulta, incluidas las de solo dry run.
    cliente = ClienteFalso(bytes_dry=GIB)
    monkeypatch.setattr(bq, "_construir_cliente", lambda: cliente)
    sql = tmp_path / "q.sql"
    sql.write_text("SELECT 1", encoding="utf-8")
    registro = tmp_path / "registro.csv"
    assert bq.main([str(sql), "--solo-dry-run", "--registro", str(registro)]) == 0
    lineas = registro.read_text(encoding="utf-8").splitlines()
    fila = lineas[1].split(",")
    assert fila[0] == "q"
    assert fila[2] == "0"  # facturado
    assert fila[5] == "solo dry run"  # nota


def test_ejecutar_pasa_los_parametros_al_dry_run_y_a_la_ejecucion(tmp_path):
    from google.cloud import bigquery

    cliente = ClienteFalso(bytes_dry=GIB, bytes_facturados=GIB, filas=[{"a": 1}])
    params = [bigquery.ArrayQueryParameter("cuentas", "STRING", ["GAAA", "GBBB"])]
    bq.ejecutar(
        cliente,
        "q",
        "SELECT 1 WHERE x IN UNNEST(@cuentas)",
        dir_datos=tmp_path / "data",
        registro=tmp_path / "registro.csv",
        parametros=params,
    )
    # Tanto el dry run como la ejecución real recibieron los parámetros.
    assert all(c.query_parameters == params for c in cliente.configs)
    # El registro guarda el conteo del parámetro, no los valores.
    fila = (tmp_path / "registro.csv").read_text(encoding="utf-8").splitlines()[1].split(",")
    assert fila[4] == "cuentas=2"
    assert "GAAA" not in (tmp_path / "registro.csv").read_text(encoding="utf-8")


def test_escribe_filas_por_streaming(tmp_path):
    filas = [{"a": 1}, {"a": 2}, {"a": 3}]
    cliente = ClienteFalso(bytes_dry=GIB, bytes_facturados=GIB, filas=filas)
    res = bq.ejecutar(
        cliente, "q", "SELECT 1", dir_datos=tmp_path / "data", registro=tmp_path / "registro.csv"
    )
    assert res["filas"] == 3
    lineas = (tmp_path / "data" / "q.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lineas) == 3
    assert json.loads(lineas[0]) == {"a": 1}
    assert json.loads(lineas[2]) == {"a": 3}
    # Se escribió recorriendo el iterador (streaming), no una lista materializada aparte.
    assert cliente.ultimo_resultado.iterado is True


def test_bajo_el_max_filas_no_aborta(tmp_path):
    cliente = ClienteFalso(bytes_dry=GIB, bytes_facturados=GIB, filas=[{"a": 1}], total_rows=5)
    res = bq.ejecutar(
        cliente,
        "q",
        "SELECT 1",
        dir_datos=tmp_path / "data",
        registro=tmp_path / "registro.csv",
        max_filas=10,
    )
    assert res["filas"] == 1
