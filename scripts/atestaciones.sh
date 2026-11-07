#!/usr/bin/env bash
# Contrato Soroban de atestaciones de Lumen Lupa (T7, D-16): construir, desplegar, publicar y consultar.
#
# Lo corre José en SU máquina, con una identidad de `stellar keys` que él crea. Este script nunca lee,
# imprime ni pide una llave secreta: a `stellar` solo le pasa el NOMBRE de la identidad. Claude no lo
# corre contra la red: en las pruebas solo corre con --dry-run o con un `stellar` falso.
#
# Uso:
#   scripts/atestaciones.sh [--dry-run] construir
#   scripts/atestaciones.sh [--dry-run] [--confirmo-mainnet] desplegar <red> <identidad>
#   scripts/atestaciones.sh [--dry-run] [--confirmo-mainnet] publicar <red> <identidad> <id_contrato> \
#       <reporte.json> --version-metodologia <n> --ventana-inicio <t> --ventana-fin <t> [--hash-datos <hex>]
#   scripts/atestaciones.sh [--dry-run] [--confirmo-mainnet] [--fuente <identidad>] consultar <red> \
#       <id_contrato> <hash_reporte>
#
#   <red>        testnet o mainnet. Con mainnet hay que agregar --confirmo-mainnet; sin ella, error.
#   <t>          segundos Unix o fecha ISO 8601 con zona, por ejemplo 2026-09-21T00:00:00Z.
#   --dry-run    imprime los comandos sin ejecutarlos: no llama a stellar ni a sha256sum. En `publicar`
#                sí calcula las huellas del reporte en local (lee el archivo, sin red).
#   --fuente     identidad o dirección G… con la que `consultar` simula la llamada (no firma ni paga).
#                Sin ella, el CLI usa STELLAR_ACCOUNT o la identidad fijada con `stellar keys use`.
#
# Variables opcionales: ATESTACIONES_RUST (toolchain de Rust para `construir`; por defecto 1.97.0, la del
# CI) y LUMEN_LUPA_PYTHON (intérprete del puente; por defecto python3 ≥ 3.11).
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTRATO="$RAIZ/contratos/atestaciones"
TARGET="wasm32v1-none"
TOOLCHAIN="${ATESTACIONES_RUST:-1.97.0}"
WASM="$CONTRATO/target/$TARGET/release/lumen_lupa_atestaciones.wasm"
PYTHON="${LUMEN_LUPA_PYTHON:-python3}"

DRY_RUN=0
CONFIRMO_MAINNET=0
FUENTE=""
OPCIONES_PUENTE=()
POSICIONALES=()

uso() {
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"
}

falla() {
    echo "error: $*" >&2
    exit 2
}

# Imprime un argumento listo para copiar en bash o zsh.
citar() {
    local a="$1"
    if [[ "$a" =~ ^[A-Za-z0-9_./:=+@%,-]+$ ]]; then
        printf '%s' "$a"
    else
        printf "'%s'" "${a//\'/\'\\\'\'}"
    fi
}

mostrar() {
    local prefijo="$1"
    shift
    printf '%s' "$prefijo"
    local a
    for a in "$@"; do
        printf ' '
        citar "$a"
    done
    printf '\n'
}

# Muestra el comando y, salvo con --dry-run, lo ejecuta.
ejecutar() {
    if ((DRY_RUN)); then
        mostrar "[dry-run]" "$@"
        return 0
    fi
    mostrar "+" "$@"
    "$@"
}

validar_red() {
    case "$1" in
        testnet) ;;
        mainnet)
            if ((!CONFIRMO_MAINNET)); then
                falla "red mainnet: agrega --confirmo-mainnet (lee antes contratos/atestaciones/README.md, sección Mainnet)"
            fi
            ;;
        *) falla "red desconocida '$1': usa testnet o mainnet" ;;
    esac
}

# Solo el NOMBRE de una identidad de `stellar keys` (o una dirección pública G…). Nunca una llave secreta
# ni una frase semilla; si lo parece, falla sin repetir lo que se pasó.
validar_identidad() {
    if [[ "$1" =~ ^S[A-Z2-7]{55}$ || "$1" == *[[:space:]]* ]]; then
        falla "eso parece una llave secreta o una frase semilla; pasa el NOMBRE de una identidad de 'stellar keys' (no se muestra lo que escribiste)"
    fi
    if [[ ! "$1" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$ ]]; then
        falla "identidad inválida: usa el nombre de una identidad de 'stellar keys'"
    fi
}

validar_id_contrato() {
    if [[ ! "$1" =~ ^C[A-Z2-7]{55}$ && ! "$1" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ ]]; then
        falla "id de contrato inválido: usa la dirección C… que imprimió 'desplegar' (o un alias del CLI)"
    fi
}

validar_hash() {
    if [[ ! "$1" =~ ^[0-9a-fA-F]{64}$ ]]; then
        falla "hash inválido: deben ser 64 caracteres hexadecimales (sha256)"
    fi
}

sha256_de() {
    if command -v sha256sum >/dev/null 2>&1; then
        ejecutar sha256sum "$1"
    else
        ejecutar shasum -a 256 "$1"
    fi
}

