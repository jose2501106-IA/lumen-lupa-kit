"""Salidas del puntaje: resultado.json y resultado.md, en inglés (contrato T9, diseño 4).

resultado.md lleva un renglón por señal, el acuerdo entre revisores (citando el §8: se mide con las
respuestas tal como se dieron), los desacuerdos con su resolución, el veredicto del entregable 1 y la nota
del §9 sobre lo que significa pasar, citada tal cual del protocolo y recalculada para el n de la muestra.
resultado.json lleva lo mismo con los valores exactos (sumas de pesos sin redondeo) y, por alerta revisada,
las respuestas y la etiqueta de cada revisor, las resueltas si las hay y la etiqueta que cuenta.

Ninguna salida copia `evidencia` ni `notas` de los revisores (pueden tener cuentas o enlaces privados, §11).
El único texto libre que sale es el de cada resolución, porque el §8 pide listar cada desacuerdo con ella.
"""

from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Context, Decimal
from fractions import Fraction
from pathlib import Path

from lumen_lupa.revision.alertas import SENALES
from lumen_lupa.revision.comun import PROTOCOLO, ErrorRevision, texto_decimal, texto_fraccion
from lumen_lupa.revision.estadistica import cdf_binomial, cota_superior_cp, max_falsos_positivos
from lumen_lupa.revision.muestreo import CENSO
from lumen_lupa.revision.puntaje import (
    COMPLETO,
    FALTA_DOBLE_REVISION,
    UMBRAL_ACUERDO,
    Acuerdo,
    PuntajeSenal,
    Resultado,
)
from lumen_lupa.revision.rubrica import CONSISTENTE, ETIQUETAS, FALSO_POSITIVO, INDETERMINADO

# Textos de REVIEW_PROTOCOL.md, tal cual (una prueba los compara con el archivo del protocolo).
ACUERDO_SECCION_8 = (
    "We report agreement on the label (share of matches and Cohen's kappa), computed on the answers as "
    "first given, before any disagreement is settled."
)
DOBLE_REVISION_SECCION_8 = (
    "If agreement is below 70%, every reviewed alert gets a second set of answers before scoring."
)
DESACUERDOS_SECCION_8 = (
    "Disagreements are settled question by question against the rubric. Each one is listed with its "
    "resolution, and the score uses the settled answers."
)
NOTA_SECCION_9 = (
    "With 25 alerts drawn by weight, a signal whose flagged volume is 10% wrong passes about 90% of the "
    "time; one that is 30% wrong passes about 9% of the time. Four false positives out of 25 means that, "
    "with 90% confidence, less than 30% of the flagged volume is wrong (one-sided Clopper–Pearson bound: "
    "29.5%). A census has no sampling error, so its limit is the 20% target itself; a sample needs a "
    "margin, which is why its limit is 4 of 25."
)
ENTREGABLE_SECCION_9 = (
    "**Deliverable 1 is met if at least one signal passes.** A signal that does not pass is reported with "
    "its numbers and marked low-confidence, and its volume is not subtracted in report #1."
)

CIFRAS_JSON = 12  # cifras significativas de las proporciones, la kappa y la cota en resultado.json
_CONTEXTO = Context(prec=60)


def porcentaje(fraccion: Fraction, decimales: int = 2) -> str:
    """Una proporción como porcentaje en texto, redondeado a la mitad hacia arriba: 1/3 → "33.33%"."""
    valor = _CONTEXTO.divide(Decimal(fraccion.numerator * 100), Decimal(fraccion.denominator))
    return f"{valor.quantize(Decimal(1).scaleb(-decimales), rounding=ROUND_HALF_UP, context=_CONTEXTO)}%"


def _decimales(fraccion: Fraction, decimales: int) -> str:
    valor = _CONTEXTO.divide(Decimal(fraccion.numerator), Decimal(fraccion.denominator))
    return str(valor.quantize(Decimal(1).scaleb(-decimales), rounding=ROUND_HALF_UP, context=_CONTEXTO))


def _kappa(kappa: Fraction | None) -> str:
    return "undefined" if kappa is None else _decimales(kappa, 3)


def _texto(fraccion: Fraction | None) -> str | None:
    return None if fraccion is None else texto_fraccion(fraccion, CIFRAS_JSON)


def _celda_md(texto: str) -> str:
    """Texto libre dentro de una celda de tabla de Markdown: la barra vertical no puede cortar la tabla."""
    return texto.replace("|", "\\|")


# --- resultado.json -------------------------------------------------------------------------------------


def _acuerdo_json(acuerdo: Acuerdo) -> dict[str, object]:
    return {
        "double_reviewed": acuerdo.dobles,
        "matches": acuerdo.coincidencias,
        "share": _texto(acuerdo.proporcion),
        "kappa": _texto(acuerdo.kappa),
    }


