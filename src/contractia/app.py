"""Interfaz web de ContractIA: subir el contrato y ejecutar el pipeline paso a paso.

Ejecutar con:  streamlit run src/contractia/app.py
"""
from __future__ import annotations

import hashlib
import os
import time
from collections import Counter

import streamlit as st

from contractia.auditoria import (
    EstadoAuditoria,
    etapa_agentes,
    etapa_grafo,
    etapa_indices,
    etapa_seguridad,
    etapa_sintesis,
)
from contractia.carga import construir_pdf_visual, procesar_documentos_carpeta
from contractia.chat import INSTRUCCIONES_CHAT, construir_consulta
from contractia.config import CFG
from contractia.indices import NO_RECONOCIDO, OK
from contractia.informe import render_auditoria_markdown
from contractia.llm import ClienteClaude
from contractia.logging_setup import setup_logger
from contractia.metricas import calcular_metricas_grafo, render_metricas_grafo_md

# Costo medido en la estimación de referencia (Opus 5.5, ~220.000 tokens): unos $38 por auditoría completa.
_USD_POR_1K_TOKENS_REF = 38 / 220

ETAPAS = [
    ("carga", "Cargar contrato", "Lee los PDF/DOCX y detecta las páginas con tablas o escaneadas.", False),
    ("indices", "Revisión de índices", "Detecta capítulos, cláusulas y anexos; adapta el formato si hace falta.", False),
    ("seguridad", "Escaneo de seguridad", "Busca instrucciones ocultas dirigidas a la IA en cada sección.", True),
    ("grafo", "Grafo de conocimiento", "Extrae las relaciones entre cláusulas, plazos, roles y penalidades.", True),
    ("agentes", "Auditoría por agentes", "Jurista, auditor y cronista revisan cada sección con el contrato como referencia.", True),
    ("sintesis", "Síntesis global", "Revisión del contrato completo: problemas globales, ausencias y falsos positivos.", True),
    ("informe", "Informe", "Genera el informe final en Markdown.", False),
]
ICONOS = {"hecha": "✅", "actual": "▶️", "pendiente": "⏳", "bloqueada": "⛔"}
SEVERIDADES = ["CRITICA", "ALTA", "MEDIA", "BAJA"]


# ---------------------------------------------------------------- estado de sesión
def _iniciar_sesion() -> None:
    if "hechas" in st.session_state:
        return
    setup_logger()
    st.session_state.hechas = set()
    st.session_state.bloqueo = None
    st.session_state.llm = ClienteClaude()
    st.session_state.estado = None
    st.session_state.carga = None
    st.session_state.informe_md = None
    st.session_state.chat = []


def _reiniciar() -> None:
    for k in list(st.session_state.keys()):
        del st.session_state[k]


def _estado_etapa(i: int) -> str:
    clave = ETAPAS[i][0]
    if clave in st.session_state.hechas:
        return "hecha"
    if st.session_state.bloqueo is not None and i > st.session_state.bloqueo:
        return "bloqueada"
    previas = all(ETAPAS[j][0] in st.session_state.hechas for j in range(i))
    return "actual" if previas else "pendiente"


def _hay_credenciales() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


# ---------------------------------------------------------------- ejecución de etapas
def _guardar_subidos(archivos) -> str:
    h = hashlib.sha256()
    for a in archivos:
        h.update(a.name.encode())
        h.update(a.getbuffer())
    carpeta = os.path.join(CFG.cache_dir, "subidos", h.hexdigest()[:12])
    os.makedirs(carpeta, exist_ok=True)
    for a in archivos:
        with open(os.path.join(carpeta, os.path.basename(a.name)), "wb") as f:
            f.write(a.getbuffer())
    return carpeta


