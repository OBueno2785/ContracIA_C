"""Revisión de índices (primera fase): capítulos, cláusulas y anexos detectados por el regex.

Si la revisión encuentra problemas, intenta adaptar el formato del contrato al estándar
(`normalizacion`) y se queda con la versión que mejor reconoce el regex.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .logging_setup import get_logger
from .normalizacion import marcar_indices, normalizar_formato
from .segmentacion import (
    _extraer_num_anexo,
    _extraer_num_cap,
    _extraer_numeros_clausula,
    _roman_to_int,
    _validar_secuencia_clausulas,
    crear_indice_global_clausulas,
    separar_en_secciones,
)

log = get_logger("indices")

OK = "OK"
CON_OBSERVACIONES = "CON_OBSERVACIONES"
NO_RECONOCIDO = "NO_RECONOCIDO"


@dataclass
class ReporteIndices:
    estado: str
    capitulos: list[dict] = field(default_factory=list)
    anexos: list[dict] = field(default_factory=list)
    n_clausulas: int = 0
    n_primer_nivel: int = 0
    n_subniveles: int = 0
    problemas: list[str] = field(default_factory=list)
    normalizado: bool = False
    cambios_formato: int = 0
    lineas_indice: int = 0
    entradas_indice: int = 0
    ejemplos_cambios: list[tuple[str, str]] = field(default_factory=list)

    def puntaje(self) -> int:
        if self.estado == NO_RECONOCIDO:
            return -1
        return 10 * (len(self.capitulos) + len(self.anexos)) + self.n_clausulas - 5 * len(self.problemas)

    def to_dict(self) -> dict:
        return asdict(self)


def _a_int(num: str) -> int:
    if num.isdigit():
        return int(num)
    return _roman_to_int(num)


def _es_primer_nivel(cid: str) -> bool:
    # 3.5 es de primer nivel; 3.5.2 (o 3.5.a) es una subcláusula.
    return len(cid.split(".")) == 2


def _huecos(numeros: list[int]) -> list[int]:
    if not numeros:
        return []
    return sorted(set(range(1, max(numeros) + 1)) - set(numeros))


def revisar_indices(texto: str) -> tuple[list[dict], ReporteIndices]:
    secciones = separar_en_secciones(texto)
    capitulos = [s for s in secciones if s["tipo"] == "CAPITULO" and _extraer_num_cap(s["titulo"]) != "?"]
    if not capitulos:
        return secciones, ReporteIndices(
            estado=NO_RECONOCIDO,
            problemas=["No se reconoció ningún encabezado de capítulo con el formato 'Capítulo <número> <TÍTULO>'."],
        )

    problemas: list[str] = []
    caps_info: list[dict] = []
    for s in capitulos:
        num = _extraer_num_cap(s["titulo"])
        n = _a_int(num)
        ids = _extraer_numeros_clausula(s["contenido"], str(n)) if n else []
        _, faltantes, _ = _validar_secuencia_clausulas(ids, str(n)) if ids else (True, [], 0)
        primer_nivel = sum(_es_primer_nivel(c) for c in ids)
        caps_info.append({
            "n": num, "titulo": s["titulo"], "n_clausulas": len(ids),
            "n_primer_nivel": primer_nivel, "n_subniveles": len(ids) - primer_nivel, "faltantes": faltantes,
        })
        if not ids:
            problemas.append(f"{s['titulo']}: sin cláusulas numeradas como {n}.x")
        elif faltantes:
            problemas.append(f"{s['titulo']}: faltan las cláusulas {', '.join(faltantes)}")

    huecos_caps = _huecos([_a_int(c["n"]) for c in caps_info if _a_int(c["n"])])
    if huecos_caps:
        problemas.append("Capítulos no detectados: " + ", ".join(str(h) for h in huecos_caps))

    anexos = [s for s in secciones if s["tipo"] == "ANEXO"]
    anexos_info = [{"n": _extraer_num_anexo(s["titulo"]), "titulo": s["titulo"]} for s in anexos]
    huecos_anexos = _huecos([int(a["n"]) for a in anexos_info if a["n"].isdigit()])
    if huecos_anexos:
        problemas.append("Anexos no detectados: " + ", ".join(str(h) for h in huecos_anexos))

    indice_global = crear_indice_global_clausulas(secciones)
    primer_nivel = sum(_es_primer_nivel(c) for c in indice_global)
    return secciones, ReporteIndices(
        estado=CON_OBSERVACIONES if problemas else OK,
        capitulos=caps_info,
        anexos=anexos_info,
        n_clausulas=len(indice_global),
        n_primer_nivel=primer_nivel,
        n_subniveles=len(indice_global) - primer_nivel,
        problemas=problemas,
    )


def _contrastar_con_indice(reporte: ReporteIndices, entradas: list[tuple[str, str]]) -> None:
    """Agrega como observación lo que el índice lista y no aparece en el cuerpo."""
    if reporte.estado == NO_RECONOCIDO or not entradas:
        return
    caps_cuerpo = {_a_int(c["n"]) for c in reporte.capitulos}
    anexos_cuerpo = {a["n"].upper() for a in reporte.anexos}
    faltan_caps = sorted({int(n) for t, n in entradas if t == "CAPITULO"} - caps_cuerpo)
    faltan_anexos = sorted({n for t, n in entradas if t == "ANEXO"} - anexos_cuerpo,
                           key=lambda x: (not x.isdigit(), int(x) if x.isdigit() else 0, x))
    if faltan_caps:
        reporte.problemas.append("Capítulos listados en el índice pero no encontrados en el cuerpo: "
                                 + ", ".join(str(n) for n in faltan_caps))
    if faltan_anexos:
        reporte.problemas.append("Anexos listados en el índice pero no encontrados en el cuerpo: "
                                 + ", ".join(faltan_anexos))
    if reporte.problemas and reporte.estado == OK:
        reporte.estado = CON_OBSERVACIONES


def preparar_contrato(texto: str) -> tuple[str, list[dict], ReporteIndices, list[tuple[str, str]]]:
    """Revisa los índices y, si hay problemas, prueba el texto normalizado.

    La tabla de contenidos y los listados de anexos se neutralizan antes, para que capítulos,
    cláusulas y anexos se detecten solo en el cuerpo del contrato.
    Devuelve (texto usado, secciones, reporte, cambios de formato aplicados).
    """
    log.info("FASE 0: Revisión de índices (capítulos, cláusulas y anexos)...")
    texto, entradas_indice, lineas_indice = marcar_indices(texto)
    if lineas_indice:
        log.info("  Tabla de contenidos: %d líneas ignoradas (%d capítulos/anexos listados).",
                 lineas_indice, len(entradas_indice))

    texto_usado, secciones, reporte, cambios = _elegir_formato(texto)
    reporte.lineas_indice = lineas_indice
    reporte.entradas_indice = len(entradas_indice)
    _contrastar_con_indice(reporte, entradas_indice)
    return texto_usado, secciones, reporte, cambios


def _elegir_formato(texto: str) -> tuple[str, list[dict], ReporteIndices, list[tuple[str, str]]]:
    secciones, reporte = revisar_indices(texto)
    log.info(
        "  Formato original: %s — %d capítulos, %d anexos, %d cláusulas, %d observaciones.",
        reporte.estado, len(reporte.capitulos), len(reporte.anexos), reporte.n_clausulas, len(reporte.problemas),
    )
    if reporte.estado == OK:
        return texto, secciones, reporte, []

    texto_norm, cambios = normalizar_formato(texto)
    if not cambios:
        log.info("  No hay encabezados que adaptar al formato estándar.")
        return texto, secciones, reporte, []

    secciones_n, reporte_n = revisar_indices(texto_norm)
    log.info(
        "  Formato adaptado (%d cambios): %s — %d capítulos, %d anexos, %d cláusulas, %d observaciones.",
        len(cambios), reporte_n.estado, len(reporte_n.capitulos), len(reporte_n.anexos),
        reporte_n.n_clausulas, len(reporte_n.problemas),
    )
    if reporte_n.puntaje() > reporte.puntaje():
        reporte_n.normalizado = True
        reporte_n.cambios_formato = len(cambios)
        reporte_n.ejemplos_cambios = cambios[:15]
        return texto_norm, secciones_n, reporte_n, cambios

    log.info("  El formato adaptado no mejora la detección; se usa el original.")
    return texto, secciones, reporte, []
