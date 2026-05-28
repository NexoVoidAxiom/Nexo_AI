"""
core/ollama.py — Cliente Ollama defensivo para Void Axiom.
==========================================================
Implementa:
  · Streaming HTTP nativo sobre /api/chat de Ollama.
  · Sanitizador multicapa: bloquea bleeding, ecos, mezcla idiomática.
  · Retry con jitter de temperatura: +0.4°C por cada reintento.
  · Timeout configurable por variable de entorno.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import AsyncIterator

import httpx

from void_axiom.agents.registry import INTRUDER_ID

log = logging.getLogger("void.ollama")

OLLAMA_BASE_URL = os.getenv("VOID_OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
REQUEST_TIMEOUT = float(os.getenv("VOID_OLLAMA_TIMEOUT", "90.0"))
MAX_RETRIES     = int(os.getenv("VOID_MAX_RETRIES", "3"))
RETRY_TEMP_JITTER = float(os.getenv("VOID_RETRY_TEMP_JITTER", "0.4"))

# Markers de respuesta corrupta — invalidan la salida sin imprimirla
_CORRUPT_MARKERS = (
    "fallback", "salida invalida", "salida inválida", "ruta de emergencia",
    "respuesta de emergencia", "llamada a ollama", "ollama fallo",
    "inferencia invalida", "inferencia inválida", "estoyo", "menzando",
    "hablare ahora", "hablaré ahora",
)

_LANG_MIX_RE = re.compile(
    r"\b(with locks|with grandes|cantidades of|datos of|grandes of)\b",
    re.IGNORECASE,
)


def _is_corrupt(text: str) -> bool:
    lower = text.lower()
    if any(m in lower for m in _CORRUPT_MARKERS):
        return True
    if _LANG_MIX_RE.search(text):
        return True
    return False


class OllamaClient:
    """
    Cliente HTTP para Ollama. Usa /api/chat con stream=True.

    El método `stream()` es un generador asíncrono de tokens.
    No mantiene estado entre llamadas — cada llamada es independiente.
    """

    def __init__(self, base_url: str = OLLAMA_BASE_URL) -> None:
        limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
        self._http = httpx.AsyncClient(
            base_url = base_url,
            timeout  = httpx.Timeout(REQUEST_TIMEOUT, read=REQUEST_TIMEOUT * 2),
            limits   = limits,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def stream(
        self,
        model:   str,
        system:  str,
        history: list[dict],
        prompt:  str,
        options: dict,
    ) -> AsyncIterator[str]:
        """
        Streaming de tokens desde Ollama.

        El system prompt se reinyecta siempre como primer mensaje.
        El historial se pasa como mensajes previos.
        La temperatura puede incrementarse en reintentos para romper bucles.

        Yields:
            Tokens individuales de texto.
        """
        messages = _build_messages(system, history, prompt)
        base_temp = options.get("temperature", 0.25)

        for attempt in range(MAX_RETRIES):
            current_options = dict(options)
            if attempt > 0:
                current_options["temperature"] = min(
                    base_temp + RETRY_TEMP_JITTER * attempt, 0.95
                )
                log.info(
                    "Reintento %d/%d para modelo %s (temp=%.2f)",
                    attempt + 1, MAX_RETRIES, model, current_options["temperature"]
                )

            full_text = ""
            try:
                async with self._http.stream(
                    "POST", "/api/chat",
                    json={"model": model, "messages": messages,
                          "stream": True, "options": current_options},
                ) as resp:
                    if resp.status_code != 200:
                        log.warning(
                            "Ollama HTTP %d para modelo %s", resp.status_code, model
                        )
                        continue

                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        token = data.get("message", {}).get("content", "")
                        if token:
                            full_text += token
                            yield token

                        if data.get("done", False):
                            break

            except httpx.TimeoutException:
                log.error("Timeout en modelo %s (intento %d)", model, attempt + 1)
                continue
            except Exception as exc:
                log.error("Error en stream de %s: %s", model, exc)
                continue

            # Validar calidad de la respuesta completa
            if _is_corrupt(full_text):
                log.warning(
                    "Respuesta corrupta detectada en modelo %s (intento %d). Reintentando…",
                    model, attempt + 1
                )
                # Limpiar output emitido — el caller es responsable de descartar
                continue

            # Respuesta válida — terminar
            return

        # Se agotaron los reintentos sin respuesta válida
        log.error("Modelo %s: todos los reintentos fallaron.", model)
        raise RuntimeError(f"Modelo {model}: sin respuesta válida tras {MAX_RETRIES} intentos.")


def _build_messages(system: str, history: list[dict], prompt: str) -> list[dict]:
    """
    Construye la lista de mensajes para /api/chat.

    El system prompt va siempre primero.
    El historial sigue, con roles normalizados.
    El prompt del usuario va al final.
    """
    messages: list[dict] = [{"role": "system", "content": system}]

    for msg in history:
        role = msg.get("role", "user")
        content = msg.get("content", "").strip()
        if content:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": prompt})
    return messages
