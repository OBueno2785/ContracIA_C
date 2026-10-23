"""Tests de carga: tablas en DOCX y detección de páginas visuales en PDF."""
import base64
import io

import pytest
from docx import Document
from pypdf import PdfReader

from contractia.carga import (
    _leer_docx,
    construir_pdf_visual,
    procesar_documentos_carpeta,
)


@pytest.fixture
def docx_con_tabla(tmp_path):
    doc = Document()
    doc.add_paragraph("CAPÍTULO I DISPOSICIONES GENERALES")
    tabla = doc.add_table(rows=2, cols=3)
    for i, fila in enumerate([("Hito", "Plazo", "Penalidad"), ("Entrega", "30 Días", "1%")]):
        for j, valor in enumerate(fila):
            tabla.cell(i, j).text = valor
    doc.add_paragraph("1.1 Texto posterior a la tabla.")
    ruta = tmp_path / "contrato.docx"
    doc.save(ruta)
    return ruta


def _pdf_con_tabla(ruta):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    with PdfPages(ruta) as pdf:
        for n in (1, 3):
            fig = plt.figure(figsize=(8.5, 11))
            fig.text(0.1, 0.9, f"Pagina {n} " + "texto corrido del contrato " * 40, wrap=True)
            pdf.savefig(fig)
            plt.close(fig)
        fig, ax = plt.subplots(figsize=(8.5, 11))
        ax.axis("off")
        ax.table(cellText=[["Hito", "Plazo"], ["Entrega", "30 dias"], ["Pago", "60 dias"]], loc="center")
        pdf.savefig(fig)
        plt.close(fig)


def test_docx_conserva_tablas_en_orden(docx_con_tabla):
    texto = _leer_docx(str(docx_con_tabla))[0]
    assert "Hito | Plazo | Penalidad" in texto
    assert "Entrega | 30 Días | 1%" in texto
    assert texto.index("DISPOSICIONES") < texto.index("Hito") < texto.index("posterior")


def test_procesar_carpeta_incluye_tablas_docx(docx_con_tabla):
    _, texto = procesar_documentos_carpeta(str(docx_con_tabla.parent))
    assert "Entrega | 30 Días | 1%" in texto


def test_pdf_visual_selecciona_pagina_con_tabla(tmp_path):
    _pdf_con_tabla(tmp_path / "contrato.pdf")
    b64, paginas = construir_pdf_visual(str(tmp_path), max_paginas=10)
    assert paginas == ["contrato.pdf p.3"]
    assert len(PdfReader(io.BytesIO(base64.b64decode(b64))).pages) == 1


def test_pdf_visual_respeta_limite_y_cero(tmp_path):
    _pdf_con_tabla(tmp_path / "contrato.pdf")
    assert construir_pdf_visual(str(tmp_path), max_paginas=0) == (None, [])


def test_pdf_visual_sin_pdfs(docx_con_tabla):
    assert construir_pdf_visual(str(docx_con_tabla.parent), max_paginas=10) == (None, [])
