"""Entry point: _build_llm, _save_report, main()."""
from __future__ import annotations

import time

from .auditoria import ejecutar_auditoria_contrato
from .carga import construir_pdf_visual, procesar_documentos_carpeta
from .chat import iniciar_chat_interactivo
from .config import CFG
from .grafo import construir_indice_nodos_por_cid
from .informe import render_auditoria_markdown
from .llm import ClienteClaude
from .logging_setup import get_logger, setup_logger
from .metricas import calcular_metricas_grafo, render_metricas_grafo_md

log = get_logger("main")


def _build_llm() -> ClienteClaude:
    if not CFG.enable_llm:
        raise RuntimeError("CFG.enable_llm=False. Actívalo para usar el LLM.")
    log.info("Modelo: %s | effort por etapa: %s", CFG.modelo, CFG.effort)
    return ClienteClaude()


def _save_report(md_text: str, filename: str | None = None) -> None:
    filename = filename or CFG.reporte_md
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(md_text or "")
        log.info("Informe guardado en '%s'.", filename)
    except OSError as e:
        log.warning("No se pudo guardar el informe: %s", e)


def main(chat: bool = True) -> None:
    setup_logger()

    try:
        llm = _build_llm()
    except Exception as e:  # noqa: BLE001
        log.error("No se pudo inicializar el LLM: %s", e)
        return

    log.info("PASO 1: Procesando el contrato desde '%s'...", CFG.ruta_contrato)
    try:
        _, texto_contrato = procesar_documentos_carpeta(CFG.ruta_contrato)
    except Exception as e:  # noqa: BLE001
        log.error("Error leyendo el contrato: %s", e)
        return

    if not texto_contrato:
        log.error("No se encontraron documentos en '%s'. Abortando.", CFG.ruta_contrato)
        return

    log.info("PASO 1b: Detectando páginas con tablas o escaneadas...")
    try:
        pdf_visual, paginas_visuales = construir_pdf_visual(CFG.ruta_contrato, CFG.max_paginas_visuales)
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo armar el PDF de páginas visuales; se sigue solo con texto: %s", e)
        pdf_visual, paginas_visuales = None, []

    start_time = time.time()
    try:
        resultado = ejecutar_auditoria_contrato(texto_contrato=texto_contrato, llm=llm, pdf_visual=pdf_visual)
        resultado["paginas_visuales"] = paginas_visuales
    except Exception as e:  # noqa: BLE001
        log.error("Error ejecutando el pipeline de auditoría: %s", e)
        import traceback
        traceback.print_exc()
        return

    elapsed = time.time() - start_time
    log.info("AUDITORÍA COMPLETADA en %.2fs", elapsed)
    log.info("USO DE TOKENS:\n%s", llm.tokens.resumen())

    try:
        md = render_auditoria_markdown(resultado)
        if "grafo" in resultado:
            md += "\n\n" + render_metricas_grafo_md(
                calcular_metricas_grafo(resultado["grafo"], resultado["mapa_clausula_a_seccion"])
            )
        md += f"\n\n---\n*Modelo: {CFG.modelo} · Tiempo de ejecución: {elapsed:.2f}s*\n\n"
        md += f"## Uso de Tokens\n\n{llm.tokens.resumen()}\n"
    except Exception as e:  # noqa: BLE001
        log.warning("Error renderizando informe: %s", e)
        md = "Sin datos."

    _save_report(md)

    if not chat or resultado.get("abortado_por_seguridad"):
        return

    if "grafo" in resultado and "secciones" in resultado:
        indice_nodos = construir_indice_nodos_por_cid(resultado["grafo"])
        iniciar_chat_interactivo(
            resultado["grafo"],
            resultado["secciones"],
            resultado["indice_secciones"],
            indice_nodos,
            llm,
        )


if __name__ == "__main__":
    main()
