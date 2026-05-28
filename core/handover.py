"""
core/handover.py — Handover Narrativo entre agentes de Void Axiom.
==================================================================
El "pase de batuta": cuando un agente termina su turno, genera un
contexto mínimo que orienta al siguiente sin contaminar su identidad.

Principios:
  1. El handover NO es memoria — es un gancho narrativo de 1-2 frases.
  2. La identidad del receptor se reinyecta desde registry.py, no del handover.
  3. El handover se inserta como mensaje `system` efímero ANTES del turno.
  4. Si el handover genera bleeding (el agente A habla como B), se descarta.

Tipos de handover:
  · CHAT_TO_CODE   → ARCH-7 cede a CODA por intent de código detectado
  · CODE_TO_CHAT   → CODA devuelve control a ARCH-7 tras respuesta técnica
  · INTRUDER_IN    → El Intruso interrumpe (probabilístico, siempre breve)
  · INTRUDER_OUT   → El Intruso se retira, el hilo vuelve al agente anterior
  · REBEL_REACT    → REBx3 reacciona a la respuesta del agente anterior
  · STANDARD       → Turno normal sin swap de modo

El frontend puede consumir los eventos de handover vía SSE para
mostrar la animación de "transferencia de batuta".
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import AsyncIterator

log = logging.getLogger("void.handover")


# ══════════════════════════════════════════════════════════════════════════════
#  TIPOS DE HANDOVER
# ══════════════════════════════════════════════════════════════════════════════

class HandoverType(Enum):
    CHAT_TO_CODE   = auto()
    CODE_TO_CHAT   = auto()
    INTRUDER_IN    = auto()
    INTRUDER_OUT   = auto()
    REBEL_REACT    = auto()
    STANDARD       = auto()


# ── Plantillas de gancho narrativo ────────────────────────────────────────────
# El {last_fragment} es el último fragmento significativo del agente saliente.
# Máximo 2 frases. No contiene identidad del receptor.

_HOOKS: dict[HandoverType, str] = {
    HandoverType.CHAT_TO_CODE: (
        "El análisis arquitectónico indica una tarea de implementación. "
        "El fragmento relevante del contexto anterior: «{last_fragment}»"
    ),
    HandoverType.CODE_TO_CHAT: (
        "La implementación está resuelta. "
        "Retomando el hilo conversacional: «{last_fragment}»"
    ),
    HandoverType.INTRUDER_IN: (
        # El Intruso no recibe contexto — es interferencia pura
        ""
    ),
    HandoverType.INTRUDER_OUT: (
        "Interferencia registrada. Continuando el hilo activo."
    ),
    HandoverType.REBEL_REACT: (
        "Respuesta del agente anterior para revisión crítica: «{last_fragment}»"
    ),
    HandoverType.STANDARD: (
        "Continuación del turno. Contexto inmediato: «{last_fragment}»"
    ),
}

# Longitud máxima del fragmento extraído de la respuesta anterior
_MAX_FRAGMENT_CHARS = 160

# Marcadores que indican que el fragmento está contaminado (bleeding)
_BLEEDING_MARKERS = re.compile(
    r"\b(eres|soy|mi identidad|mi nombre es|instrucciones|system prompt|"
    r"reglas absolutas|directrices|identidad activa)\b",
    re.IGNORECASE,
)


# ══════════════════════════════════════════════════════════════════════════════
#  EVENTO DE HANDOVER (SSE)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class HandoverEvent:
    """
    Evento emitido vía SSE al cliente cuando ocurre un handover.
    El frontend lo usa para la animación de "pase de batuta".
    """
    handover_type:   HandoverType
    from_agent:      str
    to_agent:        str
    reason:          str
    vram_freed_gb:   float = 0.0
    vram_loaded_gb:  float = 0.0
    timestamp:       float = field(default_factory=time.time)

    def to_sse(self) -> str:
        """Serializa el evento como línea SSE parseable por el frontend."""
        return (
            f'\ndata: {{"type":"handover","from":"{self.from_agent}",'
            f'"to":"{self.to_agent}","reason":"{self.reason}",'
            f'"vram_freed":{self.vram_freed_gb:.1f},'
            f'"vram_loaded":{self.vram_loaded_gb:.1f}}}\n\n'
        )

    def to_terminal(self) -> str:
        """Versión legible para el terminal de debug."""
        arrow = f"{self.from_agent} → {self.to_agent}"
        vram_info = ""
        if self.vram_freed_gb or self.vram_loaded_gb:
            vram_info = (
                f" | VRAM liberada: {self.vram_freed_gb:.1f}GB"
                f" → cargada: {self.vram_loaded_gb:.1f}GB"
            )
        return f"\n[HANDOVER] {arrow} | {self.reason}{vram_info}\n"


# ══════════════════════════════════════════════════════════════════════════════
#  MOTOR DE HANDOVER NARRATIVO
# ══════════════════════════════════════════════════════════════════════════════

class NarrativeHandover:
    """
    Genera los mensajes de contexto efímero que orientan al agente receptor.

    No almacena estado conversacional — eso es responsabilidad de VoidMemory.
    Solo procesa el fragmento saliente y devuelve el gancho narrativo.
    """

    def build_context_injection(
        self,
        handover_type: HandoverType,
        last_response: str,
        from_agent: str,
        to_agent: str,
    ) -> str:
        """
        Construye el mensaje `system` efímero que recibe el agente receptor.

        Returns:
            String listo para insertar como system message contextual.
            Vacío si el handover es INTRUDER_IN (no contamina al Intruso).
        """
        if handover_type == HandoverType.INTRUDER_IN:
            return ""

        fragment = self._extract_fragment(last_response)

        if not fragment:
            template = _HOOKS[handover_type].replace("«{last_fragment}»", "").strip()
            return template

        hook = _HOOKS[handover_type].format(last_fragment=fragment)
        return hook.strip()

    def _extract_fragment(self, text: str) -> str:
        """
        Extrae el fragmento más significativo de la respuesta del agente saliente.

        Reglas:
          · Toma las últimas 2 oraciones completas.
          · Descarta si contiene markers de bleeding.
          · Trunca a _MAX_FRAGMENT_CHARS.
        """
        if not text or not text.strip():
            return ""

        # Limpiar prefijos de terminal (AR: ..., CO: ..., etc.)
        cleaned = re.sub(r"^[A-Z⬡]{1,4}\s*[|:]\s*", "", text.strip())
        cleaned = re.sub(r"\[0x[0-9A-Fa-f]+\]\s*", "", cleaned)  # Intruso hex

        # Tomar las últimas 2 oraciones
        sentences = re.split(r"(?<=[.!?])\s+", cleaned)
        fragment = " ".join(sentences[-2:]) if len(sentences) >= 2 else cleaned

        # Truncar
        if len(fragment) > _MAX_FRAGMENT_CHARS:
            fragment = fragment[:_MAX_FRAGMENT_CHARS].rsplit(" ", 1)[0] + "…"

        # Descartar si hay bleeding
        if _BLEEDING_MARKERS.search(fragment):
            log.warning(
                "Fragmento de handover descartado por bleeding: %.60s…", fragment
            )
            return ""

        return fragment.strip()

    def classify_transition(
        self,
        from_agent: str,
        to_agent: str,
    ) -> HandoverType:
        """Infiere el tipo de handover a partir de los agentes involucrados."""
        if to_agent == "...":
            return HandoverType.INTRUDER_IN
        if from_agent == "...":
            return HandoverType.INTRUDER_OUT
        if to_agent == "CODA":
            return HandoverType.CHAT_TO_CODE
        if from_agent == "CODA":
            return HandoverType.CODE_TO_CHAT
        if to_agent == "REBx3":
            return HandoverType.REBEL_REACT
        return HandoverType.STANDARD

    async def emit_and_inject(
        self,
        from_agent: str,
        to_agent: str,
        last_response: str,
        reason: str = "",
        vram_freed_gb: float = 0.0,
        vram_loaded_gb: float = 0.0,
    ) -> tuple[HandoverEvent, str]:
        """
        Genera el evento SSE y la inyección de contexto en un solo paso.

        Returns:
            (HandoverEvent, context_injection_string)
        """
        htype = self.classify_transition(from_agent, to_agent)
        injection = self.build_context_injection(
            htype, last_response, from_agent, to_agent
        )
        event = HandoverEvent(
            handover_type  = htype,
            from_agent     = from_agent,
            to_agent       = to_agent,
            reason         = reason or htype.name,
            vram_freed_gb  = vram_freed_gb,
            vram_loaded_gb = vram_loaded_gb,
        )
        log.info(
            "Handover %s: %s → %s | fragmento=%d chars",
            htype.name, from_agent, to_agent, len(injection)
        )
        return event, injection


# ── Singleton global ──────────────────────────────────────────────────────────
narrative_handover = NarrativeHandover()
