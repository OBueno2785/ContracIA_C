"""Carga de documentos PDF/DOCX desde una carpeta y selección de páginas visuales (tablas, escaneos)."""
from __future__ import annotations

import base64
import io
import os

from .logging_setup import get_logger

log = get_logger("carga")

# Una página con menos caracteres que esto y con imágenes se trata como escaneada.
_MIN_CHARS_PAGINA_TEXTO = 200
# Límite práctico del PDF adjunto (la API acepta hasta 32 MB por petición, incluido el resto del prompt).
_MAX_BYTES_PDF_VISUAL = 20 * 1024 * 1024


def _resolver_carpeta(folder_path: str) -> str | None:
    for candidata in (os.path.join(os.getcwd(), folder_path), folder_path):
        if os.path.exists(candidata):
            return candidata
    log.warning("La carpeta '%s' no existe.", folder_path)
    return None


def _leer_pdf(file_path: str) -> list[str]:
    from pypdf import PdfReader
    return [pagina.extract_text() or "" for pagina in PdfReader(file_path).pages]


def _leer_docx(file_path: str) -> list[str]:
    """Párrafos y tablas en el orden del documento; cada fila de tabla como 'celda | celda'."""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(file_path)
    lineas: list[str] = []
    for hijo in doc.element.body.iterchildren():
        etiqueta = hijo.tag.rsplit("}", 1)[-1]
        if etiqueta == "p":
            lineas.append(Paragraph(hijo, doc).text)
        elif etiqueta == "tbl":
            for fila in Table(hijo, doc).rows:
                celdas: list[str] = []
                for celda in fila.cells:
                    texto = " ".join(celda.text.split())
                    if not celdas or celdas[-1] != texto:  # celdas combinadas se repiten
                        celdas.append(texto)
                lineas.append(" | ".join(celdas))
            lineas.append("")
    return ["\n".join(lineas)]


def procesar_documentos_carpeta(folder_path: str) -> tuple[list[str] | None, str | None]:
    paginas_combinadas: list[str] = []
    texto_completo = ""
    full_folder_path = _resolver_carpeta(folder_path)
    if full_folder_path is None:
        return None, None

    archivos_en_carpeta = sorted(os.listdir(full_folder_path))
    if not archivos_en_carpeta: return None, None

    log.info("Procesando carpeta: %s", full_folder_path)
    for file_name in archivos_en_carpeta:
        file_path = os.path.join(full_folder_path, file_name)
        if not os.path.isfile(file_path): continue
        try:
            if file_name.lower().endswith('.pdf'):
                paginas = _leer_pdf(file_path)
            elif file_name.lower().endswith('.docx'):
                paginas = _leer_docx(file_path)
            else:
                continue
            log.info("  - Cargado: %s (%d páginas/bloques)", file_name, len(paginas))
            paginas_combinadas.extend(paginas)
            if texto_completo:
                texto_completo += f"\n\n=== ARCHIVO: {file_name} ===\n\n"
            texto_completo += "\n\n".join(paginas)
        except Exception as e:  # noqa: BLE001
            log.warning("  No se pudo cargar %s: %s", file_name, e)

    return paginas_combinadas, texto_completo


def _es_pagina_visual(pagina) -> bool:
    if pagina.find_tables():
        return True
    return len(pagina.chars) < _MIN_CHARS_PAGINA_TEXTO and bool(pagina.images)


def construir_pdf_visual(folder_path: str, max_paginas: int) -> tuple[str | None, list[str]]:
    """Reúne en un PDF las páginas con tablas o escaneadas, cuyo texto extraído suele perder estructura.

    Devuelve el PDF en base64 (listo para un bloque `document` de la API) y la lista de páginas incluidas.
    """
    import pdfplumber
    from pypdf import PdfReader, PdfWriter

    full_folder_path = _resolver_carpeta(folder_path)
    if full_folder_path is None or max_paginas <= 0:
        return None, []

    seleccion: list[tuple[str, int]] = []
    for file_name in sorted(os.listdir(full_folder_path)):
        if not file_name.lower().endswith(".pdf"):
            continue
        file_path = os.path.join(full_folder_path, file_name)
        try:
            with pdfplumber.open(file_path) as pdf:
                for i, pagina in enumerate(pdf.pages):
                    if _es_pagina_visual(pagina):
                        seleccion.append((file_path, i))
                    pagina.flush_cache()  # sin esto, un contrato de 300+ páginas acumula cientos de MB
        except Exception as e:  # noqa: BLE001
            log.warning("  No se pudo analizar el formato de %s: %s", file_name, e)

    if not seleccion:
        log.info("No se detectaron páginas con tablas o escaneadas.")
        return None, []
    if len(seleccion) > max_paginas:
        log.warning(
            "%d páginas visuales detectadas; se adjuntan solo las primeras %d (CONTRACTIA_MAX_PAGINAS_VISUALES).",
            len(seleccion), max_paginas,
        )
        seleccion = seleccion[:max_paginas]

    writer = PdfWriter()
    lectores: dict[str, PdfReader] = {}
    for file_path, i in seleccion:
        lector = lectores.setdefault(file_path, PdfReader(file_path))
        writer.add_page(lector.pages[i])
    buffer = io.BytesIO()
    writer.write(buffer)
    datos = buffer.getvalue()
    if len(datos) > _MAX_BYTES_PDF_VISUAL:
        log.warning(
            "El PDF de páginas visuales pesa %.1f MB (máx. %d MB); no se adjunta. Reduce CONTRACTIA_MAX_PAGINAS_VISUALES.",
            len(datos) / 1e6, _MAX_BYTES_PDF_VISUAL // (1024 * 1024),
        )
        return None, []

    descripciones = [f"{os.path.basename(f)} p.{i + 1}" for f, i in seleccion]
    log.info("PDF visual: %d páginas (%.1f MB): %s", len(seleccion), len(datos) / 1e6, ", ".join(descripciones))
    return base64.standard_b64encode(datos).decode("ascii"), descripciones
