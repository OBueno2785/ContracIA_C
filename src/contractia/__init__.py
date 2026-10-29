"""ContractIA — auditoría contractual multi-agente con GraphRAG sobre la API de Claude.

Importaciones ligeras disponibles siempre. Los módulos que llaman al modelo
(llm, grafo, auditoria, chat, main) requieren el SDK `anthropic`.
"""
__version__ = "0.3.0"

from .config import CFG, RELACIONES_VALIDAS, TIPOS_NODO_VALIDOS
from .informe import dashboard_severidades, deduplicar_hallazgos, render_auditoria_markdown
from .logging_setup import get_logger, setup_logger
from .models import (
    Hallazgo,
    RespuestaAuditor,
    RespuestaCronista,
    RespuestaExtraccion,
    RespuestaJurista,
    RespuestaSeguridad,
    TripletaGrafo,
)
from .segmentacion import (
    construir_mapa_clausula_a_seccion,
    crear_indice_capitulos_anexos,
    crear_indice_de_clausulas_por_seccion,
    crear_indice_global_clausulas,
    separar_en_secciones,
)
from .utils import (
    canonicalizar_nodo,
    extraer_cid_de_string,
    hash_secciones,
    parse_json_seguro,
)

__all__ = [
    "CFG",
    "RELACIONES_VALIDAS",
    "TIPOS_NODO_VALIDOS",
    "ClienteClaude",
    "ContadorTokens",
    "Hallazgo",
    "RespuestaAuditor",
    "RespuestaCronista",
    "RespuestaExtraccion",
    "RespuestaJurista",
    "RespuestaSeguridad",
    "RespuestaSintesis",
    "TripletaGrafo",
    "_build_llm",
    "_save_report",
    "calcular_metricas_grafo",
    "canonicalizar_nodo",
    "construir_grafo_conocimiento",
    "construir_indice_nodos_por_cid",
    "construir_mapa_clausula_a_seccion",
    "construir_pdf_visual",
    "consultar_contrato_graphrag",
    "crear_indice_capitulos_anexos",
    "crear_indice_de_clausulas_por_seccion",
    "crear_indice_global_clausulas",
    "dashboard_severidades",
    "deduplicar_hallazgos",
    "ejecutar_auditoria_contrato",
    "ejecutar_sintesis_global",
    "extraer_cid_de_string",
    "get_logger",
    "hash_secciones",
    "iniciar_chat_interactivo",
    "main",
    "normalizar_formato",
    "obtener_contexto_grafo",
    "parse_json_seguro",
    "preparar_contrato",
    "procesar_documentos_carpeta",
    "render_auditoria_markdown",
    "render_metricas_grafo_md",
    "render_revision_indices_md",
    "render_sintesis_md",
    "revisar_indices",
    "separar_en_secciones",
    "setup_logger",
    "verificar_seguridad_documento",
    "visualizar_grafo",
]
