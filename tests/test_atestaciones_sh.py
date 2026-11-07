"""Pruebas sin red de scripts/atestaciones.sh (contrato T7, criterio 5).

Cada prueba pone al frente del PATH un `stellar`, un `sha256sum` y un `shasum` falsos que solo anotan su
llamada en un archivo. Así se comprueba que `--dry-run` no ejecuta nada y que, sin `--dry-run`, el
script llama a `stellar` con los argumentos correctos, sin tocar ninguna red.
"""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lumen_lupa.atestacion import calcular_huellas

RAIZ = Path(__file__).resolve().parents[1]
SCRIPT = RAIZ / "scripts" / "atestaciones.sh"
REPORTE = RAIZ / "tests" / "fixtures" / "atestacion" / "reporte_ejemplo.json"
# Forma del reporte del demo de T6; huellas calculadas con jq y sha256sum (ver tests/test_atestacion.py).
REPORTE_T6 = RAIZ / "tests" / "fixtures" / "atestacion" / "reporte_forma_t6.json"
HASH_REPORTE_T6 = "eb81935624a0965ba0585e3b6caf99e0374bc070a1c6f674cf51d80637ed2e09"
HASH_METODOLOGIA_T6 = "6c5a8053a77d5a56f7509b3eb8a158de7da8fb3ede198cc0a6989fd638f0330f"
HASH_DATOS_T6 = "fe7603c55137b3e5454af9dcc5ff98e16ce7236a0c46b0b41c927b7216a239c7"
README = RAIZ / "contratos" / "atestaciones" / "README.md"
WASM = (
    RAIZ
    / "contratos"
    / "atestaciones"
    / "target"
    / "wasm32v1-none"
    / "release"
    / "lumen_lupa_atestaciones.wasm"
)
CONTRATO = "C" + "A" * 51 + "D2KM"  # forma de una dirección de contrato; no existe en ninguna red
HASH = "ab" * 32
BASH = shutil.which("bash")


@pytest.fixture
def entorno(tmp_path):
    binarios = tmp_path / "bin"
    binarios.mkdir()
    llamadas = tmp_path / "llamadas.log"
    for nombre in ("stellar", "sha256sum", "shasum"):
        falso = binarios / nombre
        falso.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "{nombre} $*" >> "{llamadas}"\n')
        falso.chmod(0o755)
    env = {
        "PATH": f"{binarios}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "LUMEN_LUPA_PYTHON": sys.executable,
    }
    return env, llamadas


def correr(entorno, *argumentos):
    env, _ = entorno
    return subprocess.run(
        [BASH, str(SCRIPT), *argumentos], capture_output=True, text=True, env=env, cwd=RAIZ, timeout=60
    )


def llamadas(entorno) -> list[str]:
    _, archivo = entorno
    return archivo.read_text().splitlines() if archivo.exists() else []


def test_sintaxis_de_bash():
    assert subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True).returncode == 0


def test_desplegar_en_mainnet_sin_confirmacion_sale_con_error(entorno):
    r = correr(entorno, "desplegar", "mainnet", "algo", "--dry-run")
    assert r.returncode != 0
    assert "--confirmo-mainnet" in r.stderr
    assert r.stdout == ""
    assert llamadas(entorno) == []


def test_mainnet_sin_confirmacion_falla_en_todos_los_subcomandos(entorno):
    for argumentos in (
        ["publicar", "mainnet", "jose", CONTRATO, str(REPORTE)],
        ["consultar", "mainnet", CONTRATO, HASH],
    ):
        r = correr(entorno, "--dry-run", *argumentos)
        assert r.returncode != 0, argumentos
        assert "--confirmo-mainnet" in r.stderr
    assert llamadas(entorno) == []


def test_desplegar_en_testnet_con_dry_run_imprime_y_no_ejecuta(entorno):
    r = correr(entorno, "--dry-run", "desplegar", "testnet", "jose")
    assert r.returncode == 0, r.stderr
    lineas = r.stdout.splitlines()
    assert lineas == [
        f"[dry-run] sha256sum {WASM}",
        "[dry-run] stellar keys address jose",
        f"[dry-run] stellar contract deploy --wasm {WASM} --source-account jose --network testnet "
        "-- --admin '<dirección pública de jose>'",
    ]
    assert llamadas(entorno) == []


def test_desplegar_en_mainnet_con_confirmacion_y_dry_run(entorno):
    r = correr(entorno, "--dry-run", "--confirmo-mainnet", "desplegar", "mainnet", "jose")
    assert r.returncode == 0, r.stderr
    assert "--network mainnet" in r.stdout
    assert llamadas(entorno) == []


def test_construir_con_dry_run(entorno):
    r = correr(entorno, "--dry-run", "construir")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == [
        f"[dry-run] env RUSTUP_TOOLCHAIN=1.97.0 stellar contract build --manifest-path "
        f"{RAIZ / 'contratos' / 'atestaciones' / 'Cargo.toml'} --locked",
        f"[dry-run] sha256sum {WASM}",
    ]
    assert llamadas(entorno) == []


