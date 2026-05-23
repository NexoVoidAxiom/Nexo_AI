"""
Cliente Ollama defensivo para Void Axiom.

No devuelve texto de emergencia. Si Ollama falla, la salida se suprime y el
orquestador salta al siguiente agente.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import OLLAMA_CONFIG
from app.void_agents import BASE_SYSTEM_GUARD, CORRUPT_RESPONSE_MARKERS, STOP_SEQUENCES
from app.void_memory import contains_corrupt_marker


def _normalize_base_url(value: str) -> str:
    value = str(value or "http://127.0.0.1:11434").strip().rstrip("/")
    if not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    return value


def _normalize_keep_alive(value: str) -> str:
    value = str(value).strip()
    if value.lstrip("-").isdigit():
        return f"{value}s"
    return value or "-1s"


@dataclass(frozen=True)
class OllamaResult:
    model: str
    text: str
    attempts: int


class OllamaChatClient:
    """HTTP client con reintentos, cola GPU y supresion de salidas corruptas."""

    def __init__(self) -> None:
        self.base_url = _normalize_base_url(
            os.getenv("VOID_OLLAMA_BASE_URL", OLLAMA_CONFIG.get("base_url", ""))
        )
        self.keep_alive = _normalize_keep_alive(os.getenv("OLLAMA_KEEP_ALIVE", "-1"))
        self.max_retries = int(os.getenv("VOID_OLLAMA_HTTP_RETRIES", "2"))
        concurrency = int(os.getenv("VOID_OLLAMA_CONCURRENCY", "4"))
        limits = httpx.Limits(max_connections=16, max_keepalive_connections=8)
        self._client = httpx.AsyncClient(timeout=90.0, limits=limits)
        self._gpu_gate = asyncio.Semaphore(max(1, concurrency))
        self._warned: set[tuple[str, str]] = set()
        self._model_cache: tuple[float, set[str]] = (0.0, set())

    async def close(self) -> None:
        await self._client.aclose()

    async def chat(
        self,
        *,
        agent_id: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        options: dict[str, Any],
    ) -> OllamaResult | None:
        runtime_system = f"{BASE_SYSTEM_GUARD}\n\nIDENTIDAD ACTIVA:\n{system_prompt}".strip()
        if not await self.model_available(model):
            self._warn_once(agent_id, f"model_not_installed:{model}")
            return None

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": runtime_system},
                {"role": "user", "content": user_prompt},
            ],
            "options": {
                **options,
                "stop": STOP_SEQUENCES,
            },
            "stream": False,
            "keep_alive": self.keep_alive,
        }

        last_reason = "unknown"
        for attempt in range(1, self.max_retries + 2):
            try:
                async with self._gpu_gate:
                    resp = await self._client.post(
                        f"{self.base_url}/api/chat",
                        json=payload,
                        timeout=90.0,
                    )
                if resp.status_code >= 400:
                    last_reason = f"http_{resp.status_code}"
                    if resp.status_code == 404:
                        self._warn_once(agent_id, f"model_not_found:{model}")
                        return None
                    if resp.status_code == 400 and self._strip_unsupported_options(payload, resp.text):
                        self._warn_once(agent_id, "native_options_downgraded")
                        continue
                    await self._sleep_before_retry(attempt)
                    continue

                data = resp.json()
                text = self._extract_text(data)
                if self._is_corrupt(text):
                    last_reason = "corrupt_or_empty"
                    await self._sleep_before_retry(attempt)
                    continue

                return OllamaResult(model=model, text=text, attempts=attempt)
            except (httpx.TimeoutException, httpx.TransportError):
                last_reason = "transport"
            except ValueError:
                last_reason = "bad_json"
            except Exception:
                last_reason = "unexpected"

            await self._sleep_before_retry(attempt)

        self._warn_once(agent_id, last_reason)
        return None

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        message = data.get("message")
        if isinstance(message, dict):
            value = message.get("content", "")
            if isinstance(value, str):
                return value.strip()
        value = data.get("response", "")
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _is_corrupt(text: str) -> bool:
        if not str(text or "").strip():
            return True
        if contains_corrupt_marker(text):
            return True
        lowered = text.lower()
        return any(marker in lowered for marker in CORRUPT_RESPONSE_MARKERS)

    @staticmethod
    def _strip_unsupported_options(payload: dict[str, Any], error_text: str) -> bool:
        """Ollama native /api/chat may lag OpenAI-compatible fields.

        We keep frequency/presence penalties in config and Modelfiles as requested,
        but if the local native API rejects them, we remove only those fields and
        retry. The anti-loop filter still enforces the behavior at backend level.
        """
        lowered = str(error_text or "").lower()
        options = payload.get("options")
        if not isinstance(options, dict):
            return False
        changed = False
        for key in ("frequency_penalty", "presence_penalty"):
            if key in options and (key in lowered or "invalid" in lowered or "unknown" in lowered):
                options.pop(key, None)
                changed = True
        return changed

    async def model_available(self, model: str) -> bool:
        names = await self._available_models()
        normalized = str(model or "").strip()
        if normalized in names:
            return True
        if not normalized.endswith(":latest") and f"{normalized}:latest" in names:
            return True
        short = normalized.removesuffix(":latest")
        return any(item.removesuffix(":latest") == short for item in names)

    async def _available_models(self) -> set[str]:
        cached_at, cached_names = self._model_cache
        if cached_names and time.monotonic() - cached_at < 20.0:
            return cached_names
        try:
            resp = await self._client.get(f"{self.base_url}/api/tags", timeout=5.0)
            resp.raise_for_status()
            names = {
                str(item.get("name") or item.get("model") or "").strip()
                for item in resp.json().get("models", [])
            }
            names.discard("")
            self._model_cache = (time.monotonic(), names)
            return names
        except Exception:
            return cached_names

    async def _sleep_before_retry(self, attempt: int) -> None:
        if attempt <= self.max_retries:
            await asyncio.sleep(0.18 * attempt + random.uniform(0.03, 0.18))

    def _warn_once(self, agent_id: str, reason: str) -> None:
        key = (agent_id, reason)
        if key in self._warned:
            return
        self._warned.add(key)
        print(f"[VOID][WARN] {agent_id}: output suppressed ({reason})", flush=True)
