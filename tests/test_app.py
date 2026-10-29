"""Recorre la interfaz web completa con AppTest y un cliente de Anthropic simulado (sin red ni costo)."""
import types
from pathlib import Path

import pytest
from docx import Document

from contractia import models

APP = Path(__file__).resolve().parents[1] / "src" / "contractia" / "app.py"
USO = types.SimpleNamespace(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=0, cache_read_input_tokens=500)


def _respuesta(schema):
    if schema is models.RespuestaSeguridad:
        return schema(es_seguro=True)
    if schema is models.RespuestaExtraccion:
        return schema(tripletas=[models.TripletaGrafo(origen="Cláusula 1.1", relacion="REFERENCIA_A", destino="Cláusula 1.2")])
    if schema is models.RespuestaAuditor:
        return schema(hay_inconsistencias=True, hallazgos=[models.Hallazgo(clausula_afectada="1.1", explicacion="x", severidad="ALTA")])
    if schema is models.RespuestaSintesis:
        return schema(resumen_ejecutivo="Resumen ejecutivo de prueba.")
    return schema()


def _mensaje(schema):
    return types.SimpleNamespace(parsed_output=_respuesta(schema), usage=USO, stop_reason="end_turn", stop_details=None)


class _StreamAsync:
    def __init__(self, kw): self.kw = kw
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get_final_message(self): return _mensaje(self.kw["output_format"])


class _ClienteAsync:
    def __init__(self, **kw):
        async def parse(**k): return _mensaje(k["output_format"])
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(parse=parse, stream=lambda **k: _StreamAsync(k)))

    async def close(self): pass


class _StreamSync:
    text_stream = iter(["La cláusula 1.1 remite a la 1.2."])
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get_final_message(self): return types.SimpleNamespace(usage=USO, stop_reason="end_turn")


class _ClienteSync:
    def __init__(self, **kw):
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(stream=lambda **k: _StreamSync()))


@pytest.fixture
def contrato(tmp_path, monkeypatch):
    import anthropic

    from contractia.config import CFG, actualizar_config
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _ClienteAsync)
    monkeypatch.setattr(anthropic, "Anthropic", _ClienteSync)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-prueba")
    cache_original = CFG.cache_dir
    actualizar_config(cache_dir=str(tmp_path / "cache"))
    carpeta = tmp_path / "contrato"
    carpeta.mkdir()
    doc = Document()
    for linea in ["CAPÍTULO I DISPOSICIONES GENERALES", "1.1 El Concesionario cumple la Cláusula 1.2.",
                  "1.2 El plazo será de 30 Días.", "CAPÍTULO II PLAZOS DEL CONTRATO", "2.1 El plazo es de un año.",
                  "2.2 Las Partes lo aceptan."]:
        doc.add_paragraph(linea)
    doc.save(carpeta / "contrato.docx")
    yield str(carpeta)
    actualizar_config(cache_dir=cache_original)


def _sin_errores(at):
    assert not at.exception, at.exception
    assert not [e.value for e in at.error], [e.value for e in at.error]


_ICONO_A_ESTADO = {
    ":green[:material/check_circle:]": "hecha",
    ":blue[:material/radio_button_checked:]": "actual",
    ":gray[:material/radio_button_unchecked:]": "pendiente",
    ":red[:material/block:]": "bloqueada",
}


def _progreso(at) -> list[str]:
    """Estado de cada etapa según el ícono de la barra lateral."""
    return [estado for m in at.sidebar.markdown for icono, estado in _ICONO_A_ESTADO.items() if m.value.startswith(icono)]


def test_recorrido_completo(contrato):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    _sin_errores(at)

    at.segmented_control(key="origen").set_value("Carpeta local").run()
    at.text_input[0].set_value(contrato).run()
    next(b for b in at.button if b.label == "Cargar contrato").click().run()
    _sin_errores(at)
    assert {m.label: m.value for m in at.metric}["Archivos"] == "1"

    at.button(key="btn_indices").click().run()
    _sin_errores(at)
    metricas = {m.label: m.value for m in at.metric}
    assert (metricas["Capítulos"], metricas["Cláusulas"]) == ("2", "4")

    next(b for b in at.button if b.label.startswith("Ejecutar las")).click().run()
    _sin_errores(at)
    progreso = _progreso(at)
    assert progreso == ["hecha"] * 7, progreso
    assert any("Resumen ejecutivo de prueba" in m.value for m in at.markdown)
    assert {m.label: m.value for m in at.metric}["Costo acumulado"] != "$0.00"

    at.chat_input[0].set_value("¿Qué dice la cláusula 1.1?").run()
    _sin_errores(at)
    assert any("remite a la 1.2" in m.value for m in at.markdown)

    at.button(key="btn_indices").click().run()
    progreso = _progreso(at)
    assert progreso[:4] == ["hecha", "hecha", "actual", "pendiente"], progreso
