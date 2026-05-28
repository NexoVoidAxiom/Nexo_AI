"""
dispatcher.py — Motor de Enrutamiento VRAM-Aware para Void Axiom
================================================================
Hardware Target: RTX 3090 (24 GB VRAM) · i7-9700K · 32 GB RAM

MAPA DE VRAM (24 GB total)
───────────────────────────────────────────────────────────────
  Arch-7   │ qwen2.5:7b-instruct-q8_0        │ ≈  8.5 GB
  Coda     │ qwen2.5-coder:32b-instruct-q4KM │ ≈ 19.5 GB
  REBx3    │ qwen2.5:3b-instruct-q8_0        │ ≈  3.5 GB
  Intruder │ qwen2.5:1.5b-instruct-q8_0      │ ≈  1.8 GB

SETS ACTIVOS
───────────────────────────────────────────────────────────────
  CHAT_SET  │ Arch-7 + REBx3 + Intruder      │ ≈ 13.8 GB ✓
  CODE_SET  │ Coda   + Intruder               │ ≈ 21.3 GB ✓

FLUJO DE HANDOFF
───────────────────────────────────────────────────────────────
  Usuario → Arch-7 (modo chat, default)
      └─[intent ≥ CODE_THRESHOLD]
          → emitir evento HANDOFF_INIT
          → descargar Arch-7 + REBx3 (liberar 12 GB)
          → cargar Coda 32B
          → responder con streaming
          → restaurar CHAT_SET en background
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import AsyncIterator, Literal

import httpx

from app.config import OLLAMA_CONFIG, HARDWARE_PROFILE
from app.intent_classifier import IntentClassifier, IntentType
from app.void_agents import AGENTS, INTRUDER_AGENT, ALL_AGENTS
from app.void_ollama import OllamaClient

log = logging.getLogger("void.dispatcher")


# ══════════════════════════════════════════════════════════════════════════════
#  CONSTANTES DE VRAM
# ══════════════════════════════════════════════════════════════════════════════

VRAM_BUDGET_GB: float = 24.0

# VRAM aproximada por modelo (Q8/Q4_KM según modelo)
MODEL_VRAM_GB: dict[str, float] = {
    "arch7":    8.5,
    "coda":    19.5,
    "rebx3":    3.5,
    "intruder": 1.8,
}

CHAT_SET = frozenset({"arch7", "rebx3", "intruder"})   # 13.8 GB
CODE_SET = frozenset({"coda", "intruder"})              # 21.3 GB

# Tiempo máximo (segundos) para cargar/descargar un modelo
MODEL_LOAD_TIMEOUT = 45.0
MODEL_UNLOAD_TIMEOUT = 10.0

# Tiempo que Coda permanece cargado tras su última respuesta antes de restaurar chat
CODE_IDLE_TTL = 90.0  # segundos


class DispatchMode(Enum):
    CHAT = auto()  # Arch-7 activo
    CODE = auto()  # Coda activo
    TRANSITIONING = auto()  # swap en curso


@dataclass
class HandoffEvent:
    """Evento de traspaso emitido al cliente vía SSE."""
    from_agent: str
    to_agent: str
    reason: str
    vram_freed_gb: float
    vram_loaded_gb: float
    timestamp: float = field(default_factory=time.time)

    def to_sse(self) -> str:
        return (
            f"\n[HANDOFF] {self.from_agent} → {self.to_agent} | "
            f"Razón: {self.reason} | "
            f"VRAM liberada: {self.vram_freed_gb:.1f}GB → cargada: {self.vram_loaded_gb:.1f}GB\n"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  GESTOR DE VRAM
# ══════════════════════════════════════════════════════════════════════════════

class VRAMManager:
    """
    Controla la carga/descarga de modelos en Ollama.
    
    Ollama no expone una API de gestión de VRAM directa; el truco es:
    - Cargar modelo → POST /api/generate con keep_alive=-1 (permanente)
    - Descargar modelo → POST /api/generate con keep_alive=0 (descarga inmediata)
    
    OLLAMA_MAX_LOADED_MODELS=2 en el entorno garantiza que si intentamos
    cargar un tercero Ollama ejerce el LRU por sí solo, pero controlamos
    el orden manualmente para maximizar predictibilidad.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._loaded: set[str] = set()  # model_keys actualmente en VRAM
        self._lock = asyncio.Lock()

    @property
    def vram_used_gb(self) -> float:
        return sum(MODEL_VRAM_GB.get(k, 0) for k in self._loaded)

    @property
    def vram_free_gb(self) -> float:
        return VRAM_BUDGET_GB - self.vram_used_gb

    async def warm_model(self, model_key: str, model_name: str) -> bool:
        """Precarga un modelo en VRAM enviando un ping vacío con keep_alive infinito."""
        async with httpx.AsyncClient(timeout=MODEL_LOAD_TIMEOUT) as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": model_name,
                        "prompt": "",
                        "keep_alive": -1,
                        "stream": False,
                        "options": {"num_predict": 1, "num_gpu": 99},
                    },
                )
                if resp.status_code == 200:
                    self._loaded.add(model_key)
                    log.info("Modelo cargado: %s (%.1f GB | libre: %.1f GB)",
                             model_name, MODEL_VRAM_GB.get(model_key, 0), self.vram_free_gb)
                    return True
                log.warning("Ollama %d al cargar %s", resp.status_code, model_name)
                return False
            except Exception as exc:
                log.error("Error cargando %s: %s", model_name, exc)
                return False

    async def evict_model(self, model_key: str, model_name: str) -> bool:
        """Descarga un modelo de VRAM (keep_alive=0)."""
        async with httpx.AsyncClient(timeout=MODEL_UNLOAD_TIMEOUT) as client:
            try:
                await client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": model_name,
                        "prompt": "",
                        "keep_alive": 0,
                        "stream": False,
                        "options": {"num_predict": 1},
                    },
                )
                self._loaded.discard(model_key)
                log.info("Modelo descargado: %s", model_name)
                return True
            except Exception as exc:
                log.warning("Error descargando %s: %s", model_name, exc)
                self._loaded.discard(model_key)  # Optimista: asumimos que se descargó
                return False

    async def switch_to_code_set(self) -> float:
        """
        Libera CHAT_SET (Arch-7 + REBx3) y carga Coda.
        Retorna VRAM liberada en GB.
        """
        freed = 0.0

        # Descargar Arch-7 y REBx3 en paralelo
        evict_tasks = []
        for key in ("arch7", "rebx3"):
            if key in self._loaded:
                model_name = ALL_AGENTS.get(
                    {"arch7": "ARCH-7", "rebx3": "REBx3"}[key], {}
                ).get("model", key)
                freed += MODEL_VRAM_GB.get(key, 0)
                evict_tasks.append(self.evict_model(key, model_name))

        if evict_tasks:
            await asyncio.gather(*evict_tasks)

        # Cargar Coda
        coda_model = AGENTS["CODA"]["model"]
        await self.warm_model("coda", coda_model)

        return freed

    async def restore_chat_set(self) -> None:
        """Descarga Coda y recarga Arch-7 + REBx3 en segundo plano."""
        coda_model = AGENTS["CODA"]["model"]
        await self.evict_model("coda", coda_model)

        reload_tasks = [
            self.warm_model("arch7", AGENTS["ARCH-7"]["model"]),
            self.warm_model("rebx3", AGENTS["REBx3"]["model"]),
        ]
        await asyncio.gather(*reload_tasks)
        log.info("CHAT_SET restaurado. VRAM usada: %.1f GB", self.vram_used_gb)


