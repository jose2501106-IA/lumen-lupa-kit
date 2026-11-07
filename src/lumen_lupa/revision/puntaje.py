"""Puntaje de la revisión (REVIEW_PROTOCOL.md §1, §7, §8 y §9; contrato T9, diseño 4).

Entradas: la muestra (de `muestra`), las respuestas del primer revisor, las del segundo si las hay, las
resoluciones de los desacuerdos si las hay y la lista de alertas, obligatoria si alguna señal está en censo
(de ahí salen los pesos).

Validación, antes de calcular nada:

- Primer revisor: exactamente una respuesta por id a revisar; sin faltantes ni sobrantes (los duplicados
  los rechaza la lectura del CSV).
- Segundo revisor: solo ids a revisar y, entre ellos, todos los que le tocan según la muestra (§8). Puede
  contestar más, hasta todas, para la doble revisión completa.
- Resoluciones: solo de desacuerdos, es decir, de alertas cuyas dos respuestas difieren en al menos una
  pregunta.
- Lista de alertas: su huella debe ser la de la muestra, y la muestra la que sale de esa lista con su semilla
  y su n (una muestra editada a mano no pasa).

Respuestas que cuentan (§8): las del primer revisor, salvo en los desacuerdos resueltos, donde cuentan las
resueltas. El acuerdo se mide siempre con las respuestas tal como se dieron (las del primer y las del segundo
revisor), antes de resolver ningún desacuerdo; las resoluciones no lo cambian.

Por señal: el conteo de cada etiqueta (la rúbrica la deriva de las respuestas que cuentan, §7) y la regla
del §9:

- Censo: pasa si los falsos positivos suman a lo más el 20% del peso revisado y los indeterminados a lo más
  un tercio; los dos límites son inclusivos y se comparan sin redondeo (sumas de Decimal y fracciones).
- Muestra de n: pasa si hay a lo más `max_falsos_positivos(n)` falsos positivos (4 de 25) y a lo más ⌊n/3⌋
  indeterminados (8 de 25). Se informa la cota de Clopper–Pearson de una cola al 90% de la proporción de
  falsos positivos.

Estado, en este orden: "incompleto: falta doble revisión" si no hay respuestas del segundo revisor, o si el
acuerdo total es menor que 70% y no todas las revisadas tienen segunda respuesta; "incompleto: faltan
resoluciones" si algún desacuerdo no está resuelto; si no, "completo". Un resultado incompleto da los
conteos, pero no aplica la regla ni da veredicto: el §8 pide la doble revisión antes del puntaje, y el
puntaje usa las respuestas resueltas.

Entregable 1 (§1 y §9): se cumple si al menos una señal pasa.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from fractions import Fraction

from lumen_lupa.revision.alertas import ListaAlertas, huella
from lumen_lupa.revision.comun import ErrorRevision, lista_corta, suma_exacta
from lumen_lupa.revision.estadistica import (
    cota_superior_cp,
    kappa_cohen,
    max_falsos_positivos,
    max_indeterminados,
    proporcion_de_acuerdo,
)
from lumen_lupa.revision.muestreo import CENSO, Muestra, SorteoSenal, armar_muestra
from lumen_lupa.revision.rubrica import (
    ETIQUETAS,
    FALSO_POSITIVO,
    INDETERMINADO,
    PREGUNTAS,
    Resolucion,
    Respuesta,
)

COMPLETO = "completo"
FALTA_DOBLE_REVISION = "incompleto: falta doble revisión"
FALTAN_RESOLUCIONES = "incompleto: faltan resoluciones"
MAX_FP_CENSO = Fraction(1, 5)  # §9: falsos positivos ≤ 20% del peso revisado
MAX_INDETERMINADOS_CENSO = Fraction(1, 3)  # §9: indeterminados ≤ un tercio del peso revisado
UMBRAL_ACUERDO = Fraction(7, 10)  # §8: con acuerdo menor que 70%, segunda respuesta para todas


@dataclass(frozen=True, slots=True)
class Acuerdo:
    """Acuerdo sobre la etiqueta entre los dos revisores, en las alertas con las dos respuestas."""

    dobles: int
    coincidencias: int
    proporcion: Fraction | None
    kappa: Fraction | None


@dataclass(frozen=True, slots=True)
class PuntajeSenal:
    senal: str
    modo: str
    poblacion: int
    revisadas: int
    conteos: dict[str, int]  # por etiqueta (de las respuestas que cuentan), en el orden de ETIQUETAS
    peso_revisado: Decimal | None  # solo en censo
    peso_por_etiqueta: dict[str, Decimal] | None  # solo en censo
    participacion: dict[str, Fraction] | None  # solo en censo: peso de la etiqueta / peso revisado
    max_falsos_positivos: int | None  # solo en muestra; None también si ningún c cumple
    max_indeterminados: int | None  # solo en muestra
    cota_falsos_positivos: Fraction | None  # solo en muestra
    cumple_falsos_positivos: bool
    cumple_indeterminados: bool
    acuerdo: Acuerdo  # con las respuestas tal como se dieron
    pasa: bool | None  # None si el resultado está incompleto


@dataclass(frozen=True, slots=True)
class Desacuerdo:
    alerta_id: str
    senal: str
    preguntas: tuple[str, ...]  # las que difieren entre las dos respuestas tal como se dieron
    primera: str  # etiqueta del primer revisor
    segunda: str  # etiqueta del segundo revisor
    resuelta: str | None  # etiqueta de las respuestas resueltas; None si falta la resolución
    resolucion: str | None  # texto de la resolución


@dataclass(frozen=True, slots=True)
class Resultado:
    muestra: Muestra
    senales: tuple[PuntajeSenal, ...]
    acuerdo: Acuerdo
    hay_segundo: bool
    todas_dobles: bool
    desacuerdos: tuple[Desacuerdo, ...]
    estado: str
    entregable_1: bool | None  # None si el resultado está incompleto
    respuestas: Mapping[str, Respuesta]  # primer revisor, tal como contestó
    segundas: Mapping[str, Respuesta]  # segundo revisor, tal como contestó
    resoluciones: Mapping[str, Resolucion]
    definitivas: Mapping[str, Respuesta]  # las que cuentan para el puntaje

    def senales_que_pasan(self) -> list[str]:
        return [s.senal for s in self.senales if s.pasa]

    def sin_resolver(self) -> list[Desacuerdo]:
        return [d for d in self.desacuerdos if d.resuelta is None]


def _validar_lista(muestra: Muestra, lista: ListaAlertas | None) -> None:
    en_censo = [s.senal for s in muestra.senales if s.modo == CENSO]
    if lista is None:
        if en_censo:
            raise ErrorRevision(
                f"en censo ({', '.join(en_censo)}) el puntaje usa el peso de cada alerta: "
                "hace falta la lista de alertas (--alertas)"
            )
        return
    huella_lista = huella(lista)
    if huella_lista != muestra.huella:
        raise ErrorRevision(
            f"la huella de la lista de alertas ({huella_lista}) no es la de la muestra ({muestra.huella}): "
            "no es la misma lista"
        )
    if armar_muestra(lista, muestra.semilla, muestra.n) != muestra:
        raise ErrorRevision(
            "la muestra no es la que sale de la lista de alertas con su semilla y su n; "
            "vuelve a sacarla con `muestra`"
        )


def _validar_respuestas(
    muestra: Muestra, respuestas: Mapping[str, Respuesta], segundas: Mapping[str, Respuesta] | None
) -> None:
    revisar = muestra.ids_a_revisar()
    conjunto = set(revisar)
    que = "respuestas del primer revisor (--respuestas)"
    faltan = [i for i in revisar if i not in respuestas]
    if faltan:
        raise ErrorRevision(f"{que}: faltan {len(faltan)} de las alertas a revisar: {lista_corta(faltan)}")
    sobran = [i for i in respuestas if i not in conjunto]
    if sobran:
        raise ErrorRevision(
            f"{que}: sobran {len(sobran)} que no están entre las alertas a revisar: {lista_corta(sobran)}"
        )
    if segundas is None:
        return
    que = "respuestas del segundo revisor (--segundo)"
    sobran = [i for i in segundas if i not in conjunto]
    if sobran:
        raise ErrorRevision(
            f"{que}: sobran {len(sobran)} que no están entre las alertas a revisar: {lista_corta(sobran)}"
        )
    faltan = [i for i in muestra.ids_segundo_revisor() if i not in segundas]
    if faltan:
        raise ErrorRevision(
            f"{que}: faltan {len(faltan)} de las que le tocan según la muestra (§8): {lista_corta(faltan)}"
        )


def _desacuerdos(
    muestra: Muestra,
    respuestas: Mapping[str, Respuesta],
    segundas: Mapping[str, Respuesta],
    resoluciones: Mapping[str, Resolucion],
) -> tuple[Desacuerdo, ...]:
    """Alertas con dos respuestas que difieren en al menos una pregunta, por señal y en orden del sorteo."""
    lista = []
    for sorteo in muestra.senales:
        for i in sorteo.revisar:
            if i not in segundas:
                continue
            primera, segunda = respuestas[i], segundas[i]
            preguntas = tuple(p for p in PREGUNTAS if getattr(primera, p) != getattr(segunda, p))
            if preguntas:
                resolucion = resoluciones.get(i)
                lista.append(
                    Desacuerdo(
                        alerta_id=i,
                        senal=sorteo.senal,
                        preguntas=preguntas,
                        primera=primera.etiqueta,
                        segunda=segunda.etiqueta,
                        resuelta=None if resolucion is None else resolucion.etiqueta,
                        resolucion=None if resolucion is None else resolucion.texto,
                    )
                )
    return tuple(lista)


def _validar_resoluciones(desacuerdos: Sequence[Desacuerdo], resoluciones: Mapping[str, Resolucion]) -> None:
    ids = {d.alerta_id for d in desacuerdos}
    sobran = [i for i in resoluciones if i not in ids]
    if sobran:
        raise ErrorRevision(
            f"resoluciones (--resoluciones): {len(sobran)} no son desacuerdos (alertas con dos respuestas "
            f"que difieren en al menos una pregunta): {lista_corta(sobran)}"
        )


def _acuerdo(
    ids: Sequence[str], respuestas: Mapping[str, Respuesta], segundas: Mapping[str, Respuesta]
) -> Acuerdo:
    """Acuerdo con las respuestas tal como se dieron (§8), nunca con las resueltas."""
    pares = [(respuestas[i].etiqueta, segundas[i].etiqueta) for i in ids if i in segundas]
    return Acuerdo(
        dobles=len(pares),
        coincidencias=sum(a == b for a, b in pares),
        proporcion=proporcion_de_acuerdo(pares),
        kappa=kappa_cohen(pares, ETIQUETAS),
    )


def _puntuar_senal(
    sorteo: SorteoSenal,
    n: int,
    definitivas: Mapping[str, Respuesta],
    pesos: Mapping[str, Decimal],
    acuerdo: Acuerdo,
) -> PuntajeSenal:
    """Conteos y regla del §9 de una señal (`pasa` queda en None: lo decide el estado del resultado)."""
    etiquetas = {i: definitivas[i].etiqueta for i in sorteo.revisar}
    conteos = {e: sum(1 for x in etiquetas.values() if x == e) for e in ETIQUETAS}
    comun = {
        "senal": sorteo.senal,
        "modo": sorteo.modo,
        "poblacion": sorteo.poblacion,
        "revisadas": len(sorteo.revisar),
        "conteos": conteos,
        "acuerdo": acuerdo,
        "pasa": None,
    }
    if sorteo.modo == CENSO:
        peso_revisado = suma_exacta(pesos[i] for i in sorteo.revisar)
        por_etiqueta = {
            e: suma_exacta(pesos[i] for i in sorteo.revisar if etiquetas[i] == e) for e in ETIQUETAS
        }
        participacion = {e: Fraction(por_etiqueta[e]) / Fraction(peso_revisado) for e in ETIQUETAS}
        return PuntajeSenal(
            **comun,
            peso_revisado=peso_revisado,
            peso_por_etiqueta=por_etiqueta,
            participacion=participacion,
            max_falsos_positivos=None,
            max_indeterminados=None,
            cota_falsos_positivos=None,
            cumple_falsos_positivos=participacion[FALSO_POSITIVO] <= MAX_FP_CENSO,
            cumple_indeterminados=participacion[INDETERMINADO] <= MAX_INDETERMINADOS_CENSO,
        )
    maximo_fp, maximo_ind = max_falsos_positivos(n), max_indeterminados(n)
    return PuntajeSenal(
        **comun,
        peso_revisado=None,
        peso_por_etiqueta=None,
        participacion=None,
        max_falsos_positivos=maximo_fp,
        max_indeterminados=maximo_ind,
        cota_falsos_positivos=cota_superior_cp(conteos[FALSO_POSITIVO], n),
        cumple_falsos_positivos=maximo_fp is not None and conteos[FALSO_POSITIVO] <= maximo_fp,
        cumple_indeterminados=conteos[INDETERMINADO] <= maximo_ind,
    )


def puntuar(
    muestra: Muestra,
    respuestas: Mapping[str, Respuesta],
    segundas: Mapping[str, Respuesta] | None = None,
    lista: ListaAlertas | None = None,
    resoluciones: Mapping[str, Resolucion] | None = None,
) -> Resultado:
    """Valida las entradas y calcula el resultado (ver las reglas en el docstring del módulo)."""
    _validar_lista(muestra, lista)
    _validar_respuestas(muestra, respuestas, segundas)
    hay_segundo = segundas is not None
    segundas = dict(segundas or {})
    resoluciones = dict(resoluciones or {})
    desacuerdos = _desacuerdos(muestra, respuestas, segundas, resoluciones)
    _validar_resoluciones(desacuerdos, resoluciones)
    pesos = lista.pesos() if lista is not None else {}
    revisar = muestra.ids_a_revisar()
    definitivas = {i: resoluciones[i].respuesta if i in resoluciones else respuestas[i] for i in revisar}

    acuerdo = _acuerdo(revisar, respuestas, segundas)
    todas_dobles = hay_segundo and set(segundas) == set(revisar)
    acuerdo_bajo = acuerdo.proporcion is not None and acuerdo.proporcion < UMBRAL_ACUERDO
    if (revisar and not hay_segundo) or (acuerdo_bajo and not todas_dobles):
        estado = FALTA_DOBLE_REVISION
    elif any(d.resuelta is None for d in desacuerdos):
        estado = FALTAN_RESOLUCIONES
    else:
        estado = COMPLETO

    senales = []
    for sorteo in muestra.senales:
        puntaje = _puntuar_senal(
            sorteo, muestra.n, definitivas, pesos, _acuerdo(sorteo.revisar, respuestas, segundas)
        )
        if estado == COMPLETO:
            puntaje = replace(puntaje, pasa=puntaje.cumple_falsos_positivos and puntaje.cumple_indeterminados)
        senales.append(puntaje)
    return Resultado(
        muestra=muestra,
        senales=tuple(senales),
        acuerdo=acuerdo,
        hay_segundo=hay_segundo,
        todas_dobles=todas_dobles,
        desacuerdos=desacuerdos,
        estado=estado,
        entregable_1=any(s.pasa for s in senales) if estado == COMPLETO else None,
        respuestas=dict(respuestas),
        segundas=segundas,
        resoluciones=resoluciones,
        definitivas=definitivas,
    )
