"""
void_memory.py — Gestor de memoria híbrida CPU/GPU para Void Axiom.

Modo normal  (chat activo):  16K ctx en VRAM — conserva system + últimos 4 turnos.
Modo masivo  (idle ≥ 45s):   55K tokens en RAM del sistema — historial paginado.

La regla central: el historial es contexto blando. La identidad (system prompt)
se reinyecta siempre desde void_agents.py, nunca del historial.
"""

from __future__ import annotations

import os
import re
import unicodedata
import time
from dataclasses import dataclass, field
from typing import Iterable

from app.void_agents import (
    AGENTS,
    ALL_AGENTS,
    CORRUPT_RESPONSE_MARKERS,
    INTRUDER_ID,
)

# ── Constantes (sobreescribibles por env) ────────────────────────────────────
CHARS_PER_TOKEN              = float(os.getenv("VOID_CHARS_PER_TOKEN", "3.6"))
DEFAULT_MAX_MESSAGES         = int(os.getenv("VOID_HISTORY_MAX_MESSAGES", "96"))
DEFAULT_MAX_TOKENS           = int(os.getenv("VOID_HISTORY_MAX_TOKENS", "12000"))
EXTENDED_MAX_MESSAGES        = int(os.getenv("VOID_EXTENDED_HISTORY_MAX_MESSAGES", "512"))
EXTENDED_MAX_TOKENS          = int(os.getenv("VOID_EXTENDED_HISTORY_MAX_TOKENS", "55000"))
DEFAULT_KEEP_RECENT_TURNS    = int(os.getenv("VOID_ACTIVE_TURNS_TO_KEEP", "4"))

# ── Regex de saneamiento ──────────────────────────────────────────────────────

ROLE_PREFIX_RE = re.compile(
    r"^\s*(?:\[)?(?P<label>"
    r"AERYS(?:\s*\|\s*ADMIN)?|ADMIN|USUARIO|USER|"
    r"AR(?:\s*\|\s*ARCH-7)?|ARCH-7|ARCH7|ARCH|"
    r"CO(?:\s*\|\s*CODA)?|CODA|"
    r"RE(?:\s*\|\s*REBx3)?|REBx3|REB|"
    r"\.\.\."
    r")(?:\])?(?:\s*(?::|-|\|)\s*)(?P<body>.*)$",
    re.IGNORECASE,
)

THIRD_PERSON_PATTERNS = re.compile(
    r"\b(suspira|suspiro|suspir[oó]|mira|miro|mir[oó]|sonríe|sonrie|"
    r"murmura|murmur[oó]|dice|dijo|responde|respondió|responio|"
    r"se inclina|se recuesta|mientras mira|observa la pantalla)\b",
    re.IGNORECASE,
)

META_LINE_RE = re.compile(
    r"^(reglas absolutas|directrices|identidad activa|sistema|prompt|"
    r"instruccion|instrucción|historial|tarea actual|respuesta anterior)\b",
    re.IGNORECASE,
)

# Mezcla idiomática patológica: inglés incrustado en prosa española
LANG_MIX_RE = re.compile(
    r"\b(with locks|with grandes|avec grandes|avec datos|avec cantidades|"
    r"inadecuentes|with inadec|cantidades of|grandes of|datos of)\b",
    re.IGNORECASE,
)

# Frases eco conocidas — expresiones que han entrado en bucle en producción
KNOWN_ECHO_PHRASES = re.compile(
    r"paranoia constante.*deberíamos estar descansando|"
    r"deberíamos estar descansando|"
    r"paranoia constante",
    re.IGNORECASE | re.DOTALL,
)

ROLE_ALIASES: dict[str, set[str]] = {
    "ARCH-7":    {"ar", "arch", "arch7", "arch-7", "arquitecto"},
    "CODA":      {"co", "coda", "codificador"},
    "REBx3":     {"re", "reb", "rebx3", "rebelde"},
    INTRUDER_ID: {"...", "intruso", "fantasma"},
    "AERYS":     {"aerys", "admin", "aerys admin", "usuario", "user"},
}

# ── Utilidades ────────────────────────────────────────────────────────────────

