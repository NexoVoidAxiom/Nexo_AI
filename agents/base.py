"""
agents/base.py — Protocolo y contrato base de los agentes de Void Axiom.

Cada agente es un dataclass inmutable con:
  · Identidad (id, sigil, display, color)
  · Configuración de inferencia (model, num_ctx, num_predict, temperature...)
  · System prompt (reinyectado en CADA llamada — nunca viene del historial)
  · Capacidades opcionales (fallback_models, memory_mode)

Esta capa NO tiene dependencias en FastAPI ni en Ollama.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

MemoryMode = Literal["active", "extended", "minimal"]


@dataclass(frozen=True)
class AgentConfig:
    """
    Contrato inmutable de un agente.

    Todos los campos de inferencia tienen valores por defecto seguros.
    El `system_prompt` es la única fuente de identidad del agente —
    el historial de conversación es contexto blando, nunca identidad.
    """

    # ── Identidad ──────────────────────────────────────────────────────────
    agent_id:      str        # "ARCH-7", "CODA", "REBx3", "..."
    sigil:         str        # Prefijo de terminal: "AR", "CO", "RE", "⬡"
    display:       str        # Etiqueta UI: "AR | ARCH-7"
    color:         str        # Hex para el frontend
    model:         str        # Nombre del modelo en Ollama

    # ── Prompt de identidad ────────────────────────────────────────────────
    system_prompt: str        # Reinyectado en cada llamada HTTP

    # ── Parámetros de inferencia ───────────────────────────────────────────
    num_ctx:       int   = 16_384
    num_predict:   int   = 200
    num_gpu:       int   = 99       # 99 = usar toda la VRAM disponible
    temperature:   float = 0.25
    top_p:         float = 0.80
    top_k:         int   = 24
    repeat_penalty: float = 1.22
    repeat_last_n:  int   = 256
    frequency_penalty: float = 1.4
    presence_penalty:  float = 0.8

    # ── Resiliencia ────────────────────────────────────────────────────────
    fallback_models: tuple[str, ...] = field(default_factory=tuple)
    memory_mode:     MemoryMode = "active"

    def inference_options(self) -> dict:
        """Construye el dict `options` listo para enviar a Ollama."""
        return {
            "temperature":        self.temperature,
            "top_p":              self.top_p,
            "top_k":              self.top_k,
            "repeat_penalty":     self.repeat_penalty,
            "repeat_last_n":      self.repeat_last_n,
            "frequency_penalty":  self.frequency_penalty,
            "presence_penalty":   self.presence_penalty,
            "num_ctx":            self.num_ctx,
            "num_predict":        self.num_predict,
            "num_gpu":            self.num_gpu,
            "stop":               ["\n\n", "◆", "<|im_end|>", "<|endoftext|>"],
        }
