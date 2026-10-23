"""Schemas Pydantic para structured output (P1 4.2)."""
from __future__ import annotations

from pydantic import BaseModel, Field


# ----- Hallazgos de auditoría -----
class Hallazgo(BaseModel):
    clausula_afectada: str = Field(default="General")
    tipo: str = Field(default="ERROR")
    cita: str = Field(default="")
    explicacion: str = Field(default="")
    severidad: str = Field(default="MEDIA")


class RespuestaJurista(BaseModel):
    hay_inconsistencias: bool = False
    hallazgos: list[Hallazgo] = Field(default_factory=list)


class RespuestaAuditor(BaseModel):
    hay_inconsistencias: bool = False
    hallazgos: list[Hallazgo] = Field(default_factory=list)


class RespuestaCronista(BaseModel):
    hay_procedimientos: bool = False
    hay_errores_logicos: bool = False
    hay_inconsistencia_plazos: bool = False
    hallazgos_procesos: list[Hallazgo] = Field(default_factory=list)


# ----- Seguridad -----
class RespuestaSeguridad(BaseModel):
    es_seguro: bool = True
    evidencia: str = "Ninguna"


# ----- Extracción del grafo -----
class TripletaGrafo(BaseModel):
    origen: str = Field(description="Entidad origen (ej. 'Cláusula 5.1')")
    relacion: str = Field(description="Una de: REFERENCIA_A, ESTABLECE_PLAZO, MODIFICA_A, DEPENDE_DE, OBLIGA_A")
    destino: str = Field(description="Entidad destino")
    contexto: str = Field(default="", description="Una frase corta sobre el motivo")
    tipo_origen: str = Field(default="", description="Una de: Cláusula, Plazo, Rol, Entregable, Penalidad")
    tipo_destino: str = Field(default="", description="Una de: Cláusula, Plazo, Rol, Entregable, Penalidad")


class RespuestaExtraccion(BaseModel):
    tripletas: list[TripletaGrafo] = Field(default_factory=list)


# ----- Síntesis global -----
class HallazgoObservado(BaseModel):
    seccion: str = Field(default="", description="Sección donde se reportó el hallazgo observado")
    clausula_afectada: str = Field(default="General")
    motivo: str = Field(default="", description="Por qué parece duplicado o falso positivo, citando el contrato")


class RespuestaSintesis(BaseModel):
    resumen_ejecutivo: str = Field(default="", description="Panorama del contrato y de sus problemas principales")
    hallazgos_globales: list[Hallazgo] = Field(
        default_factory=list,
        description="Problemas que solo se ven mirando el contrato completo (contradicciones entre capítulos, reparto de riesgos, coherencia económica)",
    )
    ausencias: list[Hallazgo] = Field(
        default_factory=list,
        description="Elementos ausentes o incompletos (procedimientos sin plazo, obligaciones sin consecuencia, remisiones a anexos que no existen)",
    )
    hallazgos_observados: list[HallazgoObservado] = Field(
        default_factory=list,
        description="Hallazgos de los agentes que parecen duplicados o falsos positivos",
    )