# Compila el WASM que se despliega con la herramienta oficial: soroban-sdk ≥ 28 exige
# `stellar contract build` (stellar-cli ≥ 25.2.0), que además quita del spec los tipos sin uso. Fija el
# toolchain de Rust (RUSTUP_TOOLCHAIN) para que otra persona con las mismas versiones obtenga el mismo
# sha256. Ese sha256 es distinto del que imprime el CI, que compila con cargo sin stellar-cli.
construir() {
    [[ $# -eq 0 ]] || falla "uso: construir (sin argumentos)"
    if [[ -n "${RUSTFLAGS:-}" ]]; then
        falla "RUSTFLAGS está definida: la compilación no sería reproducible; quítala y vuelve a correr"
    fi
    ejecutar env "RUSTUP_TOOLCHAIN=$TOOLCHAIN" stellar contract build \
        --manifest-path "$CONTRATO/Cargo.toml" --locked
    sha256_de "$WASM"
}

# Despliega el WASM ya compilado. El constructor recibe como admin la dirección pública de la identidad.
desplegar() {
    [[ $# -eq 2 ]] || falla "uso: desplegar <red> <identidad>"
    local red="$1" identidad="$2" admin
    validar_red "$red"
    validar_identidad "$identidad"
    if ((!DRY_RUN)) && [[ ! -f "$WASM" ]]; then
        falla "no existe $WASM; corre antes: scripts/atestaciones.sh construir"
    fi
    sha256_de "$WASM"
    if ((DRY_RUN)); then
        ejecutar stellar keys address "$identidad"
        admin="<dirección pública de $identidad>"
    else
        mostrar "+" stellar keys address "$identidad"
        admin="$(stellar keys address "$identidad")"
    fi
    ejecutar stellar contract deploy --wasm "$WASM" --source-account "$identidad" --network "$red" \
        -- --admin "$admin"
}

tiene_opcion() {
    local i
    for ((i = 0; i < ${#OPCIONES_PUENTE[@]}; i += 2)); do
        if [[ "${OPCIONES_PUENTE[i]}" == "$1" ]]; then
            return 0
        fi
    done
    return 1
}

puente() {
    PYTHONPATH="$RAIZ/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" -m lumen_lupa.atestacion "$@"
}

# Calcula las huellas del reporte con el puente de Python (src/lumen_lupa/atestacion.py), que también
# arma el comando `stellar contract invoke … -- publicar …`; aquí solo se ejecuta (o se muestra).
publicar() {
    [[ $# -eq 4 ]] || falla "uso: publicar <red> <identidad> <id_contrato> <reporte.json> --version-metodologia <n> --ventana-inicio <t> --ventana-fin <t> [--hash-datos <hex>]"
    local red="$1" identidad="$2" id="$3" reporte="$4" salida linea
    validar_red "$red"
    validar_identidad "$identidad"
    validar_id_contrato "$id"
    [[ -f "$reporte" ]] || falla "no existe el reporte: $reporte"
    local requerida
    for requerida in --version-metodologia --ventana-inicio --ventana-fin; do
        tiene_opcion "$requerida" || falla "publicar necesita $requerida (no se deduce del reporte)"
    done
    local extra=()
    if ((CONFIRMO_MAINNET)); then
        extra=(--confirmo-mainnet)
    fi
    salida="$(puente comando "$reporte" --red "$red" --identidad "$identidad" --id-contrato "$id" \
        --formato lineas ${extra[@]+"${extra[@]}"} ${OPCIONES_PUENTE[@]+"${OPCIONES_PUENTE[@]}"})"
    local comando=()
    while IFS= read -r linea; do
        comando+=("$linea")
    done <<<"$salida"
    ejecutar "${comando[@]}"
}

# Consulta una atestación. Solo simula (--send=no): no firma, no paga y no cambia nada en la red.
consultar() {
    [[ $# -eq 3 ]] || falla "uso: consultar <red> <id_contrato> <hash_reporte>"
    local red="$1" id="$2" hash="$3"
    validar_red "$red"
    validar_id_contrato "$id"
    validar_hash "$hash"
    local fuente=()
    if [[ -n "$FUENTE" ]]; then
        validar_identidad "$FUENTE"
        fuente=(--source-account "$FUENTE")
    fi
    ejecutar stellar contract invoke --id "$id" ${fuente[@]+"${fuente[@]}"} --network "$red" --send=no \
        -- consultar --hash_reporte "$hash"
}

while (($#)); do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --confirmo-mainnet) CONFIRMO_MAINNET=1 ;;
        --fuente)
            [[ $# -ge 2 ]] || falla "falta el valor de --fuente"
            FUENTE="$2"
            shift
            ;;
        --version-metodologia | --ventana-inicio | --ventana-fin | --hash-datos)
            [[ $# -ge 2 ]] || falla "falta el valor de $1"
            OPCIONES_PUENTE+=("$1" "$2")
            shift
            ;;
        -h | --help)
            uso
            exit 0
            ;;
        -*) falla "opción desconocida: $1 (mira --help)" ;;
        *) POSICIONALES+=("$1") ;;
    esac
    shift
done

[[ ${#POSICIONALES[@]} -ge 1 ]] || {
    uso >&2
    exit 2
}
SUBCOMANDO="${POSICIONALES[0]}"
ARGS=("${POSICIONALES[@]:1}")

if [[ ${#OPCIONES_PUENTE[@]} -gt 0 && "$SUBCOMANDO" != "publicar" ]]; then
    falla "las opciones --version-metodologia, --ventana-inicio, --ventana-fin y --hash-datos solo van con publicar"
fi
if [[ -n "$FUENTE" && "$SUBCOMANDO" != "consultar" ]]; then
    falla "--fuente solo va con consultar"
fi

case "$SUBCOMANDO" in
    construir) construir ${ARGS[@]+"${ARGS[@]}"} ;;
    desplegar) desplegar ${ARGS[@]+"${ARGS[@]}"} ;;
    publicar) publicar ${ARGS[@]+"${ARGS[@]}"} ;;
    consultar) consultar ${ARGS[@]+"${ARGS[@]}"} ;;
    *) falla "subcomando desconocido '$SUBCOMANDO': usa construir, desplegar, publicar o consultar" ;;
esac