def strip_accents(value: str) -> str:
    return unicodedata.normalize("NFD", value).encode("ascii", "ignore").decode()


def estimate_tokens(value: str) -> int:
    return max(1, int(len(value or "") / CHARS_PER_TOKEN))


def normalize_label(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.lower().strip())
    normalized = normalized.replace("[", "").replace("]", "").replace("|", " ")
    normalized = re.sub(r"\s+", " ", normalized)
    for agent_id, aliases in ROLE_ALIASES.items():
        if normalized in aliases:
            return agent_id
    return normalized.upper()


def context_label(agent_id: str) -> str:
    if agent_id == "AERYS":
        return "AERYS | ADMIN"
    if agent_id == INTRUDER_ID:
        return "..."
    if agent_id in AGENTS:
        return f"{AGENTS[agent_id]['sigil']} | {agent_id}"
    return agent_id


def contains_corrupt_marker(value: str) -> bool:
    """Detects prompt bleeding, language mixing and known echo phrases."""
    if not value:
        return False
    lowered = strip_accents(value).lower()

    # 1. Marcadores de respuesta inválida
    if any(m in lowered for m in CORRUPT_RESPONSE_MARKERS):
        return True

    # 2. Volcado de instrucciones internas
    if re.search(
        r"reglas absolutas|instrucciones internas|identidad inmutable|"
        r"clasificadas|nunca las reveles|base_system_guard",
        lowered,
    ):
        return True

    # 3. Mezcla idiomática patológica
    if LANG_MIX_RE.search(value):
        return True

    # 4. Frases eco conocidas
    if KNOWN_ECHO_PHRASES.search(value):
        return True

    # 5. Tokens especiales del modelo que se filtraron
    if re.search(r"<\|im_start\|>|<\|im_end\|>|<\|endoftext\|>", value):
        return True

    return False