def test_publicar_con_dry_run_usa_las_huellas_del_puente(entorno):
    huellas = calcular_huellas(REPORTE)
    r = correr(
        entorno,
        "--dry-run",
        "publicar",
        "testnet",
        "jose",
        CONTRATO,
        str(REPORTE),
        "--version-metodologia",
        "1",
        "--ventana-inicio",
        "2026-09-21T00:00:00Z",
        "--ventana-fin",
        "2026-10-04T23:59:59Z",
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == [
        f"[dry-run] stellar contract invoke --id {CONTRATO} --source-account jose --network testnet "
        f"-- publicar --hash_reporte {huellas.hash_reporte} --hash_metodologia {huellas.hash_metodologia} "
        f"--hash_datos {huellas.hash_datos} --version_metodologia 1 "
        "--ventana_inicio 1789948800 --ventana_fin 1791158399"
    ]
    assert llamadas(entorno) == []


def paso_publicar_del_readme() -> list[str]:
    """El comando `--dry-run publicar` del paso 4 del README del contrato, tal como está escrito."""
    lineas = README.read_text(encoding="utf-8").splitlines()
    inicio = next(
        i for i, linea in enumerate(lineas) if linea.startswith("scripts/atestaciones.sh --dry-run publicar")
    )
    partes = []
    for linea in lineas[inicio:]:
        partes.append(linea.removesuffix("\\").strip())
        if not linea.endswith("\\"):
            break
    return shlex.split(" ".join(partes))


def test_paso_publicar_del_readme_corre_en_dry_run_con_reporte_de_t6(entorno):
    argumentos = paso_publicar_del_readme()
    assert argumentos[:3] == ["scripts/atestaciones.sh", "--dry-run", "publicar"]
    # Solo se cambian las dos piezas que José pone: el ID de su contrato y la ruta del reporte del demo.
    assert argumentos.count("C…") == 1
    assert argumentos.count("salidas/demo/reporte.json") == 1
    sustituciones = {"C…": CONTRATO, "salidas/demo/reporte.json": str(REPORTE_T6)}
    r = correr(entorno, *[sustituciones.get(a, a) for a in argumentos[1:]])
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == [
        f"[dry-run] stellar contract invoke --id {CONTRATO} --source-account jose --network testnet "
        f"-- publicar --hash_reporte {HASH_REPORTE_T6} --hash_metodologia {HASH_METODOLOGIA_T6} "
        f"--hash_datos {HASH_DATOS_T6} --version_metodologia 1 "
        "--ventana_inicio 1767225600 --ventana_fin 1767830399"
    ]
    assert llamadas(entorno) == []


def test_publicar_sin_version_ni_ventana_falla_sin_ejecutar(entorno):
    r = correr(entorno, "--dry-run", "publicar", "testnet", "jose", CONTRATO, str(REPORTE))
    assert r.returncode != 0
    assert "--version-metodologia" in r.stderr
    assert llamadas(entorno) == []


def test_consultar_sin_dry_run_llama_al_stellar_falso_solo_simulando(entorno):
    r = correr(entorno, "--fuente", "jose", "consultar", "testnet", CONTRATO, HASH)
    assert r.returncode == 0, r.stderr
    assert llamadas(entorno) == [
        f"stellar contract invoke --id {CONTRATO} --source-account jose --network testnet --send=no "
        f"-- consultar --hash_reporte {HASH}"
    ]


def test_publicar_sin_dry_run_llama_al_stellar_falso(entorno):
    r = correr(
        entorno,
        "publicar",
        "testnet",
        "jose",
        CONTRATO,
        str(REPORTE),
        "--version-metodologia",
        "1",
        "--ventana-inicio",
        "1789948800",
        "--ventana-fin",
        "1791158399",
    )
    assert r.returncode == 0, r.stderr
    [llamada] = llamadas(entorno)
    assert llamada.startswith(
        f"stellar contract invoke --id {CONTRATO} --source-account jose --network testnet"
    )
    assert f"--hash_reporte {calcular_huellas(REPORTE).hash_reporte}" in llamada


@pytest.mark.parametrize("parece_secreto", ["S" + "B" * 55, "doce palabras de una frase semilla"])
def test_identidad_que_parece_secreta_falla_sin_repetirla(entorno, parece_secreto):
    r = correr(entorno, "--dry-run", "desplegar", "testnet", parece_secreto)
    assert r.returncode != 0
    assert "llave secreta" in r.stderr
    assert parece_secreto not in r.stdout + r.stderr
    assert llamadas(entorno) == []


@pytest.mark.parametrize(
    ("argumentos", "mensaje"),
    [
        (["--dry-run", "firmar"], "subcomando desconocido"),
        (["--dry-run", "desplegar", "futurenet", "jose"], "red desconocida"),
        (["--dry-run", "desplegar", "testnet"], "uso: desplegar"),
        (["--dry-run", "consultar", "testnet", CONTRATO, "abc"], "hash inválido"),
        (["--dry-run", "consultar", "testnet", "GABC", HASH], "id de contrato"),
        (["--dry-run", "--version-metodologia", "1", "construir"], "solo van con publicar"),
        (["--dry-run", "--fuente", "jose", "construir"], "solo va con consultar"),
        (["--dry-run", "--rapido", "construir"], "opción desconocida"),
    ],
)
def test_errores_de_uso(entorno, argumentos, mensaje):
    r = correr(entorno, *argumentos)
    assert r.returncode != 0
    assert mensaje in r.stderr
    assert llamadas(entorno) == []


def test_desplegar_sin_wasm_compilado_pide_construir(entorno):
    if WASM.exists():
        pytest.skip("hay un WASM compilado en esta máquina")
    r = correr(entorno, "desplegar", "testnet", "jose")
    assert r.returncode != 0
    assert "construir" in r.stderr
    assert llamadas(entorno) == []


def test_ayuda(entorno):
    r = correr(entorno, "--help")
    assert r.returncode == 0
    assert "--confirmo-mainnet" in r.stdout
    assert "construir" in r.stdout
