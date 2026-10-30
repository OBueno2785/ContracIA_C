"""Interfaz web de ContractIA: subir el contrato y ejecutar el pipeline paso a paso.

Ejecutar con:  streamlit run src/contractia/app.py
El aspecto (colores, tipografía, radios) vive en .streamlit/config.toml.
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

# Estimación de referencia (Opus 5.5, ~220.000 tokens): unos $38 por auditoría completa,
# repartidos así entre las etapas que usan el modelo.
_USD_POR_1K_TOKENS_REF = 38 / 220
_PARTE_DEL_COSTO = {"seguridad": 0.033, "grafo": 0.098, "agentes": 0.842, "sintesis": 0.027}

# clave, nombre, descripción, usa el modelo, ícono
ETAPAS = [
    ("carga", "Cargar contrato", "Lee los PDF/DOCX y detecta las páginas con tablas o escaneadas.", False, ":material/upload_file:"),
    ("indices", "Revisión de índices", "Detecta capítulos, cláusulas y anexos antes de llamar al modelo; adapta el formato si hace falta.", False, ":material/format_list_numbered:"),
    ("seguridad", "Escaneo de seguridad", "Busca instrucciones ocultas dirigidas a la IA en cada sección y en las páginas escaneadas.", True, ":material/shield:"),
    ("grafo", "Grafo de conocimiento", "Extrae las relaciones entre cláusulas, plazos, roles y penalidades.", True, ":material/hub:"),
    ("agentes", "Auditoría por agentes", "Jurista, auditor y cronista revisan cada sección con el contrato completo como referencia.", True, ":material/groups:"),
    ("sintesis", "Síntesis global", "Revisión del contrato completo: problemas globales, ausencias y posibles falsos positivos.", True, ":material/summarize:"),
    ("informe", "Informe", "Genera el informe final en Markdown, listo para descargar.", False, ":material/description:"),
]
ESTADO_VISUAL = {
    # icono del paso, color del texto, badge
    "hecha": (":green[:material/check_circle:]", "", ("Completada", ":material/check:", "green")),
    "actual": (":orange[:material/radio_button_checked:]", "", ("Siguiente paso", ":material/arrow_forward:", "orange")),
    "pendiente": (":gray[:material/radio_button_unchecked:]", "gray", ("Pendiente", ":material/schedule:", "gray")),
    "bloqueada": (":red[:material/block:]", "gray", ("Bloqueada", ":material/block:", "red")),
}
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


def _miles(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _costo_total_estimado() -> float | None:
    carga = st.session_state.carga
    return carga["tokens_estimados"] / 1000 * _USD_POR_1K_TOKENS_REF if carga else None


def _costo_etapa(clave: str) -> str:
    total = _costo_total_estimado()
    if clave not in _PARTE_DEL_COSTO:
        return "Sin costo"
    if total is None:
        return "Usa el modelo"
    return f"Usa el modelo · ≈ \\${max(total * _PARTE_DEL_COSTO[clave], 0.5):,.0f}"


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


def _boton_ejecutar(i: int) -> None:
    clave, nombre, _, usa_modelo, icono = ETAPAS[i]
    repetir = clave in st.session_state.hechas
    with st.container(horizontal=True, vertical_alignment="center"):
        pulsado = st.button(
            f"{'Volver a ejecutar' if repetir else 'Ejecutar'}: {nombre}",
            key=f"btn_{clave}", type="secondary" if repetir else "primary",
            icon=":material/replay:" if repetir else icono,
        )
        if not repetir:
            st.caption(_costo_etapa(clave))
    if usa_modelo and not repetir and not _hay_credenciales():
        st.caption(":orange[:material/key:] No se detectó ANTHROPIC_API_KEY; puedes ingresarla en la barra lateral.")
    if pulsado:
        try:
            with st.spinner(f"Ejecutando {nombre.lower()}..."):
                _ejecutar(i)
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(str(e), title=f"Falló «{nombre}»", icon=":material/error:")


# ---------------------------------------------------------------- vistas de resultados
def _vista_carga() -> None:
    c = st.session_state.carga
    cols = st.columns(4)
    cols[0].metric("Archivos", len(c["archivos"]), border=True)
    cols[1].metric("Páginas", _miles(c["paginas"]), border=True)
    cols[2].metric("Palabras", _miles(c["palabras"]), border=True)
    cols[3].metric("Tokens (aprox.)", _miles(c["tokens_estimados"]), border=True)
    st.caption(":material/attach_file: " + " · ".join(c["archivos"]))
    if c["paginas_visuales"]:
        with st.expander(f"{len(c['paginas_visuales'])} páginas se enviarán también como imagen (tablas o escaneos)",
                         icon=":material/table_view:"):
            st.caption(", ".join(c["paginas_visuales"]))


def _vista_indices() -> None:
    estado = st.session_state.estado
    rev = estado.revision
    if rev.estado == NO_RECONOCIDO:
        st.error("No se detectaron capítulos con el formato «Capítulo <número> <TÍTULO>», ni siquiera tras adaptar "
                 "el formato. No se llamó al modelo.", title="Estructura no reconocida", icon=":material/error:")
    cols = st.columns(4)
    cols[0].metric("Capítulos", len(rev.capitulos), border=True)
    cols[1].metric("Anexos", len(rev.anexos), border=True)
    cols[2].metric("Cláusulas", rev.n_primer_nivel, border=True,
                   help="Primer nivel: 1.1, 3.5… (número del capítulo y un número más).")
    cols[3].metric("Subcláusulas", rev.n_subniveles, border=True,
                   help="Segundo nivel o más: 1.1.2, 2.1.4…")
    if getattr(rev, "lineas_indice", 0):
        st.caption(f":material/toc: Se ignoraron {rev.lineas_indice} líneas de la tabla de contenidos: la estructura "
                   "se detecta solo en el cuerpo del contrato.")
    if rev.normalizado:
        st.info(f"El formato se adaptó al estándar ({rev.cambios_formato} cambios en encabezados y numeración).",
                icon=":material/auto_fix_high:")
        with st.expander("Ver cambios de formato", icon=":material/compare_arrows:"):
            st.dataframe([{"Antes": a, "Después": d} for a, d in estado.cambios_formato], hide_index=True,
                         alt="Cambios de formato aplicados al contrato")
            st.download_button("Descargar texto adaptado", estado.texto, file_name="contrato_normalizado.txt",
                               icon=":material/download:")
    if rev.problemas:
        with st.expander(f"Observaciones ({len(rev.problemas)})", expanded=rev.estado != OK, icon=":material/warning:"):
            for p in rev.problemas:
                st.markdown(f"- {p}")
    if rev.capitulos:
        maximo = max(c["n_primer_nivel"] for c in rev.capitulos) or 1
        st.dataframe(
            [{"N.º": c["n"], "Capítulo": c["titulo"].split(" ", 2)[-1].capitalize(),
              "Cláusulas": c["n_primer_nivel"], "Subcláusulas": c["n_subniveles"],
              "Faltantes": ", ".join(c["faltantes"]) or "—"} for c in rev.capitulos],
            hide_index=True, height=min(38 * (len(rev.capitulos) + 1) + 2, 420),
            column_config={
                "N.º": st.column_config.TextColumn(width="small"),
                "Cláusulas": st.column_config.ProgressColumn(
                    format="%d", min_value=0, max_value=maximo, help="Primer nivel: 1.1, 3.5…"),
                "Subcláusulas": st.column_config.NumberColumn(
                    width="small", help="Segundo nivel o más: 1.1.2, 2.1.4…"),
            },
            alt="Capítulos detectados con su número de cláusulas",
        )
    if rev.anexos:
        with st.expander(f"Anexos detectados ({len(rev.anexos)})", icon=":material/folder_open:"):
            st.markdown("\n".join(f"- {a['titulo']}" for a in rev.anexos))


def _vista_seguridad() -> None:
    e = st.session_state.estado
    if e.es_seguro:
        st.success("No se encontraron instrucciones ocultas en el texto ni en las páginas escaneadas.",
                   title="Documento limpio", icon=":material/verified_user:")
    else:
        st.error(e.evidencia_seguridad, title="Alerta de seguridad: la auditoría se detuvo", icon=":material/gpp_bad:")


def _vista_grafo() -> None:
    e = st.session_state.estado
    m = calcular_metricas_grafo(e.grafo, e.mapa_clausula_a_seccion)
    cols = st.columns(4)
    cols[0].metric("Nodos", _miles(m["n_nodos"]), border=True)
    cols[1].metric("Relaciones", _miles(m["n_aristas"]), border=True)
    cols[2].metric("Cobertura de cláusulas", f"{m['cobertura_pct']}%", border=True)
    cols[3].metric("Cláusulas inventadas", m["n_inventadas"], border=True)
    if e.ruta_grafo_png and os.path.exists(e.ruta_grafo_png):
        with st.expander("Ver el grafo", icon=":material/hub:"):
            st.image(e.ruta_grafo_png, width="stretch", alt="Grafo de conocimiento del contrato")
    if m["distribucion_relaciones"]:
        with st.expander("Relaciones por tipo", icon=":material/bar_chart:"):
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
    cols = st.columns(5)
    cols[0].metric("Hallazgos", len(filas), border=True)
    for c, sev in zip(cols[1:], SEVERIDADES, strict=True):
        c.metric(sev.capitalize(), conteo.get(sev, 0), border=True)
    if filas:
        sel = st.pills("Severidad", SEVERIDADES, selection_mode="multi", default=SEVERIDADES, key="filtro_sev",
                       format_func=str.capitalize)
        st.dataframe([f for f in filas if f["Severidad"] in sel or f["Severidad"] not in SEVERIDADES],
                     hide_index=True, alt="Hallazgos de los agentes por sección")


def _vista_sintesis() -> None:
    s = st.session_state.estado.sintesis
    if not s:
        st.warning("La síntesis global no se pudo generar; revisa el log.", icon=":material/warning:")
        return
    with st.container(border=True):
        st.markdown("**:material/summarize: Resumen ejecutivo**")
        st.markdown(s.get("resumen_ejecutivo", ""))
    for clave, titulo, icono in [("hallazgos_globales", "Hallazgos globales", ":material/travel_explore:"),
                                 ("ausencias", "Elementos ausentes o incompletos", ":material/rule:")]:
        items = s.get(clave) or []
        with st.expander(f"{titulo} ({len(items)})", expanded=bool(items), icon=icono):
            if items:
                st.dataframe(
                    [{"Cláusula": h.get("clausula_afectada"), "Tipo": h.get("tipo"), "Severidad": h.get("severidad"),
                      "Problema": h.get("explicacion"), "Cita": h.get("cita")} for h in items],
                    hide_index=True, alt=titulo,
                )
            else:
                st.caption("Sin elementos.")
    observados = s.get("hallazgos_observados") or []
    if observados:
        with st.expander(f"Posibles duplicados o falsos positivos ({len(observados)})", icon=":material/flaky:"):
            st.dataframe(observados, hide_index=True, alt="Hallazgos observados por la síntesis")


def _vista_informe() -> None:
    md = st.session_state.informe_md
    st.download_button("Descargar informe (.md)", md, file_name="informe_auditoria_contrato.md", type="primary",
                       icon=":material/download:")
    with st.expander("Vista previa del informe", icon=":material/visibility:"):
        st.markdown(md)


VISTAS = {
    "carga": _vista_carga, "indices": _vista_indices, "seguridad": _vista_seguridad, "grafo": _vista_grafo,
    "agentes": _vista_agentes, "sintesis": _vista_sintesis, "informe": _vista_informe,
}


def _resumen_etapa(clave: str) -> str:
    """Una línea con el resultado de una etapa completada (se muestra en la tarjeta plegada)."""
    e: EstadoAuditoria | None = st.session_state.estado
    c = st.session_state.carga
    if clave == "carga":
        return f"{' · '.join(c['archivos'])} · {_miles(c['paginas'])} páginas · {len(c['paginas_visuales'])} con tablas o escaneadas"
    if clave == "indices":
        r = e.revision
        return (f"{len(r.capitulos)} capítulos · {len(r.anexos)} anexos · {r.n_primer_nivel} cláusulas · "
                f"{r.n_subniveles} subcláusulas")
    if clave == "seguridad":
        return "Documento limpio" if e.es_seguro else "Alerta de seguridad"
    if clave == "grafo":
        return f"{_miles(e.grafo.number_of_nodes())} nodos · {_miles(e.grafo.number_of_edges())} relaciones"
    if clave == "agentes":
        return f"{len(_filas_hallazgos(e.resultados_auditoria or []))} hallazgos"
    if clave == "sintesis":
        s = e.sintesis or {}
        return f"{len(s.get('hallazgos_globales') or [])} hallazgos globales · {len(s.get('ausencias') or [])} ausencias"
    return "Informe listo para descargar"


# ---------------------------------------------------------------- página
def _etiqueta_costo_corta(clave: str) -> str:
    texto = _costo_etapa(clave)
    return "gratis" if texto == "Sin costo" else texto.replace("Usa el modelo · ", "").replace("Usa el modelo", "modelo")


def _barra_lateral() -> None:
    with st.sidebar:
        st.markdown("## :orange[:material/balance:] ContractIA")
        st.caption("Auditoría contractual con agentes y Claude")

        hechas = len(st.session_state.hechas)
        st.progress(hechas / len(ETAPAS), text=f"{hechas} de {len(ETAPAS)} etapas completadas")
        for i, (clave, nombre, _, _, _) in enumerate(ETAPAS):
            est = _estado_etapa(i)
            icono, color, _ = ESTADO_VISUAL[est]
            texto = f"**{nombre}**" if est == "actual" else nombre
            if color:
                texto = f":{color}[{texto}]"
            with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center", gap="small"):
                st.markdown(f"{icono} {texto}")
                st.caption(_etiqueta_costo_corta(clave), width="content")

        c = st.session_state.carga
        if c:
            with st.container(border=True):
                st.caption(":orange[:material/payments:] **Costo estimado**")
                total = _costo_total_estimado()
                st.markdown(f"### \\${total * 0.7:,.0f} – \\${total * 1.4:,.0f}")
                st.caption(f"Auditoría completa con {CFG.modelo}. La revisión de índices no tiene costo.")
            with st.container(border=True):
                st.markdown(":orange[:material/picture_as_pdf:] **Contrato**")
                st.caption(" · ".join(c["archivos"]))
                cols = st.columns(2)
                cols[0].metric("Páginas", _miles(c["paginas"]))
                cols[1].metric("Con tablas", len(c["paginas_visuales"]))
        if st.button("Nueva auditoría", icon=":material/restart_alt:", width="stretch"):
            _reiniciar()
            st.rerun()

        llm: ClienteClaude = st.session_state.llm
        if llm.tokens.por_etiqueta:
            with st.expander("Tokens por etapa", icon=":material/receipt_long:"):
                st.dataframe(
                    [{"Etapa": k, "Llamadas": v["calls"], "Entrada": v["input"], "Caché": v["cache_read"],
                      "Salida": v["output"], "USD": round(llm.tokens.costo_usd(CFG.modelo, k) or 0, 2)}
                     for k, v in sorted(llm.tokens.por_etiqueta.items())],
                    hide_index=True, alt="Uso de tokens y costo por etapa",
                )
        if not _hay_credenciales():
            with st.expander("Configurar API key", icon=":material/key:", expanded=True):
                clave = st.text_input("ANTHROPIC_API_KEY", type="password",
                                      help="Solo se usa en esta sesión; no se guarda en disco.")
                if clave:
                    os.environ["ANTHROPIC_API_KEY"] = clave
                    st.rerun()


def _encabezado() -> None:
    with st.container(horizontal=True, vertical_alignment="center"):
        with st.container():
            if st.session_state.carga:
                st.title("Auditoría del contrato")
                st.caption(" · ".join(st.session_state.carga["archivos"]))
            else:
                st.title("Cargar contrato")
                st.caption("Sube el contrato y ContractIA revisará su estructura antes de llamar al modelo.")
        with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center"):
            if _hay_credenciales():
                st.badge("API key conectada", icon=":material/check_circle:", color="green")
            else:
                st.badge("Sin API key", icon=":material/key_off:", color="orange")
            st.badge(CFG.modelo, icon=":material/memory:", color="gray")
            costo = st.session_state.llm.tokens.costo_usd(CFG.modelo)
            st.metric("Costo acumulado", f"${costo:,.2f}" if costo is not None else "n/d", width="content")


def _paso_carga() -> None:
    origen = st.segmented_control("Origen del contrato", ["Subir archivos", "Carpeta local"], default="Subir archivos",
                                  key="origen", required=True, label_visibility="collapsed")
    if origen == "Carpeta local":
        carpeta = st.text_input("Carpeta con el contrato", value=CFG.ruta_contrato)
        archivos = None
    else:
        archivos = st.file_uploader("Contrato (PDF o DOCX; puede ser más de un archivo)",
                                    type=["pdf", "docx"], accept_multiple_files=True)
        carpeta = None
    usar_cache = st.toggle("Reutilizar resultados guardados en caché (grafo, hallazgos, síntesis)", value=True)
    if st.button("Cargar contrato", type="primary", icon=":material/upload_file:", disabled=not (archivos or carpeta)):
        try:
            with st.spinner("Leyendo el contrato y detectando páginas con tablas (puede tardar un par de minutos)..."):
                _correr_carga(_guardar_subidos(archivos) if archivos else carpeta, usar_cache)
            st.session_state.hechas.add("carga")
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(str(e), title="No se pudo cargar el contrato", icon=":material/error:")


def _cabecera_etapa(i: int, est: str) -> None:
    clave, nombre, descripcion, _, icono = ETAPAS[i]
    etiqueta, icono_badge, color = ESTADO_VISUAL[est][2]
    with st.container(horizontal=True, vertical_alignment="center"):
        st.markdown(f"### {icono} {i + 1} · {nombre}")
        st.badge(etiqueta, icon=icono_badge, color=color)
    st.caption(_resumen_etapa(clave) if est == "hecha" else descripcion)


QUE_OBTENDRAS = [
    ("Revisión de índices", "Capítulos, cláusulas y anexos detectados; adapta formatos distintos al estándar."),
    ("Escaneo de seguridad", "Detecta instrucciones ocultas dirigidas a la IA antes de analizar."),
    ("Auditoría por agentes", "Jurista, auditor y cronista revisan cada sección con el contrato como referencia."),
    ("Síntesis global", "Contradicciones entre capítulos, ausencias y posibles falsos positivos."),
    ("Chat con el contrato", "Preguntas respondidas solo con las partes del contrato relacionadas."),
]
SEGURIDAD = [
    ("Solo en tu equipo", "La interfaz escucha únicamente en localhost."),
    ("Escaneo previo", "Si detecta instrucciones ocultas, la auditoría se detiene."),
    ("Clave protegida", "La API key vive en tu archivo .env y no se sube a git."),
]


def _contratos_recientes(limite: int = 5) -> list[dict]:
    """Contratos subidos antes desde esta interfaz (carpetas en cache/subidos)."""
    base = os.path.join(CFG.cache_dir, "subidos")
    if not os.path.isdir(base):
        return []
    filas = []
    for nombre in os.listdir(base):
        carpeta = os.path.join(base, nombre)
        archivos = [f for f in sorted(os.listdir(carpeta)) if f.lower().endswith((".pdf", ".docx"))] if os.path.isdir(carpeta) else []
        if archivos:
            rutas = [os.path.join(carpeta, f) for f in archivos]
            filas.append({
                "carpeta": carpeta, "archivos": archivos,
                "mb": sum(os.path.getsize(r) for r in rutas) / 1e6,
                "fecha": max(os.path.getmtime(r) for r in rutas),
            })
    return sorted(filas, key=lambda f: -f["fecha"])[:limite]


def _pantalla_inicio() -> None:
    izq, der = st.columns([2, 1], gap="medium")
    with izq, st.container(border=True, height="stretch"):
        st.markdown("### :orange[:material/upload_file:] Arrastra el contrato aquí")
        _paso_carga()
    with der, st.container(border=True, height="stretch"):
        st.markdown("### Qué obtendrás")
        for n, (titulo, texto) in enumerate(QUE_OBTENDRAS, 1):
            with st.container(horizontal=True, vertical_alignment="top", gap="small"):
                st.badge(str(n), color="orange")
                with st.container(gap="xxsmall"):
                    st.markdown(f"**{titulo}**")
                    st.caption(texto)
        st.caption(":orange[:material/info:] ContractIA no evalúa la validez legal frente a normas externas: "
                   "revisa la coherencia interna del contrato.")

    izq, der = st.columns([2, 1], gap="medium")
    with izq, st.container(border=True, height="stretch"):
        st.markdown("### Contratos recientes")
        recientes = _contratos_recientes()
        if not recientes:
            st.caption("Aún no has subido contratos desde esta interfaz.")
        for r in recientes:
            with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
                with st.container(gap="xxsmall"):
                    st.markdown(f":orange[:material/description:] **{' · '.join(r['archivos'])}**")
                    st.caption(f"{r['mb']:.1f} MB · subido el {time.strftime('%d/%m/%Y %H:%M', time.localtime(r['fecha']))}")
                if st.button("Continuar", key=f"reciente_{os.path.basename(r['carpeta'])}", icon=":material/arrow_forward:"):
                    try:
                        with st.spinner("Leyendo el contrato y detectando páginas con tablas..."):
                            _correr_carga(r["carpeta"], usar_cache=True)
                        st.session_state.hechas.add("carga")
                        st.rerun()
                    except Exception as e:  # noqa: BLE001
                        st.error(str(e), title="No se pudo cargar el contrato", icon=":material/error:")
    with der, st.container(border=True, height="stretch"):
        st.markdown("### :orange[:material/shield:] Seguridad")
        for titulo, texto in SEGURIDAD:
            with st.container(gap="xxsmall"):
                st.markdown(f"**{titulo}**")
                st.caption(texto)


def _pestana_auditoria() -> None:
    if not st.session_state.carga:
        _pantalla_inicio()
        return

    hechas = [i for i in range(len(ETAPAS)) if _estado_etapa(i) == "hecha"]
    ultima_hecha = hechas[-1] if hechas else None

    pendientes_vistos = False
    for i, (clave, _, _, _, _) in enumerate(ETAPAS):
        est = _estado_etapa(i)
        if est in ("pendiente", "bloqueada"):
            pendientes_vistos = True
            continue
        with st.container(border=True):
            _cabecera_etapa(i, est)
            if clave == "carga" and est != "hecha":
                _paso_carga()
                continue
            if est == "hecha":
                repetible = clave != "carga" and st.session_state.bloqueo in (None, i)
                if i == ultima_hecha:
                    VISTAS[clave]()
                    if repetible:
                        _boton_ejecutar(i)
                else:
                    with st.expander("Ver detalle", icon=":material/unfold_more:"):
                        VISTAS[clave]()
                        if repetible:
                            _boton_ejecutar(i)
            elif est == "actual":
                _boton_ejecutar(i)

    if pendientes_vistos:
        with st.container(border=True):
            for i, (clave, nombre, descripcion, _, icono) in enumerate(ETAPAS):
                est = _estado_etapa(i)
                if est not in ("pendiente", "bloqueada"):
                    continue
                with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
                    with st.container(gap="xxsmall"):
                        st.markdown(f"{ESTADO_VISUAL[est][0]} **{i + 1} · {nombre}**")
                        st.caption(descripcion)
                    st.caption("Bloqueada" if est == "bloqueada" else _costo_etapa(clave).replace("Usa el modelo · ", ""),
                               width="content")

    pendientes = [i for i in range(1, len(ETAPAS)) if _estado_etapa(i) in ("actual", "pendiente")]
    if "carga" in st.session_state.hechas and len(pendientes) > 1 and st.session_state.bloqueo is None:
        with st.container(border=True, horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
            with st.container(gap="xxsmall"):
                st.markdown("**¿Prefieres correr todo de una vez?**")
                st.caption(f"Las {len(pendientes)} etapas restantes se ejecutan en orden y se detienen ante una alerta.")
            if st.button(f"Ejecutar las {len(pendientes)} etapas restantes", icon=":material/fast_forward:"):
                try:
                    for i in pendientes:
                        with st.spinner(f"{ETAPAS[i][1]}..."):
                            _ejecutar(i)
                        if st.session_state.bloqueo is not None:
                            break
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(str(e), title=f"La ejecución se detuvo en «{ETAPAS[i][1]}»", icon=":material/error:")


def _pestana_chat() -> None:
    e: EstadoAuditoria | None = st.session_state.estado
    if e is None or e.grafo is None:
        st.info("El chat se habilita cuando el grafo de conocimiento (etapa 4) está construido.", icon=":material/lock:")
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
                st.error(respuesta, icon=":material/error:")
        st.session_state.chat += [("user", pregunta), ("assistant", respuesta)]


def main() -> None:
    st.set_page_config(page_title="ContractIA", page_icon=":material/balance:", layout="wide")
    _iniciar_sesion()
    _barra_lateral()
    _encabezado()
    tab_auditoria, tab_chat = st.tabs([":material/fact_check: Auditoría", ":material/forum: Chat sobre el contrato"])
    with tab_auditoria:
        _pestana_auditoria()
    with tab_chat:
        _pestana_chat()


main()
