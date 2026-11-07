"""Pruebas sin red de la herramienta del protocolo de revisión (`src/lumen_lupa/revision/`, contrato T9).

Los valores esperados no salen del código bajo prueba:

- La huella del fixture se calculó con jq y sha256sum (los pesos del fixture ya vienen sin ceros sobrantes):
    jq -cS '.alertas |= sort_by(.id)' tests/fixtures/revision/alertas.json | tr -d '\\n' | sha256sum
- La tabla de verdad de la etiqueta (24 combinaciones) se escribió a mano a partir del §7.
- Las kappas (16/31 en el ejemplo; 23/36, 5/8 y "sin definir" en el fixture) se calcularon a mano.
- 0.902, 0.090 y 0.295 son los números que publica el §9 del protocolo.
- Con las resoluciones del fixture, S5-A-006 pasa de `indeterminado` a `consistente` (q2) y S5-B-003 sigue en
  `falso_positivo` (q4): los conteos de S5 pasan de 17/3/5 a 18/3/4 y el acuerdo no cambia.
"""

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import lumen_lupa.revision
from lumen_lupa.revision import (
    armar_muestra,
    huella,
    leer_alertas,
    leer_muestra,
    puntuar,
    validar_lista,
    validar_muestra,
)
from lumen_lupa.revision.__main__ import main
from lumen_lupa.revision.alertas import Alerta
from lumen_lupa.revision.comun import PROTOCOLO, texto_decimal
from lumen_lupa.revision.estadistica import (
    cdf_binomial,
    cota_superior_cp,
    kappa_cohen,
    max_falsos_positivos,
    max_indeterminados,
    proporcion_de_acuerdo,
)
from lumen_lupa.revision.muestreo import llave, orden_del_sorteo, segundo_revisor, sortear
from lumen_lupa.revision.puntaje import COMPLETO, FALTA_DOBLE_REVISION, FALTAN_RESOLUCIONES
from lumen_lupa.revision.resultado import (
    ACUERDO_SECCION_8,
    DESACUERDOS_SECCION_8,
    DOBLE_REVISION_SECCION_8,
    ENTREGABLE_SECCION_9,
    NOTA_SECCION_9,
    como_json,
    como_markdown,
)
from lumen_lupa.revision.rubrica import (
    CONSISTENTE,
    ETIQUETAS,
    FALSO_POSITIVO,
    INDETERMINADO,
    Resolucion,
    Respuesta,
    etiqueta,
    leer_resoluciones,
    leer_respuestas,
    normalizar_respuesta,
)

FIXTURE = Path(__file__).parent / "fixtures" / "revision"
SRC = Path(lumen_lupa.revision.__file__).resolve().parents[2]
SEMILLA = "0123456789abcdef" * 4
HUELLA_FIXTURE = "b63f43c338c985487fc4d5ea38a11765f61f3f66abbc68100094a1a99bfc1bc2"
CON, FPO, IND = CONSISTENTE, FALSO_POSITIVO, INDETERMINADO
PATRON = {CON: ("yes", "yes", "no", "no"), FPO: ("no", "no", "no", "no"), IND: ("unclear", "yes", "no", "no")}


def alerta(id_alerta: str, peso: str = "0.01", mercado: str = "A") -> dict:
    return {"id": id_alerta, "senal": id_alerta[:2], "mercado": mercado, "peso": peso}


def lista_json(alertas: list[dict]) -> dict:
    return {"version": 1, "semana": {"desde": "2026-09-28", "hasta": "2026-10-04"}, "alertas": alertas}


def respuestas(etiquetas: dict[str, str]) -> dict[str, Respuesta]:
    """Respuestas que dan cada etiqueta pedida (una combinación fija por etiqueta)."""
    return {i: Respuesta(i, *PATRON[e], linea=k) for k, (i, e) in enumerate(etiquetas.items(), start=2)}


def semillas(cuantas: int) -> list[str]:
    return [hashlib.sha256(f"semilla de prueba {k}".encode()).hexdigest() for k in range(cuantas)]


def protocolo() -> str:
    """REVIEW_PROTOCOL.md: en la raíz del kit exportado o en scripts/kit/ del repo."""
    raiz = Path(__file__).resolve().parent.parent
    for ruta in (raiz / "REVIEW_PROTOCOL.md", raiz / "scripts" / "kit" / "REVIEW_PROTOCOL.md"):
        if ruta.is_file():
            return ruta.read_text(encoding="utf-8")
    pytest.fail("REVIEW_PROTOCOL.md no está en la raíz (kit) ni en scripts/kit/ (repo)")


# --- El protocolo que se ejecuta ------------------------------------------------------------------------


def test_los_textos_y_la_formula_vienen_del_protocolo():
    texto = " ".join(protocolo().split())
    assert f"*Version {PROTOCOLO}." in texto
    for cita in (
        ACUERDO_SECCION_8,
        DOBLE_REVISION_SECCION_8,
        DESACUERDOS_SECCION_8,
        NOTA_SECCION_9,
        ENTREGABLE_SECCION_9,
    ):
        assert cita in texto, cita
    assert NOTA_SECCION_9.endswith("which is why its limit is 4 of 25.")  # el párrafo completo del §9
    for fragmento in (
        "**25 alerts or fewer:** every alert is reviewed (census)",
        'u = (int(sha256(seed + ":" + alert_id)) + 0.5) / 2**256',
        "key = ln(u) / weight",
        "computed with 50-digit decimal arithmetic",
        "ties are broken by alert ID",
        "At least a third of the reviewed alerts (rounded up, so at least 9 of 25)",
        "1. `falso_positivo` (false positive) if Q1 is `no`, Q3 is `yes` or Q4 is `yes`.",
        "2. `consistente` (consistent with the pattern) if Q1 and Q2 are `yes`, and Q3 and Q4 are `no`.",
        "If agreement is below 70%, every reviewed alert gets a second set of answers before scoring.",
        "no more than 4 of the 25 are false positives, and no more than 8 are indeterminate",
    ):
        assert fragmento in texto, fragmento


# --- Huella (§4) ----------------------------------------------------------------------------------------

_ID = st.from_regex(r"S[15]-[A-C]-[0-9]{3}", fullmatch=True)
_PESO = st.integers(1, 10_000).map(lambda k: format(Decimal(k).scaleb(-4), "f"))