# ══════════════════════════════════════════════════════════════════════════════
#  DISPATCHER PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

class ModelDispatcher:
    """
    Orquestador central de Void Axiom.

    Expone dispatch() como generador asíncrono de tokens.
    El frontend consume el stream SSE directamente.

    Ejemplo de uso en main.py:
        dispatcher = ModelDispatcher()
        await dispatcher.initialize()

        async def stream_endpoint(request: ChatRequest):
            return StreamingResponse(
                dispatcher.dispatch(request.message, request.history),
                media_type="text/event-stream",
            )
    """

    def __init__(self):
        self.ollama_url = OLLAMA_CONFIG["base_url"]
        self.vram = VRAMManager(self.ollama_url)
        self.classifier = IntentClassifier()
        self.ollama = OllamaClient()
        self._mode = DispatchMode.CHAT
        self._last_code_call: float = 0.0
        self._restore_task: asyncio.Task | None = None
        self._dispatch_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Precarga el CHAT_SET al arrancar el servidor."""
        log.info("Void Axiom Dispatcher: iniciando precarga de CHAT_SET...")
        tasks = [
            self.vram.warm_model("arch7",    AGENTS["ARCH-7"]["model"]),
            self.vram.warm_model("rebx3",    AGENTS["REBx3"]["model"]),
            self.vram.warm_model("intruder", INTRUDER_AGENT["model"]),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                log.error("Error en precarga: %s", r)
        log.info("Dispatcher listo. VRAM usada: %.1f / %.1f GB",
                 self.vram.vram_used_gb, VRAM_BUDGET_GB)

    # ── Punto de entrada principal ──────────────────────────────────────────

    async def dispatch(
        self,
        user_input: str,
        history: list[dict],
        requesting_agent: str = "ARCH-7",
    ) -> AsyncIterator[str]:
        """
        Analiza el input, enruta al agente correcto y emite tokens en streaming.

        Flujo:
          1. Clasificar intención
          2. Si CODE intent Y modo chat → ejecutar handoff
          3. Llamar al modelo correcto
          4. Si handoff → programar restauración del CHAT_SET
        """
        intent = self.classifier.classify(user_input)
        log.debug("Input: %.60s... | Intent: %s | Modo: %s",
                  user_input, intent.name, self._mode.name)

        # ── Handoff: chat → code ──────────────────────────────────────────
        if intent in (IntentType.CODE, IntentType.LOGIC) and self._mode == DispatchMode.CHAT:
            async with self._dispatch_lock:
                if self._mode == DispatchMode.CHAT:  # doble check tras lock
                    self._mode = DispatchMode.TRANSITIONING

                    # Cancelar restauración pendiente si existe
                    if self._restore_task and not self._restore_task.done():
                        self._restore_task.cancel()

                    # Emitir evento de handoff al cliente
                    event = HandoffEvent(
                        from_agent="ARCH-7",
                        to_agent="CODA",
                        reason=f"Intent detectado: {intent.name}",
                        vram_freed_gb=MODEL_VRAM_GB["arch7"] + MODEL_VRAM_GB["rebx3"],
                        vram_loaded_gb=MODEL_VRAM_GB["coda"],
                    )
                    yield event.to_sse()

                    # Swap de modelos
                    freed = await self.vram.switch_to_code_set()
                    self._mode = DispatchMode.CODE
                    log.info("Handoff completado. VRAM liberada: %.1f GB", freed)

            # Responder con Coda
            async for token in self._call_agent("CODA", user_input, history):
                yield token

            # Programar restauración en background
            self._last_code_call = time.time()
            self._restore_task = asyncio.create_task(
                self._schedule_restore(CODE_IDLE_TTL)
            )

        # ── Modo code activo (Coda ya cargado) ───────────────────────────
        elif self._mode == DispatchMode.CODE and intent in (IntentType.CODE, IntentType.LOGIC):
            self._last_code_call = time.time()  # Extender TTL
            async for token in self._call_agent("CODA", user_input, history):
                yield token

        # ── Modo transitioning: encolar y esperar ─────────────────────────
        elif self._mode == DispatchMode.TRANSITIONING:
            yield "\n[SISTEMA] Swap de modelos en curso. Espera...\n"
            # Polling hasta que el modo cambie (máximo 30s)
            for _ in range(60):
                await asyncio.sleep(0.5)
                if self._mode != DispatchMode.TRANSITIONING:
                    break
            target = "CODA" if self._mode == DispatchMode.CODE else "ARCH-7"
            async for token in self._call_agent(target, user_input, history):
                yield token

        # ── Modo chat normal ──────────────────────────────────────────────
        else:
            async for token in self._call_agent("ARCH-7", user_input, history):
                yield token

    # ── Helpers privados ────────────────────────────────────────────────────

    async def _call_agent(
        self,
        agent_id: str,
        user_input: str,
        history: list[dict],
    ) -> AsyncIterator[str]:
        """Llama a un agente específico con streaming."""
        agent = ALL_AGENTS.get(agent_id) or INTRUDER_AGENT
        try:
            async for token in self.ollama.stream(
                model=agent["model"],
                system=_build_system(agent),
                history=history,
                prompt=user_input,
                options=_build_options(agent),
            ):
                yield token
        except Exception as exc:
            log.error("Error en agente %s: %s", agent_id, exc)
            # Intentar fallback al primer modelo alternativo
            for fb_model in agent.get("fallback_models", []):
                try:
                    async for token in self.ollama.stream(
                        model=fb_model,
                        system=_build_system(agent),
                        history=history,
                        prompt=user_input,
                        options=_build_options(agent),
                    ):
                        yield token
                    return
                except Exception:
                    continue
            yield f"\n[ERROR] Agente {agent_id} no disponible.\n"

    async def _schedule_restore(self, delay: float) -> None:
        """Restaura el CHAT_SET tras `delay` segundos de inactividad en código."""
        await asyncio.sleep(delay)
        # Si hubo otra llamada reciente, posponer
        if time.time() - self._last_code_call < delay * 0.9:
            self._restore_task = asyncio.create_task(
                self._schedule_restore(delay)
            )
            return
        log.info("Restaurando CHAT_SET (idle %.0fs)...", delay)
        self._mode = DispatchMode.TRANSITIONING
        await self.vram.restore_chat_set()
        self._mode = DispatchMode.CHAT

    async def status(self) -> dict:
        """Devuelve el estado actual del dispatcher (para endpoint /status)."""
        return {
            "mode": self._mode.name,
            "vram_used_gb": round(self.vram.vram_used_gb, 1),
            "vram_free_gb": round(self.vram.vram_free_gb, 1),
            "vram_total_gb": VRAM_BUDGET_GB,
            "loaded_models": list(self.vram._loaded),
            "last_code_call": self._last_code_call,
        }


# ══════════════════════════════════════════════════════════════════════════════
#  UTILIDADES INTERNAS
# ══════════════════════════════════════════════════════════════════════════════

def _build_system(agent: dict) -> str:
    """Combina BASE_SYSTEM_GUARD + prompt de honestidad + prompt del agente."""
    from app.prompts.system_prompts import BASE_SYSTEM_GUARD, HONESTY_PROTOCOL
    return "\n\n".join([
        BASE_SYSTEM_GUARD,
        HONESTY_PROTOCOL,
        agent["system_prompt"],
    ])


def _build_options(agent: dict) -> dict:
    """Construye el dict de opciones para Ollama."""
    return {
        "temperature":       agent.get("temperature", 0.25),
        "top_p":             agent.get("top_p", 0.80),
        "top_k":             agent.get("top_k", 24),
        "repeat_penalty":    agent.get("repeat_penalty", 1.22),
        "repeat_last_n":     agent.get("repeat_last_n", 256),
        "frequency_penalty": agent.get("frequency_penalty", 1.4),
        "presence_penalty":  agent.get("presence_penalty", 0.8),
        "num_ctx":           agent.get("num_ctx", 16384),
        "num_predict":       agent.get("num_predict", 512),
        "num_gpu":           agent.get("num_gpu", 99),
        "stop":              ["\n\n", "◆"],
    }


# Singleton global — importar desde main.py
dispatcher = ModelDispatcher()
