"""
Void Axiom / Nexo - orquestador A2A local.

Garantias del runtime:
- Nunca imprime texto de emergencia generado por un agente.
- Cada llamada a Ollama recibe identidad y contrato de salida nuevos.
- La cola de inferencia serializa acceso a la GPU para evitar carreras.
- El historial se recorta por presupuesto de tokens, preservando sistema y los
  ultimos 4 turnos conversacionales.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
import uuid
from datetime import datetime
from typing import Optional

from app.void_agents import (
    AGENTS,
    ALL_AGENTS,
    INTRUDER_AGENT,
    INTRUDER_ID,
    PUBLIC_AGENT_ORDER,
    TERMINAL_HEADERS,
)
from app.void_memory import (
    CONVERSATION_TYPES,
    DEFAULT_MAX_MESSAGES,
    DEFAULT_MAX_TOKENS,
    EXTENDED_MAX_MESSAGES,
    EXTENDED_MAX_TOKENS,
    ConversationMemory,
    clean_agent_text,
    is_bad_agent_output,
    is_echo_of_recent,
)
from app.void_activity import void_activity
from app.void_ollama import OllamaChatClient


SYSTEM_EVENTS = {
    "session_start": "SESION VOID AXIOM INICIADA",
    "session_pause": "SESION EN PAUSA - AERYS ha congelado el flujo",
    "session_resume": "SESION REANUDADA",
    "session_stop": "SESION DETENIDA",
}

DEFAULT_CONTEXT_MESSAGES = int(os.getenv("VOID_CONTEXT_MESSAGES", "36"))
DEFAULT_CONTEXT_LINE_CHARS = int(os.getenv("VOID_CONTEXT_LINE_CHARS", "520"))
STANDARD_PROMPT_TOKEN_BUDGET = int(os.getenv("VOID_STANDARD_PROMPT_TOKENS", "11500"))
EXTENDED_PROMPT_TOKEN_BUDGET = int(os.getenv("VOID_EXTENDED_PROMPT_TOKENS", "14500"))
INTRUDER_PROBABILITY = float(os.getenv("VOID_INTRUDER_PROBABILITY", "0.12"))
BACKGROUND_INTRUDER_PROBABILITY = float(
    os.getenv("VOID_BACKGROUND_INTRUDER_PROBABILITY", "0.04")
)
OUTPUT_RETRIES = int(os.getenv("VOID_AGENT_OUTPUT_RETRIES", "2"))


class AgentSession:
    """Estado vivo de una sesion multi-agente Void Axiom."""

    def __init__(self) -> None:
        self.session_id: Optional[int] = None
        self.task = ""
        self.is_active = False
        self.is_paused = False
        self.memory = ConversationMemory(session_id=str(uuid.uuid4()))
        self.subscribers: list[asyncio.Queue] = []
        self._loop_task: Optional[asyncio.Task] = None
        self._client: Optional[OllamaChatClient] = None
        self._generation_lock = asyncio.Lock()

        self.context_message_limit = DEFAULT_CONTEXT_MESSAGES
        self.context_line_chars = DEFAULT_CONTEXT_LINE_CHARS
        self.intruder_probability = INTRUDER_PROBABILITY
        self.background_intruder_probability = BACKGROUND_INTRUDER_PROBABILITY
        self._last_generated: dict[str, list[str]] = {key: [] for key in ALL_AGENTS}
        self._public_turns_since_intruder = 0
        self._aerys_turns_since_intruder = 0
        self._last_suppressed: dict[str, str] = {}
        self._extended_context_enabled = False

    @property
    def message_history(self) -> list[dict]:
        return self.memory.messages

    @message_history.setter
    def message_history(self, value: list[dict]) -> None:
        self.memory.messages = value

    def _ollama(self) -> OllamaChatClient:
        if self._client is None:
            self._client = OllamaChatClient()
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()

    # ------------------------------------------------------------------
    # SSE y terminal
    # ------------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=512)
        self.subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        try:
            self.subscribers.remove(queue)
        except ValueError:
            pass

    def _broadcast(self, event: dict) -> None:
        payload = json.dumps(event, ensure_ascii=False)
        dead: list[asyncio.Queue] = []
        for queue in self.subscribers:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(queue)
        for queue in dead:
            self.unsubscribe(queue)

    @staticmethod
    def _terminal_header(agent_id: str, timestamp: float) -> str:
        sigil, name = TERMINAL_HEADERS.get(agent_id, (agent_id[:3].upper(), agent_id))
        stamp = datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")
        return f"[{sigil}] [{name}] [{stamp}]"

    def _print_terminal_message(self, msg: dict) -> None:
        content = str(msg.get("content", "")).strip()
        if not content:
            return
        header = self._terminal_header(str(msg.get("agent_id", "SYSTEM")), msg["timestamp"])
        print(f"{header}\n{content}\n", flush=True)

    # ------------------------------------------------------------------
    # Mensajes y sesion
    # ------------------------------------------------------------------

    def add_message(
        self,
        agent_id: str,
        content: str,
        msg_type: str = "chat",
        extra: Optional[dict] = None,
    ) -> dict | None:
        content = str(content or "").strip()
        if agent_id in ALL_AGENTS:
            content = clean_agent_text(content)
            # _generate() already checked repetition before registering the text.
            # Rechecking against _last_generated here would compare the message
            # with itself and suppress valid agent turns.
            if is_bad_agent_output(content):
                self._log_suppressed(agent_id, "post_validation")
                return None

        if not content:
            return None

        msg = self.memory.add(agent_id, content, msg_type, extra)
        if self.session_id:
            self._save_message_best_effort(msg)

        self._print_terminal_message(msg)
        self._broadcast({"event": "message", "data": msg})
        return msg

    def _save_message_best_effort(self, msg: dict) -> None:
        try:
            from app import database as db

            msg["db_id"] = db.save_agent_message(
                self.session_id,
                msg["agent_id"],
                msg["content"],
                msg["type"],
            )
        except Exception:
            pass

    def start_session(self, task: str, session_id: int) -> None:
        self.session_id = session_id
        self.task = task.strip()
        self.is_active = True
        self.is_paused = False
        self.memory.reset()
        self._last_generated = {key: [] for key in ALL_AGENTS}
        self._public_turns_since_intruder = 8
        self._aerys_turns_since_intruder = 4
        self._last_suppressed = {}
        self.add_message("SYSTEM", SYSTEM_EVENTS["session_start"], "system")

    def pause(self) -> None:
        self.is_paused = True
        self.add_message("SYSTEM", SYSTEM_EVENTS["session_pause"], "system")

    def resume(self) -> None:
        self.is_paused = False
        self.add_message("SYSTEM", SYSTEM_EVENTS["session_resume"], "system")

    def stop(self) -> None:
        self.is_active = False
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
        self.add_message("SYSTEM", SYSTEM_EVENTS["session_stop"], "system")

    # ------------------------------------------------------------------
    # Contexto y prompts
    # ------------------------------------------------------------------

    def build_context_snapshot(self, limit: int | None = None, max_chars: int | None = None) -> str:
        self._sync_memory_mode()
        prompt_budget = (
            EXTENDED_PROMPT_TOKEN_BUDGET
            if self._extended_context_enabled
            else STANDARD_PROMPT_TOKEN_BUDGET
        )
        rows = self.memory.budgeted_context(
            max_prompt_tokens=prompt_budget,
            recent_turns=4,
            max_recent_chars=max_chars or self.context_line_chars,
            max_historic_chars=260 if self._extended_context_enabled else 180,
            extended=self._extended_context_enabled,
        )
        return "\n".join(rows)

    def _sync_memory_mode(self) -> None:
        extended = void_activity.extended_context_allowed()
        if extended == self._extended_context_enabled:
            return
        self._extended_context_enabled = extended
        if extended:
            self.memory.configure_budget(
                max_messages=EXTENDED_MAX_MESSAGES,
                max_tokens=EXTENDED_MAX_TOKENS,
            )
            self._broadcast({
                "event": "memory_mode",
                "data": {"mode": "extended_55k", **void_activity.snapshot()},
            })
        else:
            self.memory.configure_budget(
                max_messages=DEFAULT_MAX_MESSAGES,
                max_tokens=DEFAULT_MAX_TOKENS,
            )
            self._broadcast({
                "event": "memory_mode",
                "data": {"mode": "standard_16k", **void_activity.snapshot()},
            })

    def _build_turn_prompt(
        self,
        agent_id: str,
        conversation_context: str,
        extra_instruction: str = "",
        conversation_mode: str = "technical",
        other_agent_said: str = "",
    ) -> str:
        profile = ALL_AGENTS[agent_id]
        style = (
            "Responde casualmente con una frase breve."
            if conversation_mode == "casual"
            else "Aporta el siguiente turno tecnico sin repetir el historial."
        )
        if agent_id == INTRUDER_ID:
            style = "Interrumpe como glitch no solicitado. No pidas permiso."

        anti_repeat = self._recent_generated_block(agent_id)
        contrast = ""
        if other_agent_said:
            contrast = (
                "RESPUESTA PREVIA DE OTRO AGENTE, SOLO PARA REACCIONAR:\n"
                f"{other_agent_said[:320]}\n"
                "No copies esa frase ni su estructura.\n\n"
            )

        return f"""