def _senal_json(s: PuntajeSenal) -> dict[str, object]:
    censo = s.modo == CENSO
    return {
        "mode": s.modo,
        "population": s.poblacion,
        "reviewed": s.revisadas,
        "counts": s.conteos,
        "weight_reviewed": texto_decimal(s.peso_revisado) if censo else None,
        "weight_by_label": {e: texto_decimal(p) for e, p in s.peso_por_etiqueta.items()} if censo else None,
        "weight_share": {e: _texto(p) for e, p in s.participacion.items()} if censo else None,
        "max_false_positives": s.max_falsos_positivos,
        "max_indeterminate": s.max_indeterminados,
        "false_positive_upper_bound_90": _texto(s.cota_falsos_positivos),
        "passes": s.pasa,
        "low_confidence": s.pasa is False,
        "reasons": _motivos(s, s.revisadas) if s.pasa is False else [],
        "agreement": _acuerdo_json(s.acuerdo),
    }


def como_json(r: Resultado) -> dict[str, object]:
    m = r.muestra
    presentes = {s.senal for s in r.senales}
    return {
        "protocol_version": PROTOCOLO,
        "week": {"from": m.desde, "to": m.hasta},
        "alert_list_sha256": m.huella,
        "seed": m.semilla,
        "n": m.n,
        "status": r.estado,
        "status_reason": _motivo_estado(r),
        "deliverable_1_met": r.entregable_1,
        "passing_signals": r.senales_que_pasan(),
        "signals": {s.senal: _senal_json(s) for s in r.senales},
        "signals_without_alerts": [s for s in SENALES if s not in presentes],
        "agreement": {
            **_acuerdo_json(r.acuerdo),
            "threshold": texto_fraccion(UMBRAL_ACUERDO),
            "second_reviewer_answers": r.hay_segundo,
            "every_reviewed_alert_double": r.todas_dobles,
            "note": ACUERDO_SECCION_8,
        },
        "disagreements": [
            {
                "id": d.alerta_id,
                "signal": d.senal,
                "questions": list(d.preguntas),
                "first_label": d.primera,
                "second_label": d.segunda,
                "settled_label": d.resuelta,
                "resolution": d.resolucion,
            }
            for d in r.desacuerdos
        ],
        "alerts": [
            {
                "id": i,
                "signal": sorteo.senal,
                "first": r.respuestas[i].como_dict(),
                "second": r.segundas[i].como_dict() if i in r.segundas else None,
                "settled": r.resoluciones[i].respuesta.como_dict() if i in r.resoluciones else None,
                "scored_label": r.definitivas[i].etiqueta,
            }
            for sorteo in m.senales
            for i in sorteo.revisar
        ],
    }


# --- resultado.md ---------------------------------------------------------------------------------------


def _motivo_estado(r: Resultado) -> str | None:
    if r.estado == COMPLETO:
        return None
    if r.estado != FALTA_DOBLE_REVISION:
        return (
            f"Disagreements without settled answers: {len(r.sin_resolver())} of {len(r.desacuerdos)}; "
            "section 8 settles each one, and the score uses the settled answers."
        )
    if not r.hay_segundo:
        return (
            "There are no second-reviewer answers; section 8 requires them for at least a third of the "
            "reviewed alerts."
        )
    a = r.acuerdo
    return (
        f"Label agreement is {porcentaje(a.proporcion)} ({a.coincidencias} of {a.dobles}), below 70%, and "
        "not every reviewed alert has a second set of answers; section 8 requires one for every reviewed "
        "alert before scoring."
    )


def _motivos(s: PuntajeSenal, n: int) -> list[str]:
    """Por qué una señal no cumple la regla del §9."""
    motivos = []
    if s.modo == CENSO:
        if not s.cumple_falsos_positivos:
            motivos.append(
                f"false positives hold {porcentaje(s.participacion[FALSO_POSITIVO])} of the reviewed weight "
                "(at most 20%)"
            )
        if not s.cumple_indeterminados:
            motivos.append(
                f"indeterminate alerts hold {porcentaje(s.participacion[INDETERMINADO])} of the reviewed "
                "weight (at most a third)"
            )
        return motivos
    if not s.cumple_falsos_positivos:
        if s.max_falsos_positivos is None:
            motivos.append(
                f"with n = {n}, no number of false positives keeps the one-sided 90% Clopper–Pearson bound "
                "below 30%"
            )
        else:
            motivos.append(
                f"{s.conteos[FALSO_POSITIVO]} false positives of {n} (at most {s.max_falsos_positivos})"
            )
    if not s.cumple_indeterminados:
        motivos.append(f"{s.conteos[INDETERMINADO]} indeterminate of {n} (at most {s.max_indeterminados})")
    return motivos


