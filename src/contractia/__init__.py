"""ContractIA — auditoría contractual multi-agente con GraphRAG sobre la API de Claude.

Importaciones ligeras disponibles siempre. Los módulos que llaman al modelo
(llm, grafo, auditoria, chat, main) requieren el SDK `anthropic`.
"""
__version__ = "0.3.0"

from .logging_setup import setup_logger, get_logger
from .config import CFG, RELACIONES_VALIDAS, TIPOS_NODO_VALIDOS
from .segmentacion import (
    separar_en_secciones,
    construir_mapa_clausula_a_seccion,
    crear_indice_global_clausulas,
    crear_indice_capitulos_anexos,
    crear_indice_de_clausulas_por_seccion,
)
from .utils import (
    parse_json_seguro,
    extraer_cid_de_string,
    canonicalizar_nodo,
    hash_secciones,
)
from .models import (
    Hallazgo,
    RespuestaJurista,
    RespuestaAuditor,
    RespuestaCronista,
    RespuestaSeguridad,
    TripletaGrafo,
    RespuestaExtraccion,
)
from .informe import render_auditoria_markdown, dashboard_severidades, deduplicar_hallazgos

__all__ = [
    "setup_logger", "get_logger",
    "CFG", "RELACIONES_VALIDAS", "TIPOS_NODO_VALIDOS",
    "procesar_documentos_carpeta",
    "separar_en_secciones", "construir_mapa_clausula_a_seccion",
    "crear_indice_global_clausulas", "crear_indice_capitulos_anexos",
    "crear_indice_de_clausulas_por_seccion",
    "construir_grafo_conocimiento", "construir_indice_nodos_por_cid",
    "visualizar_grafo", "obtener_contexto_grafo",
    "ClienteClaude", "ContadorTokens", "calcular_metricas_grafo", "render_metricas_grafo_md",
    "ejecutar_auditoria_contrato", "verificar_seguridad_documento",
    "consultar_contrato_graphrag", "iniciar_chat_interactivo",
    "render_auditoria_markdown", "dashboard_severidades", "deduplicar_hallazgos",
    "Hallazgo", "RespuestaJurista", "RespuestaAuditor", "RespuestaCronista",
    "RespuestaSeguridad", "TripletaGrafo", "RespuestaExtraccion",
    "_build_llm", "_save_report", "main",
]