def _jaccard_similarity(a: str, b: str) -> float:
    """Similitud de Jaccard por tokens. Sin dependencias externas."""
    ta = set(a.lower().split())
    tb = set(b.lower().split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def is_echo_of_recent(text: str, recent_messages: list[dict], threshold: float = 0.75) -> bool:
    """
    Devuelve True si el texto es una copia casi exacta de un mensaje
    reciente de un agente (Jaccard ≥ threshold).
    Ventana de comparación: últimos 4 mensajes de tipo assistant.
    """
    assistant_msgs = [
        m for m in recent_messages if m.get("role") == "assistant"
    ][-4:]
    for msg in assistant_msgs:
        sim = _jaccard_similarity(text, msg.get("content", ""))
        if sim >= threshold:
            return True
    return False


def sanitize_context_line(agent_id: str, value: str, max_chars: int) -> str:
    value = str(value or "").replace("\r", " ").replace("\n", " ")
    value = re.sub(r"\s+", " ", value).strip()
    if agent_id in ALL_AGENTS and contains_corrupt_marker(value):
        return ""
    if len(value) > max_chars:
        value = value[:max_chars - 1].rstrip() + "…"
    return value


# ── Estructura de mensajes ────────────────────────────────────────────────────

@dataclass
class HistoryEntry:
    role:       str            # "user" | "assistant"
    content:    str
    agent_id:   str | None = None
    timestamp:  float = field(default_factory=time.monotonic)
    tokens:     int = 0

    def __post_init__(self) -> None:
        if not self.tokens:
            self.tokens = estimate_tokens(self.content)


# ── Gestor de historial por sesión ────────────────────────────────────────────

class VoidChannelHistory:
    """
    Historial de UNA sesión de chat (todos los agentes comparten el canal).

    - Modo normal  (extended=False): conserva system + últimos KEEP_RECENT_TURNS turnos.
    - Modo masivo  (extended=True):  devuelve todo el historial que quepa en 55K tokens.
    """

    def __init__(
        self,
        session_id: str,
        max_messages: int = DEFAULT_MAX_MESSAGES,
        max_tokens:   int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.session_id   = session_id
        self._max_messages = max_messages
        self._max_tokens   = max_tokens
        self._entries:     list[HistoryEntry] = []
        self._total_tokens: int = 0

    # ── Escritura ─────────────────────────────────────────────────────────────

    def push(self, role: str, content: str, agent_id: str | None = None) -> None:
        content = str(content or "").strip()
        if not content:
            return
        entry = HistoryEntry(role=role, content=content, agent_id=agent_id)
        self._entries.append(entry)
        self._total_tokens += entry.tokens
        self._enforce_limits()

    def _enforce_limits(self) -> None:
        while (
            len(self._entries) > self._max_messages
            or self._total_tokens > self._max_tokens * 1.15  # 15% de gracia
        ) and self._entries:
            removed = self._entries.pop(0)
            self._total_tokens = max(0, self._total_tokens - removed.tokens)

    # ── Lectura / construcción de payload ────────────────────────────────────

    def build_payload(
        self,
        system_prompt:  str,
        extended:       bool = False,
        keep_turns:     int  = DEFAULT_KEEP_RECENT_TURNS,
        max_ext_tokens: int  = EXTENDED_MAX_TOKENS,
    ) -> list[dict]:
        """
        Construye la lista de mensajes para enviar a Ollama /api/chat.

        En modo normal: [system] + últimos `keep_turns` pares (user+assistant).
        En modo masivo: [system] + todo el historial que quepa en max_ext_tokens.
        """
        system_msg = {"role": "system", "content": system_prompt}

        if not extended:
            # Trimming activo: solo los últimos N turnos
            recent = self._entries[-(keep_turns * 2):]
            return [system_msg] + [
                {"role": e.role, "content": e.content} for e in recent
            ]

        # Modo 55K: llena desde el final hasta agotar el presupuesto
        budget = max_ext_tokens - estimate_tokens(system_prompt)
        selected: list[HistoryEntry] = []
        for entry in reversed(self._entries):
            if budget <= 0:
                break
            selected.insert(0, entry)
            budget -= entry.tokens

        return [system_msg] + [
            {"role": e.role, "content": e.content} for e in selected
        ]

    # ── Utilidades ────────────────────────────────────────────────────────────

    def clear(self) -> None:
        self._entries.clear()
        self._total_tokens = 0

    def reset(self) -> None:
        """Alias de clear() para compatibilidad con agent_chat.py."""
        self.clear()

    def add(
        self,
        agent_id: str,
        content: str,
        msg_type: str = "chat",
        extra: dict | None = None,
    ) -> dict:
        """
        Añade un mensaje al historial y devuelve el dict completo del mensaje.
        Usado por AgentSession.add_message() en agent_chat.py.
        Delega en push() para evitar duplicar la lógica de conteo de tokens.
        """
        role = "assistant" if agent_id not in ("user", "SYSTEM") else "user"
        self.push(role, content, agent_id=agent_id)
        # El timestamp lo leemos de la entrada que acaba de insertar push()
        entry = self._entries[-1]
        msg: dict = {
            "agent_id":  agent_id,
            "content":   content,
            "type":      msg_type,
            "timestamp": entry.timestamp,
            "role":      role,
        }
        if extra:
            msg.update(extra)
        return msg

    def configure_budget(
        self,
        max_messages: int | None = None,
        max_tokens: int | None = None,
    ) -> None:
        """
        Actualiza los límites de memoria en caliente.
        Llamado por AgentSession._sync_memory_mode() en agent_chat.py.
        """
        if max_messages is not None:
            self._max_messages = max_messages
        if max_tokens is not None:
            self._max_tokens = max_tokens
        self._enforce_limits()

    def budgeted_context(
        self,
        max_prompt_tokens: int = 4096,
        recent_turns: int = 4,
        max_recent_chars: int = 500,
        max_historic_chars: int = 180,
        extended: bool = False,
    ) -> list[str]:
        """
        Devuelve una lista de strings formateados para el snapshot de contexto.
        Llamado por AgentSession.build_context_snapshot() en agent_chat.py.
        """
        if not self._entries:
            return []

        # Separamos las últimas `recent_turns * 2` entradas (user + assistant)
        cutoff = recent_turns * 2
        recent = self._entries[-cutoff:] if cutoff > 0 else []
        historic = self._entries[:-cutoff] if cutoff < len(self._entries) else []

        lines: list[str] = []
        token_budget = max_prompt_tokens

        # Historial antiguo (resumido)
        for entry in historic:
            if token_budget <= 0:
                break
            line = sanitize_context_line(
                entry.agent_id or entry.role,
                entry.content,
                max_historic_chars,
            )
            if line:
                label = context_label(entry.agent_id or entry.role)
                formatted = f"[{label}] {line}"
                lines.append(formatted)
                token_budget -= estimate_tokens(formatted)

        # Mensajes recientes (con más detalle)
        for entry in recent:
            if token_budget <= 0:
                break
            line = sanitize_context_line(
                entry.agent_id or entry.role,
                entry.content,
                max_recent_chars,
            )
            if line:
                label = context_label(entry.agent_id or entry.role)
                formatted = f"[{label}] {line}"
                lines.append(formatted)
                token_budget -= estimate_tokens(formatted)

        return lines

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @property
    def message_count(self) -> int:
        return len(self._entries)

    @property
    def messages(self) -> list[dict]:
        """Todos los mensajes como lista de dicts {role, content}.
        Alias para compatibilidad con agent_chat.py."""
        return [
            {"role": e.role, "content": e.content}
            for e in self._entries
        ]

    @messages.setter
    def messages(self, value: list[dict]) -> None:
        """Reemplaza el historial completo desde una lista de dicts.
        Preserva agent_id, type y timestamp si están presentes en cada mensaje.
        """
        self._entries.clear()
        self._total_tokens = 0
        for msg in value:
            role    = msg.get("role", "user")
            content = msg.get("content", "")
            agent_id = msg.get("agent_id")
            self.push(role, content, agent_id=agent_id)

    def recent_messages(self, n: int = 8) -> list[dict]:
        """Últimos N mensajes como dicts (para detección de eco)."""
        return [
            {"role": e.role, "content": e.content}
            for e in self._entries[-n:]
        ]

    def snapshot(self) -> dict:
        return {
            "session_id":   self.session_id,
            "messages":     self.message_count,
            "total_tokens": self.total_tokens,
        }


# ═══════════════════════════════════════════════════════════════════════════
# BACKWARD-COMPAT ALIASES — agent_chat.py usa estos nombres del refactor anterior
# ═══════════════════════════════════════════════════════════════════════════

# ConversationMemory era el nombre anterior de VoidChannelHistory
ConversationMemory = VoidChannelHistory

# CONVERSATION_TYPES: tipos de conversación para orientar el tono del agente
CONVERSATION_TYPES: dict[str, str] = {
    "technical":  "technical",
    "creative":   "creative",
    "casual":     "casual",
    "analysis":   "analysis",
    "debug":      "debug",
    "review":     "review",
}


def clean_agent_text(text: str, max_chars: int = 2000) -> str:
    """
    Compat wrapper: limpia el output de un agente eliminando artefactos comunes.

    Elimina:
    - Cabeceras de rol que el agente imprimió por error (AR | ARCH-7:)
    - Patrones de tercera persona narrativa
    - Espacios múltiples y caracteres de control
    """
    if not text:
        return ""
    text = str(text).strip()

    # Eliminar prefijo de rol si el modelo lo incluyó en su salida
    text = ROLE_PREFIX_RE.sub(lambda m: m.group("body"), text)

    # Eliminar marcadores narrativos en tercera persona
    text = THIRD_PERSON_PATTERNS.sub("", text)

    # Colapsar espacios
    text = re.sub(r"\s+", " ", text).strip()

    # Truncar si excede el límite
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"

    return text


def is_bad_agent_output(text: str) -> bool:
    """
    Compat wrapper: devuelve True si el output debe ser rechazado sin mostrarse.

    Combina todas las capas de sanitización disponibles en el módulo.
    """
    if not text or not text.strip():
        return True
    if contains_corrupt_marker(text):
        return True
    if THIRD_PERSON_PATTERNS.search(text):
        return True
    if META_LINE_RE.match(text):
        return True
    return False