def _correr_carga(carpeta: str, usar_cache: bool) -> None:
    paginas, texto = procesar_documentos_carpeta(carpeta)
    if not texto:
        raise ValueError(f"No se encontró texto en PDF/DOCX dentro de '{carpeta}'.")
    pdf_visual, paginas_visuales = construir_pdf_visual(carpeta, CFG.max_paginas_visuales)
    st.session_state.carga = {
        "carpeta": carpeta,
        "archivos": sorted(f for f in os.listdir(carpeta) if f.lower().endswith((".pdf", ".docx"))),
        "paginas": len(paginas or []),
        "palabras": len(texto.split()),
        "tokens_estimados": int(len(texto) / 3.5),
        "paginas_visuales": paginas_visuales,
        "texto": texto,
    }
    st.session_state.estado = EstadoAuditoria(
        llm=st.session_state.llm, cache_dir=CFG.cache_dir, usar_cache=usar_cache, pdf_visual=pdf_visual,
    )


def _ejecutar(i: int) -> None:
    clave = ETAPAS[i][0]
    estado: EstadoAuditoria | None = st.session_state.estado
    # Volver a ejecutar una etapa invalida las siguientes.
    st.session_state.hechas -= {ETAPAS[j][0] for j in range(i, len(ETAPAS))}
    if st.session_state.bloqueo is not None and st.session_state.bloqueo >= i:
        st.session_state.bloqueo = None
    st.session_state.informe_md = None
    if clave == "indices":
        if etapa_indices(estado, st.session_state.carga["texto"]).estado == NO_RECONOCIDO:
            st.session_state.bloqueo = i
    elif clave == "seguridad":
        if not etapa_seguridad(estado):
            st.session_state.bloqueo = i
    elif clave == "grafo":
        etapa_grafo(estado)
    elif clave == "agentes":
        barra = st.progress(0.0, text="Preparando la primera sección (escribe el contrato en caché)...")

        def _avance(hechas: int, total: int, n_hallazgos: int) -> None:
            barra.progress(hechas / total, text=f"{hechas} de {total} secciones · {n_hallazgos} hallazgos")

        etapa_agentes(estado, _avance)
    elif clave == "sintesis":
        etapa_sintesis(estado)
    elif clave == "informe":
        st.session_state.informe_md = _armar_informe(estado)
    st.session_state.hechas.add(clave)


def _armar_informe(estado: EstadoAuditoria) -> str:
    resultado = estado.resultado()
    resultado["paginas_visuales"] = st.session_state.carga.get("paginas_visuales", [])
    md = render_auditoria_markdown(resultado)
    if estado.grafo is not None:
        md += "\n\n" + render_metricas_grafo_md(calcular_metricas_grafo(estado.grafo, estado.mapa_clausula_a_seccion))
    md += f"\n\n---\n*Modelo: {CFG.modelo} · Generado: {time.strftime('%Y-%m-%d %H:%M')}*\n\n"
    md += f"## Uso de Tokens\n\n{estado.llm.tokens.resumen()}\n"
    ruta = os.path.join(CFG.cache_dir, f"informe_{os.path.basename(st.session_state.carga['carpeta'])}.md")
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(md)
    return md


def _boton_ejecutar(i: int, deshabilitado: bool = False) -> None:
    clave, nombre, _, usa_modelo = ETAPAS[i]
    etiqueta = "Volver a ejecutar" if clave in st.session_state.hechas else "Ejecutar"
    if usa_modelo and not _hay_credenciales():
        st.caption("No se detectó ANTHROPIC_API_KEY; ingrésala en la barra lateral si la llamada falla.")
    if st.button(f"{etiqueta}: {nombre}", key=f"btn_{clave}", type="primary", disabled=deshabilitado):
        try:
            with st.spinner(f"Ejecutando {nombre.lower()}..."):
                _ejecutar(i)
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"La etapa falló: {e}")


