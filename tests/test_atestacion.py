"""Pruebas sin red del puente de atestaciones (src/lumen_lupa/atestacion.py, contrato T7).

Los hashes esperados se calcularon con `sha256sum` de coreutils, no con Python, sobre el archivo fijo
tests/fixtures/atestacion/reporte_ejemplo.json y sobre el texto canónico escrito a mano:
  sha256sum tests/fixtures/atestacion/reporte_ejemplo.json
  printf '%s' 'v0.1-demo{"lista":[3,1],"nombre":"ñandú","umbral_a":0.5,"umbral_b":2}' | sha256sum
  printf '%s' 'v0.1-demo' | sha256sum

El reporte en formato de T6 (tests/fixtures/atestacion/reporte_forma_t6.json) es SOLO la forma de
`methodology_version`, `parametros` y `hash_datos` del golden de T6
(claude/t6-demo-detector:tests/golden/reporte_demo_semilla_2026.json), extraída con
  jq '{methodology_version, parametros, hash_datos}'
y sus huellas esperadas salen de jq y sha256sum, no de Python:
  sha256sum tests/fixtures/atestacion/reporte_forma_t6.json
  printf '%s%s' v0.1-demo "$(jq -cS .parametros tests/fixtures/atestacion/reporte_forma_t6.json)" | sha256sum
  jq -r .hash_datos.valor tests/fixtures/atestacion/reporte_forma_t6.json
"""

import json
import re
from pathlib import Path

import pytest

from lumen_lupa import atestacion
from lumen_lupa.atestacion import ErrorAtestacion, Huellas, calcular_huellas, comando_publicar

REPORTE = Path(__file__).parent / "fixtures" / "atestacion" / "reporte_ejemplo.json"
HASH_REPORTE = "ba919b725d3527447153e28fe01d9cab0ac3ea70fb44d6fe240e0fd67db50e21"
HASH_METODOLOGIA = "d4a4780831567c5bbc88045979329b32869a6fa658a91be34466b0cb2369cd7d"
HASH_SOLO_VERSION = "38b212a0a9598f4a62811bb93e7f9f13e3a7bbb2902640b47bd4e7e205bfda8a"
HASH_DATOS = "0d1bc6195d5a9843207d551bbabaf3395d91d1d40cdbe979a71bc65f190217f4"
CONTRATO = "C" + "A" * 51 + "D2KM"  # forma de una dirección de contrato; no existe en ninguna red
# Ventana del estudio (D-15): 2026-09-21 00:00:00 a 2026-10-04 23:59:59 UTC.
INICIO, FIN = 1_789_948_800, 1_791_158_399

REPORTE_T6 = Path(__file__).parent / "fixtures" / "atestacion" / "reporte_forma_t6.json"
HASH_REPORTE_T6 = "eb81935624a0965ba0585e3b6caf99e0374bc070a1c6f674cf51d80637ed2e09"
HASH_METODOLOGIA_T6 = "6c5a8053a77d5a56f7509b3eb8a158de7da8fb3ede198cc0a6989fd638f0330f"
HASH_DATOS_T6 = "fe7603c55137b3e5454af9dcc5ff98e16ce7236a0c46b0b41c927b7216a239c7"
# `periodo` del reporte del demo de T6: 2026-01-01 a 2026-01-07, fechas incluidas (00:00:00 a 23:59:59 UTC).
INICIO_T6, FIN_T6 = 1_767_225_600, 1_767_830_399


def escribir(tmp_path: Path, contenido: str | bytes, nombre: str = "reporte.json") -> Path:
    ruta = tmp_path / nombre
    if isinstance(contenido, str):
        contenido = contenido.encode("utf-8")
    ruta.write_bytes(contenido)
    return ruta


# --- huellas ---------------------------------------------------------------------------------------


def test_huellas_conocidas_del_archivo_fijo():
    assert calcular_huellas(REPORTE) == Huellas(HASH_REPORTE, HASH_METODOLOGIA, HASH_DATOS)


def test_hash_reporte_son_los_bytes_tal_cual(tmp_path):
    # Mismo JSON con otro formato: otro hash de reporte, mismas huellas de metodología y datos.
    datos = json.loads(REPORTE.read_text(encoding="utf-8"))
    otro = escribir(tmp_path, json.dumps(datos, ensure_ascii=False))
    huellas = calcular_huellas(otro)
    assert huellas.hash_reporte != HASH_REPORTE
    assert (huellas.hash_metodologia, huellas.hash_datos) == (HASH_METODOLOGIA, HASH_DATOS)


