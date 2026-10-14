"""Entry point: _build_llm, _save_report, main()."""
from __future__ import annotations
import time
from typing import Optional

from .config import CFG
from .logging_setup import get_logger, setup_logger
from .carga import procesar_documentos_carpeta
from .llm import ClienteClaude
from .metricas import calcular_metricas_grafo, render_metricas_grafo_md
from .auditoria import ejecutar_auditoria_contrato
from .grafo import construir_indice_nodos_por_cid
from .informe import render_auditoria_markdown
from .chat import iniciar_chat_interactivo

log = get_logger("main")


def _build_llm() -> ClienteClaude:
    if not CFG.enable_llm:
        raise RuntimeError("CFG.enable_llm=False. Actívalo para usar el LLM.")
    log.info("Modelo: %s | effort por etapa: %s", CFG.modelo, CFG.effort)
    return ClienteClaude()


def _save_report(md_text: str, filename: Optional[str] = None) -> None:
    filename = filename or CFG.reporte_md
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(md_text or "")
        log.info("Informe guardado en '%s'.", filename)
    except Exception as e:
        log.warning("No se pudo guardar el informe: %s", e)


def _mostrar(md: str) -> None:
    try:
        from IPython.display import display, Markdown
        display(Markdown(md))
    except Exception:
        print(md)


def main(chat: bool = True) -> None:
    setup_logger()

    try:
        llm = _build_llm()
    except Exception as e:
        log.error("No se pudo inicializar el LLM: %s", e)
        return

    log.info("PASO 1: Procesando el contrato desde '%s'...", CFG.ruta_contrato)
    try:
        _, texto_contrato = procesar_documentos_carpeta(CFG.ruta_contrato)
    except Exception as e:
        log.error("Error leyendo el contrato: %s", e)
        return

    if not texto_contrato:
        log.error("No se encontraron documentos en '%s'. Abortando.", CFG.ruta_contrato)
        return

    start_time = time.time()
    try:
        resultado = ejecutar_auditoria_contrato(texto_contrato=texto_contrato, llm=llm)
    except Exception as e:
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
        _mostrar(md)
    except Exception as e:
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