def test_huella_del_fixture_es_la_de_jq_y_sha256sum():
    assert huella(validar_lista(json.loads((FIXTURE / "alertas.json").read_text(encoding="utf-8")))) == (
        HUELLA_FIXTURE
    )


@given(st.lists(st.tuples(_ID, _PESO), min_size=1, max_size=30, unique_by=lambda t: t[0]), st.data())
@settings(max_examples=60, deadline=None)
def test_huella_no_cambia_al_reordenar_y_cambia_al_modificar_un_peso(pares, data):
    filas = [alerta(i, peso=p, mercado=i[3]) for i, p in pares]
    base = huella(validar_lista(lista_json(filas)))
    revueltas = data.draw(st.permutations(filas))
    assert huella(validar_lista(lista_json(list(revueltas)))) == base
    k = data.draw(st.integers(0, len(filas) - 1))
    otra_forma = [dict(f) for f in filas]
    otra_forma[k]["peso"] = filas[k]["peso"] + "000"  # mismo valor, otra escritura
    assert huella(validar_lista(lista_json(otra_forma))) == base
    otro_valor = [dict(f) for f in filas]
    otro_valor[k]["peso"] = format(Decimal(filas[k]["peso"]) / 2, "f")
    assert huella(validar_lista(lista_json(otro_valor))) != base


def test_huella_normaliza_el_peso_y_cubre_cada_campo():
    base = huella(validar_lista(lista_json([alerta("S1-A-001", "0.5"), alerta("S5-B-001", "1")])))
    for peso in ("0.50", "00.5", "0.500000"):
        assert (
            huella(validar_lista(lista_json([alerta("S1-A-001", peso), alerta("S5-B-001", "1.0")]))) == base
        )
    assert texto_decimal(Decimal("1E-7")) == "0.0000001" and texto_decimal(Decimal("1.000")) == "1"
    cambios = (
        lista_json([alerta("S1-A-001", "0.5", mercado="B"), alerta("S5-B-001", "1")]),
        lista_json([alerta("S1-A-001", "0.5"), alerta("S5-B-002", "1")]),
        {
            **lista_json([alerta("S1-A-001", "0.5"), alerta("S5-B-001", "1")]),
            "semana": {"desde": "2026-09-21", "hasta": "2026-09-27"},
        },
    )
    for datos in cambios:
        assert huella(validar_lista(datos)) != base


# --- Sorteo (§5, §6 y §8) -------------------------------------------------------------------------------


def test_censo_si_la_poblacion_es_menor_o_igual_que_n_y_muestra_de_n_si_es_mayor():
    filas = [alerta(f"S1-A-{k:03d}") for k in range(25)] + [alerta(f"S5-A-{k:03d}") for k in range(26)]
    lista = validar_lista(lista_json(filas))
    s1, s5 = armar_muestra(lista, SEMILLA).senales
    assert (s1.modo, s1.poblacion, len(s1.revisar)) == ("censo", 25, 25)
    assert set(s1.revisar) == {a.id for a in lista.de_senal("S1")}
    assert (s5.modo, s5.poblacion, len(s5.revisar)) == ("muestra", 26, 25)
    assert set(s5.revisar) < {a.id for a in lista.de_senal("S5")}
    assert armar_muestra(lista, SEMILLA, n=26).senales[1].modo == "censo"
    assert [s.senal for s in armar_muestra(validar_lista(lista_json(filas[:3])), SEMILLA).senales] == ["S1"]


def test_misma_semilla_mismo_sorteo_y_otra_semilla_otro():
    pesos = [format(Decimal(k + 1).scaleb(-3), "f") for k in range(60)]
    lista = validar_lista(lista_json([alerta(f"S5-A-{k:03d}", pesos[k]) for k in range(60)]))
    una = armar_muestra(lista, SEMILLA)
    assert armar_muestra(lista, SEMILLA) == una
    assert armar_muestra(lista, SEMILLA.upper()) == una  # la semilla se usa en minúsculas
    assert una.semilla == SEMILLA
    otra = armar_muestra(lista, "f" * 64)
    assert set(otra.senales[0].revisar) != set(una.senales[0].revisar)
    assert validar_muestra(una.como_dict()) == una


def test_el_sorteo_va_por_peso():
    """Con 200 semillas: la alerta pesada sale casi siempre y una ligera rara vez (umbrales holgados)."""
    pesada = [Alerta("S5-A-000", "S5", "A", Decimal("0.5"))]
    pesada += [Alerta(f"S5-A-{k:03d}", "S5", "A", Decimal("0.001")) for k in range(1, 41)]
    ligera = [Alerta("S5-B-000", "S5", "B", Decimal("0.001"))]
    ligera += [Alerta(f"S5-B-{k:03d}", "S5", "B", Decimal("0.5")) for k in range(1, 41)]
    sale = {"pesada n=25": 0, "ligera entre pesadas n=25": 0, "pesada n=2": 0, "una ligera n=2": 0}
    for semilla in semillas(200):
        sale["pesada n=25"] += "S5-A-000" in sortear(semilla, pesada, 25)[1]
        sale["ligera entre pesadas n=25"] += "S5-B-000" in sortear(semilla, ligera, 25)[1]
        dos = sortear(semilla, pesada, 2)[1]
        sale["pesada n=2"] += "S5-A-000" in dos
        sale["una ligera n=2"] += "S5-A-001" in dos
    # Esperado: ~200, ~0.4, ~199 y ~5 de 200.
    assert sale["pesada n=25"] >= 195 and sale["pesada n=2"] >= 180, sale
    assert sale["ligera entre pesadas n=25"] <= 20 and sale["una ligera n=2"] <= 40, sale


def test_llave_sigue_la_formula_del_protocolo():
    id_alerta, peso = "S1-A-001", Decimal("0.0123")
    entero = int(hashlib.sha256(f"{SEMILLA}:{id_alerta}".encode()).hexdigest(), 16)
    esperada = math.log((entero + 0.5) / 2**256) / 0.0123
    calculada = llave(SEMILLA, id_alerta, peso)
    assert math.isclose(float(calculada), esperada, rel_tol=1e-12)
    assert len(calculada.as_tuple().digits) == 50  # 50 dígitos


