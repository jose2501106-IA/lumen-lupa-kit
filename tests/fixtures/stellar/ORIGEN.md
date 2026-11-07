# Fixtures de Stellar (T3b, paso 11) — origen

Filas crudas **reales** del **mercado de control** (`USDC:GA5ZSEJY…/XLM:nativo`), extraídas el
2026-10-09 de los datos descargados en Codespaces (fuera de git). Sirven para probar la traducción
**sin red** (`tests/test_stellar_hubble.py`). Ninguna fila toca una cuenta de otros mercados del
estudio: se filtraron explícitamente al construirlos.

| Archivo | Filas | Origen (consulta) | Contenido |
| --- | ---: | --- | --- |
| `02_trades_control_fixture.jsonl` | 15 | `queries/estudio/02_trades_*.sql` (history_trades, ventana 2026-09-21..10-04) | 8 trade_type 1 (libro de órdenes) y 7 trade_type 2 (pool clásico) del par USDC/XLM; uno cumple el invariante de precio a ±1 stroop |
| `03_operaciones_control_fixture.jsonl` | 6 | `queries/estudio/03_operaciones_*.sql` (history_operations + successful, misma ventana) | 1 payment (type 1), 1 path_payment_strict_receive (2), 1 path_payment_strict_send (13), 1 payment con destino **muxed** (M…), 1 operación de **transacción fallida** (successful=false), 1 create_account (type 0) |

No hay filas sintéticas: todos los casos requeridos existían en datos reales del mercado de control.
Los montos son FLOAT64 crudos (así se bajaron); la traducción los pasa a Decimal(7).
