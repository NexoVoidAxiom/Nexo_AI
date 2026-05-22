"""
Cliente Ollama defensivo para Void Axiom.

No devuelve texto de emergencia. Si Ollama falla, la salida se suprime y el
orquestador salta al siguiente agente.
"""

from __future__ import annotations

import asyncio
import os
import random
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
        return value
    return value or "-1"


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
        concurrency = int(os.getenv("VOID_OLLAMA_CONCURRENCY", "1"))
        limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
        self._client = httpx.AsyncClient(timeout=90.0, limits=limits)
        self._gpu_gate = asyncio.Semaphore(max(1, concurrency))
        self._last_warning: dict[str, str] = {}

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

    async def _sleep_before_retry(self, attempt: int) -> None:
        if attempt <= self.max_retries:
            await asyncio.sleep(0.18 * attempt + random.uniform(0.03, 0.18))

    def _warn_once(self, agent_id: str, reason: str) -> None:
        if self._last_warning.get(agent_id) == reason:
            return
        self._last_warning[agent_id] = reason
        print(f"[VOID][WARN] {agent_id}: output suppressed ({reason})", flush=True)