def test_orden_del_sorteo_empates_por_id_y_sin_redondear():
    assert orden_del_sorteo({"b": Decimal(-1), "a": Decimal(-1), "c": Decimal("-0.5")}) == ["c", "a", "b"]
    # Difieren en la cifra 34: el signo menos con el contexto por omisión (28 cifras) las empataría.
    mayor, menor = (
        Decimal("-0.1000000000000000000000000000000001"),
        Decimal("-0.1000000000000000000000000000000002"),
    )
    assert orden_del_sorteo({"a": menor, "b": mayor}) == ["b", "a"]


def test_segundo_revisor_es_el_techo_de_un_tercio_subconjunto_y_determinista():
    for k in range(1, 61):
        ids = [f"S5-A-{j:03d}" for j in range(k)]
        segundo = segundo_revisor(SEMILLA, ids)
        resumen = {i: hashlib.sha256(f"{SEMILLA}:segundo:{i}".encode()).hexdigest() for i in ids}
        assert segundo == sorted(ids, key=resumen.get)[: math.ceil(k / 3)]
        assert len(segundo) == math.ceil(k / 3) and set(segundo) <= set(ids)
        assert segundo_revisor(SEMILLA, list(reversed(ids))) == segundo
    assert len(segundo_revisor(SEMILLA, [f"S5-A-{j:03d}" for j in range(25)])) == 9


_PESO_ALERTA = st.integers(1, 1000).map(lambda k: Decimal(k).scaleb(-3))


@given(st.lists(_PESO_ALERTA, min_size=1, max_size=60), st.integers(1, 30), st.sampled_from(semillas(5)))
@settings(max_examples=40, deadline=None)
def test_propiedades_del_sorteo(pesos, n, semilla):
    alertas = [Alerta(f"S5-A-{k:03d}", "S5", "A", p) for k, p in enumerate(pesos)]
    modo, revisar = sortear(semilla, alertas, n)
    assert modo == ("censo" if len(alertas) <= n else "muestra")
    assert len(revisar) == min(n, len(alertas)) == len(set(revisar))
    assert set(revisar) <= {a.id for a in alertas}
    assert sortear(semilla, list(reversed(alertas)), n) == (modo, revisar)  # no depende del orden de entrada
    dentro = [llave(semilla, a.id, a.peso) for a in alertas if a.id in revisar]
    fuera = [llave(semilla, a.id, a.peso) for a in alertas if a.id not in revisar]
    assert not fuera or min(dentro) >= max(fuera)  # las n llaves más grandes


# --- Etiqueta (§7) --------------------------------------------------------------------------------------

TABLA_DE_VERDAD = [  # q1, q2, q3, q4 -> etiqueta, escrita a mano a partir del §7
    ("yes", "yes", "no", "no", CON),
    ("yes", "no", "no", "no", IND),
    ("yes", "yes", "yes", "no", FPO),
    ("yes", "no", "yes", "no", FPO),
    ("yes", "yes", "no", "yes", FPO),
    ("yes", "no", "no", "yes", FPO),
    ("yes", "yes", "yes", "yes", FPO),
    ("yes", "no", "yes", "yes", FPO),
    ("no", "yes", "no", "no", FPO),
    ("no", "no", "no", "no", FPO),
    ("no", "yes", "yes", "no", FPO),
    ("no", "no", "yes", "no", FPO),
    ("no", "yes", "no", "yes", FPO),
    ("no", "no", "no", "yes", FPO),
    ("no", "yes", "yes", "yes", FPO),
    ("no", "no", "yes", "yes", FPO),
    ("unclear", "yes", "no", "no", IND),
    ("unclear", "no", "no", "no", IND),
    ("unclear", "yes", "yes", "no", FPO),
    ("unclear", "no", "yes", "no", FPO),
    ("unclear", "yes", "no", "yes", FPO),
    ("unclear", "no", "no", "yes", FPO),
    ("unclear", "yes", "yes", "yes", FPO),
    ("unclear", "no", "yes", "yes", FPO),
]


def test_tabla_de_verdad_de_la_etiqueta():
    assert len({fila[:4] for fila in TABLA_DE_VERDAD}) == 24  # las 24 combinaciones, sin repetir
    for q1, q2, q3, q4, esperada in TABLA_DE_VERDAD:
        assert etiqueta(q1, q2, q3, q4) == esperada, (q1, q2, q3, q4)


@pytest.mark.parametrize(
    "texto, pregunta, esperada",
    [
        ("yes", "q1", "yes"),
        (" YES ", "q2", "yes"),
        ("Sí", "q3", "yes"),
        ("SI", "q4", "yes"),
        ("sí", "q1", "yes"),  # "sí" con el acento como carácter aparte (NFD)
        ("No", "q2", "no"),
        ("unclear", "q1", "unclear"),
        ("Incierto", "q1", "unclear"),
    ],
)
def test_respuestas_aceptadas(texto, pregunta, esperada):
    assert normalizar_respuesta(texto, pregunta) == esperada


@pytest.mark.parametrize(
    "texto, pregunta", [("unclear", "q2"), ("incierto", "q4"), ("maybe", "q1"), ("", "q3"), ("y", "q1")]
)
def test_respuestas_rechazadas(texto, pregunta):
    with pytest.raises(lumen_lupa.revision.ErrorRevision, match="no es una respuesta válida"):
        normalizar_respuesta(texto, pregunta)


def test_csv_con_bom_y_renglones_vacios(tmp_path):
    ruta = tmp_path / "r.csv"
    contenido = 'alerta_id,q1,q2,q3,q4,evidencia,notas\nS1-A-001,sí,si,no,no,"a, b",\n\n'
    ruta.write_bytes(b"\xef\xbb\xbf" + contenido.encode("utf-8"))
    (respuesta,) = leer_respuestas(ruta, "el CSV").values()
    assert (respuesta.alerta_id, respuesta.etiqueta, respuesta.linea) == ("S1-A-001", CON, 2)


# --- Estadística (§8 y §9) ------------------------------------------------------------------------------