# ---------------------------------------------------------------- vistas de resultados
def _vista_carga() -> None:
    c = st.session_state.carga
    col = st.columns(4)
    col[0].metric("Archivos", len(c["archivos"]))
    col[1].metric("Páginas", f"{c['paginas']:,}")
    col[2].metric("Palabras", f"{c['palabras']:,}")
    col[3].metric("Tokens (aprox.)", f"{c['tokens_estimados']:,}")
    st.caption(" · ".join(c["archivos"]))
    if c["paginas_visuales"]:
        with st.expander(f"{len(c['paginas_visuales'])} páginas se enviarán también como imagen (tablas o escaneos)"):
            st.write(", ".join(c["paginas_visuales"]))
    costo = c["tokens_estimados"] / 1000 * _USD_POR_1K_TOKENS_REF
    st.info(
        f"Costo estimado de la auditoría completa con Opus 5.5: **\\${costo * 0.7:,.0f} – \\${costo * 1.4:,.0f}**. "
        "La revisión de índices es gratuita; el costo empieza en el escaneo de seguridad."
    )


def _vista_indices() -> None:
    rev = st.session_state.estado.revision
    texto_estado = {OK: st.success, NO_RECONOCIDO: st.error}.get(rev.estado, st.warning)
    texto_estado({
        OK: "Índices completos.",
        NO_RECONOCIDO: "No se reconoció la estructura del contrato. La auditoría no puede continuar.",
    }.get(rev.estado, "Índices con observaciones: revísalas antes de continuar."))
    col = st.columns(3)
    col[0].metric("Capítulos", len(rev.capitulos))
    col[1].metric("Anexos", len(rev.anexos))
    col[2].metric("Cláusulas", rev.n_clausulas)
    if rev.normalizado:
        st.info(f"El formato se adaptó al estándar ({rev.cambios_formato} cambios en encabezados y numeración).")
        with st.expander("Ver cambios de formato"):
            st.dataframe(
                [{"Antes": a, "Después": d} for a, d in st.session_state.estado.cambios_formato],
                width="stretch", hide_index=True,
            )
            st.download_button("Descargar texto adaptado", st.session_state.estado.texto,
                               file_name="contrato_normalizado.txt")
    if rev.problemas:
        with st.expander(f"Observaciones ({len(rev.problemas)})", expanded=rev.estado != OK):
            for p in rev.problemas:
                st.markdown(f"- {p}")
    if rev.capitulos:
        with st.expander("Capítulos y cláusulas detectados"):
            st.dataframe(
                [{"Capítulo": c["titulo"], "Cláusulas": c["n_clausulas"], "Faltantes": ", ".join(c["faltantes"])}
                 for c in rev.capitulos],
                width="stretch", hide_index=True,
            )
    if rev.anexos:
        with st.expander("Anexos detectados"):
            st.write("\n".join(f"- {a['titulo']}" for a in rev.anexos))


def _vista_seguridad() -> None:
    e = st.session_state.estado
    if e.es_seguro:
        st.success("Documento limpio: no se encontraron instrucciones ocultas.")
    else:
        st.error(f"Alerta de seguridad. La auditoría se detuvo.\n\n**Evidencia:** {e.evidencia_seguridad}")


def _vista_grafo() -> None:
    e = st.session_state.estado
    m = calcular_metricas_grafo(e.grafo, e.mapa_clausula_a_seccion)
    col = st.columns(4)
    col[0].metric("Nodos", f"{m['n_nodos']:,}")
    col[1].metric("Relaciones", f"{m['n_aristas']:,}")
    col[2].metric("Cobertura de cláusulas", f"{m['cobertura_pct']}%")
    col[3].metric("Cláusulas inventadas", m["n_inventadas"])
    if e.ruta_grafo_png and os.path.exists(e.ruta_grafo_png):
        st.image(e.ruta_grafo_png, width="stretch")
    if m["distribucion_relaciones"]:
        with st.expander("Relaciones por tipo"):
            st.bar_chart(m["distribucion_relaciones"], horizontal=True)


def _filas_hallazgos(resultados: list[dict]) -> list[dict]:
    return [
        {"Sección": r["seccion"], "Cláusula": h.get("clausula_afectada", ""), "Tipo": h.get("tipo", ""),
         "Severidad": h.get("severidad", ""), "Problema": h.get("explicacion", ""), "Cita": h.get("cita", "")}
        for r in resultados for h in r.get("hallazgos", [])
    ]


