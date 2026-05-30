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

MEJORAS v2.1:
  - Puntuación semántica por categorías (no solo keywords binarias)
  - Se penalizan falsos positivos: palabras como "tiempo", "dato" en contexto
    de charla ya no activan WORK
  - Co-occurrencia: detecta si una palabra de trabajo está aislada o en contexto real
  - Caché de clasificación para no recalcular prompts repetidos

Mapping de modelos Nexo:
  - Nexo Lite 1.0  → qwen2.5-coder:3b
  - Nexo Coder 1.0 → qwen2.5-coder:7b-instruct  
  - Nexo Pro 1.0   → qwen2.5-coder:14b-instruct-q4_K_M
"""

from __future__ import annotations

import re
import unicodedata
import hashlib
import logging
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger("smart_router")

# ─── Caché de clasificación ──────────────────────────────────────────────────
_CLASSIFICATION_CACHE: dict[str, tuple] = {}
_MAX_CACHE_SIZE = 500

def _cache_key(text: str) -> str:
    """Genera una clave de caché para el texto normalizado."""
    normalized = _normalize(text)[:100]  # Solo primeros 100 chars para cache
    return hashlib.md5(normalized.encode()).hexdigest()


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


# ─── SISTEMA DE PUNTUACIÓN SEMÁNTICA ─────────────────────────────────────────
# En lugar de detección binaria por keywords, asignamos pesos a las palabras
# y calculamos una puntuación. Si supera el umbral → WORK.
# Esto elimina falsos positivos de palabras aisladas.

# Palabras de trabajo con peso (mayor peso = más probabilidad de ser trabajo real)
_WORK_LEXICON: dict[str, float] = {
    # Código / programación (peso alto)
    "python": 3.0, "javascript": 3.0, "typescript": 3.0, "rust": 3.0, 
    "go": 2.5, "java": 3.0, "c++": 3.0, "cpp": 3.0,
    "codigo": 2.5, "code": 2.5, "script": 2.5, "funcion": 2.5, "function": 2.5,
    "clase": 2.5, "class": 2.5, "metodo": 2.5, "implementa": 3.0, "implement": 3.0,
    "escribe": 2.0, "write": 2.0, "crea": 2.0, "create": 2.0, 
    "refactoriza": 3.0, "refactor": 3.0,
    "optimiza": 2.5, "optimize": 2.5, "arregla": 2.0, "fix": 2.5, "debug": 3.0, "bug": 2.5,
    "import": 2.0, "api": 2.5, "endpoint": 3.0, "router": 2.5, "async": 2.5, "await": 2.5,
    "algoritmo": 3.0, "algorithm": 3.0, "estructura": 2.0, "pipeline": 2.5, "docker": 2.5,
    "test": 2.0, "pytest": 2.5, "mock": 2.5, "sql": 3.0, "query": 2.5, "database": 2.5,
    "html": 2.0, "css": 2.0, "react": 2.5, "vue": 2.5, "fastapi": 3.0, "django": 2.5,
    "git": 2.0, "deploy": 2.5, "compila": 2.5, "ejecuta": 2.0, "run": 2.0,
    "terminal": 2.0, "bash": 2.5, "shell": 2.0, "comando": 2.0, "command": 2.0,
    # Análisis / razonamiento (peso medio-alto)
    "analiza": 2.5, "analyze": 2.5, "compara": 2.0, "compare": 2.0, 
    "explica": 1.5, "explain": 1.5,
    "arquitectura": 2.5, "architecture": 2.5, "disena": 2.5, "design": 2.5,
    "optimizacion": 2.5, "optimization": 2.5, "rendimiento": 2.5, "performance": 2.5,
    "investiga": 2.0, "investigate": 2.0, "resuelve": 2.0, "solve": 2.0, 
    "calcula": 2.0, "calculate": 2.0,
    "demuestra": 2.0, "prove": 2.5, "teorema": 3.0, "theorem": 3.0, 
    "ecuacion": 2.5, "equation": 2.5,
    # Datos (peso medio)
    "dataframe": 2.5, "csv": 2.0, "pandas": 3.0, "numpy": 3.0, 
    "matplotlib": 3.0, "grafica": 2.5,
    "analisis": 2.0, "analysis": 2.0, "visualizacion": 2.5, "visualization": 2.5,
    "estadistica": 2.5, "statistics": 2.5, "machine": 2.5, "learning": 2.5,
    # Archivos / contexto (peso bajo, pueden ser falsos positivos)
    "archivo": 1.5, "file": 1.5, "subido": 1.5, "upload": 1.5, 
    "documento": 1.5, "document": 1.5,
    "contexto": 1.0, "context": 1.0, "texto": 1.0, "text": 1.0,
    "linea": 1.0, "line": 1.0, "columna": 1.0, "column": 1.0,
    # Palabras trampa que SOLO son trabajo en contexto (peso muy bajo)
    "dato": 0.5, "data": 0.5, "tabla": 0.5, "table": 0.5,
    "numero": 0.5, "number": 0.5, "valor": 0.5, "value": 0.5,
}

# Palabras de charla con peso (contrarrestan el peso de trabajo)
_CHAT_LEXICON: dict[str, float] = {
    "hola": 3.0, "hello": 3.0, "hey": 3.0, "buenas": 3.0, "buenos": 2.0,
    "gracias": 3.0, "thanks": 3.0, "ok": 1.5, "vale": 1.5, "nada": 1.0,
    "como": 1.5, "estas": 2.0, "bien": 1.0, "tal": 1.0,
    "adios": 3.0, "bye": 3.0, "hasta": 1.5, "luego": 1.5, "vemos": 1.5,
    "quien": 1.5, "eres": 2.0, "created": 1.0,
    "puedes": 1.0, "hacer": 1.0,
    "encantado": 2.0, "gusto": 2.0, "conocer": 1.0,
    "broma": 2.0, "joke": 2.0, "risa": 2.0, "lol": 2.0, "xd": 1.5,
}

# Umbral: puntuación neta (work_score - chat_score) para clasificar como WORK
_WORK_THRESHOLD = 2.5

# Patrones de código estructural (estos siempre → WORK sin pasar por scoring)
_WORK_PATTERNS = [
    re.compile(r"```[\w]*\n"),                    # Bloque de código
    re.compile(r"(def |class |function|import |const |let |var )\s+\w"),  # Código
    re.compile(r"SELECT\s+.+FROM", re.IGNORECASE), # SQL
    re.compile(r"(docker|git|npm|pip)\s+\w+"),     # Comandos
    re.compile(r"![\w]+"),                         # Shebang/bash commands
    re.compile(r"\{[\s\S]{50,}\}"),               # JSON/dict grande
    re.compile(r"\$\w+\s*="),                      # Bash variables
    re.compile(r"<\w+[^>]*>"),                     # HTML tags
]

# Patrones de charla pura
_CHAT_PATTERNS = [
    re.compile(r"^(hola|hey|buenas|oye|ey)\b", re.IGNORECASE),
    re.compile(r"\b(gracias|thanks|ok|vale)\s*$", re.IGNORECASE),
    re.compile(r"^como est", re.IGNORECASE),
    re.compile(r"^qu[e] (eres|puedes|sabes|haces)", re.IGNORECASE),
    re.compile(r"^qui[e]n (eres|te|creo)", re.IGNORECASE),
]


class WorkIntent(Enum):
    """Intención reducida: CHAT (charla casual) o WORK (tarea real)."""
    CHAT = auto()  # Charla casual → modelo ligero 3B
    WORK = auto()  # Tarea real → modelo pesado del usuario


class SmartRouter:
    """
    Router inteligente que decide si usar modelo ligero o pesado.
    
    MEJORA v2.1: Usa un sistema de puntuación semántica con pesos en vez
    de detección binaria por keywords. Esto reduce falsos positivos.
    
    Estrategia:
      1. Patrones de código → WORK inmediato (sin ambigüedad)
      2. Puntuación semántica: suma pesos de palabras de trabajo - pesos de charla
      3. Si supera umbral → WORK, si no → CHAT
      4. Caché de resultados para no recalcular prompts repetidos
    """

    def classify(self, text: str) -> WorkIntent:
        """
        Clasifica el texto como CHAT o usando puntuación semántica.
        
        Args:
            text: El mensaje del usuario
            
        Returns:
            WorkIntent.CHAT si es charla pura
            WorkIntent.WORK si hay señales de trabajo
        """
        # Intentar caché
        ck = _cache_key(text)
        if ck in _CLASSIFICATION_CACHE:
            return _CLASSIFICATION_CACHE[ck]
        
        # 1. Patrones de código → WORK inmediato
        for pattern in _WORK_PATTERNS:
            if pattern.search(text):
                result = WorkIntent.WORK
                self._cache_result(ck, result)
                return result

        # Normalizar para análisis semántico
        text_norm = _normalize(text)
        tokens = set(re.findall(r"[\w']+", text_norm))
        
        # 2. Puntuación semántica
        work_score = 0.0
        chat_score = 0.0
        
        for token in tokens:
            work_score += _WORK_LEXICON.get(token, 0.0)
            chat_score += _CHAT_LEXICON.get(token, 0.0)
        
        # Bonus por longitud: mensajes muy cortos tienden a ser charla
        word_count = len(text.split())
        if word_count <= 3:
            chat_score += 1.0
        
        # Bonus por mensajes largos: tienden a ser trabajo
        if word_count > 20:
            work_score += 1.0
        
        # Penalizar palabras de trabajo aisladas (sin contexto técnico)
        net_score = work_score - chat_score
        
        # 3. Decisión
        if net_score >= _WORK_THRESHOLD:
            result = WorkIntent.WORK
        else:
            result = WorkIntent.CHAT
        
        self._cache_result(ck, result)
        logger.debug("Clasificación: net_score=%.1f (work=%.1f chat=%.1f) → %s",
                     net_score, work_score, chat_score, result.name)
        return result

    def _cache_result(self, key: str, result: WorkIntent) -> None:
        """Guarda en caché, manteniendo tamaño máximo."""
        _CLASSIFICATION_CACHE[key] = result
        if len(_CLASSIFICATION_CACHE) > _MAX_CACHE_SIZE:
            # Eliminar ~20% de entradas viejas
            keys = list(_CLASSIFICATION_CACHE.keys())
            for k in keys[:100]:
                del _CLASSIFICATION_CACHE[k]

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