"""Línea de comandos: ``python -m lumen_lupa.revision {huella,muestra,puntaje}`` (contrato T9).

- ``huella --alertas F``: imprime el sha256 de la lista de alertas (§4).
- ``muestra --alertas F --semilla HEX [--n 25] --salida F``: escribe la muestra en JSON (§5, §6 y §8).
- ``puntaje --muestra F --respuestas A.csv [--segundo B.csv] [--resoluciones R.csv] [--alertas F]
  --salida DIR``: escribe resultado.json y resultado.md en DIR (§7, §8 y §9).

Sale con 0 si todo salió bien y con 2 si una entrada no es válida (el mensaje dice cuál y por qué).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lumen_lupa.revision.alertas import huella, leer_alertas
from lumen_lupa.revision.comun import PROTOCOLO, ErrorRevision
from lumen_lupa.revision.muestreo import N_MUESTRA, armar_muestra, leer_muestra, texto_muestra
from lumen_lupa.revision.puntaje import COMPLETO, puntuar
from lumen_lupa.revision.resultado import escribir, porcentaje
from lumen_lupa.revision.rubrica import leer_resoluciones, leer_respuestas


def _argumentos() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lumen_lupa.revision",
        description=(
            f"Herramienta pública del protocolo de revisión con datos reales (REVIEW_PROTOCOL.md, versión "
            f"{PROTOCOLO}). Solo lee archivos locales: sin red."
        ),
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    h = sub.add_parser("huella", help="imprime el sha256 de la lista de alertas (§4)")
    h.add_argument("--alertas", required=True, type=Path, help="lista de alertas (JSON)")

    m = sub.add_parser("muestra", help="sortea las alertas a revisar y las del segundo revisor (§6 y §8)")
    m.add_argument("--alertas", required=True, type=Path, help="lista de alertas (JSON)")
    m.add_argument("--semilla", required=True, help="hash del ledger del §5: 64 caracteres hexadecimales")
    m.add_argument(
        "--n", type=int, default=N_MUESTRA, help=f"tamaño de la muestra por señal (por omisión, {N_MUESTRA})"
    )
    m.add_argument("--salida", required=True, type=Path, help="archivo JSON donde se escribe la muestra")

    p = sub.add_parser("puntaje", help="etiquetas, regla de aprobación, acuerdo y veredicto (§7 a §9)")
    p.add_argument("--muestra", required=True, type=Path, help="muestra escrita por `muestra`")
    p.add_argument("--respuestas", required=True, type=Path, help="CSV del primer revisor")
    p.add_argument("--segundo", type=Path, help="CSV del segundo revisor (§8)")
    p.add_argument(
        "--resoluciones",
        type=Path,
        help="CSV con las respuestas resueltas de los desacuerdos (§8): alerta_id, q1 a q4, resolucion",
    )
    p.add_argument(
        "--alertas", type=Path, help="lista de alertas (JSON); obligatoria si alguna señal está en censo"
    )
    p.add_argument("--salida", required=True, type=Path, help="directorio para resultado.json y resultado.md")
    return parser


def _huella(args: argparse.Namespace) -> None:
    print(huella(leer_alertas(args.alertas)))


def _muestra(args: argparse.Namespace) -> None:
    muestra = armar_muestra(leer_alertas(args.alertas), args.semilla, args.n)
    try:
        args.salida.parent.mkdir(parents=True, exist_ok=True)
        args.salida.write_text(texto_muestra(muestra), encoding="utf-8")
    except OSError as e:
        raise ErrorRevision(f"no pude escribir la muestra en {args.salida}: {e.strerror}") from e
    print(f"Muestra: {args.salida}")
    print(f"  semilla {muestra.semilla} · n = {muestra.n} · huella de la lista {muestra.huella}")
    for s in muestra.senales:
        print(
            f"  {s.senal}: {s.modo} · {s.poblacion} alertas · {len(s.revisar)} a revisar · "
            f"{len(s.segundo_revisor)} también para el segundo revisor"
        )
    if not muestra.senales:
        print("  la lista no tiene alertas: no hay nada que revisar")


def _puntaje(args: argparse.Namespace) -> None:
    muestra = leer_muestra(args.muestra)
    respuestas = leer_respuestas(args.respuestas, "el CSV del primer revisor")
    segundas = None if args.segundo is None else leer_respuestas(args.segundo, "el CSV del segundo revisor")
    resoluciones = (
        None if args.resoluciones is None else leer_resoluciones(args.resoluciones, "el CSV de resoluciones")
    )
    lista = None if args.alertas is None else leer_alertas(args.alertas)
    resultado = puntuar(muestra, respuestas, segundas, lista, resoluciones)
    ruta_json, ruta_md = escribir(resultado, args.salida)
    print(f"Resultado: {ruta_json} y {ruta_md}")
    for s in resultado.senales:
        veredicto = "pendiente" if s.pasa is None else ("pasa" if s.pasa else "no pasa")
        print(f"  {s.senal}: {s.modo} · {s.revisadas} revisadas · {veredicto}")
    a = resultado.acuerdo
    if a.dobles:
        kappa = "sin definir" if a.kappa is None else f"{float(a.kappa):.3f}"
        print(f"  acuerdo: {a.coincidencias} de {a.dobles} ({porcentaje(a.proporcion)}) · kappa {kappa}")
    if resultado.desacuerdos:
        resueltos = len(resultado.desacuerdos) - len(resultado.sin_resolver())
        print(f"  desacuerdos: {len(resultado.desacuerdos)} · resueltos: {resueltos}")
    if resultado.estado != COMPLETO:
        print(f"  estado: {resultado.estado} · sin veredicto del entregable 1")
    else:
        print(f"  entregable 1: {'cumplido' if resultado.entregable_1 else 'no cumplido'}")


def main(argv: list[str] | None = None) -> int:
    args = _argumentos().parse_args(argv)
    comandos = {"huella": _huella, "muestra": _muestra, "puntaje": _puntaje}
    try:
        comandos[args.comando](args)
    except ErrorRevision as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