def test_metodologia_sin_parametros_es_solo_la_version(tmp_path):
    sin = escribir(tmp_path, f'{{"methodology_version": "v0.1-demo", "hash_datos": "{HASH_DATOS}"}}')
    assert calcular_huellas(sin).hash_metodologia == HASH_SOLO_VERSION
    con_null = escribir(
        tmp_path, f'{{"methodology_version": "v0.1-demo", "parametros": null, "hash_datos": "{HASH_DATOS}"}}'
    )
    assert calcular_huellas(con_null).hash_metodologia == HASH_SOLO_VERSION


def test_parametros_en_otro_orden_dan_la_misma_huella(tmp_path):
    reordenado = escribir(
        tmp_path,
        f'{{"hash_datos": "{HASH_DATOS}", '
        '"parametros": {"lista": [3, 1], "umbral_a": 0.5, "nombre": "ñandú", "umbral_b": 2}, '
        '"methodology_version": "v0.1-demo"}',
    )
    assert calcular_huellas(reordenado).hash_metodologia == HASH_METODOLOGIA


def test_hash_datos_por_argumento_si_el_reporte_no_lo_trae(tmp_path):
    sin = escribir(tmp_path, '{"methodology_version": "v0.1-demo"}')
    assert calcular_huellas(sin, HASH_DATOS.upper()).hash_datos == HASH_DATOS


def test_hash_datos_del_reporte_y_argumento_iguales_se_acepta():
    assert calcular_huellas(REPORTE, HASH_DATOS).hash_datos == HASH_DATOS


# --- errores claros: no inventa ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("contenido", "mensaje"),
    [
        (f'{{"parametros": {{}}, "hash_datos": "{HASH_DATOS}"}}', "methodology_version"),
        (f'{{"methodology_version": "", "hash_datos": "{HASH_DATOS}"}}', "texto no vacío"),
        (f'{{"methodology_version": 1, "hash_datos": "{HASH_DATOS}"}}', "texto no vacío"),
        ('{"methodology_version": "v0.1-demo"}', "--hash-datos"),
        ('{"methodology_version": "v0.1-demo", "hash_datos": "abc"}', "64 caracteres"),
        (
            f'{{"methodology_version": "v0.1-demo", '
            f'"hash_datos": "{HASH_DATOS}", "hash_datos": "{HASH_DATOS}"}}',
            "repite",
        ),
        (
            f'{{"methodology_version": "v0.1-demo", '
            f'"parametros": {{"x": NaN}}, "hash_datos": "{HASH_DATOS}"}}',
            "NaN",
        ),
        ("[1, 2]", "objeto JSON"),
        ("{no es json", "JSON válido"),
    ],
)
def test_errores_del_reporte(tmp_path, contenido, mensaje):
    with pytest.raises(ErrorAtestacion, match=mensaje):
        calcular_huellas(escribir(tmp_path, contenido))


def test_error_si_el_archivo_no_existe(tmp_path):
    with pytest.raises(ErrorAtestacion, match="no pude leer"):
        calcular_huellas(tmp_path / "no-existe.json")


def test_error_si_no_es_utf8(tmp_path):
    with pytest.raises(ErrorAtestacion, match="UTF-8"):
        calcular_huellas(escribir(tmp_path, b'{"methodology_version": "\xff"}'))


def test_error_si_el_argumento_contradice_al_reporte():
    with pytest.raises(ErrorAtestacion, match="no coincide"):
        calcular_huellas(REPORTE, "f" * 64)


# --- reporte en el formato de T6: `hash_datos` es un objeto ----------------------------------------


def test_huellas_del_reporte_en_formato_t6():
    assert calcular_huellas(REPORTE_T6) == Huellas(HASH_REPORTE_T6, HASH_METODOLOGIA_T6, HASH_DATOS_T6)


def test_comando_con_reporte_en_formato_t6(capsys):
    argumentos = [
        "comando", str(REPORTE_T6), "--red", "testnet", "--identidad", "jose", "--id-contrato", CONTRATO,
        "--version-metodologia", "1", "--ventana-inicio", "2026-01-01T00:00:00Z",
        "--ventana-fin", "2026-01-07T23:59:59Z", "--formato", "lineas",
    ]  # fmt: skip
    assert atestacion.main(argumentos) == 0
    assert capsys.readouterr().out.splitlines() == [
        "stellar", "contract", "invoke",
        "--id", CONTRATO,
        "--source-account", "jose",
        "--network", "testnet",
        "--", "publicar",
        "--hash_reporte", HASH_REPORTE_T6,
        "--hash_metodologia", HASH_METODOLOGIA_T6,
        "--hash_datos", HASH_DATOS_T6,
        "--version_metodologia", "1",
        "--ventana_inicio", str(INICIO_T6),
        "--ventana_fin", str(FIN_T6),
    ]  # fmt: skip


