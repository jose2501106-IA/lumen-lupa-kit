# Contrato de atestaciones de Lumen Lupa (Soroban)

Registro en Stellar de la **huella** de cada reporte de integridad de mercado (D-16, contrato T7). El reporte
sigue privado (D-03): en la cadena solo quedan hashes y números. Quien recibe un reporte puede comprobar en la
cadena que es auténtico, que no fue alterado, cuándo se publicó y con qué metodología.

Claude escribió y probó el contrato; **José lo despliega y lo usa con su propia identidad del CLI de Stellar**.
Ninguna llave pasa por Claude, por este repo ni por el CI.

## Qué guarda y por qué solo hashes

Cada atestación se guarda con la clave `hash_reporte` (sha256 de los bytes de `reporte.json`):

| Campo | Tipo | Qué es |
| --- | --- | --- |
| `hash_metodologia` | `BytesN<32>` | sha256 de `methodology_version` + el JSON canónico de los parámetros del reporte |
| `hash_datos` | `BytesN<32>` | sha256 de los datos de entrada del reporte (spec §6) |
| `version_metodologia` | `u32` | versión numérica de la metodología |
| `ventana_inicio`, `ventana_fin` | `u64` | ventana analizada, en segundos Unix (inicio < fin) |
| `publicada_en` | `u64` | timestamp del ledger en que se publicó |
| `publicador` | `Address` | el admin que la publicó |
| `revocada` | `bool` | `true` si el admin la revocó; revocar no borra |

Por construcción el contrato **no puede** guardar nombres de tokens, cuentas ni cifras de volumen: los
parámetros de su interfaz pública solo son `BytesN<32>`, `u32`, `u64` y `Address`, y la atestación solo
agrega un `bool`. No hay `String`, `Symbol` ni `Bytes` de longitud libre. Dos pruebas lo comprueban: una sobre
el spec que generan las macros (`src/test.rs`) y otra, en CI, sobre el spec del WASM compilado
(`tests/wasm.rs`). Así se respetan D-03 y la regla de no publicar acusaciones.

Lo que una atestación **sí** prueba: que un reporte con ese hash exacto existía en la fecha del ledger y que lo
publicó el admin. Lo que **no** prueba: que sus conclusiones sean correctas. Una huella no revela el contenido,
pero quien tenga el mismo archivo puede confirmar que existe una atestación de él.

## Interfaz

| Función | Quién | Qué hace | Errores |
| --- | --- | --- | --- |
| `__constructor(admin: Address)` | al desplegar | fija el admin una sola vez | — |
| `publicar(hash_reporte, hash_metodologia, hash_datos: BytesN<32>, version_metodologia: u32, ventana_inicio: u64, ventana_fin: u64)` | admin | registra la atestación; nunca sobrescribe | `YaExiste` (1), `VentanaInvalida` (2) |
| `consultar(hash_reporte: BytesN<32>) -> Option<Atestacion>` | cualquiera | devuelve la atestación o `None` | — |
| `revocar(hash_reporte: BytesN<32>)` | admin | marca `revocada = true` | `NoExiste` (3), `YaRevocada` (4) |
| `cambiar_admin(nuevo: Address)` | admin actual **y** el nuevo | cambia el admin | `MismoAdmin` (5) |
| `admin() -> Address` | cualquiera | admin actual | — |
| `version() -> u32` | cualquiera | versión del código (`1`) | — |

Los códigos de error son estables (prueba `codigos_de_error_estables`). Eventos: `atestacion_publicada`
(tema: el hash del reporte; datos: el resto de la atestación), `atestacion_revocada` (tema: el hash; datos: el
admin) y `admin_cambiado` (datos: el anterior y el nuevo).