def test_numeros_del_protocolo():
    assert round(float(cdf_binomial(4, 25, Fraction(1, 10))), 3) == 0.902
    assert round(float(cdf_binomial(4, 25, Fraction(3, 10))), 3) == 0.090
    assert round(float(cota_superior_cp(4, 25)), 3) == 0.295
    assert max_falsos_positivos(25) == 4
    assert max_indeterminados(25) == 8


def test_max_falsos_positivos_coincide_con_la_definicion_literal():
    """El mayor c cuya cota de Clopper–Pearson (bisección) es menor que 0.30; None si ninguno."""
    for n in [*range(1, 31), 40, 50]:
        literal = None
        for c in range(n + 1):
            if cota_superior_cp(c, n) >= Fraction(3, 10):
                break
            literal = c
        assert max_falsos_positivos(n) == literal, n
    assert max_falsos_positivos(6) is None and max_falsos_positivos(7) == 0
    assert max_indeterminados(7) == 2 and max_indeterminados(30) == 10


def test_cota_de_clopper_pearson_en_los_bordes():
    assert cota_superior_cp(25, 25) == 1
    for n in (1, 5, 25):  # x = 0: (1 − p)^n = 0.10
        assert math.isclose(float(cota_superior_cp(0, n)), 1 - 0.1 ** (1 / n), rel_tol=1e-12)
    assert math.isclose(float(cdf_binomial(4, 25, cota_superior_cp(4, 25))), 0.1, rel_tol=1e-9)
    assert cdf_binomial(25, 25, Fraction(1, 2)) == 1 and cdf_binomial(3, 25, 0) == 1


def test_kappa_de_un_ejemplo_calculado_a_mano():
    # 10 alertas. Primer revisor: 5 CON, 3 FPO, 2 IND; segundo: 5 CON, 3 FPO, 2 IND; coinciden 7.
    # p_o = 7/10; p_e = (5·5 + 3·3 + 2·2)/100 = 38/100; kappa = (0.70 − 0.38)/(1 − 0.38) = 32/62 = 16/31.
    pares = [(CON, CON)] * 4 + [(CON, FPO), (FPO, FPO), (FPO, FPO), (FPO, IND), (IND, IND), (IND, CON)]
    assert proporcion_de_acuerdo(pares) == Fraction(7, 10)
    assert kappa_cohen(pares, ETIQUETAS) == Fraction(16, 31)
    assert kappa_cohen([(b, a) for a, b in pares], ETIQUETAS) == Fraction(16, 31)
    assert kappa_cohen([(CON, CON)] * 5, ETIQUETAS) is None  # p_e = 1
    assert kappa_cohen([], ETIQUETAS) is None and proporcion_de_acuerdo([]) is None


# --- Regla de aprobación (§9) ---------------------------------------------------------------------------


def _muestra(filas: list[dict], n: int = 25):
    lista = validar_lista(lista_json(filas))
    return lista, armar_muestra(lista, SEMILLA, n)


def _puntuar_muestra(fp: int, ind: int):
    lista, muestra = _muestra([alerta(f"S5-A-{k:03d}") for k in range(30)])
    revisar = muestra.senales[0].revisar
    r = respuestas({i: FPO if k < fp else IND if k < fp + ind else CON for k, i in enumerate(revisar)})
    return puntuar(muestra, r, {i: r[i] for i in muestra.ids_segundo_revisor()}, lista).senales[0]


@pytest.mark.parametrize(
    "fp, ind, pasa",
    [(4, 0, True), (5, 0, False), (0, 8, True), (0, 9, False), (4, 8, True), (5, 8, False), (4, 9, False)],
)
def test_bordes_de_la_regla_en_muestra(fp, ind, pasa):
    s = _puntuar_muestra(fp, ind)
    assert (s.modo, s.revisadas, s.conteos[FPO], s.conteos[IND]) == ("muestra", 25, fp, ind)
    assert (s.max_falsos_positivos, s.max_indeterminados) == (4, 8)
    assert s.pasa is pasa
    assert s.cota_falsos_positivos == cota_superior_cp(fp, 25)


@pytest.mark.parametrize(
    "casos, participacion, pasa",
    [
        ([("0.2", FPO), ("0.2", CON), ("0.2", CON), ("0.2", CON), ("0.2", CON)], (FPO, Fraction(1, 5)), True),
        (
            [("0.2001", FPO), ("0.1999", CON), ("0.2", CON), ("0.2", CON), ("0.2", CON)],
            (FPO, Fraction(2001, 10000)),
            False,
        ),
        ([("0.1", IND), ("0.1", CON), ("0.1", CON)], (IND, Fraction(1, 3)), True),
        ([("0.1001", IND), ("0.1", CON), ("0.0999", CON)], (IND, Fraction(1001, 3000)), False),
    ],
    ids=["FP 20% exacto", "FP 20.01%", "indeterminados un tercio exacto", "indeterminados más de un tercio"],
)
def test_bordes_de_la_regla_en_censo(casos, participacion, pasa):
    lista, muestra = _muestra([alerta(f"S1-A-{k:03d}", peso=p) for k, (p, _) in enumerate(casos)])
    r = respuestas({f"S1-A-{k:03d}": e for k, (_, e) in enumerate(casos)})
    (s,) = puntuar(muestra, r, {i: r[i] for i in muestra.ids_segundo_revisor()}, lista).senales
    etiqueta_, proporcion = participacion
    assert s.modo == "censo" and s.participacion[etiqueta_] == proporcion
    assert s.pasa is pasa


def test_entregable_1_se_cumple_si_al_menos_una_senal_pasa():
    filas = [alerta(f"S1-A-{k:03d}") for k in range(5)] + [alerta(f"S5-A-{k:03d}") for k in range(5)]
    lista, muestra = _muestra(filas)
    for etiquetas_s1, etiquetas_s5, cumplido in ((CON, CON, True), (FPO, CON, True), (FPO, FPO, False)):
        r = respuestas(
            {
                **{i: etiquetas_s1 for i in muestra.senales[0].revisar},
                **{i: etiquetas_s5 for i in muestra.senales[1].revisar},
            }
        )
        resultado = puntuar(muestra, r, {i: r[i] for i in muestra.ids_segundo_revisor()}, lista)
        assert resultado.estado == COMPLETO and resultado.entregable_1 is cumplido


