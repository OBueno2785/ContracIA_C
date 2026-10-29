"""Adapta contratos con otro formato al estándar que reconocen los regex de `segmentacion`.

Formato estándar (ej. VFC PTAR Cajamarca):
    Capítulo III EVENTOS A LA FECHA DE CIERRE      <- encabezado en su propia línea, título en mayúsculas
    3.1. El CONCESIONARIO garantiza ...             <- cláusulas con el número del capítulo como prefijo
    Anexo 15 CUADRO DE PENALIDADES                  <- encabezado en su propia línea, título en mayúsculas

Solo reescribe encabezados y numeración; no toca el resto del texto.
"""
from __future__ import annotations

import re
import unicodedata

from .segmentacion import _roman_to_int

_SEP = r"[ \t]*(?:[:.\-–—][ \t]*)*"
_N_ABREV = r"(?:N(?:[°º]|ro\.?|o\.|\.)[ \t]*)?"
_UNIDAD_ORDINAL = r"(?:PRIMER[OA]?|SEGUND[OA]|TERCER[OA]?|CUART[OA]|QUINT[OA]|SEXT[OA]|S[EÉ]P?TIM[OA]|OCTAV[OA]|NOVEN[OA])"
_TOKEN_NUM = rf"[A-ZÁÉÍÓÚÜÑ0-9]+(?:[ \t-]+{_UNIDAD_ORDINAL})?\b"

_CAP_LINEA = re.compile(rf"^[ \t]*CAP[IÍ]TULO[ \t]+{_N_ABREV}(?P<num>{_TOKEN_NUM}){_SEP}(?P<titulo>.*)$", re.IGNORECASE)
_CLAUSULA_LINEA = re.compile(
    rf"^[ \t]*CL[AÁ]USULA[ \t]+{_N_ABREV}(?P<num>{_TOKEN_NUM})(?!\.\d){_SEP}(?P<titulo>.*)$", re.IGNORECASE
)
_ANEXO_LINEA = re.compile(rf"^[ \t]*ANEXOS?[ \t]+{_N_ABREV}(?P<num>{_TOKEN_NUM})(?!\.\d){_SEP}(?P<titulo>.*)$", re.IGNORECASE)
_ROMANO_LINEA = re.compile(r"^[ \t]*(?P<num>[IVXL]+)[ \t]*[.)\-–—][ \t]+(?P<titulo>[^a-záéíóúñ]+)$")
_CAP_ESTANDAR = re.compile(r"^Capítulo (?P<num>[IVXLCDM]+) ", re.MULTILINE)

_SEPARADOR_CLAUSULA = re.compile(r"^(?P<ind>[ \t]*)(?P<id>\d+(?:\.\d+)+)[ \t]*(?:\.[ \t]*[-–—]+|[-–—]+|\.\)|\)|:)[ \t]*", re.MULTILINE)
_PARRAFO_SIMPLE = re.compile(r"^(?P<ind>[ \t]*)(?P<k>\d{1,3})[ \t]*[.)\-–—]+[ \t]+(?=[A-ZÁÉÍÓÚÜÑ¿¡\"“(])", re.MULTILINE)
_SUBNUMERAL = re.compile(r"^(?P<ind>[ \t]*)(?P<k>\d{1,3})(?P<resto>(?:\.\d+)+)(?=[.)\-–—:]?[ \t])", re.MULTILINE)

_DECENAS = [("CUADRAGESIM", 40), ("QUINCUAGESIM", 50), ("TRIGESIM", 30), ("VIGESIM", 20), ("DECIM", 10)]
_UNIDADES = [("PRIMER", 1), ("SEGUND", 2), ("TERCER", 3), ("CUART", 4), ("QUINT", 5), ("SEXT", 6),
             ("SEPTIM", 7), ("SETIM", 7), ("OCTAV", 8), ("NOVEN", 9)]
_ESPECIALES = {"UNDECIM": 11, "DUODECIM": 12, "UNIC": 1}
_TERMINACIONES = {"", "O", "A", "OS", "AS"}


def _es_linea_de_indice(linea: str) -> bool:
    # Entradas de la tabla de contenido: puntos guía y número de página al final.
    return bool(re.search(r"[ .·•]{3,}\s*\d+\s*$", linea)) or linea.count(".") >= 6


