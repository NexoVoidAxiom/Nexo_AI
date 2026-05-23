"""
void_activity.py — Monitor de actividad para Void Axiom.

HTTP no sabe si una página "sigue abierta" salvo por peticiones recientes o
streams activos. Este módulo modela eso con dos señales:
  · Actividad reciente de la app principal (/, /api/chats...)
  · Streams de chat de usuario actualmente vivos

Void Axiom activa el modo 55K RAM solo cuando AMBAS señales están frías,
evitando competir con el chat principal por GPU/RAM.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Generator

MAIN_IDLE_SECONDS = float(os.getenv("VOID_MAIN_IDLE_SECONDS", "45"))
CHAT_IDLE_SECONDS = float(os.getenv("VOID_CHAT_IDLE_SECONDS", "30"))


@dataclass
class VoidActivityMonitor:
    """
    Registra la actividad de la plataforma con resolución de segundos.

    Señales:
      mark_main()        — cualquier request HTTP a la app principal
      mark_chat()        — inicio/tick de un stream de chat de usuario
      chat_stream()      — context manager que engloba un stream completo
    """
    last_main_activity: float = field(default_factory=lambda: 0.0)
    last_chat_activity: float = field(default_factory=lambda: 0.0)
    active_chat_streams: int = 0

    # ── Marcadores de actividad ───────────────────────────────────────────────

    def mark_main(self) -> None:
        """Llamar en cada request entrante del frontend."""
        self.last_main_activity = time.monotonic()

    def mark_chat(self) -> None:
        """Llamar al inicio de cualquier interacción de chat de usuario."""
        now = time.monotonic()
        self.last_main_activity = now
        self.last_chat_activity = now

    @contextmanager
    def chat_stream(self) -> Generator[None, None, None]:
        """
        Context manager para un stream de chat activo.
        Incrementa el contador al entrar, lo decrementa al salir.
        Garantiza que el contador no baje de 0 aunque haya excepciones.
        """
        self.active_chat_streams += 1
        self.mark_chat()
        try:
            yield
        finally:
            self.active_chat_streams = max(0, self.active_chat_streams - 1)
            self.last_chat_activity  = time.monotonic()

    # ── Predicados de reposo ──────────────────────────────────────────────────

    def main_is_idle(self) -> bool:
        """True si no hay actividad en la app principal en los últimos N segundos."""
        if self.last_main_activity <= 0:
            return True
        return time.monotonic() - self.last_main_activity >= MAIN_IDLE_SECONDS

    def chat_is_idle(self) -> bool:
        """True si no hay streams activos y el último chat fue hace N segundos."""
        if self.active_chat_streams > 0:
            return False
        if self.last_chat_activity <= 0:
            return True
        return time.monotonic() - self.last_chat_activity >= CHAT_IDLE_SECONDS

    def extended_context_allowed(self) -> bool:
        """
        True solo cuando AMBAS señales están frías.
        Esta es la condición que activa el modo 55K tokens en RAM.
        """
        return self.main_is_idle() and self.chat_is_idle()

    # ── Diagnóstico ───────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        now = time.monotonic()
        return {
            "main_idle":               self.main_is_idle(),
            "chat_idle":               self.chat_is_idle(),
            "extended_context_allowed": self.extended_context_allowed(),
            "active_chat_streams":     self.active_chat_streams,
            "seconds_since_main": (
                None if self.last_main_activity <= 0
                else round(now - self.last_main_activity, 2)
            ),
            "seconds_since_chat": (
                None if self.last_chat_activity <= 0
                else round(now - self.last_chat_activity, 2)
            ),
            "thresholds": {
                "main_idle_s": MAIN_IDLE_SECONDS,
                "chat_idle_s": CHAT_IDLE_SECONDS,
            },
        }


# ── Singleton global ──────────────────────────────────────────────────────────
void_activity = VoidActivityMonitor()