# --- Acuerdo entre revisores (§8) -----------------------------------------------------------------------


def resueltas(etiquetas: dict[str, str], texto: str = "Settled against the rubric.") -> dict[str, Resolucion]:
    """Resoluciones que dan cada etiqueta pedida."""
    return {i: Resolucion(r, texto) for i, r in respuestas(etiquetas).items()}


def _con_segundo(desacuerdos: int, extra: int = 0, todas: bool = False, resolver: bool = False):
    """25 de 30 alertas, todas consistentes para el primer revisor. El segundo contesta las 9 que le tocan
    (más `extra`, o todas) y difiere en las primeras `desacuerdos` que contesta; con `resolver`, cada
    desacuerdo se resuelve como consistente."""
    lista, muestra = _muestra([alerta(f"S5-A-{k:03d}") for k in range(30)])
    revisar = muestra.senales[0].revisar
    designadas = muestra.ids_segundo_revisor()
    otras = [i for i in revisar if i not in designadas]
    contestadas = designadas + (otras if todas else otras[:extra])
    segundas = respuestas({i: IND if k < desacuerdos else CON for k, i in enumerate(contestadas)})
    resoluciones = resueltas(dict.fromkeys(contestadas[:desacuerdos], CON)) if resolver else None
    return puntuar(muestra, respuestas(dict.fromkeys(revisar, CON)), segundas, lista, resoluciones)


def test_acuerdo_menor_que_70_sin_doble_revision_completa_queda_incompleto():
    resultado = _con_segundo(desacuerdos=3)  # 6 de 9; también faltan resoluciones, pero esto va antes
    assert resultado.acuerdo.proporcion == Fraction(6, 9) and not resultado.todas_dobles
    assert resultado.estado == FALTA_DOBLE_REVISION == "incompleto: falta doble revisión"
    assert resultado.entregable_1 is None and all(s.pasa is None for s in resultado.senales)
    assert como_json(resultado)["deliverable_1_met"] is None
    texto = como_markdown(resultado)
    assert "`incompleto: falta doble revisión`" in texto and "**No verdict.**" in texto


def test_acuerdo_menor_que_70_con_doble_revision_completa_da_veredicto():
    resultado = _con_segundo(desacuerdos=8, todas=True, resolver=True)  # 17 de 25
    assert resultado.acuerdo.proporcion == Fraction(17, 25) and resultado.todas_dobles
    assert resultado.estado == COMPLETO and resultado.entregable_1 is True


def test_acuerdo_de_70_exacto_no_pide_doble_revision_completa():
    resultado = _con_segundo(desacuerdos=3, extra=1, resolver=True)  # 7 de 10
    assert resultado.acuerdo.proporcion == Fraction(7, 10) and not resultado.todas_dobles
    assert resultado.estado == COMPLETO


def test_sin_segundo_revisor_queda_incompleto():
    lista, muestra = _muestra([alerta(f"S5-A-{k:03d}") for k in range(30)])
    resultado = puntuar(muestra, respuestas(dict.fromkeys(muestra.ids_a_revisar(), CON)), None, lista)
    assert resultado.estado == FALTA_DOBLE_REVISION and resultado.entregable_1 is None
    assert "There are no second-reviewer answers" in como_markdown(resultado)


# --- Resoluciones de los desacuerdos (§8) ---------------------------------------------------------------


def _un_desacuerdo_en_cinco_falsos_positivos():
    """25 de 30. El primer revisor marca 5 falsos positivos (4 es el máximo: no pasa). El segundo contesta
    las 9 que le tocan igual que el primero, salvo una de las falsas positivas, que para él es consistente."""
    lista, muestra = _muestra([alerta(f"S5-A-{k:03d}") for k in range(30)])
    revisar, designadas = muestra.senales[0].revisar, muestra.ids_segundo_revisor()
    falsas = [designadas[0], *[i for i in revisar if i not in designadas][:4]]
    primeras = respuestas({i: FPO if i in falsas else CON for i in revisar})
    segundas = respuestas(dict.fromkeys(designadas, CON))
    return lista, muestra, primeras, segundas, designadas[0]


def test_sin_resoluciones_queda_incompleto():
    lista, muestra, primeras, segundas, en_desacuerdo = _un_desacuerdo_en_cinco_falsos_positivos()
    resultado = puntuar(muestra, primeras, segundas, lista)
    assert [d.alerta_id for d in resultado.desacuerdos] == [en_desacuerdo]
    assert resultado.estado == FALTAN_RESOLUCIONES == "incompleto: faltan resoluciones"
    assert resultado.entregable_1 is None and all(s.pasa is None for s in resultado.senales)
    assert resultado.senales[0].conteos[FPO] == 5  # los conteos salen, con las respuestas del primer revisor
    assert como_json(resultado)["deliverable_1_met"] is None
    texto = como_markdown(resultado)
    assert "`incompleto: faltan resoluciones`" in texto and "pending: resolutions missing" in texto
    assert "**No verdict.**" in texto


def test_una_resolucion_que_cambia_la_etiqueta_cambia_el_puntaje():
    lista, muestra, primeras, segundas, en_desacuerdo = _un_desacuerdo_en_cinco_falsos_positivos()
    a_favor = puntuar(muestra, primeras, segundas, lista, resueltas({en_desacuerdo: CON}, "Q1 yes | Q3 no"))
    en_contra = puntuar(muestra, primeras, segundas, lista, resueltas({en_desacuerdo: FPO}))
    assert a_favor.estado == en_contra.estado == COMPLETO
    assert (a_favor.senales[0].conteos[FPO], a_favor.senales[0].pasa, a_favor.entregable_1) == (4, True, True)
    assert (en_contra.senales[0].conteos[FPO], en_contra.senales[0].pasa, en_contra.entregable_1) == (
        5,
        False,
        False,
    )
    assert (
        a_favor.definitivas[en_desacuerdo].etiqueta == CON
        and a_favor.respuestas[en_desacuerdo].etiqueta == FPO
    )
    assert a_favor.desacuerdos[0].resuelta == CON and en_contra.desacuerdos[0].resuelta == FPO
    fila = f"| {en_desacuerdo} | S5 | q1, q2 | `{FPO}` | `{CON}` | `{CON}` | Q1 yes \\| Q3 no |"
    assert fila in como_markdown(a_favor)  # la barra del texto no corta la tabla