def _sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def ordinal_a_int(palabra: str) -> int | None:
    """'PRIMERA' -> 1, 'DÉCIMO SEGUNDA' -> 12, 'VIGESIMOTERCERO' -> 23. None si no es un ordinal."""
    s = re.sub(r"[\s\-]+", "", _sin_tildes(palabra).upper())
    for raiz, valor in _ESPECIALES.items():
        if s.startswith(raiz) and s[len(raiz):] in _TERMINACIONES:
            return valor
    total = 0
    for raiz, valor in _DECENAS:
        if s.startswith(raiz):
            total, s = valor, s[len(raiz):]
            break
    if total and s[:1] in ("O", "A") and any(s[1:].startswith(raiz) for raiz, _ in _UNIDADES):
        s = s[1:]
    for raiz, valor in _UNIDADES:
        if s.startswith(raiz):
            total, s = total + valor, s[len(raiz):]
            break
    return total if total and s in _TERMINACIONES else None


def int_a_romano(n: int) -> str:
    valores = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
               (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for v, r in valores:
        while n >= v:
            out, n = out + r, n - v
    return out


def _numero(token: str) -> int | None:
    t = token.strip().upper()
    if t.isdigit():
        return int(t)
    if re.fullmatch(r"[IVXLCDM]+", t) and _roman_to_int(t) > 0:
        return _roman_to_int(t)
    return ordinal_a_int(t)


def _numero_anexo(token: str) -> str | None:
    t = token.strip().upper()
    if t.isdigit():
        return str(int(t))
    if re.fullmatch(r"[IVXLCDM]+", t) and _roman_to_int(t) > 0:
        return t
    if re.fullmatch(r"[A-Z]", t):
        return t
    n = ordinal_a_int(t)
    return str(n) if n else None


def _es_titulo(texto: str) -> bool:
    t = texto.strip().rstrip(":.-–— ").strip()
    if not t or len(t) > 150 or re.match(r"^\d", t) or not re.search(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{3,}", t):
        return False
    if not re.search(r"[a-záéíóúñ]", t):
        return bool(re.search(r"[A-ZÁÉÍÓÚÜÑ]{2,}", t))
    if texto.strip().endswith(".") or not t[0].isupper():
        return False
    palabras = [p for p in re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", t) if len(p) >= 4]
    return bool(palabras) and len(palabras) <= 15 and sum(p[0].isupper() for p in palabras) / len(palabras) >= 0.6


def _limpiar_titulo(texto: str) -> str:
    # Solo caracteres que aceptan los regex de títulos en `segmentacion`.
    t = re.sub(r"[–—]", "-", texto.upper())
    t = re.sub(r"[^A-ZÁÉÍÓÚÜÑ0-9 ,\-./()º°]", " ", t)
    return " ".join(t.split()).strip(" :.-")


def _siguiente_no_vacia(lineas: list[str], i: int) -> int | None:
    for j in range(i + 1, min(i + 3, len(lineas))):
        if lineas[j].strip():
            return j
    return None


def _titulo_desde_siguiente(lineas: list[str], i: int) -> tuple[str, list[int]] | None:
    """Encabezado sin título en su línea: toma la línea siguiente (y una más si continúa en mayúsculas)."""
    j = _siguiente_no_vacia(lineas, i)
    if (j is None or not _es_titulo(lineas[j]) or re.search(r"[a-záéíóúñ]", lineas[j])
            or _es_encabezado(lineas[j])):
        return None
    titulo, usadas = lineas[j].strip(), [j]
    k = _siguiente_no_vacia(lineas, j)
    if (k is not None and k == j + 1 and _es_titulo(lineas[k]) and not re.search(r"[a-záéíóúñ]", lineas[k])
            and not titulo.endswith((".", ":")) and not _es_encabezado(lineas[k])):
        titulo, usadas = f"{titulo} {lineas[k].strip()}", [j, k]
    return titulo, usadas


def _es_encabezado(linea: str) -> bool:
    return bool(_CAP_LINEA.match(linea) or _CLAUSULA_LINEA.match(linea) or _ANEXO_LINEA.match(linea))


def _reescribir_encabezados(
    lineas: list[str], patron: re.Pattern, formatear, cambios: list[tuple[str, str]]
) -> list[str]:
    out = list(lineas)
    consumidas: set[int] = set()
    for i, linea in enumerate(lineas):
        if i in consumidas or _es_linea_de_indice(linea):
            continue
        m = patron.match(linea)
        if not m:
            continue
        titulo, usadas = m.group("titulo"), []
        if not titulo.strip():
            r = _titulo_desde_siguiente(lineas, i)
            if r is None:
                continue
            titulo, usadas = r
        if not _es_titulo(titulo):
            continue
        nueva = formatear(m.group("num"), _limpiar_titulo(titulo))
        if nueva is None or nueva == linea.strip():
            continue
        cambios.append((" / ".join([linea.strip()] + [lineas[j].strip() for j in usadas]), nueva))
        out[i] = nueva
        for j in usadas:
            out[j] = ""
            consumidas.add(j)
    return out


def _fmt_capitulo(num: str, titulo: str) -> str | None:
    n = _numero(num)
    return f"Capítulo {int_a_romano(n)} {titulo}" if n else None


def _fmt_anexo(num: str, titulo: str) -> str | None:
    n = _numero_anexo(num)
    return f"Anexo {n} {titulo}" if n else None


def _contar(lineas: list[str], patron: re.Pattern) -> int:
    """Encabezados con título válido, en su línea o en la siguiente (no cuenta referencias partidas)."""
    n = 0
    for i, linea in enumerate(lineas):
        m = patron.match(linea)
        if not m or _es_linea_de_indice(linea):
            continue
        titulo = m.group("titulo")
        if (titulo.strip() and _es_titulo(titulo)) or (not titulo.strip() and _titulo_desde_siguiente(lineas, i)):
            n += 1
    return n


def _numeracion_relativa(texto: str, cambios: list[tuple[str, str]]) -> str:
    """En capítulos sin cláusulas 'N.x', convierte párrafos '1.', '2.' (y '1.1') en 'N.1.', 'N.2.' ('N.1.1')."""
    cortes = list(_CAP_ESTANDAR.finditer(texto))
    if not cortes:
        return texto
    partes = [texto[: cortes[0].start()]]
    for idx, m in enumerate(cortes):
        fin = cortes[idx + 1].start() if idx + 1 < len(cortes) else len(texto)
        bloque = texto[m.start():fin]
        n = _roman_to_int(m.group("num"))
        if n and not re.search(rf"^[ \t]*{n}\.\d", bloque, re.MULTILINE):
            numeros = [int(x.group("k")) for x in _PARRAFO_SIMPLE.finditer(bloque)]
            if len(numeros) >= 2 and numeros[0] == 1:
                vistos = set(numeros)

                def _par(x, n=n):
                    nuevo = f"{x.group('ind')}{n}.{x.group('k')}. "
                    cambios.append((x.group(0).strip(), nuevo.strip()))
                    return nuevo

                def _sub(x, n=n, vistos=vistos):
                    if int(x.group("k")) not in vistos:
                        return x.group(0)
                    nuevo = f"{x.group('ind')}{n}.{x.group('k')}{x.group('resto')}"
                    cambios.append((x.group(0).strip(), nuevo.strip()))
                    return nuevo

                bloque = _SUBNUMERAL.sub(_sub, bloque)
                bloque = _PARRAFO_SIMPLE.sub(_par, bloque)
        partes.append(bloque)
    return "".join(partes)


def normalizar_formato(texto: str) -> tuple[str, list[tuple[str, str]]]:
    """Devuelve el texto en formato estándar y la lista de cambios (antes, después)."""
    cambios: list[tuple[str, str]] = []
    lineas = texto.replace("\r", "").split("\n")

    n_capitulos, n_clausulas = _contar(lineas, _CAP_LINEA), _contar(lineas, _CLAUSULA_LINEA)
    if n_capitulos >= max(2, n_clausulas):
        lineas = _reescribir_encabezados(lineas, _CAP_LINEA, _fmt_capitulo, cambios)
    elif n_clausulas >= 2:
        # Contratos organizados por "CLÁUSULA PRIMERA: OBJETO" con numerales 1.1, 1.2: cada cláusula es un capítulo.
        lineas = _reescribir_encabezados(lineas, _CLAUSULA_LINEA, _fmt_capitulo, cambios)
    else:
        romanos = [m for m in map(_ROMANO_LINEA.match, lineas) if m and _es_titulo(m.group("titulo"))]
        if len(romanos) >= 3 and _roman_to_int(romanos[0].group("num")) == 1:
            lineas = _reescribir_encabezados(lineas, _ROMANO_LINEA, _fmt_capitulo, cambios)

    lineas = _reescribir_encabezados(lineas, _ANEXO_LINEA, _fmt_anexo, cambios)
    texto_out = "\n".join(lineas)

    def _sep(m):
        nuevo = f"{m.group('ind')}{m.group('id')}. "
        if m.group(0) != nuevo:
            cambios.append((m.group(0).strip(), nuevo.strip()))
        return nuevo

    texto_out = _SEPARADOR_CLAUSULA.sub(_sep, texto_out)
    texto_out = _numeracion_relativa(texto_out, cambios)
    return texto_out, cambios
