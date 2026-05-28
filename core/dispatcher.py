"""
core/dispatcher.py — Motor de Orquestación VRAM-Aware de Void Axiom.
=====================================================================
Hardware target: GTX 1080 Ti (11 GB VRAM) · i7-9700K · 32 GB RAM

MAPA DE VRAM (24 GB total)
───────────────────────────────────────────────────────────────
  ARCH-7A  │ qwen2.5-coder:7b             │ ≈  4.5 GB  │ primario
  ARCH-7B  │ qwen2.5-coder:7b             │ ≈  4.5 GB  │ swap
  Coda     │ qwen3-coder:14b              │ ≈  8.5 GB
  REBx3    │ qwen2.5-coder:3b             │ ≈  2.0 GB
  Intruder │ qwen2.5-coder:3b             │ ≈  2.0 GB

SETS ACTIVOS
───────────────────────────────────────────────────────────────
  CHAT_SET  │ ARCH-7A + REBx3 + Intruder  │ ≈  8.5 GB ✓
  CODE_SET  │ Coda + Intruder              │ ≈ 10.5 GB ✓

FLUJO DE HANDOFF
───────────────────────────────────────────────────────────────
  Input → Clasificador de intención (< 1ms, sin GPU)
       └─[CODE/LOGIC intent]
           → emitir HandoverEvent (SSE)
           → GPUQueue.submit(swap_to_code_set)
           → responder con Coda en streaming
           → restaurar CHAT_SET en background (90s idle)
       └─[CHAT intent]
           → responder con Arch-7 en streaming
           → REBx3 reacciona (A2A, turno siguiente)
           → Intruso (12% probabilístico)

Si la GPU está ocupada: la tarea se encola en GPUQueue.
Si la cola está llena: HTTP 503, nunca crash.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from enum import Enum, auto
from typing import AsyncIterator

from void_axiom.agents.registry import (
    AGENTS, INTRUDER_ID, INTRUDER_PROBABILITY, get_agent
)
from void_axiom.core.classifier import IntentClassifier, IntentType
from void_axiom.core.gpu_queue import GPUQueue, QueueFullError, gpu_queue
from void_axiom.core.handover import HandoverEvent, NarrativeHandover, narrative_handover
from void_axiom.core.ollama import OllamaClient
from void_axiom.core.vram import VRAMManager, MODEL_VRAM_GB

log = logging.getLogger("void.dispatcher")

# ── Constantes ────────────────────────────────────────────────────────────────
CODE_IDLE_TTL    = 90.0    # Segundos de inactividad antes de restaurar CHAT_SET
MODEL_LOAD_TIMEOUT = 45.0


class DispatchMode(Enum):
    CHAT         = auto()   # Arch-7 + REBx3 + Intruder activos
    CODE         = auto()   # Coda activo
    TRANSITIONING = auto()  # Swap en curso — encolar nuevas solicitudes


# ══════════════════════════════════════════════════════════════════════════════
#  DISPATCHER PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

class ModelDispatcher:
    """
    Orquestador central de Void Axiom.

    Expone `dispatch()` como generador asíncrono de tokens SSE.
    Integra: clasificación de intención → handover narrativo → GPUQueue → streaming.

    Uso en main.py:
        dispatcher = ModelDispatcher()
        await dispatcher.initialize()

        @app.post("/api/chat/stream")
        async def stream(req: ChatRequest):
            return StreamingResponse(
                dispatcher.dispatch(req.message, req.history),
                media_type="text/event-stream",
            )
    """

    def __init__(self) -> None:
        self.vram        = VRAMManager()
        self.classifier  = IntentClassifier()
        self.ollama      = OllamaClient()
        self.handover    = narrative_handover
        self._mode       = DispatchMode.CHAT
        self._last_code_call: float = 0.0
        self._restore_task: asyncio.Task | None = None
        self._mode_lock  = asyncio.Lock()
        self._arch7_turn: int = 0   # Round-robin ARCH-7A / ARCH-7B

    # ── Ciclo de vida ──────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """
        Precarga el CHAT_SET en VRAM al arrancar el servidor.
        Arranca también el worker de GPUQueue.
        """
        gpu_queue.start()
        log.info("Void Axiom Dispatcher: precargando CHAT_SET...")
        await self.vram.warm_set({"arch7a", "rebx3", "intruder"})
        log.info(
            "Dispatcher listo. VRAM usada: %.1f / %.1f GB",
            self.vram.vram_used_gb, self.vram.VRAM_BUDGET_GB
        )

    async def shutdown(self) -> None:
        await gpu_queue.stop()
        await self.ollama.close()

    # ── Punto de entrada principal ─────────────────────────────────────────

    async def dispatch(
        self,
        user_input: str,
        history: list[dict],
        user_priority: int = 2,
    ) -> AsyncIterator[str]:
        """
        Analiza el input, enruta al agente correcto y emite tokens en streaming.

        Args:
            user_input:     Mensaje del usuario.
            history:        Historial de conversación (lista de dicts role/content).
            user_priority:  0=pioneer, 1=max, 2=free.

        Yields:
            Chunks de texto o eventos SSE de handover.
        """
        intent = self.classifier.classify(user_input)
        log.debug(
            "Input: %.60s… | intent=%s | mode=%s",
            user_input, intent.name, self._mode.name
        )

        # ── Caso: se necesita modo CODE y estamos en CHAT ─────────────────
        if intent in (IntentType.CODE, IntentType.LOGIC) and self._mode == DispatchMode.CHAT:
            async for chunk in self._handoff_to_code(user_input, history, user_priority):
                yield chunk
            return

        # ── Caso: ya estamos en CODE ──────────────────────────────────────
        if self._mode == DispatchMode.CODE and intent in (IntentType.CODE, IntentType.LOGIC):
            self._last_code_call = time.time()
            async for chunk in self._stream_agent("CODA", user_input, history, user_priority):
                yield chunk
            return

        # ── Caso: transición en curso → encolar ───────────────────────────
        if self._mode == DispatchMode.TRANSITIONING:
            yield '\ndata: {"type":"system","text":"Swap de modelos en curso…"}\n\n'
            for _ in range(60):
                await asyncio.sleep(0.5)
                if self._mode != DispatchMode.TRANSITIONING:
                    break
            target = "CODA" if self._mode == DispatchMode.CODE else "ARCH-7A"
            async for chunk in self._stream_agent(target, user_input, history, user_priority):
                yield chunk
            return

        # ── Caso: modo CHAT normal ─────────────────────────────────────────
        last_response = ""
        arch7_agent = "ARCH-7A" if self._arch7_turn % 2 == 0 else "ARCH-7B"
        self._arch7_turn += 1
        async for chunk in self._stream_agent(arch7_agent, user_input, history, user_priority):
            yield chunk
            last_response += chunk

        # REBx3 reacciona al turno de ARCH-7A o ARCH-7B
        if last_response.strip():
            async for chunk in self._rebel_reaction(
                user_input, history, last_response, user_priority, arch7_agent
            ):
                yield chunk

        # Intruso (probabilístico)
        if random.random() < INTRUDER_PROBABILITY:
            async for chunk in self._intruder_strike(user_priority):
                yield chunk

    # ── Handoff chat → code ────────────────────────────────────────────────

    async def _handoff_to_code(
        self,
        user_input: str,
        history: list[dict],
        priority: int,
    ) -> AsyncIterator[str]:
        """Ejecuta el swap CHAT_SET → CODE_SET con handover narrativo."""
        async with self._mode_lock:
            if self._mode != DispatchMode.CHAT:
                # Otra solicitud ya activó el swap; ir directo
                async for chunk in self._stream_agent("CODA", user_input, history, priority):
                    yield chunk
                return

            self._mode = DispatchMode.TRANSITIONING

            # Cancelar restauración pendiente si existe
            if self._restore_task and not self._restore_task.done():
                self._restore_task.cancel()

        # Handover narrativo (no requiere GPU)
        event, _ = await self.handover.emit_and_inject(
            from_agent    = arch7_agent,
            to_agent      = "CODA",
            last_response = "",
            reason        = f"Intent: {self.classifier.classify(user_input).name}",
            vram_freed_gb = MODEL_VRAM_GB["arch7a"] + MODEL_VRAM_GB["rebx3"],
            vram_loaded_gb= MODEL_VRAM_GB["coda"],
        )
        yield event.to_sse()
        yield event.to_terminal()

        # Swap de modelos a través de la GPU queue (no crashea si GPU ocupada)
        try:
            freed = await gpu_queue.submit(
                coro_fn  = lambda: self.vram.switch_to_code_set(),
                priority = 0,  # El swap siempre es máxima prioridad
                timeout  = MODEL_LOAD_TIMEOUT + 10,
            )
        except QueueFullError as exc:
            yield f'\ndata: {{"type":"error","text":"{exc}"}}\n\n'
            self._mode = DispatchMode.CHAT
            return

        self._mode = DispatchMode.CODE
        log.info("Handoff completado. VRAM liberada: %.1f GB", freed)

        # Responder con Coda
        last_response = ""
        async for chunk in self._stream_agent("CODA", user_input, history, priority):
            yield chunk
            last_response += chunk

        # Programar restauración en background
        self._last_code_call = time.time()
        self._restore_task = asyncio.create_task(
            self._schedule_restore(CODE_IDLE_TTL),
            name="restore_chat_set"
        )

    # ── REBx3 reacción ─────────────────────────────────────────────────────

    async def _rebel_reaction(
        self,
        user_input: str,
        history: list[dict],
        arch7_response: str,
        priority: int,
        from_agent: str = "ARCH-7A",
    ) -> AsyncIterator[str]:
        """REBx3 reacciona al último turno de ARCH-7A o ARCH-7B."""
        event, injection = await self.handover.emit_and_inject(
            from_agent    = from_agent,
            to_agent      = "REBx3",
            last_response = arch7_response,
            reason        = "Reacción de REBx3",
        )
        yield event.to_sse()

        # Construir prompt enriquecido con el gancho narrativo
        rebel_input = f"{injection}\nUsuario dijo: {user_input}" if injection else user_input
        async for chunk in self._stream_agent("REBx3", rebel_input, history, priority):
            yield chunk

    # ── Intruder strike ────────────────────────────────────────────────────

    async def _intruder_strike(self, priority: int) -> AsyncIterator[str]:
        """El Intruso interrumpe — sin contexto, sin historial."""
        event, _ = await self.handover.emit_and_inject(
            from_agent    = "SYSTEM",
            to_agent      = INTRUDER_ID,
            last_response = "",
            reason        = "Interferencia probabilística",
        )
        yield event.to_sse()
        async for chunk in self._stream_agent(INTRUDER_ID, "[GLITCH]", [], priority):
            yield chunk

    # ── Stream de un agente ────────────────────────────────────────────────

    async def _stream_agent(
        self,
        agent_id: str,
        user_input: str,
        history: list[dict],
        priority: int = 2,
    ) -> AsyncIterator[str]:
        """
        Llama a un agente a través de la GPUQueue y emite chunks.
        Si la cola está llena → yield error SSE, no crash.
        """
        agent = get_agent(agent_id)

        async def _inference():
            chunks = []
            async for token in self.ollama.stream(
                model   = agent.model,
                system  = agent.system_prompt,
                history = history,
                prompt  = user_input,
                options = agent.inference_options(),
            ):
                chunks.append(token)
            return "".join(chunks)

        # Emitir header del agente
        yield f'\ndata: {{"type":"agent_start","agent":"{agent_id}"}}\n\n'

        try:
            # Encolar en la GPU queue
            full_response = await gpu_queue.submit(
                coro_fn  = _inference,
                priority = priority,
            )
            # Emitir la respuesta en chunks simulados (streaming real en v2)
            for chunk in _chunk_response(full_response, size=4):
                yield f'data: {{"type":"token","agent":"{agent_id}","text":{chunk!r}}}\n\n'

        except QueueFullError as exc:
            yield f'\ndata: {{"type":"error","agent":"{agent_id}","text":"{exc}"}}\n\n'
        except Exception as exc:
            log.error("Error en agente %s: %s", agent_id, exc)
            # Intentar fallback
            for fb_model in agent.fallback_models:
                try:
                    async for token in self.ollama.stream(
                        model   = fb_model,
                        system  = agent.system_prompt,
                        history = history,
                        prompt  = user_input,
                        options = agent.inference_options(),
                    ):
                        yield f'data: {{"type":"token","agent":"{agent_id}","text":{token!r}}}\n\n'
                    return
                except Exception:
                    continue
            yield f'\ndata: {{"type":"error","agent":"{agent_id}","text":"Agente no disponible"}}\n\n'

        yield f'\ndata: {{"type":"agent_end","agent":"{agent_id}"}}\n\n'

    # ── Restauración del CHAT_SET ──────────────────────────────────────────

    async def _schedule_restore(self, delay: float) -> None:
        """Restaura el CHAT_SET tras `delay` segundos de inactividad en código."""
        await asyncio.sleep(delay)
        # Si hubo otra llamada reciente, posponer
        if time.time() - self._last_code_call < delay * 0.9:
            self._restore_task = asyncio.create_task(
                self._schedule_restore(delay), name="restore_chat_set"
            )
            return

        log.info("Restaurando CHAT_SET (idle %.0fs)…", delay)
        self._mode = DispatchMode.TRANSITIONING
        await self.vram.restore_chat_set()
        self._mode = DispatchMode.CHAT
        log.info("CHAT_SET restaurado.")

    # ── Estado ────────────────────────────────────────────────────────────

    async def status(self) -> dict:
        return {
            "mode":          self._mode.name,
            "vram_used_gb":  round(self.vram.vram_used_gb, 1),
            "vram_free_gb":  round(self.vram.vram_free_gb, 1),
            "vram_total_gb": self.vram.VRAM_BUDGET_GB,
            "loaded_models": list(self.vram.loaded),
            "queue_length":  gpu_queue.queue_length,
            "gpu_busy":      gpu_queue.is_busy,
        }


# ── Utilidades ────────────────────────────────────────────────────────────────

def _chunk_response(text: str, size: int = 4) -> list[str]:
    """Divide una respuesta completa en chunks del tamaño indicado."""
    return [text[i:i+size] for i in range(0, len(text), size)]


# ── Singleton global ──────────────────────────────────────────────────────────
dispatcher = ModelDispatcher()