def test_el_acuerdo_no_cambia_con_las_resoluciones():
    lista, muestra, primeras, segundas, en_desacuerdo = _un_desacuerdo_en_cinco_falsos_positivos()
    sin = puntuar(muestra, primeras, segundas, lista)
    assert (sin.acuerdo.coincidencias, sin.acuerdo.dobles) == (8, 9)
    for etiqueta_resuelta in ETIQUETAS:
        con = puntuar(muestra, primeras, segundas, lista, resueltas({en_desacuerdo: etiqueta_resuelta}))
        assert con.acuerdo == sin.acuerdo and con.senales[0].acuerdo == sin.senales[0].acuerdo
        assert como_json(con)["agreement"] == como_json(sin)["agreement"]


def test_solo_se_resuelven_desacuerdos():
    lista, muestra, primeras, segundas, en_desacuerdo = _un_desacuerdo_en_cinco_falsos_positivos()
    sin_segunda = next(i for i in muestra.ids_a_revisar() if i not in segundas)
    con_las_mismas = next(i for i in segundas if i != en_desacuerdo)
    for ajena in (sin_segunda, con_las_mismas, "S5-B-999"):
        with pytest.raises(lumen_lupa.revision.ErrorRevision, match="no son desacuerdos"):
            puntuar(muestra, primeras, segundas, lista, resueltas({en_desacuerdo: CON, ajena: CON}))
    with pytest.raises(lumen_lupa.revision.ErrorRevision, match="no son desacuerdos"):
        puntuar(muestra, primeras, None, lista, resueltas({en_desacuerdo: CON}))  # sin segundo, no hay


def test_el_texto_de_la_resolucion_va_al_resultado_en_una_linea(tmp_path):
    ruta = tmp_path / "r.csv"
    ruta.write_text(
        'alerta_id,q1,q2,q3,q4,resolucion\nS5-A-001,sí,yes,no,no,"Q1 yes:\n  shared | fee source"\n',
        encoding="utf-8",
    )
    (resolucion,) = leer_resoluciones(ruta, "el CSV de resoluciones").values()
    assert (resolucion.alerta_id, resolucion.etiqueta, resolucion.texto) == (
        "S5-A-001",
        CON,
        "Q1 yes: shared | fee source",
    )


# --- Validación: error claro y código de salida distinto de 0 -------------------------------------------


def _alertas_con(tmp_path: Path, cambiar) -> Path:
    datos = json.loads((FIXTURE / "alertas.json").read_text(encoding="utf-8"))
    cambiar(datos)
    ruta = tmp_path / "alertas_mal.json"
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    return ruta


CASOS_ALERTAS = [
    ("peso 0", lambda d: d["alertas"][0].update(peso="0"), "0 < peso ≤ 1"),
    ("peso mayor que 1", lambda d: d["alertas"][0].update(peso="1.0001"), "0 < peso ≤ 1"),
    ("peso como número", lambda d: d["alertas"][0].update(peso=0.5), "decimal en texto"),
    ("peso con exponente", lambda d: d["alertas"][0].update(peso="1E-3"), "notación fija"),
    ("señal desconocida", lambda d: d["alertas"][0].update(senal="S2"), "señal desconocida 'S2'"),
    ("id duplicado", lambda d: d["alertas"][1].update(id=d["alertas"][0]["id"]), "id repetido"),
    ("id con espacio", lambda d: d["alertas"][0].update(id="S1 A 001"), "no puede tener espacios"),
    ("mercado de dos letras", lambda d: d["alertas"][0].update(mercado="AB"), "una letra mayúscula"),
    ("mercado en minúscula", lambda d: d["alertas"][0].update(mercado="a"), "una letra mayúscula"),
    ("clave de más", lambda d: d["alertas"][0].update(volumen="12"), "sobran las claves volumen"),
    ("falta una clave", lambda d: d["alertas"][0].pop("mercado"), "faltan las claves mercado"),
    ("versión", lambda d: d.update(version=2), "version debe ser 1"),
    ("fecha inválida", lambda d: d["semana"].update(hasta="2026-02-30"), "AAAA-MM-DD"),
    ("semana al revés", lambda d: d["semana"].update(desde="2026-10-05"), "es posterior a hasta"),
]


@pytest.mark.parametrize(
    "cambiar, mensaje", [c[1:] for c in CASOS_ALERTAS], ids=[c[0] for c in CASOS_ALERTAS]
)
def test_lista_de_alertas_invalida(tmp_path, capsys, cambiar, mensaje):
    ruta = _alertas_con(tmp_path, cambiar)
    salida = tmp_path / "muestra.json"
    for args in (
        ["huella", "--alertas", str(ruta)],
        ["muestra", "--alertas", str(ruta), "--semilla", SEMILLA, "--salida", str(salida)],
    ):
        assert main(args) == 2
        error = capsys.readouterr().err
        assert error.startswith("error: ") and mensaje in error, error
    assert not salida.exists()


@pytest.mark.parametrize("semilla", ["zz" * 32, "0" * 63, "0" * 65, "0x" + "0" * 62, ""])
def test_semilla_invalida(tmp_path, capsys, semilla):
    args = ["muestra", "--alertas", str(FIXTURE / "alertas.json"), "--semilla", semilla]
    assert main([*args, "--salida", str(tmp_path / "m.json")]) == 2
    assert "64 caracteres hexadecimales" in capsys.readouterr().err


def test_n_invalido(tmp_path, capsys):
    args = ["muestra", "--alertas", str(FIXTURE / "alertas.json"), "--semilla", SEMILLA, "--n", "0"]
    assert main([*args, "--salida", str(tmp_path / "m.json")]) == 2
    assert "n (tamaño de la muestra) debe ser un entero ≥ 1" in capsys.readouterr().err


def _copia_del_fixture(tmp_path: Path) -> dict[str, Path]:
    rutas = {}
    for nombre in (
        "alertas.json",
        "respuestas_revisor_1.csv",
        "respuestas_revisor_2.csv",
        "resoluciones.csv",
        "esperado_muestra.json",
    ):
        rutas[nombre] = tmp_path / nombre
        shutil.copy(FIXTURE / nombre, rutas[nombre])
    return rutas