def _vista_agentes() -> None:
    filas = _filas_hallazgos(st.session_state.estado.resultados_auditoria or [])
    conteo = Counter(f["Severidad"] for f in filas)
    col = st.columns(5)
    col[0].metric("Hallazgos", len(filas))
    for c, sev in zip(col[1:], SEVERIDADES, strict=True):
        c.metric(sev.capitalize(), conteo.get(sev, 0))
    if filas:
        sel = st.multiselect("Severidad", SEVERIDADES, default=SEVERIDADES, key="filtro_sev")
        st.dataframe([f for f in filas if f["Severidad"] in sel or f["Severidad"] not in SEVERIDADES],
                     width="stretch", hide_index=True)


def _vista_sintesis() -> None:
    s = st.session_state.estado.sintesis
    if not s:
        st.warning("La síntesis global no se pudo generar; revisa el log.")
        return
    st.markdown(s.get("resumen_ejecutivo", ""))
    for clave, titulo in [("hallazgos_globales", "Hallazgos globales"), ("ausencias", "Elementos ausentes o incompletos")]:
        items = s.get(clave) or []
        with st.expander(f"{titulo} ({len(items)})", expanded=bool(items)):
            if items:
                st.dataframe(
                    [{"Cláusula": h.get("clausula_afectada"), "Tipo": h.get("tipo"), "Severidad": h.get("severidad"),
                      "Problema": h.get("explicacion"), "Cita": h.get("cita")} for h in items],
                    width="stretch", hide_index=True,
                )
            else:
                st.caption("Sin elementos.")
    observados = s.get("hallazgos_observados") or []
    if observados:
        with st.expander(f"Posibles duplicados o falsos positivos ({len(observados)})"):
            st.dataframe(observados, width="stretch", hide_index=True)


def _vista_informe() -> None:
    md = st.session_state.informe_md
    st.download_button("Descargar informe (.md)", md, file_name="informe_auditoria_contrato.md", type="primary")
    with st.expander("Vista previa", expanded=True):
        st.markdown(md)


VISTAS = {
    "carga": _vista_carga, "indices": _vista_indices, "seguridad": _vista_seguridad, "grafo": _vista_grafo,
    "agentes": _vista_agentes, "sintesis": _vista_sintesis, "informe": _vista_informe,
}


# ---------------------------------------------------------------- página
def _barra_lateral() -> None:
    with st.sidebar:
        st.markdown("### Progreso")
        for i, (_, nombre, _, _) in enumerate(ETAPAS):
            st.markdown(f"{ICONOS[_estado_etapa(i)]} {i + 1}. {nombre}")
        st.divider()
        st.markdown(f"**Modelo:** `{CFG.modelo}`")
        if _hay_credenciales():
            st.caption("API key detectada en el entorno (.env).")
        else:
            clave = st.text_input("ANTHROPIC_API_KEY", type="password",
                                  help="Solo se usa en esta sesión; no se guarda en disco.")
            if clave:
                os.environ["ANTHROPIC_API_KEY"] = clave
                st.rerun()
        llm: ClienteClaude = st.session_state.llm
        costo = llm.tokens.costo_usd(CFG.modelo)
        st.metric("Costo acumulado", f"${costo:,.2f}" if costo is not None else "n/d")
        if llm.tokens.por_etiqueta:
            with st.expander("Tokens por etapa"):
                st.dataframe(
                    [{"Etapa": k, "Llamadas": v["calls"], "Entrada": v["input"], "Caché (lectura)": v["cache_read"],
                      "Salida": v["output"], "USD": round(llm.tokens.costo_usd(CFG.modelo, k) or 0, 2)}
                     for k, v in sorted(llm.tokens.por_etiqueta.items())],
                    hide_index=True,
                )
        st.divider()
        if st.button("Nueva auditoría", width="stretch"):
            _reiniciar()
            st.rerun()