def test_hash_datos_en_objeto_y_argumento():
    # Si el argumento coincide con `hash_datos.valor` se acepta; si no, es un error.
    assert calcular_huellas(REPORTE_T6, HASH_DATOS_T6.upper()).hash_datos == HASH_DATOS_T6
    with pytest.raises(ErrorAtestacion, match="no coincide"):
        calcular_huellas(REPORTE_T6, "f" * 64)


def test_hash_datos_en_objeto_minimo(tmp_path):
    reporte = {"methodology_version": "v0.1-demo", "hash_datos": {"algoritmo": "sha256", "valor": HASH_DATOS}}
    assert calcular_huellas(escribir(tmp_path, json.dumps(reporte))).hash_datos == HASH_DATOS


@pytest.mark.parametrize(
    ("hash_datos", "mensaje"),
    [
        ({"algoritmo": "sha512", "valor": HASH_DATOS}, "usa el algoritmo 'sha512'"),
        ({"valor": HASH_DATOS}, "sin `algoritmo`"),
        ({"algoritmo": "sha256"}, "no trae `valor`"),
        ({"algoritmo": "sha256", "valor": None}, "no trae `valor`"),
        ({"algoritmo": "sha256", "valor": "abc"}, "`hash_datos.valor` del reporte debe ser un sha256"),
        ({"algoritmo": "sha256", "valor": 5}, "`hash_datos.valor` del reporte debe ser un sha256"),
        ([HASH_DATOS], "o un objeto"),
        (12, "o un objeto"),
    ],
)
def test_errores_de_hash_datos_en_el_reporte(tmp_path, hash_datos, mensaje):
    ruta = escribir(tmp_path, json.dumps({"methodology_version": "v0.1-demo", "hash_datos": hash_datos}))
    with pytest.raises(ErrorAtestacion, match=re.escape(mensaje)):
        calcular_huellas(ruta)
    # El argumento no rescata un campo que no se entiende: el reporte dice otra cosa.
    with pytest.raises(ErrorAtestacion, match=re.escape(mensaje)):
        calcular_huellas(ruta, HASH_DATOS)


# --- comando armado ------------------------------------------------------------------------------------


def huellas_fijas() -> Huellas:
    return Huellas(HASH_REPORTE, HASH_METODOLOGIA, HASH_DATOS)


def test_comando_publicar_armado():
    comando = comando_publicar(
        huellas_fijas(),
        red="testnet",
        identidad="jose",
        id_contrato=CONTRATO,
        version_metodologia=1,
        ventana_inicio=INICIO,
        ventana_fin=FIN,
    )
    assert comando == [
        "stellar", "contract", "invoke",
        "--id", CONTRATO,
        "--source-account", "jose",
        "--network", "testnet",
        "--", "publicar",
        "--hash_reporte", HASH_REPORTE,
        "--hash_metodologia", HASH_METODOLOGIA,
        "--hash_datos", HASH_DATOS,
        "--version_metodologia", "1",
        "--ventana_inicio", "1789948800",
        "--ventana_fin", "1791158399",
    ]  # fmt: skip


def test_ventana_en_iso_8601_igual_que_en_segundos():
    con_fechas = comando_publicar(
        huellas_fijas(),
        red="testnet",
        identidad="jose",
        id_contrato=CONTRATO,
        version_metodologia="1",
        ventana_inicio="2026-09-21T00:00:00Z",
        ventana_fin="2026-10-04T17:59:59-06:00",
    )
    con_segundos = comando_publicar(
        huellas_fijas(),
        red="testnet",
        identidad="jose",
        id_contrato=CONTRATO,
        version_metodologia=1,
        ventana_inicio=INICIO,
        ventana_fin=FIN,
    )
    assert con_fechas == con_segundos


def test_mainnet_exige_confirmacion():
    argumentos = dict(
        red="mainnet",
        identidad="jose",
        id_contrato=CONTRATO,
        version_metodologia=1,
        ventana_inicio=INICIO,
        ventana_fin=FIN,
    )
    with pytest.raises(ErrorAtestacion, match="confirmo-mainnet"):
        comando_publicar(huellas_fijas(), **argumentos)
    comando = comando_publicar(huellas_fijas(), confirmo_mainnet=True, **argumentos)
    assert comando[comando.index("--network") + 1] == "mainnet"


