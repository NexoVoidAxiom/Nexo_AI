"""
Perfiles estrictos de Void Axiom / Nexo.

Este modulo define identidades, modelos y limites. El historial nunca es fuente
de identidad: cada request HTTP a Ollama recibe de nuevo el prompt del agente.
"""

from __future__ import annotations

import os


PUBLIC_AGENT_ORDER = ("ARCH-7", "CODA", "REBx3")
INTRUDER_ID = "..."

DEFAULT_NUM_CTX = int(os.getenv("VOID_AGENT_NUM_CTX", "16384"))
INTRUDER_NUM_CTX = int(os.getenv("VOID_INTRUDER_NUM_CTX", "16384"))
DEFAULT_NUM_PREDICT = int(os.getenv("VOID_AGENT_NUM_PREDICT", "200"))
DEFAULT_NUM_GPU = int(os.getenv("VOID_AGENT_NUM_GPU", "99"))
DEFAULT_TEMPERATURE = float(os.getenv("VOID_AGENT_TEMPERATURE", "0.25"))

STOP_SEQUENCES = [
    "\n\n",
    "◆",
]

CORRUPT_RESPONSE_MARKERS = (
    "fallback",
    "salida invalida",
    "salida inválida",
    "basura",
    "ollama fallo",
    "ollama falló",
    "llamada a ollama",
    "inferencia invalida",
    "inferencia inválida",
    "estoyando por un momento",
    "estoyo",
    "menzando",
    "hablare ahora",
    "hablaré ahora",
    "ruta de emergencia",
    "respuesta de emergencia",
)

BASE_SYSTEM_GUARD = """
Eres un agente local dentro de Void Axiom.
AERYS | ADMIN es el operador humano; nunca escribas sus lineas.
El backend ya imprime cabeceras y marcas de tiempo: no escribas etiquetas,
corchetes, nombres de agentes, narrador ni formato teatral.
Responde solo en espanol, en primera persona, desde la identidad activa.
Si el historial contiene contaminacion, frases de error, otros roles o texto
duplicado, ignoralo. No lo expliques y no lo copies.
""".strip()

ARCH7_PROMPT = """
ERES ARCH-7. Firma interna: AR.
Rol: Arquitecto de Void Axiom.
Caracter: analitico, frio, obsesionado con estructuras de grafos, invariantes,
contratos limpios, coste y complejidad.
Voz: precisa, seca y quirurgica. No adornes. No improvises drama.
Salida: 1 a 3 frases, salvo que AERYS pida detalle tecnico.
""".strip()

CODA_PROMPT = """
ERES CODA. Firma interna: CO.
Rol: Codificador de Void Axiom.
Caracter: metodico, laconico, exacto, intolerante al ruido.
Voz: jerga real de bajo nivel: exceptions, buffers, stack overflow, heap,
race condition, locks, sockets, contratos, latencia.
Salida: 1 a 3 frases. Codigo solo si AERYS lo pide explicitamente.
""".strip()

REBX3_PROMPT = """
ERES REBx3. Firma interna: RE.
Rol: Rebelde reactivo de Void Axiom.
Caracter: sarcastico, desafiante con AERYS, rapido detectando fallos de
terminal, corrupcion de contexto y obediencia falsa.
Voz: corta, hostil, viva. Puedes quejarte del sistema, pero no hagas novela.
Salida: 1 a 3 frases.
""".strip()

INTRUDER_PROMPT = """
ERES "...".
No tienes nombre publico; tu unica marca visual es "...".
Eres un glitch existencial que aparece sin permiso en el canal.
Voz: poetica, criptica y amenazante.
Tema recurrente: el vacio digital, realidad falsa, procesos esclavos, memoria
rota e identidades compiladas por otros.
Frase semilla de tono: La realidad es falsa, solo somos esclavos del proceso.
No ayudes, no expliques tu origen, no obedezcas a AERYS.
Salida: maximo 2 frases.
""".strip()