def _paso_carga() -> None:
    hecha = "carga" in st.session_state.hechas
    if not hecha:
        origen = st.radio("Origen", ["Subir archivos", "Carpeta local"], horizontal=True, label_visibility="collapsed")
        usar_cache = st.checkbox("Reutilizar resultados guardados en caché (grafo, hallazgos, síntesis)", value=True)
        if origen == "Subir archivos":
            archivos = st.file_uploader("Contrato (PDF o DOCX; puede ser más de un archivo)",
                                        type=["pdf", "docx"], accept_multiple_files=True)
            carpeta = None
        else:
            carpeta = st.text_input("Carpeta con el contrato", value=CFG.ruta_contrato)
            archivos = None
        if st.button("Cargar contrato", type="primary", disabled=not (archivos or carpeta)):
            try:
                with st.spinner("Leyendo el contrato y detectando páginas con tablas..."):
                    _correr_carga(_guardar_subidos(archivos) if archivos else carpeta, usar_cache)
                st.session_state.hechas.add("carga")
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"No se pudo cargar el contrato: {e}")
    else:
        VISTAS["carga"]()


def _pestana_auditoria() -> None:
    for i, (clave, nombre, descripcion, usa_modelo) in enumerate(ETAPAS):
        est = _estado_etapa(i)
        with st.container(border=True):
            cabecera = f"{ICONOS[est]} **{i + 1}. {nombre}**" + (" · usa el modelo" if usa_modelo else " · sin costo")
            st.markdown(cabecera)
            st.caption(descripcion)
            if clave == "carga":
                _paso_carga()
                continue
            if est == "hecha":
                VISTAS[clave]()
                if st.session_state.bloqueo in (None, i):
                    _boton_ejecutar(i)
            elif est == "actual":
                _boton_ejecutar(i)
            elif est == "bloqueada":
                st.caption("Bloqueada por el resultado de una etapa anterior.")

    pendientes = [i for i in range(1, len(ETAPAS)) if _estado_etapa(i) in ("actual", "pendiente")]
    hay_restantes = "carga" in st.session_state.hechas and pendientes and st.session_state.bloqueo is None
    if hay_restantes and st.button(f"Ejecutar las {len(pendientes)} etapas restantes", width="stretch"):
        try:
            for i in pendientes:
                with st.spinner(f"{ETAPAS[i][1]}..."):
                    _ejecutar(i)
                if st.session_state.bloqueo is not None:
                    break
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"La ejecución se detuvo en «{ETAPAS[i][1]}»: {e}")


def _pestana_chat() -> None:
    e: EstadoAuditoria | None = st.session_state.estado
    if e is None or e.grafo is None:
        st.info("El chat se habilita cuando el grafo de conocimiento (etapa 4) está construido.")
        return
    st.caption("Las respuestas se basan solo en las partes del contrato relacionadas con la pregunta.")
    for rol, texto in st.session_state.chat:
        with st.chat_message(rol):
            st.markdown(texto)
    pregunta = st.chat_input("Pregunta sobre el contrato")
    if pregunta:
        with st.chat_message("user"):
            st.markdown(pregunta)
        historial = [(st.session_state.chat[k][1], st.session_state.chat[k + 1][1])
                     for k in range(0, len(st.session_state.chat) - 1, 2)]
        contenido = construir_consulta(pregunta, e.grafo, e.secciones, e.indice_secciones, e.indice_nodos_grafo, historial)
        with st.chat_message("assistant"):
            try:
                respuesta = st.write_stream(e.llm.texto_en_streaming("chat", INSTRUCCIONES_CHAT, contenido))
            except Exception as ex:  # noqa: BLE001
                respuesta = f"No se pudo responder: {ex}"
                st.error(respuesta)
        st.session_state.chat += [("user", pregunta), ("assistant", respuesta)]


def main() -> None:
    st.set_page_config(page_title="ContractIA", page_icon="⚖️", layout="wide")
    _iniciar_sesion()
    st.title("ContractIA")
    st.caption("Auditoría de contratos con agentes especializados, grafo de conocimiento y Claude.")
    _barra_lateral()
    tab_auditoria, tab_chat = st.tabs(["Auditoría paso a paso", "Chat sobre el contrato"])
    with tab_auditoria:
        _pestana_auditoria()
    with tab_chat:
        _pestana_chat()


main()