**Almacenamiento.** El admin vive en el almacenamiento de instancia; cada atestación en el persistente, con la
clave `("atest", hash_reporte)`. Al publicar, consultar o revocar se extiende el TTL: si le quedan 119 días o
menos (`UMBRAL_TTL_*`), sube a 120 días (`EXTENSION_TTL_*`). Los días se cuentan como 17,280 ledgers, la
aproximación de la guía oficial ["Storage strategies"](https://developers.stellar.org/docs/build/guides/storage/storage-strategies);
la misma guía da un TTL máximo de 3,110,400 ledgers (≈180 días). Una consulta que solo se **simula** (lo normal
para leer) no extiende nada; solo una transacción enviada lo hace. Una entrada persistente que vence no se
borra: queda archivada y se restaura
([State archival](https://developers.stellar.org/docs/learn/fundamentals/contract-development/storage/state-archival)).

**No es actualizable.** No tiene función de *upgrade*: una versión nueva del código es un contrato nuevo, con
otra dirección; las atestaciones viejas siguen en el anterior.

## Versiones (6-nov-2026)

| Pieza | Versión | De dónde |
| --- | --- | --- |
| soroban-sdk | `=29.0.1` (en `Cargo.toml`, con `Cargo.lock` versionado) | última estable en crates.io; `cargo info soroban-sdk` muestra `30.0.0-rc.1`, que es candidata |
| Rust | 1.97.0, target `wasm32v1-none` | el target lo pide la [guía de instalación](https://developers.stellar.org/docs/build/smart-contracts/getting-started/setup) |
| stellar-cli | 30.0.1 (mínimo 25.2.0) | última en crates.io; soroban-sdk ≥ 28 exige `stellar contract build` de stellar-cli ≥ 25.2.0 |

## Dos sha256: el del CI y el del WASM que se despliega

Con soroban-sdk ≥ 28, un `cargo build --target wasm32v1-none` directo falla a propósito: el SDK pide compilar
con `stellar contract build`, que después de compilar quita del spec las entradas de tipos que el contrato no
usa (*spec shaking*) y agrega sus metadatos (guía de migración v28 del SDK, `src/_migrating/v28_spec_shaking.rs`).
El CI de este repo solo puede usar rustup y cargo, así que:

- **El CI** (`.github/workflows/contrato.yml`) compila con `cargo build --release --target wasm32v1-none` y la
  misma variable que pone el CLI, imprime el sha256 de ese WASM, revisa su spec con la regla de shaking del CLI
  y lo vuelve a compilar desde otra ruta y con otro `CARGO_HOME` para demostrar que el sha256 no depende de la
  máquina. Ese WASM **no** es el que se despliega.
- **El WASM que se despliega** lo arma `scripts/atestaciones.sh construir`, que corre `stellar contract build`
  con Rust 1.97.0 fijo e imprime su sha256. Otra persona con las mismas versiones (stellar-cli, Rust y este
  commit) debería obtener el mismo sha256; eso no está comprobado aquí porque el CI no tiene stellar-cli.

Para que el CI imprima el sha256 del WASM desplegable haría falta instalar stellar-cli en el CI: queda como
pregunta abierta para José (PR de T7).

## Pasos de José en testnet

Todo se corre en **tu máquina**, no en Codespaces ni en ninguna máquina donde corra Claude: ahí vivirá tu llave.
Desde la raíz del repo.

**0. Herramientas** (una vez). Comandos de la [guía de instalación](https://developers.stellar.org/docs/build/smart-contracts/getting-started/setup):

```sh
rustup toolchain install 1.97.0 --target wasm32v1-none
cargo install --locked stellar-cli --version 30.0.1   # o: brew install stellar-cli
stellar --version
python3 --version                                      # 3.11 o más reciente, para el puente
```

**1. Tu identidad, fondeada en testnet** (comandos de la guía
[Deploy to testnet](https://developers.stellar.org/docs/build/smart-contracts/getting-started/deploy-to-testnet)):

```sh
stellar keys generate jose --network testnet --fund
stellar keys address jose          # tu dirección pública G…; esta sí se puede compartir
```

La llave secreta queda en la configuración del CLI en tu máquina (`stellar keys ls -l` muestra dónde). No la
copies a ningún chat, archivo del repo ni variable del CI. El script solo usa el **nombre** `jose`.

**2. Construir y anotar el sha256 del WASM:**

```sh
scripts/atestaciones.sh construir
```

**3. Desplegar** (el constructor recibe tu dirección pública como admin):

```sh
scripts/atestaciones.sh --dry-run desplegar testnet jose   # primero: ver los comandos sin ejecutar nada
scripts/atestaciones.sh desplegar testnet jose
```

Imprime el ID del contrato (`C…`). Anótalo junto con el sha256 del paso 2. Comprueba que lo desplegado es tu
WASM y que el admin eres tú:

```sh
stellar contract fetch --id C… --network testnet --out-file /tmp/atestaciones.wasm
sha256sum /tmp/atestaciones.wasm                     # igual al del paso 2 (en macOS: shasum -a 256)
stellar contract invoke --id C… --source-account jose --network testnet -- admin
```

**4. Publicar la atestación de un reporte.** El reporte de ejemplo sale del demo de T6:
`uv run lumen-lupa demo --semilla 2026 --salida salidas/demo` escribe `salidas/demo/reporte.json`. Ese reporte
trae `hash_datos` como objeto (`{"algoritmo": "sha256", "valor": "<64 hex>", "forma": …, "filas": …}`); el
puente toma `valor` y solo acepta el algoritmo sha256. También acepta `hash_datos` como hex plano, y si el
reporte no lo trae, hay que darlo con `--hash-datos`.

La versión numérica de la metodología y la ventana no se deducen del reporte: se dan a mano.

- **Ventana:** es el `periodo` del reporte. En el demo va de 2026-01-01 a 2026-01-07: T6 toma la primera y
  la última fecha de los swaps, ambas incluidas. Por eso la ventana va de las 00:00:00 del primer día a las
  23:59:59 UTC del último.
- **Versión:** el `1` de abajo es provisional. Falta que José decida cómo se convierte `methodology_version`
  (`"v0.1-demo"`) en un número.

Una prueba (`tests/test_atestaciones_sh.py`) corre este mismo comando con `--dry-run` sobre un reporte con la
forma del de T6.

```sh
scripts/atestaciones.sh --dry-run publicar testnet jose C… salidas/demo/reporte.json \
  --version-metodologia 1 --ventana-inicio 2026-01-01T00:00:00Z --ventana-fin 2026-01-07T23:59:59Z
```

Revisa las tres huellas en el comando impreso y córrelo sin `--dry-run`. Al firmar, el CLI imprime el hash
de la transacción en la línea `Signing transaction: …` (stellar-cli 30.0.1, `src/signer/mod.rs`): anótalo, es
la evidencia para el explorador de testnet.

**5. Verificar** (lo puede hacer quien reciba el reporte):

```sh
PYTHONPATH=src python3 -m lumen_lupa.atestacion huellas salidas/demo/reporte.json
scripts/atestaciones.sh --fuente jose consultar testnet C… <hash_reporte>
```

`huellas` calcula en local, sin red, las tres huellas del reporte: `hash_reporte` es el sha256 del archivo,
igual a `sha256sum`. `consultar` solo simula la llamada (`--send=no`): no firma ni paga. `--fuente` es la
cuenta con la que se simula; sirve cualquier identidad o dirección G….

La atestación es válida si se cumple todo esto:
- existe y no está revocada;
- su `hash_metodologia` y su `hash_datos` son los que imprimió `huellas` (en el reporte de T6, `hash_datos`
  es su `hash_datos.valor`);
- su ventana corresponde al `periodo` del reporte;
- su `publicador` es la dirección de José.

**Revocar y cambiar de admin** no están en el script; se hacen con `stellar contract invoke … -- revocar
--hash_reporte <hex>` y `… -- cambiar_admin --nuevo <G…>`. `cambiar_admin` necesita la firma del admin actual
**y** la del nuevo; el procedimiento exacto del CLI para la segunda firma no está probado aquí.

## Costos

La página oficial [Resource Limits & Fees](https://developers.stellar.org/docs/reference/resource-limits-fees)
(consultada el 6-nov-2026) no publica cifras fijas: remite a la página *Network Limits* de Stellar Lab y al
comando `stellar network settings`. Por eso aquí no se dan cifras. En testnet la cuenta se fondea con Friendbot
(`--fund`). En cualquier red, el CLI simula cada transacción antes de firmarla y con eso calcula su comisión.

## Mainnet: qué cambia

- El script exige `--confirmo-mainnet` en cualquier subcomando con red `mainnet`; sin ella sale con error.
- El CLI no trae un RPC de mainnet: su red `mainnet` por defecto dice "Bring Your Own" y remite a la lista de
  [proveedores de RPC](https://developers.stellar.org/docs/data/rpc/rpc-providers). Hay que definirla con un
  proveedor: `stellar network add mainnet --rpc-url <URL> --network-passphrase "Public Global Stellar Network ; September 2015"`.
- La cuenta necesita XLM reales (no hay Friendbot) y cada transacción cuesta comisiones y renta reales.
- La llave del admin decide quién publica: en mainnet conviene una llave aparte de la de testnet.
- Recomendación de D-16 y D-17: mainnet después de una auditoría del contrato (tramo #3). El contrato no es
  actualizable (ver arriba).

## Pruebas

- `cargo test` (en esta carpeta, sin red): publicar y consultar; duplicado rechazado sin sobrescribir; sin
  firma del admin rechazado (sin firma, firma de otra cuenta y firma del admin con otros argumentos); ventana
  inválida; revocar; `cambiar_admin` con doble firma y con una sola; consultar un hash inexistente → `None`;
  eventos al publicar, revocar y cambiar de admin; TTL extendido al publicar, consultar y revocar; el
  constructor no se puede volver a llamar (el host responde `Error(Context, InvalidAction)`); interfaz solo con
  los tipos permitidos y lista de funciones completa; códigos de error estables. `cargo test` escribe
  instantáneas en `test_snapshots/`, que no se versionan.
- `tests/wasm.rs` (solo en CI, sobre el WASM compilado): interfaz real, regla de shaking del CLI y metadatos.
- Python: `tests/test_atestacion.py` (puente) y `tests/test_atestaciones_sh.py` (script con `stellar` falso).

## Fuentes

Consultadas el 6-nov-2026:

- Stellar Docs: [Setup](https://developers.stellar.org/docs/build/smart-contracts/getting-started/setup),
  [Hello World](https://developers.stellar.org/docs/build/smart-contracts/getting-started/hello-world)
  (perfil de `Cargo.toml`, límite de 128 KB en mainnet),
  [Deploy to testnet](https://developers.stellar.org/docs/build/smart-contracts/getting-started/deploy-to-testnet),
  [Storage strategies](https://developers.stellar.org/docs/build/guides/storage/storage-strategies),
  [Test TTL extension](https://developers.stellar.org/docs/build/guides/archival/test-ttl-extension),
  [State archival](https://developers.stellar.org/docs/learn/fundamentals/contract-development/storage/state-archival),
  [Resource Limits & Fees](https://developers.stellar.org/docs/reference/resource-limits-fees).
- soroban-sdk 29.0.1: la página de docs.rs de esa versión no se pudo abrir desde esta sesión; se leyó el código
  fuente del paquete de crates.io (del que docs.rs genera la documentación): `build.rs`,
  `src/_migrating/v28_spec_shaking.rs`, `src/_migrating/v25_event_testing.rs`, `src/storage.rs`, `src/env.rs`
  y `src/lib.rs`. También la guía de migración de eventos en
  [docs.rs (27.0.4)](https://docs.rs/soroban-sdk/27.0.4/src/soroban_sdk/_migrating/v23_contractevent.rs.html).
- soroban-spec 29.0.1 (`src/shaking.rs`) y stellar-cli 30.0.1 (paquete `soroban-cli` de crates.io:
  `src/commands/contract/build.rs`, `deploy/wasm.rs`, `invoke.rs`, `fetch.rs` y `src/config/network.rs`).
- Versiones: API de crates.io (`/api/v1/crates/soroban-sdk` y `/api/v1/crates/stellar-cli`).
