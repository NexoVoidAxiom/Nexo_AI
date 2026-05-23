"""
Memoria y saneamiento del canal Void Axiom.

La regla central: el historial es contexto blando, nunca identidad. La identidad
se reinyecta en cada llamada a Ollama desde app.void_agents.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Iterable

from app.void_agents import (
    AGENTS,
    ALL_AGENTS,
    CORRUPT_RESPONSE_MARKERS,
    INTRUDER_ID,
)


CONVERSATION_TYPES = {"chat", "aerys", "interrupt"}
CHARS_PER_TOKEN = float(os.getenv("VOID_CHARS_PER_TOKEN", "3.6"))
DEFAULT_MAX_MESSAGES = int(os.getenv("VOID_HISTORY_MAX_MESSAGES", "96"))
DEFAULT_MAX_TOKENS = int(os.getenv("VOID_HISTORY_MAX_TOKENS", "12000"))
EXTENDED_MAX_MESSAGES = int(os.getenv("VOID_EXTENDED_HISTORY_MAX_MESSAGES", "512"))
EXTENDED_MAX_TOKENS = int(os.getenv("VOID_EXTENDED_HISTORY_MAX_TOKENS", "55000"))
DEFAULT_KEEP_RECENT_TURNS = int(os.getenv("VOID_ACTIVE_TURNS_TO_KEEP", "4"))

ROLE_PREFIX_RE = re.compile(
    r"^\s*(?:\[)?(?P<label>"
    r"AERYS(?:\s*\|\s*ADMIN)?|ADMIN|USUARIO|USER|"
    r"AR(?:\s*\|\s*ARCH-7)?|ARCH-7|ARCH7|ARCH|"
    r"CO(?:\s*\|\s*CODA)?|CODA|"
    r"RE(?:\s*\|\s*REBx3)?|REBx3|REB|"
    r"\.\.\."
    r")(?:\])?\s*(?::|-|\|)?\s*(?P<body>.*)$",
    re.IGNORECASE,
)

ROLE_ALIASES = {
    "ARCH-7": {"ar", "arch", "arch7", "arch-7", "arquitecto"},
    "CODA": {"co", "coda", "codificador"},
    "REBx3": {"re", "reb", "rebx3", "rebelde"},
    INTRUDER_ID: {"...", "intruso", "fantasma"},
    "AERYS": {"aerys", "admin", "aerys admin", "usuario", "user"},
}

THIRD_PERSON_PATTERNS = (
    r"\b(suspira|suspiro|suspir[oó]|mira|miro|mir[oó]|sonrie|sonr[ií]e)\b",
    r"\b(murmura|murmur[oó]|dice|dijo|responde|respond[ií]o)\b",
    r"\b(se inclina|se inclin[oó]|se recuesta|se recost[oó])\b",
    r"\b(mientras mira|mirando la pantalla|observa la pantalla)\b",
)

META_LINE_RE = re.compile(
    r"^(reglas|directrices|identidad activa|sistema|prompt|respuesta anterior|"
    r"instruccion|instrucción|historial|tarea actual)\b",
    re.IGNORECASE,
)


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


def sanitize_context_line(agent_id: str, value: str, max_chars: int) -> str:
    value = str(value or "").replace("\r", " ").replace("\n", " ")
    value = re.sub(r"\s+", " ", value).strip()
    if agent_id in ALL_AGENTS and contains_corrupt_marker(value):
        return ""
    if len(value) > max_chars:
        value = value[: max_chars - 1].rstrip() + "..."
    return value


def contains_corrupt_marker(value: str) -> bool:
    lowered = strip_accents(value).lower()
    return any(strip_accents(marker).lower() in lowered for marker in CORRUPT_RESPONSE_MARKERS)


def strip_accents(value: str) -> str:
    table = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")
    return str(value or "").translate(table)


def strip_role_prefix(agent_id: str, line: str) -> str:
    match = ROLE_PREFIX_RE.match(line)
    if not match:
        return line
    label = normalize_label(match.group("label"))
    body = match.group("body").strip()
    return body if label == agent_id else ""


def is_stage_direction(line: str) -> bool:
    lowered = strip_accents(line).lower()
    if re.match(r"^(\*|_|\().*(\*|_|\))$", line.strip()):
        return True
    if not any(re.search(pattern, lowered, re.IGNORECASE) for pattern in THIRD_PERSON_PATTERNS):
        return False
    subjects = (
        "arch",
        "coda",
        "rebx3",
        "aerys",
        "arquitecto",
        "codificador",
        "rebelde",
        " el ",
        " ella ",
    )
    padded = f" {lowered} "
    return any(subject in padded for subject in subjects)


def strip_wrapping_quotes(value: str) -> str:
    pairs = [('"', '"'), ("'", "'"), ("`", "`"), ("“", "”"), ("«", "»")]
    changed = True
    while changed and len(value) >= 2:
        changed = False
        for left, right in pairs:
            if value.startswith(left) and value.endswith(right):
                value = value[1:-1].strip()
                changed = True
    return value


def limit_sentences(value: str, max_sentences: int) -> str:
    if not value:
        return value
    parts = re.split(r"(?<=[.!?])\s+", value)
    return " ".join(parts[:max_sentences]).strip()


def clean_agent_text(agent_id: str, value: str) -> str:
    text = str(value or "")
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"^```[\w-]*\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)

    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = strip_role_prefix(agent_id, line)
        if not line:
            continue
        if META_LINE_RE.match(line):
            continue
        if is_stage_direction(line):
            continue
        cleaned_lines.append(line)

    result = " ".join(cleaned_lines)
    result = re.sub(r"\*([^*]{0,240})\*", "", result).strip()
    result = re.sub(r"\(([^)]{0,240})\)", "", result).strip()
    result = strip_wrapping_quotes(result)
    result = re.sub(r"\s+", " ", result).strip()
    result = limit_sentences(result, 2 if agent_id == INTRUDER_ID else 3)
    max_chars = 260 if agent_id == INTRUDER_ID else 520
    return result[:max_chars].strip()


def token_similarity(left: str, right: str) -> float:
    left_words = set(re.findall(r"\w+", strip_accents(left).lower()))
    right_words = set(re.findall(r"\w+", strip_accents(right).lower()))
    if not left_words or not right_words:
        return 0.0
    return len(left_words & right_words) / max(1, min(len(left_words), len(right_words)))


def has_fractal_repetition(value: str) -> bool:
    normalized = strip_accents(value).lower()
    words = re.findall(r"\w+", normalized)
    if len(words) < 12:
        return False

    for size in (3, 4, 5, 6):
        grams: dict[tuple[str, ...], int] = {}
        for i in range(0, len(words) - size + 1):
            gram = tuple(words[i : i + size])
            grams[gram] = grams.get(gram, 0) + 1
            if grams[gram] >= 3:
                return True

    sentences = [s.strip() for s in re.split(r"[.!?;]+", normalized) if len(s.strip()) > 18]
    seen: dict[str, int] = {}
    for sentence in sentences:
        seen[sentence] = seen.get(sentence, 0) + 1
        if seen[sentence] >= 2:
            return True
    return False


def is_bad_agent_output(agent_id: str, text: str, recent: Iterable[str] = ()) -> bool:
    value = str(text or "").strip()
    lowered = strip_accents(value).lower()
    if not value:
        return True
    if contains_corrupt_marker(value):
        return True
    if has_fractal_repetition(value):
        return True
    if ROLE_PREFIX_RE.match(value):
        return True
    if re.search(r"\b(aerys|admin|usuario|user)\s*:", lowered):
        return True
    if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in THIRD_PERSON_PATTERNS):
        return True
    if len(value) > (260 if agent_id == INTRUDER_ID else 520):
        return True
    alpha = sum(ch.isalpha() for ch in value)
    if len(value) > 36 and alpha / max(1, len(value)) < 0.42:
        return True
    for old in recent:
        if len(value) >= 36 and token_similarity(value, old) >= 0.86:
            return True
    return False


@dataclass
class ConversationMemory:
    max_messages: int = DEFAULT_MAX_MESSAGES
    max_tokens: int = DEFAULT_MAX_TOKENS
    keep_recent_turns: int = DEFAULT_KEEP_RECENT_TURNS
    messages: list[dict] = field(default_factory=list)
    next_id: int = 1

    def reset(self) -> None:
        self.messages = []
        self.next_id = 1

    def configure_budget(self, *, max_messages: int, max_tokens: int) -> None:
        self.max_messages = max_messages
        self.max_tokens = max_tokens
        self.trim()

    def add(
        self,
        agent_id: str,
        content: str,
        msg_type: str,
        extra: dict | None = None,
    ) -> dict:
        msg = {
            "id": self.next_id,
            "agent_id": agent_id,
            "content": content,
            "type": msg_type,
            "timestamp": time.time(),
            **(extra or {}),
        }
        self.next_id += 1
        self.messages.append(msg)
        self.trim()
        return msg

    def token_count(self) -> int:
        return sum(estimate_tokens(str(msg.get("content", ""))) for msg in self.messages)

    def trim(self) -> None:
        if len(self.messages) <= self.max_messages and self.token_count() <= self.max_tokens:
            return

        first_system = next((m for m in self.messages if m.get("type") == "system"), None)
        recent_systems = [m for m in self.messages if m.get("type") == "system"][-3:]
        conversation = [m for m in self.messages if m.get("type") in CONVERSATION_TYPES]
        protected_recent = conversation[-self.keep_recent_turns :]

        keep: dict[int, dict] = {}
        for msg in ([first_system] if first_system else []) + recent_systems + protected_recent:
            if msg:
                keep[msg["id"]] = msg

        used_tokens = sum(estimate_tokens(str(m.get("content", ""))) for m in keep.values())
        budget = max(self.max_tokens, used_tokens)

        for msg in reversed(self.messages):
            msg_id = msg["id"]
            if msg_id in keep:
                continue
            if msg.get("type") not in CONVERSATION_TYPES:
                continue
            msg_tokens = estimate_tokens(str(msg.get("content", "")))
            if len(keep) >= self.max_messages:
                continue
            if used_tokens + msg_tokens > budget:
                continue
            keep[msg_id] = msg
            used_tokens += msg_tokens

        self.messages = [keep[key] for key in sorted(keep)]

    def recent_context(self, limit: int, max_chars: int) -> list[str]:
        rows: list[str] = []
        selected: list[dict] = []
        for msg in reversed(self.messages):
            if msg.get("type") not in CONVERSATION_TYPES:
                continue
            selected.append(msg)
            if len(selected) >= limit:
                break
        for msg in reversed(selected):
            agent_id = str(msg.get("agent_id", "SYSTEM"))
            content = sanitize_context_line(agent_id, str(msg.get("content", "")), max_chars)
            if content:
                rows.append(f"{context_label(agent_id)}: {content}")
        return rows

    def budgeted_context(
        self,
        *,
        max_prompt_tokens: int,
        recent_turns: int,
        max_recent_chars: int,
        max_historic_chars: int,
        extended: bool,
    ) -> list[str]:
        """Construye contexto para el prompt sin pasar de la ventana GPU.

        La memoria puede guardar hasta 55K tokens en RAM, pero el prompt enviado
        a Ollama se segmenta: sistema inicial, un historico comprimido del medio
        y los ultimos turnos intactos.
        """
        conversation = [m for m in self.messages if m.get("type") in CONVERSATION_TYPES]
        first_system = next((m for m in self.messages if m.get("type") == "system"), None)
        recent = conversation[-recent_turns:]
        recent_ids = {m["id"] for m in recent}

        rows: list[str] = []
        used = 0

        def add_row(row: str) -> bool:
            nonlocal used
            tokens = estimate_tokens(row)
            if used + tokens > max_prompt_tokens:
                return False
            rows.append(row)
            used += tokens
            return True

        if first_system:
            add_row(f"SYSTEM: {sanitize_context_line('SYSTEM', str(first_system.get('content', '')), 220)}")

        if extended:
            add_row("MODO MEMORIA EXTENDIDA RAM: historico comprimido; ultimos 4 turnos intactos.")
            historic = [m for m in conversation if m["id"] not in recent_ids]
            # Recorrido inverso para conservar lo mas reciente del medio y evitar
            # atraer frases viejas repetidas.
            compact_rows: list[str] = []
            compact_used = 0
            historic_budget = max(1, int(max_prompt_tokens * 0.58))
            for msg in reversed(historic):
                agent_id = str(msg.get("agent_id", "SYSTEM"))
                content = sanitize_context_line(
                    agent_id,
                    str(msg.get("content", "")),
                    max_historic_chars,
                )
                if not content:
                    continue
                row = f"{context_label(agent_id)}: {content}"
                tokens = estimate_tokens(row)
                if compact_used + tokens > historic_budget:
                    continue
                compact_rows.append(row)
                compact_used += tokens
            for row in reversed(compact_rows):
                if not add_row(row):
                    break
        else:
            historic = conversation[-max(0, recent_turns * 4) : -recent_turns]
            for msg in historic:
                agent_id = str(msg.get("agent_id", "SYSTEM"))
                content = sanitize_context_line(agent_id, str(msg.get("content", "")), max_historic_chars)
                if content and not add_row(f"{context_label(agent_id)}: {content}"):
                    break

        if recent:
            add_row("ULTIMOS 4 TURNOS INTACTOS:")
        for msg in recent:
            agent_id = str(msg.get("agent_id", "SYSTEM"))
            content = sanitize_context_line(agent_id, str(msg.get("content", "")), max_recent_chars)
            if content:
                if not add_row(f"{context_label(agent_id)}: {content}"):
                    break
        return rows
