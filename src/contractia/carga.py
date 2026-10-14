"""Carga de documentos PDF/DOCX desde una carpeta (celda 2 del notebook)."""
from __future__ import annotations

import os

from .logging_setup import get_logger

log = get_logger("carga")


def _leer_pdf(file_path: str) -> list[str]:
    from pypdf import PdfReader
    return [pagina.extract_text() or "" for pagina in PdfReader(file_path).pages]


def _leer_docx(file_path: str) -> list[str]:
    from docx import Document
    return ["\n".join(p.text for p in Document(file_path).paragraphs)]


def procesar_documentos_carpeta(folder_path: str) -> tuple[list[str] | None, str | None]:
    paginas_combinadas: list[str] = []
    texto_completo = ""
    full_folder_path = os.path.join(os.getcwd(), folder_path)

    if not os.path.exists(full_folder_path):
        full_folder_path = folder_path
        if not os.path.exists(full_folder_path):
            log.warning("La carpeta '%s' no existe.", folder_path)
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
