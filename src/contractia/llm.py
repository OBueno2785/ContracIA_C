"""Cliente Claude: salida estructurada async, caché de prompts, effort por etapa y conteo de tokens."""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel

from .config import CFG
from .logging_setup import get_logger
from .metricas import ContadorTokens

log = get_logger("llm")

FALLBACK_BETA = "server-side-fallback-2026-07-01"
T = TypeVar("T", bound=BaseModel)


class RespuestaRechazada(RuntimeError):
    """El modelo (y su fallback) declinaron la petición."""


def ejecutar(coro):
    """Corre una corrutina desde código síncrono, también dentro de Jupyter/Colab (loop ya activo)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    resultado: dict = {}

    def _run():
        try:
            resultado["valor"] = asyncio.run(coro)
        except BaseException as e:  # noqa: BLE001
            resultado["error"] = e

    hilo = threading.Thread(target=_run)
    hilo.start()
    hilo.join()
    if "error" in resultado:
        raise resultado["error"]
    return resultado["valor"]


def bloques_system(instrucciones: str, referencia: str | None = None) -> list:
    # La referencia (contrato completo) va primero para que su caché se comparta entre agentes.
    bloques = []
    if referencia:
        bloques.append({"type": "text", "text": referencia, "cache_control": {"type": "ephemeral"}})
    bloques.append({"type": "text", "text": instrucciones, "cache_control": {"type": "ephemeral"}})
    return bloques


def contenido_usuario(contenido: str, pdf_visual: str | None = None) -> str | list:
    """Sin PDF, texto plano. Con PDF, el documento va primero y cacheado (es igual en todas las llamadas)."""
    if not pdf_visual:
        return contenido
    return [
        {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_visual},
            "title": "Páginas del contrato con tablas, cuadros o escaneadas",
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": contenido},
    ]


class ClienteClaude:
    def __init__(self, modelo: str | None = None):
        self.modelo = modelo or CFG.modelo
        self.tokens = ContadorTokens()
        self._async: anthropic.AsyncAnthropic | None = None
        self._sync: anthropic.Anthropic | None = None

    def _kwargs(self, etapa: str) -> dict:
        kw: dict = {
            "model": self.modelo,
            "max_tokens": CFG.max_output_tokens,
            "output_config": {"effort": CFG.effort.get(etapa, "medium")},
        }
        if CFG.usar_fallbacks:
            kw["betas"] = [FALLBACK_BETA]
            kw["fallbacks"] = "default"
        return kw

    @asynccontextmanager
    async def sesion(self):
        """Un AsyncAnthropic por event loop (cada fase corre en su propio loop)."""
        self._async = anthropic.AsyncAnthropic(
            timeout=CFG.timeout_segundos, max_retries=CFG.max_reintentos_sdk
        )
        try:
            yield self
        finally:
            await self._async.close()
            self._async = None

    async def estructurado(
        self,
        etapa: str,
        instrucciones: str,
        contenido: str,
        schema: type[T],
        referencia: str | None = None,
        pdf_visual: str | None = None,
        max_tokens: int | None = None,
        streaming: bool = False,
    ) -> T:
        if self._async is None:
            raise RuntimeError("Usa `async with cliente.sesion():` antes de llamar al modelo.")
        kwargs = self._kwargs(etapa)
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        peticion = {
            "system": bloques_system(instrucciones, referencia),
            "messages": [{"role": "user", "content": contenido_usuario(contenido, pdf_visual)}],
            "output_format": schema,
            **kwargs,
        }
        if streaming:
            # Respuestas largas: el stream evita cortes por timeout en peticiones de muchos minutos.
            async with self._async.beta.messages.stream(**peticion) as stream:
                resp = await stream.get_final_message()
        else:
            resp = await self._async.beta.messages.parse(**peticion)
        self.tokens.registrar(etapa, resp.usage)
        if resp.stop_reason == "refusal":
            categoria = getattr(resp.stop_details, "category", None) if resp.stop_details else None
            raise RespuestaRechazada(f"Petición declinada en '{etapa}' (categoría: {categoria}).")
        if resp.stop_reason == "max_tokens":
            log.warning("Respuesta truncada por max_tokens en '%s'.", etapa)
        if resp.parsed_output is None:
            raise ValueError(f"Salida estructurada vacía en '{etapa}'.")
        return resp.parsed_output

    def texto_en_streaming(self, etapa: str, instrucciones: str, contenido: str) -> Iterator[str]:
        if self._sync is None:
            self._sync = anthropic.Anthropic(
                timeout=CFG.timeout_segundos, max_retries=CFG.max_reintentos_sdk
            )
        with self._sync.beta.messages.stream(
            system=bloques_system(instrucciones),
            messages=[{"role": "user", "content": contenido}],
            **self._kwargs(etapa),
        ) as stream:
            yield from stream.text_stream
            final: Any = stream.get_final_message()
        self.tokens.registrar(etapa, final.usage)
        if final.stop_reason == "refusal":
            yield "\n[La consulta fue declinada por el modelo.]"
