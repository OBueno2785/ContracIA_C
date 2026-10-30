"""Tests de adaptación de formato y revisión de índices."""
import pytest

from contractia.indices import (
    CON_OBSERVACIONES,
    NO_RECONOCIDO,
    OK,
    preparar_contrato,
    revisar_indices,
)
from contractia.informe import render_auditoria_markdown, render_revision_indices_md
from contractia.normalizacion import int_a_romano, normalizar_formato, ordinal_a_int

ESTANDAR = """Capítulo I DISPOSICIONES GENERALES
1.1. El CONCESIONARIO cumple lo dispuesto en la Cláusula 2.1.
1.2. El CONCEDENTE supervisa.

Capítulo II PLAZOS Y GARANTÍAS
2.1. El plazo será de 30 Días conforme al
Capítulo I. Las Partes lo aceptan.
2.2. Las garantías se rigen por el Anexo 1.

Anexo 1 MODELO DE GARANTÍA
1.1. Texto del modelo.
"""


@pytest.mark.parametrize("palabra,valor", [
    ("PRIMERA", 1), ("NOVENA", 9), ("DÉCIMA", 10), ("DÉCIMO SEGUNDA", 12), ("DECIMOSEXTO", 16),
    ("DÉCIMO SÉPTIMA", 17), ("UNDÉCIMA", 11), ("VIGÉSIMO PRIMERA", 21), ("ÚNICA", 1),
    ("GARANTÍAS", None), ("NOVENTA", None),
])
def test_ordinal_a_int(palabra, valor):
    assert ordinal_a_int(palabra) == valor


def test_int_a_romano():
    assert [int_a_romano(n) for n in (1, 4, 9, 14, 19, 40)] == ["I", "IV", "IX", "XIV", "XIX", "XL"]


def test_estandar_no_se_modifica():
    texto, _, rep, cambios = preparar_contrato(ESTANDAR)
    assert rep.estado == OK and not rep.normalizado and cambios == [] and texto == ESTANDAR
    assert len(rep.capitulos) == 2 and len(rep.anexos) == 1


def test_capitulo_ordinal_con_titulo_en_linea_siguiente():
    texto = ESTANDAR.replace("Capítulo I DISPOSICIONES GENERALES", "CAPÍTULO PRIMERO\nDISPOSICIONES GENERALES")
    texto = texto.replace("Capítulo II PLAZOS Y GARANTÍAS", "CAPÍTULO SEGUNDO:\nPLAZOS Y GARANTÍAS")
    texto = texto.replace("1.1. El", "1.1.- El").replace("2.1. El", "2.1) El")
    normalizado, _ = normalizar_formato(texto)
    assert "Capítulo I DISPOSICIONES GENERALES" in normalizado
    assert "Capítulo II PLAZOS Y GARANTÍAS" in normalizado
    assert "1.1. El CONCESIONARIO" in normalizado and "2.1. El plazo" in normalizado
    _, _, rep, _ = preparar_contrato(texto)
    assert rep.normalizado and rep.estado == OK


def test_estilo_clausula_primera():
    texto = (ESTANDAR.replace("Capítulo I DISPOSICIONES GENERALES", "CLÁUSULA PRIMERA: DISPOSICIONES GENERALES")
             .replace("Capítulo II PLAZOS Y GARANTÍAS", "CLÁUSULA SEGUNDA.- PLAZOS Y GARANTÍAS"))
    _, _, rep, _ = preparar_contrato(texto)
    assert rep.estado == OK and rep.normalizado
    assert [c["n"] for c in rep.capitulos] == ["I", "II"]


def test_titulo_en_minusculas_y_anexo_numerado():
    texto = (ESTANDAR.replace("Capítulo I DISPOSICIONES GENERALES", "Capítulo 1 - Disposiciones Generales")
             .replace("Capítulo II PLAZOS Y GARANTÍAS", "Capítulo 2 - Plazos y Garantías")
             .replace("Anexo 1 MODELO DE GARANTÍA", "ANEXO N° 01\nMODELO DE GARANTÍA"))
    normalizado, _ = normalizar_formato(texto)
    assert "Capítulo I DISPOSICIONES GENERALES" in normalizado
    assert "Anexo 1 MODELO DE GARANTÍA" in normalizado


def test_numeracion_relativa_dentro_del_capitulo():
    texto = (ESTANDAR.replace("1.1. El CONCESIONARIO", "1. El CONCESIONARIO").replace("1.2. El", "2. El")
             .replace("2.1. El plazo", "1. El plazo").replace("2.2. Las", "2. Las"))
    _, _, rep, _ = preparar_contrato(texto)
    assert rep.normalizado and rep.estado == OK
    assert [c["n_clausulas"] for c in rep.capitulos] == [2, 2]


