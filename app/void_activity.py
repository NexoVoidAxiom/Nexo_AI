"""
Monitor de actividad local para Void Axiom.

HTTP no sabe si una pagina "sigue abierta" salvo por peticiones recientes o
streams activos. Este modulo modela eso con dos senales:
- actividad reciente de la app principal (/ y /api/chats...)
- streams de chat de usuario actualmente vivos

Void Axiom solo activa memoria RAM extendida de 55K cuando ambas senales estan
frias, evitando competir con el chat principal por RAM/GPU.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field


MAIN_IDLE_SECONDS = float(os.getenv("VOID_MAIN_IDLE_SECONDS", "45"))
CHAT_IDLE_SECONDS = float(os.getenv("VOID_CHAT_IDLE_SECONDS", "30"))


@dataclass
class VoidActivityMonitor:
    last_main_activity: float = field(default_factory=lambda: 0.0)
    last_chat_activity: float = field(default_factory=lambda: 0.0)
    active_chat_streams: int = 0

    def mark_main(self) -> None:
        self.last_main_activity = time.monotonic()

    def mark_chat(self) -> None:
        now = time.monotonic()
        self.last_main_activity = now
        self.last_chat_activity = now

    @contextmanager
    def chat_stream(self):
        self.active_chat_streams += 1
        self.mark_chat()
        try:
            yield
        finally:
            self.active_chat_streams = max(0, self.active_chat_streams - 1)
            self.last_chat_activity = time.monotonic()

    def main_is_idle(self) -> bool:
        if self.last_main_activity <= 0:
            return True
        return time.monotonic() - self.last_main_activity >= MAIN_IDLE_SECONDS

    def chat_is_idle(self) -> bool:
        if self.active_chat_streams > 0:
            return False
        if self.last_chat_activity <= 0:
            return True
        return time.monotonic() - self.last_chat_activity >= CHAT_IDLE_SECONDS

    def extended_context_allowed(self) -> bool:
        return self.main_is_idle() and self.chat_is_idle()

    def snapshot(self) -> dict:
        now = time.monotonic()
        return {
            "main_idle": self.main_is_idle(),
            "chat_idle": self.chat_is_idle(),
            "extended_context_allowed": self.extended_context_allowed(),
            "active_chat_streams": self.active_chat_streams,
            "seconds_since_main": None
            if self.last_main_activity <= 0
            else round(now - self.last_main_activity, 2),
            "seconds_since_chat": None
            if self.last_chat_activity <= 0
            else round(now - self.last_chat_activity, 2),
        }


void_activity = VoidActivityMonitor()

