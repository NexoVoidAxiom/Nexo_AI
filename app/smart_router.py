"""
smart_router.py — Router Inteligente para Nexo AI
==================================================
Determina automáticamente si usar un modelo ligero (3B) para charla casual
o el modelo pesado seleccionado por el usuario (Nexo Lite/Coder/Pro) 
para tareas que requieren razonamiento o código.

Flujo:
  1. Clasificar intención del mensaje (CHAT vs WORK)
  2. Si es CHAT (saludo, conversación casual) → modelo ligero 3B
  3. Si es WORK (código, análisis, razonamiento) → modelo pesado del usuario

Mapping de modelos Nexo:
  - Nexo Lite 1.0  → qwen2.5-coder:3b
  - Nexo Coder 1.0 → qwen2.5-coder:7b-instruct  
  - Nexo Pro 1.0   → qwen2.5-coder:14b-instruct-q4_K_M
"""

from __future__ import annotations

import re
import unicodedata
import logging
from enum import Enum, auto

logger = logging.getLogger("smart_router")


def _normalize(text: str) -> str:
    """Elimina acentos, convierte a minúsculas y colapsa espacios.

    'código' → 'codigo', 'Análisis' → 'analisis'.
    Evita falsos negativos por tildes o variaciones de encoding.
    """
    nfd = unicodedata.normalize("NFD", text)
    ascii_text = nfd.encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", ascii_text.lower()).strip()

# ─── Modelos disponibles ──────────────────────────────────────────────────

LIGHT_MODEL = "qwen2.5-coder:3b"  # Para charla casual

NEXO_MODELS = {
    "nexo_lite": {
        "id": "nexo_lite",
        "display": "Nexo Lite 1.0",
        "model": "qwen2.5-coder:3b",
        "description": "Ligero y rápido, ideal para tareas simples",
        "vram_gb": 2.0,
    },
    "nexo_coder": {
        "id": "nexo_coder",
        "display": "Nexo Coder 1.0",
        "model": "qwen2.5-coder:7b-instruct",
        "description": "Balance velocidad/calidad, ideal para código y análisis",
        "vram_gb": 4.5,
    },
    "nexo_pro": {
        "id": "nexo_pro",
        "display": "Nexo Pro 1.0",
        "model": "qwen2.5-coder:14b-instruct-q4_K_M",
        "description": "Máxima capacidad, razonamiento profundo (Q4_K_M)",
        "vram_gb": 8.5,
    },
}

DEFAULT_HEAVY_MODEL = "nexo_coder"  # Modelo pesado por defecto


# ─── Clasificador de intención (CHAT vs WORK) ──────────────────────────────
# Es más sensible que el IntentClassifier existente: 
# cualquier señal de "trabajo" hace saltar a WORK.
# Solo CHAT puro (saludos, preguntas simples, conversación) usa el modelo ligero.

_WORK_KEYWORDS = frozenset({
    # Código / programación
    "python", "javascript", "typescript", "rust", "go", "java", "c++", "cpp",
    "codigo", "code", "script", "funcion", "function",
    "clase", "class", "metodo", "implementa", "implement",
    "escribe", "write", "crea", "create", "refactoriza", "refactor",
    "optimiza", "optimize", "arregla", "fix", "debug", "bug", "error",
    "import", "api", "endpoint", "router", "async", "await",
    "algoritmo", "algorithm", "estructura", "pipeline", "docker",
    "test", "pytest", "mock", "sql", "query", "database", "select",
    "html", "css", "react", "vue", "fastapi", "django", "flask",
    "git", "deploy", "compila", "ejecuta", "run",
    # Análisis / razonamiento
    "analiza", "analyze", "compara", "compare", "explica", "explain",
    "arquitectura", "architecture", "disena", "design", "diagrama",
    "optimizacion", "optimization", "rendimiento", "performance",
    "investiga", "investigate", "resuelve", "solve", "calcula", "calculate",
    "paso", "step", "detalle", "detail",
    "demuestra", "prove", "teorema", "theorem", "ecuacion", "equation",
    # Datos
    "dataframe", "csv", "pandas", "numpy", "matplotlib", "grafica",
    "analisis", "analysis", "visualizacion", "visualization",
    "estadistica", "statistics", "machine", "learning",
    # Archivos / contexto
    "archivo", "file", "subido", "upload", "documento", "document",
    "contexto", "context", "texto", "text",
})

# Palabras clave que INDICAN claramente charla casual
_CHAT_KEYWORDS = frozenset({
    "hola", "hello", "hey", "buenas", "buenos", "dias", "tardes",
    "gracias", "thanks", "ok", "vale", "nada", "welcome",
    "como", "estas", "how", "are", "you", "bien", "tu",
    "que", "tal", "what", "up", "todo",
    "adios", "bye", "hasta", "luego", "nos", "vemos",
    "quien", "eres", "who", "created",
    "puedes", "hacer", "can", "do",
})

# Patrones que indican claramente que es charla (no trabajo)
_CHAT_PATTERNS = [
    re.compile(r"^(hola|hey|buenas|oye|ey)\b", re.IGNORECASE),
    re.compile(r"\b(gracias|thanks|ok|vale)\s*$", re.IGNORECASE),
    re.compile(r"^como est", re.IGNORECASE),
    re.compile(r"^qu[e] (eres|puedes|sabes|haces)", re.IGNORECASE),
    re.compile(r"^qui[e]n (eres|te|creo)", re.IGNORECASE),
]