def _args_puntaje(
    rutas: dict[str, Path], segundo: bool = True, alertas: bool = True, resoluciones: bool = True
) -> list[str]:
    args = ["puntaje", "--muestra", str(rutas["esperado_muestra.json"])]
    args += ["--respuestas", str(rutas["respuestas_revisor_1.csv"])]
    args += ["--salida", str(rutas["alertas.json"].parent / "resultado")]
    if segundo:
        args += ["--segundo", str(rutas["respuestas_revisor_2.csv"])]
    if resoluciones:
        args += ["--resoluciones", str(rutas["resoluciones.csv"])]
    if alertas:
        args += ["--alertas", str(rutas["alertas.json"])]
    return args


_ULTIMA_1 = "S5-C-009,yes,yes,no,no,dossiers/S5-C-009.md,\n"
_ULTIMA_2 = "S5-C-005,yes,yes,no,no,dossiers/S5-C-005.md,\n"
_AJENA = "S5-A-002,yes,yes,no,no,dossiers/S5-A-002.md,\n"  # no salió en el sorteo
_TEXTO_2 = '"Q4 no: the payments to third parties have no public label or document, and Q4 needs one."'
_RESOLUCION_2 = f"S5-B-003,yes,no,yes,no,{_TEXTO_2}\n"
CASOS_PUNTAJE = [
    (
        "respuesta inválida",
        "respuestas_revisor_1.csv",
        "S5-C-008,yes,yes",
        "S5-C-008,yes,unclear",
        "q2 = 'unclear'",
    ),
    (
        "respuestas faltantes",
        "respuestas_revisor_1.csv",
        _ULTIMA_1,
        "",
        "faltan 1 de las alertas a revisar: S5-C-009",
    ),
    (
        "respuestas sobrantes",
        "respuestas_revisor_1.csv",
        _ULTIMA_1,
        _ULTIMA_1 + _AJENA,
        "sobran 1 que no están",
    ),
    (
        "respuesta duplicada",
        "respuestas_revisor_1.csv",
        _ULTIMA_1,
        _ULTIMA_1 * 2,
        "ya tiene respuesta en la línea 36",
    ),
    (
        "encabezado distinto",
        "respuestas_revisor_1.csv",
        "alerta_id,q1",
        "alerta,q1",
        "el encabezado debe tener",
    ),
    (
        "segundo con sobrantes",
        "respuestas_revisor_2.csv",
        _ULTIMA_2,
        _ULTIMA_2 + _AJENA,
        "segundo revisor (--segundo): sobran 1",
    ),
    (
        "segundo con faltantes",
        "respuestas_revisor_2.csv",
        _ULTIMA_2,
        "",
        "le tocan según la muestra (§8): S5-C-005",
    ),
    ("otra lista de alertas", "alertas.json", '"peso": "0.0412"', '"peso": "0.0413"', "no es la misma lista"),
    (
        "muestra reordenada",
        "esperado_muestra.json",
        '"S5-C-008",\n        "S5-B-001",',
        '"S5-B-001",\n        "S5-C-008",',
        "no es la que sale de la lista",
    ),
    (
        "segundo revisor alterado",
        "esperado_muestra.json",
        '"S1-A-003",\n        "S1-C-001",',
        '"S1-C-001",\n        "S1-A-003",',
        "los ids del segundo revisor no son",
    ),
    (
        "otro protocolo",
        "esperado_muestra.json",
        '"protocolo": "1.0"',
        '"protocolo": "1.1"',
        "es del protocolo '1.1'",
    ),
    # Resoluciones (§8): solo desacuerdos, una por alerta, con respuestas válidas y un texto publicable.
    ("resolución sin desacuerdo", "resoluciones.csv", "S5-B-003,", "S1-A-002,", "1 no son desacuerdos"),
    ("resolución sin segunda respuesta", "resoluciones.csv", "S5-B-003,", "S5-C-008,", "no son desacuerdos"),
    (
        "resolución fuera de la muestra",
        "resoluciones.csv",
        "S5-B-003,",
        "S5-A-002,",
        "pregunta): S5-A-002",
    ),
    (
        "resolución duplicada",
        "resoluciones.csv",
        _RESOLUCION_2,
        _RESOLUCION_2 * 2,
        "ya tiene respuesta en la línea 3",
    ),
    ("resolución inválida", "resoluciones.csv", "S5-B-003,yes,no,", "S5-B-003,yes,maybe,", "q2 = 'maybe'"),
    ("resolución sin texto", "resoluciones.csv", _TEXTO_2, '""', "falta el texto de la columna resolucion"),
    (
        "resolución con una cuenta",
        "resoluciones.csv",
        "Q4 no:",
        "G" + "A" * 55 + " Q4 no:",
        "forma de cuenta",
    ),
    (
        "encabezado de resoluciones",
        "resoluciones.csv",
        ",resolucion\n",
        ",resolución\n",
        "el encabezado debe tener",
    ),
]


@pytest.mark.parametrize(
    "archivo, viejo, nuevo, mensaje", [c[1:] for c in CASOS_PUNTAJE], ids=[c[0] for c in CASOS_PUNTAJE]
)
def test_entradas_invalidas_del_puntaje(tmp_path, capsys, archivo, viejo, nuevo, mensaje):
    rutas = _copia_del_fixture(tmp_path)
    texto = rutas[archivo].read_text(encoding="utf-8")
    assert texto.count(viejo) == 1
    rutas[archivo].write_text(texto.replace(viejo, nuevo), encoding="utf-8")
    assert main(_args_puntaje(rutas)) == 2
    error = capsys.readouterr().err
    assert error.startswith("error: ") and mensaje in error, error
    assert not (tmp_path / "resultado").exists()


def test_censo_sin_lista_de_alertas(tmp_path, capsys):
    rutas = _copia_del_fixture(tmp_path)
    assert main(_args_puntaje(rutas, alertas=False)) == 2
    assert "en censo (S1) el puntaje usa el peso de cada alerta" in capsys.readouterr().err


# --- De punta a punta con el fixture --------------------------------------------------------------------


