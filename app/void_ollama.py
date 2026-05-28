"""
void_ollama.py — Cliente Ollama defensivo para Void Axiom.

Implementa:
  · Mutex secuencial (Semaphore=1): una sola llamada GPU a la vez.
  · Sanitizador regex multicapa: bloquea bleeding, ecos y mezcla idiomática.
  · Retry con jitter de temperatura escalado: +0.4 por cada reintento.
  · Trigger probabilístico del Intruso (12% por defecto, configurable).
  · Modo 55K RAM: payload extendido cuando la plataforma está en reposo.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from app.void_agents import (
    AGENTS,
    ALL_AGENTS,
    BASE_SYSTEM_GUARD,
    INTRUDER_ID,
    INTRUDER_PROBABILITY,
    MAX_RETRIES,
    PUBLIC_AGENT_ORDER,
    RETRY_TEMP_JITTER,
    STOP_SEQUENCES,
)
from app.void_memory import (
    VoidChannelHistory,
    contains_corrupt_marker,
    is_echo_of_recent,
)
from app.void_activity import void_activity

logger = logging.getLogger("void_ollama")

# ── Configuración ─────────────────────────────────────────────────────────────
OLLAMA_BASE_URL   = os.getenv("VOID_OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
HTTP_RETRIES      = int(os.getenv("VOID_OLLAMA_HTTP_RETRIES", "3"))
REQUEST_TIMEOUT   = float(os.getenv("VOID_OLLAMA_TIMEOUT", "90.0"))

# ── Resultado tipado ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class OllamaResult:
    agent_id: str
    text:     str
    attempts: int
    suppressed: bool = False  # True si todos los reintentos fallaron


# ── Cliente principal ─────────────────────────────────────────────────────────

class VoidOllamaClient:
    """
    Orquesta las llamadas HTTP a Ollama para los 4 agentes de Void Axiom.

    Uso típico (desde el endpoint FastAPI):

        client = VoidOllamaClient()
        async for result in client.multi_agent_response(history, session_id):
            yield result
        await client.close()
    """

    def __init__(self) -> None:
        limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
        self._http = httpx.AsyncClient(
            base_url=OLLAMA_BASE_URL,
            timeout=httpx.Timeout(REQUEST_TIMEOUT, read=REQUEST_TIMEOUT * 2),
            limits=limits,
        )
        # SEMAPHORE = 1: serializa TODAS las llamadas GPU.
        # Elimina el cruce de hilos concurrentes que producía "49" en vez de "69".
        self._gpu_gate = asyncio.Semaphore(1)
        self._warned: set[tuple[str, str]] = set()
        self._model_cache: tuple[float, set[str]] = (0.0, set())

    async def close(self) -> None:
        await self._http.aclose()

    # ── API pública ────────────────────────────────────────────────────────────

    async def multi_agent_response(
        self,
        history:    VoidChannelHistory,
        session_id: str,
        user_input: str,
    ) -> AsyncIterator[OllamaResult]:
        """
        Genera respuestas de los agentes de forma estrictamente SECUENCIAL.

        Orden:
          1. [12% prob] Intruso interrumpe antes de los agentes principales.
          2. ARCH-7 → CODA → REBx3  (siempre en este orden, uno a la vez).

        Yields OllamaResult por cada agente que produce texto.
        Los agentes suprimidos (todos los reintentos agotados) no se yieldan.
        """
        void_activity.mark_chat()
        extended = void_activity.extended_context_allowed()
        mode_label = "55K-RAM" if extended else "16K-VRAM"
        logger.info(f"[VOID] session={session_id} modo={mode_label}")

        # Push del input del administrador
        history.push("user", f"[AERYS | ADMIN]: {user_input}")

        # ── Trigger del Intruso ────────────────────────────────────────────────
        if random.random() < INTRUDER_PROBABILITY:
            result = await self._call_safe(INTRUDER_ID, history, extended)
            if result and not result.suppressed:
                history.push("assistant", result.text, agent_id=INTRUDER_ID)
                yield result

        # ── Respuestas principales en orden secuencial ────────────────────────
        for agent_id in PUBLIC_AGENT_ORDER:
            result = await self._call_safe(agent_id, history, extended)
            if result and not result.suppressed:
                history.push("assistant", result.text, agent_id=agent_id)
                yield result

    # ── Llamada con retry y sanitización ──────────────────────────────────────

    async def _call_safe(
        self,
        agent_id: str,
        history:  VoidChannelHistory,
        extended: bool,
    ) -> OllamaResult | None:
        """
        Llama a Ollama para un agente con hasta MAX_RETRIES intentos.

        En cada reintento:
          · La temperatura sube +RETRY_TEMP_JITTER (ej: 0.25 → 0.65 → 1.05, cap 0.95)
          · Si la respuesta pasa la sanitización, se devuelve.
          · Si todos fallan, devuelve OllamaResult(suppressed=True) — nunca texto de error.
        """
        agent = AGENTS[agent_id]

        if not await self._model_available(agent["model"]):
            self._warn(agent_id, f"model_not_installed:{agent['model']}")
            return None

        for attempt in range(1, MAX_RETRIES + 1):
            jitter    = RETRY_TEMP_JITTER * (attempt - 1)
            temp      = min(agent["temperature"] + jitter, 0.95)
            messages  = history.build_payload(
                system_prompt=f"{BASE_SYSTEM_GUARD}\n\nIDENTIDAD ACTIVA:\n{agent['prompt']}",
                extended=extended,
            )

            async with self._gpu_gate:
                raw = await self._raw_http_call(agent_id, agent["model"], messages, temp, agent)

            if raw is None:
                logger.warning(f"[{agent_id}] intento {attempt}: sin respuesta HTTP")
                await self._backoff(attempt)
                continue

            # ── Sanitización multicapa ─────────────────────────────────────────
            rejection_reason = self._sanitize(raw, history.recent_messages(8))
            if rejection_reason:
                logger.warning(
                    f"[{agent_id}] intento {attempt}: respuesta rechazada "
                    f"({rejection_reason}). temp actual={temp:.2f} → "
                    f"jitter +{RETRY_TEMP_JITTER:.1f}"
                )
                await self._backoff(attempt)
                continue

            logger.debug(f"[{agent_id}] OK en intento {attempt} (temp={temp:.2f})")
            return OllamaResult(agent_id=agent_id, text=raw.strip(), attempts=attempt)

        self._warn(agent_id, "all_retries_exhausted")
        return OllamaResult(agent_id=agent_id, text="", attempts=MAX_RETRIES, suppressed=True)

    # ── Sanitizador multicapa ────────────────────────────────────────────────

    @staticmethod
    def _sanitize(text: str, recent_messages: list[dict]) -> str | None:
        """
        Devuelve el motivo de rechazo si la respuesta está contaminada,
        o None si es limpia.
        """
        if not text or not text.strip():
            return "empty"

        # Capa 1: markers de corrupción + bleeding + mezcla idiomática + ecos conocidos
        if contains_corrupt_marker(text):
            return "corrupt_marker_or_bleeding"

        # Capa 2: eco por similitud Jaccard (copia de turno anterior)
        if is_echo_of_recent(text, recent_messages, threshold=0.75):
            return "jaccard_echo"

        # Capa 3: tercera persona narrativa (comportamiento de rol teatral)
        if re.search(
            r"\b(suspira|murmura|mira fijamente|se inclina|dice en voz|mientras mira)\b",
            text, re.IGNORECASE
        ):
            return "third_person_narrative"

        # Capa 4: cabecera de rol en la propia respuesta (el agente imprime su etiqueta)
        if re.match(
            r"^\s*(AR\s*\|\s*ARCH-7|CO\s*\|\s*CODA|RE\s*\|\s*REBx3|⬡\s*\|\s*\.\.\.)\s*[:\-]",
            text, re.IGNORECASE
        ):
            return "self_header"

        return None

    # ── Llamada HTTP raw ─────────────────────────────────────────────────────

    async def _raw_http_call(
        self,
        agent_id: str,
        model:    str,
        messages: list[dict],
        temp:     float,
        agent:    dict,
    ) -> str | None:
        payload = {
            "model":    model,
            "messages": messages,
            "stream":   False,
            "keep_alive": -1,
            "options": {
                "temperature":  temp,
                "num_predict":  agent["num_predict"],
                "num_ctx":      agent["num_ctx"],
                "num_gpu":      agent["num_gpu"],
                "stop":         STOP_SEQUENCES,
            },
        }
        try:
            resp = await self._http.post("/api/chat", json=payload)

            if resp.status_code == 400:
                # Ollama puede rechazar frequency/presence_penalty en versiones antiguas.
                # Reintentamos sin ellas (el Modelfile ya las aplica nativamente).
                if any(k in resp.text.lower() for k in ("frequency", "presence", "invalid", "unknown")):
                    self._warn(agent_id, "native_options_downgraded")
                    payload["options"].pop("frequency_penalty", None)
                    payload["options"].pop("presence_penalty", None)
                    resp = await self._http.post("/api/chat", json=payload)

            resp.raise_for_status()
            data    = resp.json()
            message = data.get("message", {})
            content = message.get("content", "") if isinstance(message, dict) else ""
            return content.strip() if isinstance(content, str) and content.strip() else None

        except httpx.TimeoutException:
            logger.warning(f"[{agent_id}] Timeout HTTP")
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning(f"[{agent_id}] HTTP {exc.response.status_code}: {exc.response.text[:120]}")
            return None
        except Exception as exc:
            logger.error(f"[{agent_id}] Error inesperado: {exc}")
            return None

    # ── Modelo disponible ─────────────────────────────────────────────────────

    async def _model_available(self, model: str) -> bool:
        names = await self._available_models()
        normalized = model.strip()
        if normalized in names:
            return True
        if not normalized.endswith(":latest") and f"{normalized}:latest" in names:
            return True
        short = normalized.removesuffix(":latest")
        return any(n.removesuffix(":latest") == short for n in names)

    async def _available_models(self) -> set[str]:
        cached_at, cached = self._model_cache
        if cached and time.monotonic() - cached_at < 20.0:
            return cached
        try:
            resp = await self._http.get("/api/tags", timeout=5.0)
            resp.raise_for_status()
            names = {
                str(item.get("name") or item.get("model") or "").strip()
                for item in resp.json().get("models", [])
            }
            names.discard("")
            self._model_cache = (time.monotonic(), names)
            return names
        except Exception:
            return cached

    # ── Utilidades ────────────────────────────────────────────────────────────

    @staticmethod
    async def _backoff(attempt: int) -> None:
        """Espera exponencial leve entre reintentos."""
        await asyncio.sleep(0.2 * attempt + random.uniform(0.05, 0.15))

    def _warn(self, agent_id: str, reason: str) -> None:
        key = (agent_id, reason)
        if key not in self._warned:
            self._warned.add(key)
            logger.warning(f"[VOID] {agent_id}: suprimido ({reason})")


# ── API de agente individual (usada por agent_chat._generate) ──────────────

    async def chat(
        self,
        agent_id:      str,
        model:         str,
        system_prompt: str,
        user_prompt:   str,
        options:       dict | None = None,
    ) -> "OllamaResult | None":
        """
        Llamada de turno único para un agente concreto.

        Interfaz utilizada por AgentSession._generate() en agent_chat.py.
        Construye el payload, llama a Ollama, sanitiza y devuelve OllamaResult.
        Devuelve None si la respuesta está vacía o contaminada.
        """
        options = options or {}

        # Construimos un dict compatible con _raw_http_call
        agent_compat = {
            "num_predict": options.get("num_predict", 256),
            "num_ctx":     options.get("num_ctx", 4096),
            "num_gpu":     options.get("num_gpu", 33),
        }
        temp = float(options.get("temperature", 0.7))

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ]

        async with self._gpu_gate:
            raw = await self._raw_http_call(agent_id, model, messages, temp, agent_compat)

        if raw is None:
            return None

        rejection = self._sanitize(raw, [])
        if rejection:
            logger.warning(f"[{agent_id}] chat() respuesta rechazada ({rejection})")
            return None

        return OllamaResult(agent_id=agent_id, text=raw.strip(), attempts=1)


# ═══════════════════════════════════════════════════════════════════════════
# BACKWARD-COMPAT ALIAS — agent_chat.py importa OllamaChatClient
# La clase fue renombrada a VoidOllamaClient durante el refactor
# ═══════════════════════════════════════════════════════════════════════════
OllamaChatClient = VoidOllamaClient