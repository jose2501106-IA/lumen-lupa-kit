#!/usr/bin/env python3
"""Ejecutor único de BigQuery para Lumen Lupa.

Toda consulta a Hubble pasa por aquí. SIEMPRE:
  1. hace *dry run* e imprime los bytes que procesaría,
  2. se niega a ejecutar si el dry run pasa de 10 GiB sin la bandera --aprobado
     (el "sí" de José; ver CLAUDE.md > Reglas duras > Costo),
  3. ejecuta con maximum_bytes_billed = 20 GiB (BigQuery aborta si se pasa),
  4. consulta job.result().total_rows ANTES de iterar y aborta sin escribir filas si
     pasa de --max-filas (por defecto 200,000): un resultado enorme no debe caber en RAM.
     Aun así deja su fila en el registro con la nota "abortada" (bytes ya facturados),
  5. escribe las filas al .jsonl conforme llegan (sin acumularlas en memoria) y una fila
     de resumen en el registro.csv de la carpeta.

Parámetros de consulta (escalares y arreglos, p. ej. `UNNEST(@cuentas)`): van en un JSON
NO versionado que se pasa con --parametros. Así las listas de cuentas NUNCA se escriben en un
.sql ni en otro archivo versionado (CLAUDE.md > Reglas duras). El registro guarda cuántos valores
tenía cada parámetro, NO los valores.

La carpeta de salida se deriva del .sql: queries/<carpeta>/x.sql escribe sus filas en
data/<carpeta>/x.jsonl (fuera de git) y su resumen en queries/<carpeta>/registro.csv.
Se puede forzar con --dir-datos y --registro.

Uso:
  python3 scripts/bq.py queries/estudio/00_x.sql                       (ejecuta)
  python3 scripts/bq.py queries/estudio/00_x.sql --solo-dry-run        (solo estima)
  python3 scripts/bq.py queries/estudio/00_x.sql --aprobado            (permite > 10 GiB)
  python3 scripts/bq.py queries/estudio/00_x.sql --max-filas 500       (tope de filas)
  python3 scripts/bq.py queries/estudio/00_x.sql --parametros p.json   (parámetros @nombre)

Formato del JSON de --parametros (lista; arreglo con "valores", escalar con "valor"):
  [{"nombre": "cuentas", "tipo": "STRING", "valores": ["G...", "G..."]},
   {"nombre": "min_ops", "tipo": "INT64", "valor": 5}]

Solo lectura. No lee ni imprime credenciales: usa GOOGLE_APPLICATION_CREDENTIALS
ya configurada en el entorno.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import sys
from pathlib import Path

from google.cloud import bigquery

GIB = 1024**3
LIMITE_DRY_RUN_GIB = 10
TOPE_FACTURADO_GIB = 20
LIMITE_DRY_RUN = LIMITE_DRY_RUN_GIB * GIB
TOPE_FACTURADO = TOPE_FACTURADO_GIB * GIB
MAX_FILAS_DEF = 200_000
PROYECTO = "lumen-lupa"  # CLAUDE.md > Recursos

RAIZ = Path(__file__).resolve().parent.parent
DIR_DATOS = RAIZ / "data" / "semana1"
REGISTRO = RAIZ / "queries" / "semana1" / "registro.csv"

# Columnas del registro.csv (una fila por consulta, incluidas las de solo dry run y las abortadas).
CABECERA_REGISTRO = ["consulta", "bytes_dry_run", "bytes_facturados", "filas", "parametros", "nota", "fecha"]
# Cabecera vieja (T1/T3a, 5 columnas): al anexar a un registro con esta cabecera se migra primero.
_CABECERA_VIEJA = ["consulta", "bytes_dry_run", "bytes_facturados", "filas", "fecha"]


class LimiteExcedido(RuntimeError):
    """El dry run supera los 10 GiB y no se pasó --aprobado (falta el sí de José)."""


class ResultadoDemasiadoGrande(RuntimeError):
    """El resultado tiene más filas que --max-filas. Se aborta ANTES de materializar."""


def parametros_desde_json(ruta: str | Path) -> list:
    """Arma QueryParameter de BigQuery desde un JSON no versionado.

    Cada entrada: {"nombre", "tipo", "valores": [...]} (arreglo) o
    {"nombre", "tipo", "valor": x} (escalar). No imprime ni guarda los valores.
    """
    datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
    params = []
    for d in datos:
        nombre, tipo = d["nombre"], d["tipo"]
        if "valores" in d:
            params.append(bigquery.ArrayQueryParameter(nombre, tipo, list(d["valores"])))
        else:
            params.append(bigquery.ScalarQueryParameter(nombre, tipo, d["valor"]))
    return params


def descripcion_parametros(parametros) -> str:
    """Cuenta los valores de cada parámetro, NUNCA los valores. Ej: 'cuentas=5000;min_ops=1'."""
    if not parametros:
        return ""
    partes = []
    for p in parametros:
        n = len(p.values) if isinstance(p, bigquery.ArrayQueryParameter) else 1
        partes.append(f"{p.name}={n}")
    return ";".join(partes)


def tamano_dry_run(cliente, sql: str, parametros=None) -> int:
    """Bytes que la consulta procesaría. Gratis: no factura ni ejecuta."""
    cfg = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    if parametros:
        cfg.query_parameters = parametros
    job = cliente.query(sql, job_config=cfg)
    return int(job.total_bytes_processed)


def _config_ejecucion(parametros=None) -> bigquery.QueryJobConfig:
    # El tope SIEMPRE va: si la consulta se pasa, BigQuery la aborta sin facturar.
    cfg = bigquery.QueryJobConfig(maximum_bytes_billed=TOPE_FACTURADO)
    if parametros:
        cfg.query_parameters = parametros
    return cfg


def _serializar(valor):
    if isinstance(valor, (datetime.date, datetime.datetime, datetime.time)):
        return valor.isoformat()
    if isinstance(valor, (bytes, bytearray)):
        return valor.hex()
    return str(valor)


def _guardar_filas_stream(dir_datos: Path, nombre: str, filas) -> tuple[Path, int]:
    """Escribe cada fila en el .jsonl conforme llega (no acumula en memoria).

    Devuelve (ruta, número de filas escritas).
    """
    dir_datos = Path(dir_datos)
    dir_datos.mkdir(parents=True, exist_ok=True)
    destino = dir_datos / f"{nombre}.jsonl"
    n = 0
    with destino.open("w", encoding="utf-8") as fh:
        for fila in filas:
            fh.write(json.dumps(dict(fila), ensure_ascii=False, default=_serializar) + "\n")
            n += 1
    return destino, n


def _migrar_registro_viejo(registro: Path) -> None:
    """Si el registro tiene la cabecera vieja de 5 columnas (T1/T3a), lo migra a la de 7.

    Las filas viejas ganan `parametros` y `nota` vacíos ANTES de `fecha`, para que anexar filas
    nuevas no las desalinee (la fecha quedaba bajo `nota` y los parámetros bajo `fecha`).
    """
    with registro.open(encoding="utf-8", newline="") as fh:
        filas = list(csv.reader(fh))
    if not filas or filas[0] != _CABECERA_VIEJA:
        return
    migradas = [CABECERA_REGISTRO] + [f[:4] + ["", ""] + f[4:] for f in filas[1:]]
    with registro.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerows(migradas)


def _anotar_registro(
    registro: Path,
    nombre: str,
    bytes_dry: int,
    bytes_facturados: int,
    filas: int,
    parametros: str = "",
    nota: str = "",
) -> None:
    registro = Path(registro)
    registro.parent.mkdir(parents=True, exist_ok=True)
    nuevo = not registro.exists()
    if not nuevo:
        _migrar_registro_viejo(registro)
    with registro.open("a", encoding="utf-8", newline="") as fh:
        escritor = csv.writer(fh)
        if nuevo:
            escritor.writerow(CABECERA_REGISTRO)
        escritor.writerow(
            [nombre, bytes_dry, bytes_facturados, filas, parametros, nota, datetime.date.today().isoformat()]
        )


def ejecutar(
    cliente,
    nombre: str,
    sql: str,
    aprobado: bool = False,
    dir_datos: Path = DIR_DATOS,
    registro: Path = REGISTRO,
    max_filas: int = MAX_FILAS_DEF,
    parametros=None,
) -> dict:
    """Dry run, decide por el tope, ejecuta con maximum_bytes_billed y guarda por páginas."""
    desc_param = descripcion_parametros(parametros)
    bytes_dry = tamano_dry_run(cliente, sql, parametros)
    print(f"[dry run] {nombre}: {bytes_dry / GIB:.3f} GiB ({bytes_dry} bytes)")
    if bytes_dry > LIMITE_DRY_RUN and not aprobado:
        raise LimiteExcedido(
            f"{nombre}: el dry run ({bytes_dry / GIB:.2f} GiB) pasa de {LIMITE_DRY_RUN_GIB} GiB. "
            "Muéstrale la consulta a José y vuelve a correr con --aprobado si dice que sí."
        )
    job = cliente.query(sql, job_config=_config_ejecucion(parametros))
    filas = job.result()
    bytes_facturados = int(job.total_bytes_billed or 0)
    # Mira cuántas filas hay ANTES de materializar: un resultado enorme agota la RAM.
    total = getattr(filas, "total_rows", None)
    if total is not None and total > max_filas:
        # Aun abortada, deja su fila en el registro: los bytes ya se facturaron.
        nota = f"abortada: {total} filas > {max_filas}"
        _anotar_registro(registro, nombre, bytes_dry, bytes_facturados, 0, desc_param, nota)
        raise ResultadoDemasiadoGrande(
            f"{nombre}: el resultado tiene {total} filas (> --max-filas {max_filas}). "
            "Acótalo con GROUP BY / COUNT / LIMIT, o sube --max-filas a conciencia. "
            "No se escribió ninguna fila; sí quedó la fila 'abortada' en el registro."
        )
    destino, n = _guardar_filas_stream(dir_datos, nombre, filas)
    _anotar_registro(registro, nombre, bytes_dry, bytes_facturados, n, desc_param, "")
    return {
        "nombre": nombre,
        "bytes_dry_run": bytes_dry,
        "bytes_facturados": bytes_facturados,
        "filas": n,
        "destino": str(destino),
    }


def _construir_cliente():  # pragma: no cover - necesita credenciales (prueba `live`)
    return bigquery.Client(project=PROYECTO)


def _rutas_salida(ruta_sql: Path, dir_datos_arg, registro_arg) -> tuple[Path, Path]:
    """Deriva data/<carpeta>/ y queries/<carpeta>/registro.csv del .sql, salvo override."""
    carpeta = ruta_sql.parent.name or "semana1"
    dir_datos = Path(dir_datos_arg) if dir_datos_arg else RAIZ / "data" / carpeta
    registro = Path(registro_arg) if registro_arg else ruta_sql.parent / "registro.csv"
    return dir_datos, registro


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Ejecutor único de BigQuery (dry run + tope de bytes).")
    p.add_argument("sql", help="archivo .sql a ejecutar")
    p.add_argument("--nombre", help="nombre de los archivos de salida; por defecto, el del .sql")
    p.add_argument("--aprobado", action="store_true", help="permite un dry run > 10 GiB (el sí de José)")
    p.add_argument("--solo-dry-run", action="store_true", help="solo estima bytes; no ejecuta ni guarda")
    p.add_argument(
        "--max-filas", type=int, default=MAX_FILAS_DEF, help=f"tope de filas (def. {MAX_FILAS_DEF})"
    )
    p.add_argument("--dir-datos", help="carpeta de salida .jsonl (por defecto data/<carpeta del .sql>)")
    p.add_argument("--registro", help="ruta del registro.csv (por defecto junto al .sql)")
    p.add_argument(
        "--parametros",
        help="ruta a un JSON (NO versionado) con parámetros @nombre; nunca escribas cuentas en un .sql",
    )
    args = p.parse_args(argv)

    ruta = Path(args.sql)
    sql = ruta.read_text(encoding="utf-8")
    nombre = args.nombre or ruta.stem
    parametros = parametros_desde_json(args.parametros) if args.parametros else None
    cliente = _construir_cliente()

    dir_datos, registro = _rutas_salida(ruta, args.dir_datos, args.registro)

    if args.solo_dry_run:
        bytes_dry = tamano_dry_run(cliente, sql, parametros)
        print(f"[dry run] {nombre}: {bytes_dry / GIB:.3f} GiB ({bytes_dry} bytes)")
        # El contrato exige una fila por consulta TAMBIÉN para las de solo dry run (criterio 2c).
        _anotar_registro(
            registro, nombre, bytes_dry, 0, 0, descripcion_parametros(parametros), "solo dry run"
        )
        return 0

    try:
        res = ejecutar(
            cliente,
            nombre,
            sql,
            aprobado=args.aprobado,
            dir_datos=dir_datos,
            registro=registro,
            max_filas=args.max_filas,
            parametros=parametros,
        )
    except LimiteExcedido as e:
        print(f"RECHAZADO: {e}", file=sys.stderr)
        return 2
    except ResultadoDemasiadoGrande as e:
        print(f"ABORTADO: {e}", file=sys.stderr)
        return 3
    print(f"[ok] {nombre}: {res['filas']} filas; facturado {res['bytes_facturados'] / GIB:.3f} GiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
