"""Métricas: tokens consumidos (P1 6.2) y calidad del grafo (P2 2.6.1)."""
from __future__ import annotations

import threading
from collections import Counter
from typing import Any

import networkx as nx

from .utils import extraer_cid_de_string

_CAMPOS = ("calls", "input", "cache_write", "cache_read", "output")

# USD por millón de tokens: entrada, salida, lectura de caché (octubre 2026). La escritura en caché cuesta 1,25× la entrada.
PRECIOS_USD_MTOK: dict[str, tuple[float, float, float]] = {
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}


class ContadorTokens:
    """Acumula tokens por etapa (agente o fase) a partir de `response.usage` de la API de Claude."""

    def __init__(self):
        self.por_etiqueta: dict[str, dict[str, int]] = {}
        self._lock = threading.Lock()

    def registrar(self, etiqueta: str, usage: Any) -> None:
        if usage is None:
            return
        with self._lock:
            b = self.por_etiqueta.setdefault(etiqueta, {k: 0 for k in _CAMPOS})
            b["calls"] += 1
            b["input"] += getattr(usage, "input_tokens", 0) or 0
            b["cache_write"] += getattr(usage, "cache_creation_input_tokens", 0) or 0
            b["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0
            b["output"] += getattr(usage, "output_tokens", 0) or 0

    def costo_usd(self, modelo: str, etiqueta: str | None = None) -> float | None:
        """Costo estimado (todas las etapas, o solo `etiqueta`). None si no hay precio para el modelo."""
        precios = PRECIOS_USD_MTOK.get(modelo)
        if precios is None:
            return None
        pi, po, pr = precios
        cubos = [self.por_etiqueta[etiqueta]] if etiqueta in self.por_etiqueta else (
            [] if etiqueta else list(self.por_etiqueta.values())
        )
        return sum(
            (b["input"] * pi + b["cache_write"] * pi * 1.25 + b["cache_read"] * pr + b["output"] * po) / 1e6
            for b in cubos
        )

    def resumen(self) -> str:
        if not self.por_etiqueta: return "(sin uso registrado)"
        lineas = [
            "| Etapa | Calls | Input | Cache write | Cache read | Output |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        total = {k: 0 for k in _CAMPOS}
        for et in sorted(self.por_etiqueta):
            b = self.por_etiqueta[et]
            lineas.append(
                f"| {et} | {b['calls']} | {b['input']:,} | {b['cache_write']:,} | "
                f"{b['cache_read']:,} | {b['output']:,} |"
            )
            for k in total: total[k] += b[k]
        lineas.append(
            f"| **TOTAL** | **{total['calls']}** | **{total['input']:,}** | **{total['cache_write']:,}** | "
            f"**{total['cache_read']:,}** | **{total['output']:,}** |"
        )
        return "\n".join(lineas)


# --- P2 2.6.1: métricas de calidad del grafo ---
def calcular_metricas_grafo(
    G: nx.MultiDiGraph,
    mapa_clausula_a_seccion: dict[str, dict],
) -> dict[str, Any]:
    """Devuelve un dict con cobertura, fragmentación, distribución y top-degree.

    - cobertura: % de cláusulas en `mapa_clausula_a_seccion` que aparecen como nodo.
    - inventadas: nodos cláusula con cid que NO está en el mapa.
    - fragmentacion: para cada cid, cuántas variantes (nodos) lo contienen.
    - distribucion_relaciones: count por tipo de relación.
    - distribucion_tipos_nodo: count por atributo `tipo` de nodo.
    - componentes_debiles: nº de componentes débilmente conexos.
    - top_degree: top 10 nodos por degree total.
    """
    cids_en_mapa = set(mapa_clausula_a_seccion.keys())

    # Mapa cid -> [nodos]
    cid_a_nodos: dict[str, list[str]] = {}
    nodos_clausula: list[str] = []
    for n in G.nodes():
        cid = extraer_cid_de_string(str(n))
        if cid:
            cid_a_nodos.setdefault(cid, []).append(n)
            nodos_clausula.append(n)

    cids_en_grafo = set(cid_a_nodos.keys())
    cobertura_pct = (len(cids_en_grafo & cids_en_mapa) / max(1, len(cids_en_mapa))) * 100
    inventadas = sorted(cids_en_grafo - cids_en_mapa)

    fragmentacion = {cid: len(nodos) for cid, nodos in cid_a_nodos.items() if len(nodos) > 1}
    fragmentacion_top = sorted(fragmentacion.items(), key=lambda x: -x[1])[:10]

    distribucion_relaciones = Counter(
        data.get("relacion", "?") for _, _, data in G.edges(data=True)
    )

    distribucion_tipos_nodo = Counter(
        G.nodes[n].get("tipo", "(sin tipo)") for n in G.nodes()
    )

    componentes_debiles = nx.number_weakly_connected_components(G)

    grados = sorted(G.degree(), key=lambda x: -x[1])[:10]

    return {
        "n_nodos": G.number_of_nodes(),
        "n_aristas": G.number_of_edges(),
        "cobertura_pct": round(cobertura_pct, 1),
        "n_clausulas_mapa": len(cids_en_mapa),
        "n_clausulas_grafo": len(cids_en_grafo),
        "n_inventadas": len(inventadas),
        "inventadas": inventadas[:20],
        "n_fragmentadas": len(fragmentacion),
        "fragmentacion_top": fragmentacion_top,
        "distribucion_relaciones": dict(distribucion_relaciones),
        "distribucion_tipos_nodo": dict(distribucion_tipos_nodo),
        "componentes_debiles": componentes_debiles,
        "top_degree": grados,
    }


def render_metricas_grafo_md(m: dict[str, Any]) -> str:
    """Genera un fragmento Markdown con la salud del grafo."""
    lineas = ["## Calidad del Grafo de Conocimiento", ""]
    lineas.append(f"- **Nodos / Aristas**: {m['n_nodos']:,} / {m['n_aristas']:,}")
    lineas.append(
        f"- **Cobertura**: {m['cobertura_pct']}% "
        f"({m['n_clausulas_grafo']} de {m['n_clausulas_mapa']} cláusulas en grafo)"
    )
    lineas.append(f"- **Cláusulas inventadas por el LLM**: {m['n_inventadas']}")
    if m["inventadas"]:
        lineas.append("  - Ejemplos: " + ", ".join(m["inventadas"][:10]))
    lineas.append(f"- **Cláusulas fragmentadas (varios nodos por cid)**: {m['n_fragmentadas']}")
    if m["fragmentacion_top"]:
        ejemplos = ", ".join(f"{cid}×{n}" for cid, n in m["fragmentacion_top"][:5])
        lineas.append(f"  - Top: {ejemplos}")
    lineas.append(f"- **Componentes débilmente conexos**: {m['componentes_debiles']}")

    if m["distribucion_relaciones"]:
        lineas.append("\n### Distribución de relaciones")
        lineas.append("| Relación | Aristas |")
        lineas.append("|---|---:|")
        for rel, n in sorted(m["distribucion_relaciones"].items(), key=lambda x: -x[1]):
            lineas.append(f"| {rel} | {n:,} |")

    if m["distribucion_tipos_nodo"]:
        lineas.append("\n### Distribución de tipos de nodo")
        lineas.append("| Tipo | Nodos |")
        lineas.append("|---|---:|")
        for tipo, n in sorted(m["distribucion_tipos_nodo"].items(), key=lambda x: -x[1]):
            lineas.append(f"| {tipo} | {n:,} |")

    if m["top_degree"]:
        lineas.append("\n### Top nodos por degree")
        for n, d in m["top_degree"]:
            lineas.append(f"- `{n}` (degree {d})")

    return "\n".join(lineas)