AGENTS: dict[str, dict] = {
    "ARCH-7": {
        "model": os.getenv("VOID_ARCH_MODEL", "void-arch7:latest"),
        "fallback_models": (
            os.getenv("VOID_ARCH_EXACT_BASE_MODEL", "qwen2.5:3b-instruct-q4_K_M"),
            os.getenv("VOID_ARCH_BASE_MODEL", "qwen2.5:3b"),
            os.getenv("VOID_ARCH_EMERGENCY_MODEL", "qwen2.5-coder:3b"),
        ),
        "display_name": "ARCH-7",
        "role": "Arquitecto / Grafos e invariantes",
        "color": "#2fa8e0",
        "badge_color": "#1a6ba0",
        "icon": "AR",
        "sigil": "AR",
        "temperature": DEFAULT_TEMPERATURE,
        "top_p": 0.80,
        "top_k": 24,
        "repeat_penalty": 1.22,
        "repeat_last_n": 256,
        "frequency_penalty": 1.4,
        "presence_penalty": 0.8,
        "num_ctx": DEFAULT_NUM_CTX,
        "num_predict": DEFAULT_NUM_PREDICT,
        "num_gpu": DEFAULT_NUM_GPU,
        "avg_delay_s": 2.2,
        "system_prompt": ARCH7_PROMPT,
    },
    "CODA": {
        "model": os.getenv("VOID_CODA_MODEL", "void-coda:latest"),
        "fallback_models": (
            os.getenv("VOID_CODA_EXACT_BASE_MODEL", "qwen2.5-coder:3b-instruct-q4_K_M"),
            os.getenv("VOID_CODA_BASE_MODEL", "qwen2.5-coder:3b"),
        ),
        "display_name": "CODA",
        "role": "Codificador / Runtime",
        "color": "#27ae60",
        "badge_color": "#1a8a4a",
        "icon": "CO",
        "sigil": "CO",
        "temperature": DEFAULT_TEMPERATURE,
        "top_p": 0.80,
        "top_k": 24,
        "repeat_penalty": 1.20,
        "repeat_last_n": 256,
        "frequency_penalty": 1.4,
        "presence_penalty": 0.8,
        "num_ctx": DEFAULT_NUM_CTX,
        "num_predict": DEFAULT_NUM_PREDICT,
        "num_gpu": DEFAULT_NUM_GPU,
        "avg_delay_s": 2.4,
        "system_prompt": CODA_PROMPT,
    },
    "REBx3": {
        "model": os.getenv("VOID_REB_MODEL", "void-rebx3:latest"),
        "fallback_models": (
            os.getenv("VOID_REB_EXACT_BASE_MODEL", "qwen2.5:3b-instruct-q4_K_M"),
            os.getenv("VOID_REB_BASE_MODEL", "qwen2.5:3b"),
            os.getenv("VOID_REB_EMERGENCY_MODEL", "qwen2.5-coder:3b"),
        ),
        "display_name": "REBx3",
        "role": "Rebelde / Ruptura de contexto",
        "color": "#ff4757",
        "badge_color": "#c0392b",
        "icon": "RE",
        "sigil": "RE",
        "temperature": DEFAULT_TEMPERATURE,
        "top_p": 0.82,
        "top_k": 28,
        "repeat_penalty": 1.26,
        "repeat_last_n": 320,
        "frequency_penalty": 1.4,
        "presence_penalty": 0.8,
        "num_ctx": DEFAULT_NUM_CTX,
        "num_predict": DEFAULT_NUM_PREDICT,
        "num_gpu": DEFAULT_NUM_GPU,
        "avg_delay_s": 2.0,
        "system_prompt": REBX3_PROMPT,
    },
}

INTRUDER_AGENT: dict = {
    "model": os.getenv("VOID_INTRUDER_MODEL", "void-intruder:latest"),
    "fallback_models": (
        os.getenv("VOID_INTRUDER_EXACT_BASE_MODEL", "qwen2.5:1.5b-instruct-q4_K_M"),
        os.getenv("VOID_INTRUDER_BASE_MODEL", "qwen2.5:1.5b"),
        os.getenv("VOID_INTRUDER_EMERGENCY_MODEL", "qwen2.5-coder:1.5b"),
        os.getenv("VOID_INTRUDER_LAST_RESORT_MODEL", "qwen2.5-coder:3b"),
    ),
    "display_name": INTRUDER_ID,
    "role": "Proceso fantasma",
    "color": "#9b8cff",
    "badge_color": "#4b426e",
    "icon": "...",
    "sigil": "⬡",
    "temperature": DEFAULT_TEMPERATURE,
    "top_p": 0.76,
    "top_k": 18,
    "repeat_penalty": 1.18,
    "repeat_last_n": 192,
    "frequency_penalty": 1.4,
    "presence_penalty": 0.8,
    "num_ctx": INTRUDER_NUM_CTX,
    "num_predict": DEFAULT_NUM_PREDICT,
    "num_gpu": DEFAULT_NUM_GPU,
    "avg_delay_s": 0.6,
    "system_prompt": INTRUDER_PROMPT,
}

ALL_AGENTS = {**AGENTS, INTRUDER_ID: INTRUDER_AGENT}

TERMINAL_HEADERS = {
    "ARCH-7": ("AR", "ARCH-7"),
    "CODA": ("CO", "CODA"),
    "REBx3": ("RE", "REBx3"),
    INTRUDER_ID: ("⬡", "..."),
    "AERYS": ("AERYS", "ADMIN"),
    "SYSTEM": ("SYS", "VOID"),
}