def _celda_etiqueta(s: PuntajeSenal, e: str) -> str:
    if s.modo == CENSO:
        return f"{s.conteos[e]} ({porcentaje(s.participacion[e])} of weight)"
    return f"{s.conteos[e]} of {s.revisadas} ({porcentaje(Fraction(s.conteos[e], s.revisadas))})"


def _celda_acuerdo(a: Acuerdo) -> str:
    if a.dobles == 0:
        return "no second answers"
    return f"{a.coincidencias} of {a.dobles} ({porcentaje(a.proporcion)}), kappa {_kappa(a.kappa)}"


def _celda_pasa(s: PuntajeSenal, estado: str) -> str:
    if s.pasa is None:
        return (
            "pending: double review missing"
            if estado == FALTA_DOBLE_REVISION
            else "pending: resolutions missing"
        )
    if s.pasa:
        return "**yes**"
    return "**no**, low-confidence: " + "; ".join(_motivos(s, s.revisadas))


def _fila(s: PuntajeSenal, estado: str) -> str:
    celdas = [
        s.senal,
        "census" if s.modo == CENSO else "sample",
        str(s.poblacion),
        str(s.revisadas),
        *(_celda_etiqueta(s, e) for e in ETIQUETAS),
        "n/a (census)" if s.cota_falsos_positivos is None else porcentaje(s.cota_falsos_positivos, 1),
        _celda_acuerdo(s.acuerdo),
        _celda_pasa(s, estado),
    ]
    return "| " + " | ".join(celdas) + " |"


def _regla(n: int) -> str:
    """La regla del §9 en texto: la del censo y la de una muestra de n."""
    censo = (
        "Pass rule (section 9). Census: false positives hold no more than 20% of the reviewed weight, and "
        "indeterminate alerts no more than a third of it."
    )
    maximo = max_falsos_positivos(n)
    if n == 25:
        muestra = (
            "Sample of 25: no more than 4 of the 25 are false positives, and no more than 8 are "
            "indeterminate."
        )
    elif maximo is None:
        muestra = (
            f"Sample of {n}: no number of false positives keeps the one-sided 90% Clopper–Pearson bound "
            f"below 30%, so a sample of {n} cannot pass."
        )
    else:
        muestra = (
            f"Sample of {n}: no more than {maximo} of the {n} are false positives (the largest count whose "
            f"one-sided 90% Clopper–Pearson bound is below 30%), and no more than {n // 3} are indeterminate."
        )
    cota = (
        "The false-positive bound is the one-sided 90% Clopper–Pearson upper bound for the share of false "
        "positives (sample only)."
    )
    return f"{censo} {muestra} {cota}"


def _recalculo_seccion_9(n: int) -> str:
    maximo = max_falsos_positivos(n)
    if maximo is None:
        return (
            f"Recomputed for n = {n}: no number of false positives keeps the one-sided 90% Clopper–Pearson "
            f"bound below 30%, so a sample of {n} cannot pass."
        )
    p10 = cdf_binomial(maximo, n, Fraction(1, 10))
    p30 = cdf_binomial(maximo, n, Fraction(3, 10))
    cota = cota_superior_cp(maximo, n)
    return (
        f"Recomputed for n = {n}: P(at most {maximo} false positives | 10% wrong) = {_decimales(p10, 3)}; "
        f"P(at most {maximo} | 30% wrong) = {_decimales(p30, 3)}; one-sided 90% Clopper–Pearson bound for "
        f"{maximo} of {n}: {porcentaje(cota, 1)}."
    )