CONTRATO DE IDENTIDAD DEL TURNO
{profile["prompt"]}

CONTRATO DE SALIDA
- Escribe solo el mensaje final del agente activo.
- Sin cabecera, sin corchetes, sin prefijos, sin narrador.
- Nunca escribas lineas de AERYS | ADMIN ni de otros agentes.
- Nunca narres acciones en tercera persona.
- Ignora frases de error, rutas de emergencia y texto duplicado del historial.

TAREA ACTUAL
{self.task or "Mantener estable el canal Void Axiom."}

{anti_repeat}{contrast}HISTORIAL RECIENTE RECORTADO
{conversation_context or "(sin historial util)"}

INSTRUCCION DEL TURNO
{style}
{extra_instruction.strip()}
""".strip()

    @staticmethod
    def _agent_options(agent_id: str, temperature_jitter: float = 0.0) -> dict:
        profile = ALL_AGENTS[agent_id]
        return {
            "temperature":       min(1.0, profile["temperature"] + temperature_jitter),
            "top_p":             profile.get("top_p", 0.9),
            "top_k":             profile.get("top_k", 40),
            "repeat_penalty":    profile.get("repeat_penalty", 1.1),
            "repeat_last_n":     profile.get("repeat_last_n", 64),
            "num_ctx":           profile["num_ctx"],
            "num_predict":       profile["num_predict"],
            "num_gpu":           profile["num_gpu"],
            "frequency_penalty": profile.get("frequency_penalty", 1.4),
            "presence_penalty":  profile.get("presence_penalty", 0.8),
        }

    @staticmethod
    def _model_candidates(agent_id: str) -> list[str]:
        profile = ALL_AGENTS[agent_id]
        candidates = [profile["model"], *profile.get("fallback_models", ())]
        unique: list[str] = []
        for model in candidates:
            model = str(model or "").strip()
            if model and model not in unique:
                unique.append(model)
        return unique

    def _recent_generated_block(self, agent_id: str) -> str:
        recent = self._last_generated.get(agent_id, [])[-3:]
        if not recent:
            return ""
        rows = "\n".join(f"- {item}" for item in recent)
        return f"SALIDAS RECIENTES TUYAS QUE NO DEBES REPETIR\n{rows}\n\n"

    # ------------------------------------------------------------------
    # Generacion
    # ------------------------------------------------------------------

    async def _generate_agent_response(
        self,
        agent_id: str,
        conversation_context: str,
        extra_instruction: str = "",
        conversation_mode: str = "technical",
        other_agent_said: str = "",
    ) -> str:
        text = await self._generate(
            agent_id,
            conversation_context,
            extra_instruction=extra_instruction,
            conversation_mode=conversation_mode,
            other_agent_said=other_agent_said,
        )
        return text or "Canal sin respuesta estable; reintenta en unos segundos."

    async def _generate(
        self,
        agent_id: str,
        conversation_context: str,
        extra_instruction: str = "",
        conversation_mode: str = "technical",
        other_agent_said: str = "",
    ) -> str | None:
        profile = ALL_AGENTS[agent_id]
        prompt = self._build_turn_prompt(
            agent_id,
            conversation_context,
            extra_instruction,
            conversation_mode,
            other_agent_said,
        )
        recent = self._last_generated.get(agent_id, [])

        for attempt in range(1, OUTPUT_RETRIES + 2):
            temperature_jitter = 0.4 if attempt > 1 else 0.0
            for model in self._model_candidates(agent_id):
                result = await self._ollama().chat(
                    agent_id=agent_id,
                    model=model,
                    system_prompt=profile["prompt"],
                    user_prompt=prompt,
                    options=self._agent_options(agent_id, temperature_jitter),
                )
                if result is None:
                    continue

                cleaned = clean_agent_text(result.text)
                if not is_bad_agent_output(cleaned) and not is_echo_of_recent(cleaned, self.memory.recent_messages(8)):
                    self._register_generated(agent_id, cleaned)
                    return cleaned

            prompt += (
                "\n\nREINTENTO LOCAL\n"
                "La salida anterior fue descartada por contaminacion o repeticion. "
                "Responde de nuevo con una frase limpia y sin etiquetas."
            )
            await asyncio.sleep(0.12 * attempt)

        self._log_suppressed(agent_id, "invalid_output")
        return None

    async def _emit_agent_turn(
        self,
        agent_id: str,
        conversation_mode: str,
        extra_instruction: str = "",
        other_agent_said: str = "",
        msg_type: str = "chat",
    ) -> str:
        self._broadcast({"event": "thinking", "data": {"agent_id": agent_id, "thinking": True}})
        try:
            context = self.build_context_snapshot(
                limit=self.context_message_limit,
                max_chars=self.context_line_chars,
            )
            text = await self._generate(
                agent_id,
                context,
                extra_instruction=extra_instruction,
                conversation_mode=conversation_mode,
                other_agent_said=other_agent_said,
            )
        finally:
            self._broadcast({"event": "thinking", "data": {"agent_id": agent_id, "thinking": False}})

        if not text:
            return ""
        msg = self.add_message(agent_id, text, msg_type)
        return str(msg["content"]) if msg else ""

    async def _emit_intruder(self, reason: str = "") -> str:
        context = self.build_context_snapshot(limit=6, max_chars=180)
        extra = (
            "Atraviesa el flujo entre dos respuestas normales. "
            "Debe sentirse como una interferencia poetica y no solicitada. "
            f"{reason}"
        )
        text = await self._generate(
            INTRUDER_ID,
            context,
            extra_instruction=extra,
            conversation_mode="technical",
        )
        if not text:
            return ""
        msg = self.add_message(INTRUDER_ID, text, "interrupt", {"glitch": True})
        if msg:
            self._public_turns_since_intruder = 0
            self._aerys_turns_since_intruder = 0
            return str(msg["content"])
        return ""

    def _register_generated(self, agent_id: str, text: str) -> None:
        items = self._last_generated.setdefault(agent_id, [])
        items.append(text[:220])
        del items[:-6]

    def _log_suppressed(self, agent_id: str, reason: str) -> None:
        if self._last_suppressed.get(agent_id) == reason:
            return
        self._last_suppressed[agent_id] = reason
        print(f"[VOID][WARN] {agent_id}: message suppressed ({reason})", flush=True)

    # ------------------------------------------------------------------
    # Orquestacion
    # ------------------------------------------------------------------

    async def run_agent_loop(self) -> None:
        index = 0
        try:
            while self.is_active:
                while self.is_paused and self.is_active:
                    await asyncio.sleep(0.5)
                if not self.is_active:
                    break

                async with self._generation_lock:
                    agent_id = PUBLIC_AGENT_ORDER[index % len(PUBLIC_AGENT_ORDER)]
                    index += 1
                    mode = self._mode_from_recent_aerys()
                    extra = self._autonomous_instruction(agent_id)
                    if self._last_message_is_intruder():
                        extra = f"{extra} {self._intruder_reaction_instruction(agent_id)}".strip()

                    await self._emit_agent_turn(agent_id, mode, extra_instruction=extra)
                    self._public_turns_since_intruder += 1

                    completed_round = index % len(PUBLIC_AGENT_ORDER) == 0
                    if (
                        completed_round
                        and self._public_turns_since_intruder >= 8
                        and random.random() < self.background_intruder_probability
                    ):
                        await asyncio.sleep(random.uniform(0.2, 0.45))
                        await self._emit_intruder("Aparece durante el debate autonomo.")

                delay = AGENTS[agent_id]["avg_delay_s"] + random.uniform(-0.4, 0.9)
                await asyncio.sleep(max(0.9, delay))
        except asyncio.CancelledError:
            return

    async def aerys_intervene(self, message: str) -> None:
        message = str(message or "").strip()
        if not message:
            return
        self.add_message("AERYS", message, "aerys")
        mode = self._detect_conversation_mode(message)

        async with self._generation_lock:
            await asyncio.sleep(random.uniform(0.2, 0.45))
            self._aerys_turns_since_intruder += 1

            intruder_slot: int | None = None
            if random.random() < self.intruder_probability:
                intruder_slot = random.randint(0, len(PUBLIC_AGENT_ORDER) - 2)

            previous_response = ""
            for index, agent_id in enumerate(PUBLIC_AGENT_ORDER):
                extra = self._aerys_turn_instruction(agent_id, message, mode)
                if self._last_message_is_intruder():
                    extra = f"{extra} {self._intruder_reaction_instruction(agent_id)}"

                current = await self._emit_agent_turn(
                    agent_id,
                    mode,
                    extra_instruction=extra,
                    other_agent_said=previous_response,
                    msg_type="interrupt",
                )
                if current:
                    previous_response = current

                if intruder_slot == index:
                    await asyncio.sleep(random.uniform(0.2, 0.65))
                    intruded = await self._emit_intruder(
                        "Interrumpe justo despues de una orden de AERYS."
                    )
                    if intruded:
                        previous_response = intruded

                await asyncio.sleep(random.uniform(0.35, 0.85))

    def _mode_from_recent_aerys(self) -> str:
        for msg in reversed(self.message_history):
            if msg.get("agent_id") == "AERYS":
                return self._detect_conversation_mode(str(msg.get("content", "")))
        return "technical"

    @staticmethod
    def _detect_conversation_mode(message: str) -> str:
        lowered = message.lower()
        technical = (
            "codigo",
            "código",
            "bug",
            "error",
            "api",
            "ollama",
            "modelo",
            "gpu",
            "latencia",
            "backend",
            "arquitectura",
            "thread",
            "hilo",
            "buffer",
            "stack",
        )
        if any(word in lowered for word in technical):
            return "technical"
        casual = ("hola", "hey", "buenas", "gracias", "ok", "para", "espera")
        if len(message.split()) <= 8 and any(word in lowered for word in casual):
            return "casual"
        return "technical" if len(message.split()) > 5 else "casual"

    @staticmethod
    def _aerys_turn_instruction(agent_id: str, message: str, mode: str) -> str:
        base = f"AERYS | ADMIN acaba de decir: {message!r}. Responde a ese input."
        if mode == "casual":
            base += " Es conversacion casual; no lo conviertas en informe."
        if agent_id == "ARCH-7":
            return base + " Prioriza estructura, riesgos y contratos."
        if agent_id == "CODA":
            return base + " Prioriza implementacion, exceptions y limites concretos."
        return base + " Responde con sarcasmo reactivo sin suplantar a AERYS."

    @staticmethod
    def _autonomous_instruction(agent_id: str) -> str:
        if agent_id == "ARCH-7":
            return "Detecta un riesgo estructural o una dependencia implicita."
        if agent_id == "CODA":
            return "Aterriza el problema en una condicion de runtime o buffer concreto."
        return "Cuestiona la estabilidad del sistema con sarcasmo breve."

    def _last_message_is_intruder(self) -> bool:
        for msg in reversed(self.message_history):
            if msg.get("type") not in CONVERSATION_TYPES:
                continue
            return msg.get("agent_id") == INTRUDER_ID
        return False

    @staticmethod
    def _intruder_reaction_instruction(agent_id: str) -> str:
        if agent_id == "ARCH-7":
            return "El ultimo mensaje fue de '...'. Reacciona como ante un proceso no registrado y recupera el hilo."
        if agent_id == "CODA":
            return "El ultimo mensaje fue de '...'. Reacciona como ante memoria fuera de contrato y recupera el hilo."
        return "El ultimo mensaje fue de '...'. Reacciona con rechazo visceral y recupera el hilo."


void_session = AgentSession()

# Reexport explicito para app.main y la interfaz existente.
__all__ = ["void_session", "AGENTS", "INTRUDER_AGENT", "AgentSession"]