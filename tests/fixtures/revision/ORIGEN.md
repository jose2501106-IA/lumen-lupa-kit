# Fixture sintético del protocolo de revisión (contrato T9)

Datos inventados para probar `lumen_lupa.revision` de punta a punta. No salen de ninguna cadena: los mercados
son las letras A, B y C, los ids tienen la forma `S1-A-001` y los pesos se escribieron a mano. La semilla es un
patrón (`0123456789abcdef` cuatro veces), no el hash de un ledger real.

| Archivo | Qué es |
| --- | --- |
| `alertas.json` | Lista de alertas de la semana 2026-09-28 a 2026-10-04: 10 de S1 (van a censo) y 40 de S5 (muestra de 25), en desorden a propósito |
| `respuestas_revisor_1.csv` | Respuestas del primer revisor a las 35 alertas a revisar; algunas con sí, si o incierto |
| `respuestas_revisor_2.csv` | Respuestas del segundo revisor a las 13 que le tocan (4 de S1 y 9 de S5) |
| `resoluciones.csv` | Respuestas resueltas de los 2 desacuerdos, con una línea que explica cada resolución (§8) |
| `esperado_muestra.json` | Salida de `muestra` con esa semilla y n = 25 |
| `esperado_resultado.json`, `esperado_resultado.md` | Salida de `puntaje` con las dos respuestas, las resoluciones y la lista |

Las respuestas se escribieron para cubrir los dos caminos de la regla del §9: S1 (censo) no pasa porque un
falso positivo pesa mucho (los 2 falsos positivos son el 39.02% del peso revisado, aunque son 2 de 10), y S5
(muestra) pasa. El segundo revisor difiere en una etiqueta (S5-A-006, por q2) y en una pregunta que no cambia
la etiqueta (S5-B-003, por q4): acuerdo de 12 de 13, medido con las respuestas tal como se dieron. Las
resoluciones dejan S5-A-006 como `consistente` (q2 = yes) y S5-B-003 como `falso_positivo` (q4 = no), así que
S5 cuenta 18 consistentes, 3 falsos positivos y 4 indeterminados de 25 (sin resolver serían 17, 3 y 5).

La huella de `alertas.json` es `b63f43c338c985487fc4d5ea38a11765f61f3f66abbc68100094a1a99bfc1bc2`. Sale igual sin
la herramienta, porque los pesos ya vienen sin ceros sobrantes:

```bash
jq -cS '.alertas |= sort_by(.id)' tests/fixtures/revision/alertas.json | tr -d '\n' | sha256sum
```

Las salidas esperadas se regeneran así, desde la raíz del repositorio (después hay que revisarlas a mano):

```bash
S=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
D=tests/fixtures/revision
uv run python -m lumen_lupa.revision muestra --alertas $D/alertas.json --semilla $S --salida $D/esperado_muestra.json
uv run python -m lumen_lupa.revision puntaje --muestra $D/esperado_muestra.json \
  --respuestas $D/respuestas_revisor_1.csv --segundo $D/respuestas_revisor_2.csv \
  --resoluciones $D/resoluciones.csv --alertas $D/alertas.json \
  --salida /tmp/resultado
cp /tmp/resultado/resultado.json $D/esperado_resultado.json
cp /tmp/resultado/resultado.md $D/esperado_resultado.md
```