def _seccion_acuerdo(r: Resultado) -> list[str]:
    lineas = ["## Agreement between reviewers (section 8)", "", f"> {ACUERDO_SECCION_8}", ""]
    if not r.hay_segundo:
        return [*lineas, "No second-reviewer answers were given.", ""]
    a = r.acuerdo
    revisadas = len(r.muestra.ids_a_revisar())
    asignadas = len(r.muestra.ids_segundo_revisor())
    if a.dobles == asignadas:
        quien = (
            f"The second reviewer answered the {asignadas} of the {revisadas} reviewed alerts that the seed "
            "assigns (at least a third of each signal, rounded up)."
        )
    else:
        quien = (
            f"The second reviewer answered {a.dobles} of the {revisadas} reviewed alerts, including the "
            f"{asignadas} that the seed assigns (at least a third of each signal, rounded up)."
        )
    if a.dobles:
        cuanto = (
            f"Label agreement on the answers as first given: {a.coincidencias} of {a.dobles} "
            f"({porcentaje(a.proporcion)}); Cohen's kappa: {_kappa(a.kappa)}."
        )
    else:
        cuanto = "No alert has two sets of answers."
    lineas += [f"{quien} {cuanto}", "", f"> {DOBLE_REVISION_SECCION_8}", "", f"> {DESACUERDOS_SECCION_8}", ""]
    if not r.desacuerdos:
        return [
            *lineas,
            "The two reviewers gave the same answers to every alert they both reviewed, so the scores above "
            "use the first reviewer's answers.",
            "",
        ]
    lineas += [
        "| Alert | Signal | Questions that differ | First reviewer | Second reviewer | Settled "
        "| Resolution |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for d in r.desacuerdos:
        resuelta = "pending" if d.resuelta is None else f"`{d.resuelta}`"
        resolucion = "pending" if d.resolucion is None else _celda_md(d.resolucion)
        lineas.append(
            f"| {d.alerta_id} | {d.senal} | {', '.join(d.preguntas)} | `{d.primera}` | `{d.segunda}` "
            f"| {resuelta} | {resolucion} |"
        )
    lineas += [
        "",
        "The scores above use the settled answers: the first reviewer's answers, replaced by the settled "
        "answers for each disagreement.",
    ]
    if r.sin_resolver():
        lineas[-1] += (
            " A disagreement without a resolution still counts with the first reviewer's answers, and the "
            "result stays incomplete until every disagreement is settled."
        )
    return [*lineas, ""]


def _seccion_entregable(r: Resultado) -> list[str]:
    if r.entregable_1 is None:
        veredicto = f"**No verdict.** The result is incomplete (`{r.estado}`)."
    elif r.entregable_1:
        veredicto = f"**Met.** At least one signal passes: {', '.join(r.senales_que_pasan())}."
    else:
        veredicto = "**Not met.** No signal passes."
    return ["## Deliverable 1", "", veredicto, ""]


def como_markdown(r: Resultado) -> str:
    m = r.muestra
    if r.estado == COMPLETO:
        estado = f"complete (`{r.estado}`)"
    else:
        falta = "double review missing" if r.estado == FALTA_DOBLE_REVISION else "resolutions missing"
        estado = f"**incomplete: {falta}** (`{r.estado}`). {_motivo_estado(r)}"
    lineas = [
        "# Real-data review: result",
        "",
        "Computed with `python -m lumen_lupa.revision puntaje`, following REVIEW_PROTOCOL.md version "
        f"{PROTOCOLO}.",
        "",
        f"- Week: {m.desde} to {m.hasta}",
        f"- Alert list sha256: `{m.huella}`",
        f"- Seed: `{m.semilla}`",
        f"- Sample size (n): {m.n}",
        f"- Status: {estado}",
        "",
        "## Result by signal",
        "",
        "| Signal | Mode | Alerts | Reviewed | `consistente` | `falso_positivo` | `indeterminado` "
        "| False-positive bound | Agreement | Passes |",
        "| --- | --- | ---: | ---: | --- | --- | --- | --- | --- | --- |",
        *(_fila(s, r.estado) for s in r.senales),
        "",
    ]
    presentes = {s.senal for s in r.senales}
    lineas += [f"{s} has no alerts in the list and is not reviewed." for s in SENALES if s not in presentes]
    if len(presentes) < len(SENALES):
        lineas.append("")
    lineas += [
        f"Labels (section 7): `{CONSISTENTE}`, consistent with the pattern; `{FALSO_POSITIVO}`, false "
        f"positive; `{INDETERMINADO}`, indeterminate. In a census each label shows its count and its share "
        "of the reviewed weight; in a sample, its count out of n.",
        "",
        _regla(m.n),
        "",
        *_seccion_acuerdo(r),
        *_seccion_entregable(r),
        "## What passing means (section 9)",
        "",
        f"> {NOTA_SECCION_9}",
        "",
        _recalculo_seccion_9(m.n),
        "",
        f"> {ENTREGABLE_SECCION_9}",
    ]
    return "\n".join(lineas) + "\n"


def escribir(r: Resultado, directorio: Path) -> tuple[Path, Path]:
    """Escribe resultado.json y resultado.md en `directorio` (lo crea si no existe)."""
    directorio = Path(directorio)
    try:
        directorio.mkdir(parents=True, exist_ok=True)
        ruta_json, ruta_md = directorio / "resultado.json", directorio / "resultado.md"
        ruta_json.write_text(json.dumps(como_json(r), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        ruta_md.write_text(como_markdown(r), encoding="utf-8")
    except OSError as e:
        raise ErrorRevision(f"no pude escribir el resultado en {directorio}: {e.strerror}") from e
    return ruta_json, ruta_md
