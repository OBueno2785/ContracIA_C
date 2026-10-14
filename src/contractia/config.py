"""Configuración centralizada.

Lee env vars con fallbacks razonables. La autenticación la resuelve el SDK de
Anthropic (ANTHROPIC_API_KEY o un perfil de `ant auth login`).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _env_effort(nombre: str, defecto: str) -> str:
    return os.getenv(f"CONTRACTIA_EFFORT_{nombre.upper()}", defecto)


@dataclass(frozen=True)
class Config:
    # Modelo (Claude API)
    modelo: str = field(default_factory=lambda: os.getenv("CONTRACTIA_MODEL", "claude-opus-5-5"))
    max_output_tokens: int = field(default_factory=lambda: int(os.getenv("CONTRACTIA_MAX_OUTPUT_TOKENS", "16000")))
    timeout_segundos: int = field(default_factory=lambda: int(os.getenv("CONTRACTIA_TIMEOUT", "600")))
    max_reintentos_sdk: int = field(default_factory=lambda: int(os.getenv("CONTRACTIA_MAX_RETRIES", "4")))
    usar_fallbacks: bool = field(default_factory=lambda: os.getenv("CONTRACTIA_FALLBACKS", "1") == "1")

    # Effort por etapa (low | medium | high | xhigh | max)
    effort: dict = field(default_factory=lambda: {
        "seguridad": _env_effort("seguridad", "low"),
        "grafo": _env_effort("grafo", "low"),
        "jurista": _env_effort("jurista", "high"),
        "auditor": _env_effort("auditor", "high"),
        "cronista": _env_effort("cronista", "medium"),
        "chat": _env_effort("chat", "medium"),
    })

    # Enviar el contrato completo (cacheado) como referencia a los 3 agentes.
    # Mejora la verificación de referencias cruzadas; aumenta el costo por ejecución.
    contrato_como_referencia: bool = field(default_factory=lambda: os.getenv("CONTRACTIA_CONTRATO_REFERENCIA", "0") == "1")

    # Concurrencia
    max_concurrencia_grafo: int = field(default_factory=lambda: int(os.getenv("CONTRACTIA_CONC_GRAFO", "5")))
    max_concurrencia_auditoria: int = field(default_factory=lambda: int(os.getenv("CONTRACTIA_CONC_AUDITORIA", "3")))

    # Paths
    ruta_contrato: str = field(default_factory=lambda: os.getenv("CONTRACTIA_RUTA_CONTRATO", "contrato_nuevo"))
    reporte_md: str = field(default_factory=lambda: os.getenv("CONTRACTIA_REPORTE", "informe_auditoria_contrato.md"))
    cache_dir: str = field(default_factory=lambda: os.getenv("CONTRACTIA_CACHE_DIR", "cache"))
    log_file: str = field(default_factory=lambda: os.getenv("CONTRACTIA_LOG_FILE", "contractia.log"))

    # Feature flags
    enable_llm: bool = field(default_factory=lambda: os.getenv("CONTRACTIA_ENABLE_LLM", "1") == "1")
    usar_cache: bool = field(default_factory=lambda: os.getenv("CONTRACTIA_USAR_CACHE", "1") == "1")


CFG = Config()


def actualizar_config(**cambios) -> None:
    """Modifica CFG en sitio (todos los módulos comparten la misma instancia)."""
    validos = {f.name for f in fields(Config)}
    for k, v in cambios.items():
        if k not in validos:
            raise KeyError(f"Campo de configuración desconocido: {k}")
        object.__setattr__(CFG, k, v)


# Constantes de schema del grafo (no son configurables — son parte del contrato del modelo)
RELACIONES_VALIDAS: frozenset[str] = frozenset({
    "REFERENCIA_A", "ESTABLECE_PLAZO", "MODIFICA_A", "DEPENDE_DE", "OBLIGA_A"
})
TIPOS_NODO_VALIDOS: frozenset[str] = frozenset({
    "Cláusula", "Plazo", "Rol", "Entregable", "Penalidad"
})