@pytest.mark.parametrize(
    ("cambio", "mensaje"),
    [
        ({"red": "futurenet"}, "red desconocida"),
        ({"ventana_inicio": FIN, "ventana_fin": FIN}, "ventana es inválida"),
        ({"ventana_inicio": FIN, "ventana_fin": INICIO}, "ventana es inválida"),
        ({"ventana_inicio": "2026-09-21T00:00:00"}, "zona horaria"),
        ({"ventana_inicio": "2026-09-21T00:00:00.5Z"}, "fracciones"),
        ({"ventana_inicio": "ayer"}, "ISO 8601"),
        ({"ventana_inicio": -1}, "1970"),
        ({"ventana_fin": 2**64}, "u64"),
        ({"version_metodologia": 2**32}, "versión"),
        ({"version_metodologia": "v0.1"}, "versión"),
        ({"version_metodologia": True}, "versión"),
        ({"id_contrato": "GABC"}, "id de contrato"),
        ({"identidad": "-x"}, "identidad inválida"),
    ],
)
def test_errores_del_comando(cambio, mensaje):
    argumentos = dict(
        red="testnet",
        identidad="jose",
        id_contrato=CONTRATO,
        version_metodologia=1,
        ventana_inicio=INICIO,
        ventana_fin=FIN,
    )
    argumentos.update(cambio)
    with pytest.raises(ErrorAtestacion, match=mensaje):
        comando_publicar(huellas_fijas(), **argumentos)


@pytest.mark.parametrize("parece_secreto", ["S" + "A" * 55, "palabra " * 12])
def test_identidad_que_parece_secreta_se_rechaza_sin_repetirla(parece_secreto, capsys):
    argumentos = [
        "comando", str(REPORTE), "--red", "testnet", "--identidad", parece_secreto,
        "--id-contrato", CONTRATO, "--version-metodologia", "1",
        "--ventana-inicio", str(INICIO), "--ventana-fin", str(FIN),
    ]  # fmt: skip
    assert atestacion.main(argumentos) == 2
    salida = capsys.readouterr()
    assert "llave secreta" in salida.err
    assert parece_secreto.strip() not in salida.out + salida.err


# --- línea de comandos ---------------------------------------------------------------------------------


def test_cli_huellas(capsys):
    assert atestacion.main(["huellas", str(REPORTE)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "hash_reporte": HASH_REPORTE,
        "hash_metodologia": HASH_METODOLOGIA,
        "hash_datos": HASH_DATOS,
    }


def test_cli_comando_en_lineas(capsys):
    argumentos = [
        "comando", str(REPORTE), "--red", "testnet", "--identidad", "jose", "--id-contrato", CONTRATO,
        "--version-metodologia", "1", "--ventana-inicio", "2026-09-21T00:00:00Z",
        "--ventana-fin", str(FIN), "--formato", "lineas",
    ]  # fmt: skip
    assert atestacion.main(argumentos) == 0
    lineas = capsys.readouterr().out.splitlines()
    assert lineas[:3] == ["stellar", "contract", "invoke"]
    assert lineas[lineas.index("--hash_reporte") + 1] == HASH_REPORTE
    assert lineas[lineas.index("--ventana_inicio") + 1] == str(INICIO)


def test_cli_comando_en_una_linea(capsys):
    argumentos = [
        "comando", str(REPORTE), "--red", "testnet", "--identidad", "jose", "--id-contrato", CONTRATO,
        "--version-metodologia", "1", "--ventana-inicio", str(INICIO), "--ventana-fin", str(FIN),
    ]  # fmt: skip
    assert atestacion.main(argumentos) == 0
    salida = capsys.readouterr().out.strip()
    assert salida.startswith(
        f"stellar contract invoke --id {CONTRATO} --source-account jose --network testnet"
    )
    assert salida.endswith(f"--ventana_inicio {INICIO} --ventana_fin {FIN}")


def test_cli_error_claro_si_falta_un_campo(tmp_path, capsys):
    sin = escribir(tmp_path, f'{{"hash_datos": "{HASH_DATOS}"}}')
    assert atestacion.main(["huellas", str(sin)]) == 2
    assert "methodology_version" in capsys.readouterr().err


def test_el_modulo_solo_usa_la_biblioteca_estandar():
    import ast
    import sys

    arbol = ast.parse(Path(atestacion.__file__).read_text(encoding="utf-8"))
    modulos = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            modulos.update(a.name.split(".")[0] for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            modulos.add(nodo.module.split(".")[0])
    assert modulos <= set(sys.stdlib_module_names) | {"__future__"}, modulos
