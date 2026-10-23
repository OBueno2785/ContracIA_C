"""Tests para informe.py: deduplicar_hallazgos, dashboard_severidades, render_auditoria_markdown."""
from contractia.informe import (
    dashboard_severidades,
    deduplicar_hallazgos,
    render_auditoria_markdown,
    render_sintesis_md,
)


class TestDeduplicarHallazgos:
    def test_elimina_duplicados_exactos(self):
        h = {"tipo": "ERROR", "clausula_afectada": "5.1", "explicacion": "problema X", "cita": "", "severidad": "ALTA"}
        resultado = deduplicar_hallazgos([h, h, h])
        assert len(resultado) == 1

    def test_mantiene_distintos(self):
        hallazgos = [
            {"tipo": "ERROR", "clausula_afectada": "5.1", "explicacion": "problema X", "cita": "", "severidad": "ALTA"},
            {"tipo": "ERROR", "clausula_afectada": "5.2", "explicacion": "problema X", "cita": "", "severidad": "ALTA"},
        ]
        assert len(deduplicar_hallazgos(hallazgos)) == 2

    def test_diferente_tipo_no_deduplica(self):
        hallazgos = [
            {"tipo": "ERROR", "clausula_afectada": "5.1", "explicacion": "problema X", "cita": "", "severidad": "ALTA"},
            {"tipo": "ADVERTENCIA", "clausula_afectada": "5.1", "explicacion": "problema X", "cita": "", "severidad": "MEDIA"},
        ]
        assert len(deduplicar_hallazgos(hallazgos)) == 2

    def test_lista_vacia(self):
        assert deduplicar_hallazgos([]) == []

    def test_preserva_primer_hallazgo(self):
        h1 = {"tipo": "ERROR", "clausula_afectada": "5.1", "explicacion": "X", "cita": "cita1", "severidad": "ALTA"}
        h2 = {"tipo": "ERROR", "clausula_afectada": "5.1", "explicacion": "X", "cita": "cita2", "severidad": "MEDIA"}
        result = deduplicar_hallazgos([h1, h2])
        assert result[0]["cita"] == "cita1"


class TestDashboardSeveridades:
    def test_conteo_correcto(self):
        resultados = [{"hallazgos": [
            {"severidad": "ALTA"},
            {"severidad": "ALTA"},
            {"severidad": "MEDIA"},
            {"severidad": "BAJA"},
        ]}]
        md = dashboard_severidades(resultados)
        assert "ALTA" in md
        assert "2" in md  # dos ALTA

    def test_normaliza_acento(self):
        resultados = [{"hallazgos": [{"severidad": "CRÍTICA"}]}]
        md = dashboard_severidades(resultados)
        assert "CRITICA" in md

    def test_sin_hallazgos(self):
        md = dashboard_severidades([])
        assert "sin hallazgos" in md.lower()

    def test_contiene_total(self):
        resultados = [{"hallazgos": [{"severidad": "ALTA"}, {"severidad": "BAJA"}]}]
        md = dashboard_severidades(resultados)
        assert "TOTAL" in md
        assert "2" in md

    def test_porcentajes_suman_100(self):
        resultados = [{"hallazgos": [
            {"severidad": "ALTA"},
            {"severidad": "MEDIA"},
            {"severidad": "BAJA"},
            {"severidad": "BAJA"},
        ]}]
        md = dashboard_severidades(resultados)
        assert "100%" in md


class TestRenderAuditoriaMarkdown:
    def _resultado_base(self):
        return {
            "secciones": [{"titulo": "Capítulo 1 OBLIGACIONES", "contenido": "texto", "tipo": "CAPITULO"}],
            "indice_secciones": [{"tipo": "CAPITULO", "n": "1", "titulo": "Cap 1"}],
            "indice_global_clausulas": ["1.1", "1.2"],
            "mapa_clausula_a_seccion": {},
            "resultados_auditoria": [],
        }

    def test_sin_hallazgos(self):
        md = render_auditoria_markdown(self._resultado_base())
        assert "No se detectaron inconsistencias" in md

    def test_abortado_seguridad(self):
        resultado = {"abortado_por_seguridad": True, "evidencia": "inyeccion detectada"}
        md = render_auditoria_markdown(resultado)
        assert "ABORTADA" in md
        assert "inyeccion detectada" in md

    def test_incluye_dashboard(self):
        resultado = self._resultado_base()
        resultado["resultados_auditoria"] = [{
            "seccion": "Capítulo 1 OBLIGACIONES",
            "tipo": "CAPITULO",
            "hallazgos": [
                {"clausula_afectada": "1.1", "tipo": "ERROR", "cita": "", "explicacion": "test", "severidad": "ALTA"},
            ],
        }]
        md = render_auditoria_markdown(resultado)
        assert "Dashboard" in md
        assert "ALTA" in md

    def test_deduplica_en_render(self):
        h = {"clausula_afectada": "1.1", "tipo": "ERROR", "cita": "", "explicacion": "duplicado exacto", "severidad": "ALTA"}
        resultado = self._resultado_base()
        resultado["resultados_auditoria"] = [{
            "seccion": "Capítulo 1 OBLIGACIONES",
            "tipo": "CAPITULO",
            "hallazgos": [h, h, h],
        }]
        md = render_auditoria_markdown(resultado)
        # El informe solo debe mostrar una vez "duplicado exacto"
        assert md.count("duplicado exacto") == 1

    def test_indice_clausulas_en_informe(self):
        md = render_auditoria_markdown(self._resultado_base())
        assert "1.1" in md
        assert "1.2" in md


# ----- Síntesis global -----


def _sintesis():
    return {
        "resumen_ejecutivo": "Contrato de concesión con riesgos de plazos.",
        "hallazgos_globales": [{"clausula_afectada": "7.1", "tipo": "CONTRADICCION", "severidad": "ALTA",
                                "explicacion": "Plazo distinto al del Anexo 5", "cita": "30 Días"}],
        "ausencias": [],
        "hallazgos_observados": [{"seccion": "CAPÍTULO II", "clausula_afectada": "2.1", "motivo": "Duplicado"}],
    }


def test_render_sintesis_secciones():
    md = render_sintesis_md(_sintesis())
    assert "## Síntesis Global" in md
    assert "Hallazgos globales (1)" in md and "CONTRADICCION" in md and "Plazo distinto" in md
    assert "Elementos ausentes o incompletos (0)" in md and "Sin ausencias" in md
    assert "posibles duplicados o falsos positivos (1)" in md


def test_render_sintesis_sin_observados_omite_seccion():
    s = _sintesis()
    s["hallazgos_observados"] = []
    assert "falsos positivos" not in render_sintesis_md(s)


def test_informe_incluye_sintesis_y_paginas_visuales():
    resultado = {
        "indice_secciones": [], "indice_global_clausulas": [], "resultados_auditoria": [],
        "sintesis": _sintesis(), "paginas_visuales": ["c.pdf p.3", "c.pdf p.9"],
    }
    md = render_auditoria_markdown(resultado)
    assert "Síntesis Global" in md
    assert "(tablas/escaneos)**: 2" in md


def test_informe_sin_sintesis_sigue_funcionando():
    md = render_auditoria_markdown({"indice_secciones": [], "indice_global_clausulas": [], "resultados_auditoria": [], "sintesis": None})
    assert "Síntesis Global" not in md
