"""Cliente Claude: salida estructurada async, caché de prompts, effort por etapa y conteo de tokens."""
from __future__ import annotations
import asyncio
import threading
from contextlib import asynccontextmanager
from typing import Any, Iterator, Optional, Type, TypeVar

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
        except BaseException as e:
            resultado["error"] = e

    hilo = threading.Thread(target=_run)
    hilo.start()
    hilo.join()
    if "error" in resultado:
        raise resultado["error"]
    return resultado["valor"]


def bloques_system(instrucciones: str, referencia: Optional[str] = None) -> list:
    # La referencia (contrato completo) va primero para que su caché se comparta entre agentes.
    bloques = []
    if referencia:
        bloques.append({"type": "text", "text": referencia, "cache_control": {"type": "ephemeral"}})
    bloques.append({"type": "text", "text": instrucciones, "cache_control": {"type": "ephemeral"}})
    return bloques


class ClienteClaude:
    def __init__(self, modelo: Optional[str] = None):
        self.modelo = modelo or CFG.modelo
        self.tokens = ContadorTokens()
        self._async: Optional[anthropic.AsyncAnthropic] = None
        self._sync: Optional[anthropic.Anthropic] = None

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
        schema: Type[T],
        referencia: Optional[str] = None,
    ) -> T:
        if self._async is None:
            raise RuntimeError("Usa `async with cliente.sesion():` antes de llamar al modelo.")
        resp = await self._async.beta.messages.parse(
            system=bloques_system(instrucciones, referencia),
            messages=[{"role": "user", "content": contenido}],
            output_format=schema,
            **self._kwargs(etapa),
        )
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