def test_referencias_partidas_no_se_vuelven_encabezados():
    # "Capítulo I." al inicio de línea es una referencia dentro de una oración, no un encabezado.
    normalizado, _ = normalizar_formato(ESTANDAR.replace("Capítulo I DISPOSICIONES GENERALES", "CAPÍTULO PRIMERO\nDISPOSICIONES GENERALES"))
    assert "Capítulo I. Las Partes lo aceptan." in normalizado
    assert normalizado.count("Capítulo I ") == 1


def test_titulo_con_guion_largo_queda_compatible():
    normalizado, _ = normalizar_formato(ESTANDAR.replace("Anexo 1 MODELO DE GARANTÍA", "ANEXO N° 1\nMODELO DIGITAL (BIM – APP)"))
    assert "Anexo 1 MODELO DIGITAL (BIM - APP)" in normalizado


def test_conteo_por_nivel():
    texto = ESTANDAR.replace(
        "1.2. El CONCEDENTE supervisa.\n",
        "1.2. El CONCEDENTE supervisa.\n1.2.1. Revisa los informes.\n1.2.2. Visita las obras.\n",
    )
    _, rep = revisar_indices(texto)
    cap_i = rep.capitulos[0]
    assert (cap_i["n_primer_nivel"], cap_i["n_subniveles"], cap_i["n_clausulas"]) == (2, 2, 4)
    assert (rep.n_primer_nivel, rep.n_subniveles) == (4, 2)
    md = render_revision_indices_md(rep.to_dict())
    assert "4 cláusulas de primer nivel y 2 subcláusulas" in md


def test_observaciones_por_clausulas_faltantes():
    _, rep = revisar_indices(ESTANDAR.replace("1.2. El CONCEDENTE supervisa.\n", "1.3. El CONCEDENTE supervisa.\n"))
    assert rep.estado == CON_OBSERVACIONES
    assert any("1.2" in p for p in rep.problemas)


TABLA_DE_CONTENIDOS = """ÍNDICE
Capítulo I: DISPOSICIONES GENERALES 3
Antecedentes 3
Capítulo II: PLAZOS Y GARANTÍAS 5
Anexos del contrato
Anexo 1 Modelo de garantía
Anexo 2 Cuadro de penalidades

"""
CUERPO_CON_DOS_PUNTOS = (ESTANDAR.replace("Capítulo I DISPOSICIONES GENERALES", "Capítulo I: DISPOSICIONES GENERALES")
                         .replace("Capítulo II PLAZOS Y GARANTÍAS", "Capítulo II: PLAZOS Y GARANTÍAS"))


def test_tabla_de_contenidos_sin_puntos_guia_se_ignora():
    texto, secciones, rep, cambios = preparar_contrato(TABLA_DE_CONTENIDOS + CUERPO_CON_DOS_PUNTOS)
    # Solo se adaptan los encabezados del cuerpo, nunca las entradas del índice (que terminan en número de página).
    assert [d for _, d in cambios if d.startswith("Capítulo")] == [
        "Capítulo I DISPOSICIONES GENERALES", "Capítulo II PLAZOS Y GARANTÍAS"]
    assert rep.lineas_indice >= 4
    capitulos = [s for s in secciones if s["tipo"] == "CAPITULO"]
    assert all("1.1." in s["contenido"] or "2.1." in s["contenido"] for s in capitulos)
    assert "(índice) Capítulo I: DISPOSICIONES GENERALES 3" in texto


def test_anexos_listados_en_indice_pero_no_en_el_cuerpo():
    _, _, rep, _ = preparar_contrato(TABLA_DE_CONTENIDOS + CUERPO_CON_DOS_PUNTOS)
    assert [a["n"] for a in rep.anexos] == ["1"]  # solo el Anexo 1 del cuerpo, no los del listado
    assert any("no encontrados en el cuerpo: 2" in p for p in rep.problemas)


def test_estructura_no_reconocida():
    _, _, rep, _ = preparar_contrato("Texto libre sin capítulos ni cláusulas numeradas.\nOtra línea.")
    assert rep.estado == NO_RECONOCIDO


def test_informe_revision_y_aborto():
    _, _, rep, _ = preparar_contrato(ESTANDAR)
    md = render_revision_indices_md(rep.to_dict())
    assert "Revisión de Índices" in md and "2 capítulos, 1 anexos" in md
    _, _, rep_mal, _ = preparar_contrato("Sin estructura.")
    md_abortado = render_auditoria_markdown({"abortado_por_formato": True, "revision_indices": rep_mal.to_dict()})
    assert "NO SE RECONOCIÓ LA ESTRUCTURA" in md_abortado