def test_de_punta_a_punta_con_el_fixture(tmp_path):
    """Los tres comandos, como procesos aparte, sobre el fixture: una señal en censo y otra en muestra."""
    entorno = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(filter(None, [str(SRC), os.environ.get("PYTHONPATH")])),
    }

    def correr(*args: str) -> str:
        proceso = subprocess.run(
            [sys.executable, "-m", "lumen_lupa.revision", *args],
            capture_output=True,
            text=True,
            env=entorno,
            timeout=120,
        )
        assert proceso.returncode == 0, proceso.stderr
        return proceso.stdout

    alertas = str(FIXTURE / "alertas.json")
    assert correr("huella", "--alertas", alertas) == HUELLA_FIXTURE + "\n"
    muestra = tmp_path / "muestra.json"
    correr("muestra", "--alertas", alertas, "--semilla", SEMILLA.upper(), "--salida", str(muestra))
    assert muestra.read_text(encoding="utf-8") == (FIXTURE / "esperado_muestra.json").read_text(
        encoding="utf-8"
    )
    salida = tmp_path / "resultado"
    correr(
        "puntaje",
        "--muestra",
        str(muestra),
        "--respuestas",
        str(FIXTURE / "respuestas_revisor_1.csv"),
        "--segundo",
        str(FIXTURE / "respuestas_revisor_2.csv"),
        "--resoluciones",
        str(FIXTURE / "resoluciones.csv"),
        "--alertas",
        alertas,
        "--salida",
        str(salida),
    )
    for nombre in ("resultado.json", "resultado.md"):
        esperado = (FIXTURE / f"esperado_{nombre}").read_text(encoding="utf-8")
        assert (salida / nombre).read_text(encoding="utf-8") == esperado, nombre


def test_el_fixture_da_lo_calculado_a_mano():
    entradas = (
        leer_muestra(FIXTURE / "esperado_muestra.json"),
        leer_respuestas(FIXTURE / "respuestas_revisor_1.csv", "el CSV del primer revisor"),
        leer_respuestas(FIXTURE / "respuestas_revisor_2.csv", "el CSV del segundo revisor"),
        leer_alertas(FIXTURE / "alertas.json"),
    )
    resultado = puntuar(*entradas, leer_resoluciones(FIXTURE / "resoluciones.csv", "el CSV de resoluciones"))
    s1, s5 = resultado.senales
    # S1, censo: falsos positivos S1-B-001 (0.0864) y S1-A-004 (0.0019) de 0.2263;
    # indeterminado S1-C-002 (0.0036).
    assert (s1.modo, s1.conteos, s1.peso_revisado) == ("censo", {CON: 7, FPO: 2, IND: 1}, Decimal("0.2263"))
    assert s1.participacion[FPO] == Fraction(883, 2263) and s1.participacion[IND] == Fraction(36, 2263)
    assert s1.pasa is False
    # S5, muestra: el primer revisor dio 3 falsos positivos y 5 indeterminados de 25; la resolución de
    # S5-A-006 (q2 = yes) lo vuelve consistente, así que cuentan 18, 3 y 4.
    assert (s5.modo, s5.conteos, s5.pasa) == ("muestra", {CON: 18, FPO: 3, IND: 4}, True)
    # Acuerdo: 12 de 13. Primer revisor 11 CON, 1 FPO, 1 IND; segundo 12 CON, 1 FPO, 0 IND:
    # p_e = (11·12 + 1·1)/169 = 133/169 y kappa = (12/13 − 133/169)/(36/169) = 23/36.
    # En S5 (7 CON, 1 FPO, 1 IND contra 8 CON, 1 FPO): p_e = 57/81; kappa = (8/9 − 57/81)/(24/81) = 5/8.
    # En S1 los dos revisores usaron solo `consistente`: p_e = 1 y la kappa no está definida.
    assert (resultado.acuerdo.coincidencias, resultado.acuerdo.dobles) == (12, 13)
    assert resultado.acuerdo.kappa == Fraction(23, 36)
    assert s5.acuerdo.kappa == Fraction(5, 8) and s1.acuerdo.kappa is None
    resueltos = [(d.alerta_id, d.preguntas, d.primera, d.segunda, d.resuelta) for d in resultado.desacuerdos]
    assert resueltos == [
        ("S5-A-006", ("q2",), IND, CON, CON),
        ("S5-B-003", ("q4",), FPO, FPO, FPO),
    ]
    sin_resolver = puntuar(*entradas)
    assert sin_resolver.estado == FALTAN_RESOLUCIONES and sin_resolver.senales[1].conteos == {
        CON: 17,
        FPO: 3,
        IND: 5,
    }
    assert sin_resolver.acuerdo == resultado.acuerdo  # el acuerdo es el de las respuestas tal como se dieron
    assert (resultado.estado, resultado.entregable_1, resultado.senales_que_pasan()) == (
        COMPLETO,
        True,
        ["S5"],
    )


def test_puntaje_sin_todas_las_resoluciones_queda_incompleto(tmp_path):
    rutas = _copia_del_fixture(tmp_path)
    for args in (_args_puntaje(rutas, resoluciones=False), _args_puntaje(rutas)):
        if "--resoluciones" in args:  # solo la de S5-A-006: falta la de S5-B-003
            texto = rutas["resoluciones.csv"].read_text(encoding="utf-8")
            rutas["resoluciones.csv"].write_text(texto.replace(_RESOLUCION_2, ""), encoding="utf-8")
        assert main(args) == 0
        datos = json.loads((tmp_path / "resultado" / "resultado.json").read_text(encoding="utf-8"))
        assert (datos["status"], datos["deliverable_1_met"], datos["passing_signals"]) == (
            FALTAN_RESOLUCIONES,
            None,
            [],
        )
        assert all(s["passes"] is None for s in datos["signals"].values())


def test_el_resultado_no_copia_evidencia_ni_notas(tmp_path):
    """§11: los dossiers y las cuentas son privados; `evidencia` y `notas` no pasan al resultado."""
    rutas = _copia_del_fixture(tmp_path)
    assert main(_args_puntaje(rutas)) == 0
    for nombre in ("resultado.json", "resultado.md"):
        texto = (tmp_path / "resultado" / nombre).read_text(encoding="utf-8")
        assert "dossiers/" not in texto and "fondeo" not in texto, nombre
