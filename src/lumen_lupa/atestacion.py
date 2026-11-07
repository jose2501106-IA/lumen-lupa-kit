"""Puente entre un reporte de Lumen Lupa y el contrato Soroban de atestaciones (D-16, contrato T7).

Calcula las tres huellas que guarda el contrato y arma, sin ejecutarlo, el comando de `stellar` que las
publica. Solo usa la biblioteca estándar: no abre la red, no lee llaves y no firma nada.

Reglas (contrato T7, diseño 5):

- ``hash_reporte``: sha256 de los bytes del archivo ``reporte.json``, tal cual están en disco.
- ``hash_metodologia``: sha256 del texto de ``methodology_version`` (UTF-8) seguido, sin separador, del
  JSON canónico de ``parametros`` si el reporte los trae (clave presente y distinta de ``null``).
- ``hash_datos``: el campo ``hash_datos`` del reporte si existe; si no, el que se pase como argumento.
  El campo puede ser el hex plano (64 caracteres) o el objeto que escribe el demo de T6:
  ``{"algoritmo": "sha256", "valor": "<64 hex>", "forma": …, "filas": …}``; de ese objeto se toma
  ``valor`` y solo se acepta el algoritmo ``sha256``. Si vienen el campo y el argumento y no coinciden,
  es un error.
- JSON canónico: ``json.dumps(valor, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
  allow_nan=False)`` en UTF-8. Claves ordenadas, sin espacios, sin NaN ni infinitos.

Si falta un campo necesario o un dato no es válido, falla con un mensaje claro: no inventa valores. La
versión numérica de la metodología y la ventana (inicio y fin) no se deducen del reporte: se pasan
explícitamente.

Uso: ``python -m lumen_lupa.atestacion --help`` (Python 3.11 o más reciente).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shlex
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

CAMPO_METODOLOGIA = "methodology_version"
CAMPO_PARAMETROS = "parametros"
CAMPO_HASH_DATOS = "hash_datos"

REDES = ("testnet", "mainnet")
U32_MAX = 2**32 - 1
U64_MAX = 2**64 - 1

_HEX64 = re.compile(r"[0-9a-f]{64}")
_ENTERO = re.compile(r"[0-9]+")
_IDENTIDAD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_PARECE_SECRETO = re.compile(r"S[A-Z2-7]{55}")
_ID_CONTRATO = re.compile(r"C[A-Z2-7]{55}|[a-z0-9][a-z0-9_-]{0,63}")


class ErrorAtestacion(ValueError):
    """Dato faltante o inválido. El mensaje dice qué falta y cómo darlo."""


@dataclass(frozen=True)
class Huellas:
    """Las tres huellas de un reporte, en hexadecimal de 64 caracteres (minúsculas)."""

    hash_reporte: str
    hash_metodologia: str
    hash_datos: str


def sha256_hex(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def json_canonico(valor: object) -> bytes:
    """JSON canónico en UTF-8: claves ordenadas, sin espacios, sin NaN ni infinitos."""
    try:
        texto = json.dumps(valor, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as e:
        raise ErrorAtestacion(f"los parámetros no se pueden escribir como JSON canónico: {e}") from e
    return texto.encode("utf-8")


def _sin_claves_repetidas(pares: list[tuple[str, object]]) -> dict[str, object]:
    objeto: dict[str, object] = {}
    for clave, valor in pares:
        if clave in objeto:
            raise ErrorAtestacion(f"el reporte repite la clave {clave!r}; es ambiguo")
        objeto[clave] = valor
    return objeto


def _rechazar_constante(nombre: str) -> float:
    raise ErrorAtestacion(f"el reporte trae {nombre}, que no es JSON estándar")


def leer_reporte(ruta: Path) -> tuple[bytes, dict[str, object]]:
    """Devuelve los bytes del reporte tal cual y su contenido. Exige un objeto JSON sin claves repetidas."""
    try:
        crudo = ruta.read_bytes()
    except OSError as e:
        raise ErrorAtestacion(f"no pude leer el reporte {ruta}: {e.strerror}") from e
    try:
        datos = json.loads(
            crudo.decode("utf-8"),
            object_pairs_hook=_sin_claves_repetidas,
            parse_constant=_rechazar_constante,
        )
    except UnicodeDecodeError as e:
        raise ErrorAtestacion(f"el reporte {ruta} no está en UTF-8") from e
    except json.JSONDecodeError as e:
        raise ErrorAtestacion(f"el reporte {ruta} no es JSON válido: {e}") from e
    if not isinstance(datos, dict):
        raise ErrorAtestacion(f"el reporte {ruta} debe ser un objeto JSON")
    return crudo, datos


def hash_metodologia(reporte: dict[str, object]) -> str:
    version = reporte.get(CAMPO_METODOLOGIA)
    if version is None:
        raise ErrorAtestacion(
            f"el reporte no trae `{CAMPO_METODOLOGIA}`; sin la versión de la metodología no hay atestación"
        )
    if not isinstance(version, str) or not version.strip():
        raise ErrorAtestacion(f"`{CAMPO_METODOLOGIA}` debe ser un texto no vacío")
    contenido = version.encode("utf-8")
    parametros = reporte.get(CAMPO_PARAMETROS)
    if parametros is not None:
        contenido += json_canonico(parametros)
    return sha256_hex(contenido)


def _hex64(valor: object, que: str) -> str:
    if not isinstance(valor, str) or not _HEX64.fullmatch(valor.lower()):
        raise ErrorAtestacion(f"{que} debe ser un sha256 en hexadecimal (64 caracteres)")
    return valor.lower()


def _hash_datos_del_reporte(campo: object) -> str:
    """El campo `hash_datos` del reporte: hex plano o el objeto de T6 con `algoritmo` y `valor`."""
    if isinstance(campo, dict):
        algoritmo = campo.get("algoritmo")
        if algoritmo is None:
            raise ErrorAtestacion(
                f'`{CAMPO_HASH_DATOS}` del reporte es un objeto sin `algoritmo`; se espera "sha256"'
            )
        if algoritmo != "sha256":
            raise ErrorAtestacion(
                f"`{CAMPO_HASH_DATOS}` del reporte usa el algoritmo {algoritmo!r}; "
                "el contrato solo guarda sha256"
            )
        if campo.get("valor") is None:
            raise ErrorAtestacion(
                f"`{CAMPO_HASH_DATOS}` del reporte no trae `valor` (el sha256 de los datos)"
            )
        return _hex64(campo["valor"], f"`{CAMPO_HASH_DATOS}.valor` del reporte")
    if not isinstance(campo, str):
        raise ErrorAtestacion(
            f"el campo `{CAMPO_HASH_DATOS}` del reporte debe ser un sha256 en hexadecimal (64 caracteres) "
            'o un objeto {"algoritmo": "sha256", "valor": "<64 hex>"}'
        )
    return _hex64(campo, f"el campo `{CAMPO_HASH_DATOS}` del reporte")


def resolver_hash_datos(reporte: dict[str, object], argumento: str | None) -> str:
    del_reporte = reporte.get(CAMPO_HASH_DATOS)
    if del_reporte is not None:
        valor = _hash_datos_del_reporte(del_reporte)
        if argumento is not None and _hex64(argumento, "--hash-datos") != valor:
            raise ErrorAtestacion(
                f"--hash-datos no coincide con `{CAMPO_HASH_DATOS}` del reporte; "
                "quita el argumento o revisa el reporte"
            )
        return valor
    if argumento is None:
        raise ErrorAtestacion(
            f"el reporte no trae `{CAMPO_HASH_DATOS}`; "
            "pásalo con --hash-datos (sha256 de los datos, 64 caracteres hex)"
        )
    return _hex64(argumento, "--hash-datos")


def calcular_huellas(ruta_reporte: str | Path, hash_datos: str | None = None) -> Huellas:
    """Calcula las tres huellas de un reporte (ver las reglas en el docstring del módulo)."""
    crudo, reporte = leer_reporte(Path(ruta_reporte))
    return Huellas(
        hash_reporte=sha256_hex(crudo),
        hash_metodologia=hash_metodologia(reporte),
        hash_datos=resolver_hash_datos(reporte, hash_datos),
    )


def entero_u32(valor: str | int, que: str) -> int:
    """Entero sin signo de 32 bits, como `u32` del contrato."""
    if isinstance(valor, bool) or not (isinstance(valor, int) or _ENTERO.fullmatch(str(valor))):
        raise ErrorAtestacion(f"{que} debe ser un entero de 0 a {U32_MAX}")
    numero = int(valor)
    if not 0 <= numero <= U32_MAX:
        raise ErrorAtestacion(f"{que} debe ser un entero de 0 a {U32_MAX}")
    return numero


def segundos_unix(valor: str | int, que: str) -> int:
    """Segundos Unix (`u64` del contrato) a partir de un entero o de una fecha ISO 8601 con zona."""
    if isinstance(valor, bool):
        raise ErrorAtestacion(f"{que}: usa segundos Unix o una fecha ISO 8601 con zona")
    if isinstance(valor, int) or _ENTERO.fullmatch(str(valor)):
        numero = int(valor)
    else:
        try:
            fecha = dt.datetime.fromisoformat(str(valor))
        except ValueError as e:
            raise ErrorAtestacion(
                f"{que}: usa segundos Unix o una fecha ISO 8601 con zona, por ejemplo 2026-09-21T00:00:00Z"
            ) from e
        if fecha.tzinfo is None:
            raise ErrorAtestacion(f"{que}: la fecha necesita zona horaria (termina en Z para UTC)")
        delta = fecha - dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
        if delta.microseconds:
            raise ErrorAtestacion(f"{que}: la fecha no puede tener fracciones de segundo")
        numero = delta.days * 86_400 + delta.seconds
    if not 0 <= numero <= U64_MAX:
        raise ErrorAtestacion(f"{que} debe estar entre 1970-01-01 y el máximo de u64")
    return numero


def validar_identidad(identidad: str) -> None:
    """Solo el NOMBRE de una identidad de `stellar keys`; nunca una llave secreta ni una frase semilla."""
    if _PARECE_SECRETO.fullmatch(identidad) or any(c.isspace() for c in identidad):
        raise ErrorAtestacion(
            "la identidad parece una llave secreta o una frase semilla; pasa el NOMBRE de una identidad "
            "de 'stellar keys' (no se muestra lo que escribiste)"
        )
    if not _IDENTIDAD.fullmatch(identidad):
        raise ErrorAtestacion("identidad inválida: usa el nombre de una identidad de 'stellar keys'")


def comando_publicar(
    huellas: Huellas,
    *,
    red: str,
    identidad: str,
    id_contrato: str,
    version_metodologia: int | str,
    ventana_inicio: int | str,
    ventana_fin: int | str,
    confirmo_mainnet: bool = False,
) -> list[str]:
    """Arma, sin ejecutarlo, `stellar contract invoke … -- publicar …` como lista de argumentos.

    La versión y la ventana aceptan enteros o texto (segundos Unix o ISO 8601 con zona para la ventana).
    """
    if red not in REDES:
        raise ErrorAtestacion(f"red desconocida {red!r}: usa testnet o mainnet")
    if red == "mainnet" and not confirmo_mainnet:
        raise ErrorAtestacion("red mainnet: hace falta la confirmación explícita (--confirmo-mainnet)")
    validar_identidad(identidad)
    if not _ID_CONTRATO.fullmatch(id_contrato):
        raise ErrorAtestacion("id de contrato inválido: usa la dirección C… del contrato desplegado")
    version = entero_u32(version_metodologia, "la versión de la metodología")
    inicio = segundos_unix(ventana_inicio, "el inicio de la ventana")
    fin = segundos_unix(ventana_fin, "el fin de la ventana")
    if inicio >= fin:
        raise ErrorAtestacion(
            "la ventana es inválida: el inicio debe ser anterior al fin (el contrato lo rechaza)"
        )
    for nombre, valor in asdict(huellas).items():
        _hex64(valor, nombre)
    return [
        "stellar",
        "contract",
        "invoke",
        "--id",
        id_contrato,
        "--source-account",
        identidad,
        "--network",
        red,
        "--",
        "publicar",
        "--hash_reporte",
        huellas.hash_reporte,
        "--hash_metodologia",
        huellas.hash_metodologia,
        "--hash_datos",
        huellas.hash_datos,
        "--version_metodologia",
        str(version),
        "--ventana_inicio",
        str(inicio),
        "--ventana_fin",
        str(fin),
    ]


def _argumentos() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lumen_lupa.atestacion",
        description="Huellas de un reporte y comando para atestarlo en el contrato Soroban (T7). Sin red.",
    )
    sub = parser.add_subparsers(dest="subcomando", required=True)
    huellas = sub.add_parser("huellas", help="imprime en JSON las tres huellas del reporte")
    huellas.add_argument("reporte", type=Path)
    huellas.add_argument("--hash-datos", help="sha256 de los datos, si el reporte no trae `hash_datos`")
    comando = sub.add_parser("comando", help="imprime, sin ejecutarlo, el comando stellar … -- publicar …")
    comando.add_argument("reporte", type=Path)
    comando.add_argument("--hash-datos", help="sha256 de los datos, si el reporte no trae `hash_datos`")
    comando.add_argument("--red", required=True, choices=REDES)
    comando.add_argument("--identidad", required=True, help="nombre de una identidad de `stellar keys`")
    comando.add_argument("--id-contrato", required=True, help="dirección C… del contrato desplegado")
    comando.add_argument("--version-metodologia", required=True, help="entero (u32)")
    comando.add_argument("--ventana-inicio", required=True, help="segundos Unix o ISO 8601 con zona")
    comando.add_argument("--ventana-fin", required=True, help="segundos Unix o ISO 8601 con zona")
    comando.add_argument("--confirmo-mainnet", action="store_true", help="obligatoria con --red mainnet")
    comando.add_argument(
        "--formato",
        choices=("linea", "lineas"),
        default="linea",
        help="linea: un comando listo para copiar; lineas: un argumento por línea (lo usa el script)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    # El script puede llamar al `python3` del sistema en vez del Python 3.12 del proyecto.
    if sys.version_info < (3, 11):  # noqa: UP036
        print("error: este puente necesita Python 3.11 o más reciente", file=sys.stderr)
        return 2
    args = _argumentos().parse_args(argv)
    try:
        huellas = calcular_huellas(args.reporte, args.hash_datos)
        if args.subcomando == "huellas":
            print(json.dumps(asdict(huellas), indent=2))
            return 0
        comando = comando_publicar(
            huellas,
            red=args.red,
            identidad=args.identidad,
            id_contrato=args.id_contrato,
            version_metodologia=args.version_metodologia,
            ventana_inicio=args.ventana_inicio,
            ventana_fin=args.ventana_fin,
            confirmo_mainnet=args.confirmo_mainnet,
        )
    except ErrorAtestacion as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(shlex.join(comando) if args.formato == "linea" else "\n".join(comando))
    return 0


if __name__ == "__main__":
    sys.exit(main())
