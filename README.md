# Lumen Lupa Kit

[![CI](https://github.com/jose2501106-IA/lumen-lupa-kit/actions/workflows/ci.yml/badge.svg)](https://github.com/jose2501106-IA/lumen-lupa-kit/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Lumen Lupa compares the trading volume a Stellar token reports with the volume that survives wash-trading and
sybil checks. This kit is its open part: a normalized data model (7 DuckDB tables), a Stellar adapter
(Hubble on BigQuery → DuckDB) with a cost-capped query runner, a wash-trading simulator with labeled ground truth
(10 scenarios, 3 of them traps that must not raise alerts) and `lumen_lupa.revision`, the
tool that runs the pre-registered real-data review protocol.

## Why it matters for Stellar

Reported volume and trader counts feed decisions on Stellar that are hard to undo or that pay out:

- **Blend pools:** the creator of a Blend pool chooses its oracle, and the oracle cannot be changed after the
  pool is created.
- **Soroban oracle guardrails:** newer guardrails (the [safe-oracle](https://docs.rs/crate/safe-oracle/latest) crate,
  2026) check SDEX volume and unique-trader counts. One owner with several accounts can meet both.
- **SCF Integration Track:** SCF may disregard self-generated or wash activity.

## Verify in 5 minutes

You need Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/jose2501106-IA/lumen-lupa-kit
cd lumen-lupa-kit
uv sync --locked
uv run ruff check && uv run ruff format --check
uv run pytest -q
uv run python -m lumen_lupa.revision huella --alertas tests/fixtures/revision/alertas.json
```

No network and no credentials: the tests sign nothing and send nothing. CI runs the same checks on every push.

- [EVIDENCE.md](EVIDENCE.md): what this kit shows and how to check it.
- [REVIEW_PROTOCOL.md](REVIEW_PROTOCOL.md): how a detector's alerts are reviewed by hand with real Stellar data,
  fixed before seeing results.
- [SECURITY.md](SECURITY.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

## What is private

The detector (entity grouping, signals, scoring), its thresholds and the data of the markets the project studies
are not in this kit. The review team can request read access to the private repository.

Code, docs and messages are in Spanish; the full Spanish documentation follows. License: Apache-2.0.

---

# En español

Kit abierto de Lumen Lupa para medir cuánto del volumen de un token en Stellar es actividad orgánica: el modelo
normalizado de 7 tablas, el adaptador de Stellar (Hubble en BigQuery → DuckDB) con un ejecutor de
consultas con topes de costo, un simulador de *wash trading* con verdad etiquetada para medir detectores y la
herramienta pública del protocolo de revisión con datos reales.

## Qué es

Lumen Lupa compara el volumen que reporta un token en Stellar contra el volumen que sobrevive a señales de
*wash trading* y de cuentas *sybil*. Este repositorio es su parte abierta (*open core*):

| Pieza | Dónde | Qué hace |
| --- | --- | --- |
| Modelo normalizado | `src/lumen_lupa/modelo.py` | 7 tablas de DuckDB que llena el adaptador de Stellar; el DDL sale de las clases de Python |
| Adaptador de Stellar | `src/lumen_lupa/adaptadores/` | Traduce archivos descargados de Hubble a filas del modelo, con funciones puras y sin red |
| Ejecutor de BigQuery | `scripts/bq.py` | Toda consulta pasa por aquí: *dry run*, rechazo arriba de 10 GiB y topes de bytes facturados y de filas |
| Simulador | `src/lumen_lupa/simulador/` | 10 escenarios reproducibles por semilla, con la verdad de cada swap en una tabla aparte |
| Protocolo de revisión | `src/lumen_lupa/revision/` y `REVIEW_PROTOCOL.md` | Huella de la lista de alertas, sorteo por peso con una semilla externa, rúbrica y regla de aprobación para revisar alertas con datos reales |
| Contrato de atestaciones | `contratos/atestaciones/` | Contrato Soroban que registra reportes solo por hash: sin nombres, cuentas ni cifras |

El detector (entidades, señales, puntaje y umbrales) no está en el kit, ni los datos de los mercados que estudia
el proyecto. Las pruebas no usan la red: nada en ellas firma ni envía transacciones.
Lo único que firma y envía transacciones es `scripts/atestaciones.sh desplegar` y `publicar`: los corre a mano el dueño del contrato, con su propia identidad del CLI de Stellar (el script nunca lee ni pide una llave).

## Para quién

- **Builders de Stellar** que quieran saber cuánta actividad real hay detrás de un par o de un pool.
- **Analistas e investigadores** que necesiten datos de Hubble en un formato común sin gastar de más en BigQuery.
- **Quien construya o evalúe un detector** de *wash trading* y necesite casos etiquetados para medir precisión y
  exhaustividad, incluidas trampas que no deben dar alerta.

## Instalación

Requisitos: Python 3.12 y uv. Las versiones de todas las dependencias están fijadas en `uv.lock`. Desde la
carpeta del repositorio:

```bash
uv sync --locked
uv run pytest -q    # sin red ni credenciales
```

## Cómo correr el simulador

Cada escenario genera filas del modelo para una cadena ficticia (`chain = "sintetica"`) y, aparte, una fila de
`verdad` por swap: si es honesto o manipulado, qué señal debería dispararse y a qué entidad real pertenece. Las
direcciones son ficticias (empiezan con `SIM-`) y nunca tienen la forma de una cuenta real de Stellar. La misma
semilla produce exactamente las mismas filas.

Guarda esto como `ejemplo.py` y córrelo con `uv run python ejemplo.py`:

```python
from lumen_lupa.modelo import crear_base
from lumen_lupa.simulador import ESCENARIOS, combinar, crear_tabla_verdad, insertar_mercado

mercado = combinar({escenario: 1 for escenario in ESCENARIOS}, semilla=2026)
con = crear_base("simulado.duckdb")  # crea las tablas del modelo
crear_tabla_verdad(con)  # la verdad va aparte: un detector no debe leerla
insertar_mercado(con, mercado)
consulta = (
    "SELECT escenario, count(*) AS swaps, count(*) FILTER (WHERE manipulada) AS manipulados "
    "FROM verdad GROUP BY escenario ORDER BY escenario"
)
for fila in con.execute(consulta).fetchall():
    print(fila)
```

`combinar` arma un solo mercado con las instancias de cada escenario que le pidas; cada escenario también tiene
su propia función (`honesto(semilla=0)`, `ciclo(semilla=0)`, …). La lista completa está en
[Escenarios del benchmark](#escenarios-del-benchmark).

## Cómo usar el adaptador de Stellar

### Sin credenciales, con los fixtures

`tests/fixtures/stellar/` trae filas reales del mercado de control (USDC de Circle / XLM nativo) de la ventana
2026-09-21 a 2026-10-04; su origen está en `tests/fixtures/stellar/ORIGEN.md`. Con ellas, la carga
corre sin red:

```bash
uv run python -m lumen_lupa.adaptadores.stellar_hubble cargar --dir-datos tests/fixtures/stellar --dir-salida salidas
```

Crea `salidas/control.duckdb` con las tablas del modelo e imprime cuántas filas cargó en cada una. `holders`,
`orders` y `labels` todavía salen vacías, con el aviso "pendiente T3c".

### Con tu propio proyecto de Google Cloud (modo sandbox)

1. Crea un proyecto de Google Cloud y usa BigQuery en
   [modo sandbox](https://docs.cloud.google.com/bigquery/docs/sandbox): sin facturación, con 1 TiB de consultas
   al mes.
2. Autentícate con las credenciales por defecto de Google (por ejemplo, `gcloud auth application-default login`).
   El ejecutor no lee ni imprime credenciales; nunca las subas al repositorio.
3. En `scripts/bq.py`, cambia `PROYECTO = "lumen-lupa"` por el ID de tu proyecto.
4. Escribe tu consulta en `queries/<carpeta>/<nombre>.sql` sobre el dataset público de Hubble,
   `crypto-stellar.crypto_stellar` ([guía de Hubble](https://developers.stellar.org/docs/data/analytics/hubble/analyst-guide/queries-for-horizon-like-data)).
   Filtra siempre por la columna de partición de la tabla (en `history_trades`, `ledger_closed_at`). Nombra los
   archivos `02_trades_<algo>.sql` y `03_operaciones_<algo>.sql`: el adaptador busca esos prefijos.
5. Estima el costo: `uv run python scripts/bq.py queries/<carpeta>/<nombre>.sql --solo-dry-run`.
6. Ejecuta: `uv run python scripts/bq.py queries/<carpeta>/<nombre>.sql`. Las filas quedan en
   `data/<carpeta>/<nombre>.jsonl` y el costo de cada consulta en `queries/<carpeta>/registro.csv`.
7. Carga: `uv run python -m lumen_lupa.adaptadores.stellar_hubble cargar --dir-datos data/<carpeta> --dir-salida salidas`.

Topes de costo del ejecutor (constantes de `scripts/bq.py`):

| Tope | Valor | Qué hace |
| --- | --- | --- |
| *Dry run* previo | siempre | Imprime los bytes que procesaría la consulta; no cuesta |
| Límite del *dry run* | 10 GiB | Arriba de eso no ejecuta, salvo con `--aprobado` |
| `maximum_bytes_billed` | 20 GiB | BigQuery aborta la consulta si facturaría más |
| Filas del resultado | 200,000 | Si el resultado trae más, aborta sin escribir filas (`--max-filas` cambia el tope) |
| Parámetros | JSON fuera de git | Listas de cuentas con `--parametros`; el registro guarda cuántos valores, nunca los valores |

#### Qué archivos espera el adaptador

En `--dir-datos`, archivos JSONL (una fila por línea):

- `02_trades_*.jsonl`: filas de `history_trades`. Los activos llegan como `asset_id` y se traducen a
  `CODIGO:EMISOR` con `ASSET_POR_ID`. El adaptador lee:
  `history_operation_id`, `trade_type`, `ledger_closed_at`, `order`, `selling_liquidity_pool_id`, `buying_account_address`, `selling_account_address`, `buying_asset_id`, `buying_amount`, `selling_asset_id` y `selling_amount`.
- `03_operaciones_*.jsonl`: filas de `history_operations` de tipo 0 (`create_account`), 1 (`payment`), 2 y 13
  (*path payments*), con `successful` de `history_transactions`: solo cuentan las operaciones con
  `successful = true` (las de transacciones fallidas se descartan). El adaptador lee:
  `history_operation_id`, `asset_code`, `asset_issuer`, `asset_type`, `amount`, `type`, `source_asset_code`, `source_asset_issuer`, `source_asset_type`, `source_amount`, `closed_at`, `ledger_sequence`, `det_from`, `det_to`, `funder`, `account`, `starting_balance` y `successful`; si vienen, también `from_muxed`, `to_muxed`, `fee_account`, `source_account`, `funder_muxed` y `sponsor`.

Consulta de trades del mercado de control para un día, en ambos sentidos, del libro de órdenes (`trade_type` 1) y
del pool clásico (`trade_type` 2):

```sql
SELECT
  history_operation_id, `order`, ledger_closed_at, trade_type,
  selling_account_address, selling_asset_id, selling_amount,
  buying_account_address, buying_asset_id, buying_amount,
  price_n, price_d, selling_liquidity_pool_id, seller_is_exact
FROM `crypto-stellar.crypto_stellar.history_trades`
WHERE ledger_closed_at >= TIMESTAMP('2026-09-21 00:00:00+00')
  AND ledger_closed_at < TIMESTAMP('2026-09-22 00:00:00+00')
  AND (
    (selling_asset_id = -4025621231271331684 AND buying_asset_id = -5706705804583548011)
    OR (selling_asset_id = -5706705804583548011 AND buying_asset_id = -4025621231271331684)
  )
ORDER BY history_operation_id, `order`;
```

`MERCADOS` y `ASSET_POR_ID` (`src/lumen_lupa/adaptadores/stellar_hubble.py`) traen solo el mercado de control, y
la carga usa la ventana fija `VENTANA_DESDE`–`VENTANA_HASTA`. Para otro mercado, agrega los `asset_id` de sus dos
activos y su fila en `MERCADOS`, y ajusta la ventana.

## Modelo normalizado (7 tablas)

Las señales de un detector deberían leer solo estas tablas, sin saber de qué cadena viene el dato. Los montos van
en `DECIMAL(38,7)` (los 7 decimales de Stellar, sin pérdida) y los tiempos en UTC, sin zona.

| Tabla | Columnas, en orden |
| --- | --- |
| `swaps` | `chain`, `ts`, `ledger`, `tx_ref`, `op_ref`, `venue`, `pool_id`, `trader`, `counterparty`, `asset_in`, `amount_in`, `asset_out`, `amount_out`, `usd_low`, `usd_high` |
| `fundings` | `chain`, `ts`, `tx_ref`, `funder`, `funded`, `fee_source`, `sponsor`, `asset`, `amount`, `is_first` |
| `transfers` | `chain`, `ts`, `ledger`, `tx_ref`, `op_ref`, `sender`, `receiver`, `asset`, `amount`, `kind`, `asset_sent`, `amount_sent` |
| `holders` | `chain`, `day`, `asset`, `holders_count`, `new_holders` |
| `orders` | `chain`, `ts`, `ledger`, `tx_ref`, `account`, `market`, `op_type`, `amount`, `price` |
| `labels` | `chain`, `address`, `label`, `source` |
| `entities` | `chain`, `entity_id`, `address`, `reason` |

## Escenarios del benchmark

| # | Escenario | Qué simula |
| --- | --- | --- |
| 1 | `honesto` | Traders independientes, fondeados por orígenes distintos, holders que crecen. |
| 2 | `anillo_de_lavado` | S1: 3–6 cuentas de un mismo origen operan entre sí; posición neta ≈ 0 (ida y vuelta). |
| 3 | `ciclo` | S5: A vende a B, B a C, C a A en el SDEX; el token regresa al origen, ventana corta. |
| 4 | `bot_mecanico` | S6: una entidad con montos e intervalos casi constantes. NO es volumen falso. |
| 5 | `arbitraje` | Path payment en una sola transacción, origen = destino, ganancia neta en el activo origen. |
| 6 | `manipulacion_de_precio` | S7: mercado sin liquidez; una entidad retira su oferta, coloca una anormal, el precio salta ~100x. |
| 7 | `trampa_exchange` | TRAMPA: un exchange fondea muchas cuentas que operan independientes. Verdad: honesto. |
| 8 | `trampa_market_maker` | TRAMPA: un market maker opera en ambos lados con posición que sí cambia. Verdad: honesto. |
| 9 | `ciclo_por_transferencias` | S5 (spec v0.2 §5.2): A compra X al pool, se lo transfiere a B, B lo vende al pool y le devuelve la cotización a A, en una ventana corta. El valor regresa a A (menos comisiones). |
| 10 | `trampa_pagos_honestos` | TRAMPA: una cuenta paga a varios proveedores independientes que operan por su cuenta. El valor nunca regresa al pagador. Verdad: honesto. |

`verdad.senal_esperada` dice qué debería marcar un detector en cada swap: `S1` (ida y vuelta), `S5` (ciclos), `S7`
(precio movido por manipulación), `arbitraje` o `ninguna`. S6 (regularidad mecánica) indica un bot, no volumen
falso: por eso el bot mecánico espera `ninguna`. Las trampas son actividad honesta que se parece a la manipulada;
un detector que las marca da falsos positivos.

## Protocolo de revisión con datos reales

[`REVIEW_PROTOCOL.md`](REVIEW_PROTOCOL.md) (en inglés) fija antes de ver resultados cómo se revisan a mano las
alertas de un detector con datos reales de Stellar: qué se revisa, cómo se sortea la muestra con una semilla que
nadie controla (el hash de un ledger), cómo se etiqueta cada alerta y qué cuenta como aprobado.
`src/lumen_lupa/revision/` lo ejecuta tal como está escrito, solo con la biblioteca estándar y sin red. Con el
ejemplo sintético de `tests/fixtures/revision/` (su origen está en `ORIGEN.md`, en esa misma carpeta):

```bash
uv run python -m lumen_lupa.revision huella --alertas tests/fixtures/revision/alertas.json
uv run python -m lumen_lupa.revision muestra --alertas tests/fixtures/revision/alertas.json --semilla 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef --salida salidas/revision/muestra.json
uv run python -m lumen_lupa.revision puntaje --muestra salidas/revision/muestra.json --respuestas tests/fixtures/revision/respuestas_revisor_1.csv --segundo tests/fixtures/revision/respuestas_revisor_2.csv --resoluciones tests/fixtures/revision/resoluciones.csv --alertas tests/fixtures/revision/alertas.json --salida salidas/revision
```

`huella` imprime el sha256 de la lista de alertas, el que se congela (§4); `muestra` sortea por señal las alertas
a revisar y las que también contesta un segundo revisor (§6 y §8); `puntaje` deriva cada etiqueta de las cuatro
respuestas (§7), mide el acuerdo con las respuestas tal como se dieron, puntúa con las respuestas resueltas de
cada desacuerdo (`--resoluciones`, §8), aplica la regla de aprobación (§9) y escribe `resultado.json` y
`resultado.md`. Con datos reales, la semilla es el hash del ledger del §5 y la lista es la que se congeló.

## Contrato de atestaciones (Soroban)

`contratos/atestaciones/` es un contrato de Soroban que registra reportes solo por hash: sin nombres de tokens, cuentas ni cifras. `src/lumen_lupa/atestacion.py` es su puente en Python: calcula esos hashes. Cómo construirlo, desplegarlo y verificar una atestación: `contratos/atestaciones/README.md`.

## Cómo contribuir

- Abre un *issue* con lo que encontraste o propones; para cambios de código, un *pull request* pequeño con pruebas.
- Antes del *pull request*: `uv run ruff check`, `uv run ruff format --check` y `uv run pytest -q` deben pasar; es
  lo mismo que corre el CI.
- Las pruebas no usan red ni credenciales: salen del simulador o de fixtures. Una prueba que consulte BigQuery
  lleva `@pytest.mark.live` y no corre en el CI.
- Nunca subas credenciales, `.env` ni datos descargados (`data/`, `*.duckdb`, `*.jsonl` fuera de
  `tests/fixtures/`).
- Las dependencias van con versión exacta en `pyproject.toml` y `uv.lock`; una dependencia nueva se platica antes
  en un *issue*.
- Los ejemplos y fixtures usan el mercado de control o datos sintéticos: el kit no publica juicios sobre tokens ni
  cuentas reales, ni genera actividad en la cadena para probar.

## Licencia

Apache-2.0: ver `LICENSE` y `NOTICE`.

Los comentarios del código citan documentos internos del proyecto (la especificación, decisiones D-xx y contratos
de tarea Tn) que no vienen en el kit.