# Patrones que INDICAN claramente que es trabajo
_WORK_PATTERNS = [
    re.compile(r"```[\w]*\n"),                    # Bloque de código
    re.compile(r"(def|class|function|import|const|let|var)\s+\w"),  # Código
    re.compile(r"SELECT\s+.+FROM", re.IGNORECASE), # SQL
    re.compile(r"(docker|git|npm|pip)\s+\w+"),     # Comandos
    re.compile(r"![\w]+"),                         # Shebang/bash commands
    re.compile(r"\{[\s\S]{50,}\}"),               # JSON/dict grande
    re.compile(r"\$\w+\s*="),                      # Bash variables
    re.compile(r"<\w+[^>]*>"),                     # HTML tags
]


class WorkIntent(Enum):
    """Intención reducida: CHAT (charla casual) o WORK (tarea real)."""
    CHAT = auto()  # Charla casual → modelo ligero 3B
    WORK = auto()  # Tarea real → modelo pesado del usuario


class SmartRouter:
    """
    Router inteligente que decide si usar modelo ligero o pesado.
    
    Estrategia:
      - Si hay palabras clave de TRABAJO → WORK
      - Si hay patrones de código/estructura → WORK
      - Si SOLO hay palabras de charla y ninguna de trabajo → CHAT
      - En caso de duda (sin señales claras) → WORK (usa el modelo pesado)
        porque es mejor pecar de usar un modelo potente para responder
        una pregunta simple que usar el ligero para algo complejo.
    """

    def classify(self, text: str) -> WorkIntent:
        """
        Clasifica el texto como CHAT (charla casual) o WORK (tarea real).

        Orden de evaluación:
          1. Patrones estructurales de código → WORK inmediato
          2. Keywords de trabajo (sin acentos, normalizado) → WORK
          3. Patrones de charla + longitud del mensaje → CHAT o WORK
          4. Heurística de longitud + chat_hits → CHAT
          5. Default seguro → WORK

        Returns:
            WorkIntent.CHAT si es charla pura sin trabajo
            WorkIntent.WORK si hay cualquier señal de trabajo o duda
        """
        # Normalizar: quitar acentos + minúsculas para comparación robusta
        text_norm = _normalize(text)

        # 1. Detectar patrones de código (sobre texto original, son regexes estructurales)
        for pattern in _WORK_PATTERNS:
            if pattern.search(text):
                logger.debug("WORK detectado por patrón estructural: %s", pattern.pattern)
                return WorkIntent.WORK

        # 2. Detectar palabras clave de trabajo (tokenizando el texto normalizado)
        tokens = set(re.findall(r"[\w']+", text_norm))
        work_hits = tokens & _WORK_KEYWORDS
        chat_hits = tokens & _CHAT_KEYWORDS

        if work_hits:
            logger.debug("WORK detectado por keywords: %s", work_hits)
            return WorkIntent.WORK

        # 3. Detectar patrones de charla (sobre texto normalizado)
        for pattern in _CHAT_PATTERNS:
            if pattern.search(text_norm):
                # "hola, analiza este archivo" → tiene patrón chat PERO es largo → WORK
                if len(text.split()) > 8:
                    logger.debug("Patrón de charla detectado pero mensaje largo -> WORK")
                    return WorkIntent.WORK
                logger.debug("CHAT detectado por patrón: %s", pattern.pattern)
                return WorkIntent.CHAT

        # 4. Heurística: mensajes cortos (1-4 palabras) con palabras de charla → CHAT
        word_count = len(text.split())
        if word_count <= 4 and chat_hits:
            logger.debug("CHAT: mensaje corto con palabras de charla")
            return WorkIntent.CHAT

        # 5. En duda o texto largo → WORK (mejor pecar de usar el modelo potente)
        logger.debug("DUDA resuelta como WORK (default seguro)")
        return WorkIntent.WORK

    def get_models_for_intent(
        self,
        intent: WorkIntent,
        user_selected_model: str = DEFAULT_HEAVY_MODEL,
    ) -> dict:
        """
        Devuelve la configuración de modelos según la intención y la preferencia del usuario.
        
        Args:
            intent: CHAT o WORK
            user_selected_model: ID del modelo Nexo seleccionado por el usuario
                               (nexo_lite, nexo_coder, nexo_pro)
        
        Returns:
            dict con 'model' (nombre en Ollama) y 'is_heavy' (bool)
        """
        if intent == WorkIntent.CHAT:
            return {
                "model": LIGHT_MODEL,
                "is_heavy": False,
                "label": "Modo charla (3B)",
            }
        
        # WORK: usar el modelo pesado seleccionado por el usuario
        model_config = NEXO_MODELS.get(user_selected_model, NEXO_MODELS[DEFAULT_HEAVY_MODEL])
        return {
            "model": model_config["model"],
            "is_heavy": True,
            "label": f"Modo trabajo ({model_config['display']})",
        }

    def get_nexo_model_info(self, model_id: str) -> dict | None:
        """Devuelve la info de un modelo Nexo por su ID."""
        return NEXO_MODELS.get(model_id)

    def list_nexo_models(self) -> list[dict]:
        """Lista todos los modelos Nexo disponibles."""
        return list(NEXO_MODELS.values())


# ─── Singleton ────────────────────────────────────────────────────────────
smart_router = SmartRouter()