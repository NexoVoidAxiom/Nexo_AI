"""
intent_classifier.py — Clasificador de Intención para el Model Dispatcher
==========================================================================
Determina si un input requiere enrutamiento a Coda (32B) o puede
ser manejado por Arch-7 (7B).

Estrategia: scoring léxico + heurísticas estructurales.
Zero-latency (sin llamadas a modelos): < 1ms por clasificación.
"""

from __future__ import annotations

import re
from enum import Enum, auto


class IntentType(Enum):
    CHAT    = auto()   # Conversación, preguntas generales → Arch-7
    CODE    = auto()   # Código, debugging, implementación → Coda
    LOGIC   = auto()   # Razonamiento formal, matemáticas → Coda
    DATA    = auto()   # Análisis de datos, SQL → Coda
    UNKNOWN = auto()   # Sin señal clara → Arch-7 (default safe)


# ── Señales léxicas por categoría ───────────────────────────────────────────

_CODE_KEYWORDS: frozenset[str] = frozenset({
    # Lenguajes
    "python", "javascript", "typescript", "rust", "go", "golang", "java",
    "c++", "cpp", "c#", "csharp", "kotlin", "swift", "ruby", "php",
    "bash", "shell", "powershell", "sql", "html", "css", "react",
    "vue", "angular", "fastapi", "django", "flask", "express",
    # Actos de programación
    "código", "codigo", "code", "script", "función", "funcion", "function",
    "clase", "class", "método", "metodo", "method", "módulo", "modulo",
    "implementa", "implement", "escribe", "write", "crea", "create",
    "refactoriza", "refactor", "optimiza", "optimize", "arregla", "fix",
    "debug", "debuguea", "depura", "error", "bug", "excepción", "exception",
    "traceback", "stacktrace", "import", "librería", "libreria", "library",
    "api", "endpoint", "router", "middleware", "async", "await", "thread",
    "proceso", "process", "socket", "buffer", "heap", "stack", "pointer",
    "algoritmo", "algorithm", "estructura de datos", "data structure",
    "complejidad", "complexity", "o(n)", "o(log", "recursión", "recursion",
    "iteración", "iteracion", "loop", "bucle", "array", "lista", "list",
    "diccionario", "dictionary", "hash", "árbol", "arbol", "tree", "grafo",
    "graph", "pipeline", "ci/cd", "docker", "kubernetes", "git", "deploy",
    "test", "unittest", "pytest", "mock", "coverage", "lint",
    "orm", "migration", "schema", "query", "index", "transaction",
    "regex", "expresión regular", "parser", "tokenizer", "lexer",
    "websocket", "grpc", "protobuf", "serialization", "json", "yaml",
    "config", "env", "variable de entorno", "environment variable",
    "memory leak", "race condition", "deadlock", "mutex", "semaphore",
    "cpu", "gpu", "vram", "cuda", "tensor", "model", "inference",
    "embedding", "vector", "matrix", "numpy", "pandas", "pytorch",
})

_LOGIC_KEYWORDS: frozenset[str] = frozenset({
    # Matemáticas y razonamiento formal
    "demuestra", "demuestre", "prove", "proof", "teorema", "theorem",
    "lema", "lemma", "corolario", "corollary", "axioma", "axiom",
    "demostración", "demostracion", "demonstration",
    "ecuación", "ecuacion", "equation", "integral", "derivada",
    "derivate", "derivative", "límite", "limite", "limit",
    "matriz", "matrix", "determinante", "determinant", "eigenvector",
    "probabilidad", "probability", "estadística", "estadistica",
    "distribución", "distribucion", "distribution", "hipótesis",
    "hipotesis", "hypothesis", "varianza", "variance", "covarianza",
    # Razonamiento complejo
    "analiza", "analyze", "compara", "compare", "diseña", "design",
    "arquitectura", "architecture", "sistema", "system", "patrón",
    "patron", "pattern", "diagrama", "diagram", "flujo", "flow",
    "optimización", "optimizacion", "optimization", "complejidad",
    "complexity", "escalabilidad", "scalability", "rendimiento",
    "performance", "latencia", "latency", "throughput", "benchmark",
    # Multi-paso
    "paso a paso", "step by step", "explica cómo", "explica como",
    "explica detalladamente", "en detalle", "en profundidad",
    "¿cómo funciona", "como funciona", "how does", "how to build",
    "cómo construir", "como construir", "diseño de sistema",
    "system design", "arquitectura de software", "software architecture",
})

_DATA_KEYWORDS: frozenset[str] = frozenset({
    "select", "from", "where", "join", "group by", "order by",
    "insert", "update", "delete", "create table", "alter table",
    "dataframe", "csv", "excel", "parquet", "etl", "pipeline de datos",
    "data pipeline", "spark", "hadoop", "bigquery", "snowflake",
    "análisis de datos", "analisis de datos", "data analysis",
    "visualización", "visualizacion", "visualization", "matplotlib",
    "seaborn", "plotly", "tableau", "power bi",
})

# Señales que REDUCEN la probabilidad de intención código
_CHAT_COUNTER_SIGNALS: frozenset[str] = frozenset({
    "qué es", "que es", "what is", "quién es", "quien es", "who is",
    "cuándo", "cuando", "when", "dónde", "donde", "where",
    "por qué", "porque", "why", "cuánto cuesta", "quanto cuesta",
    "recomiéndame", "recomendame", "recommend", "qué piensas",
    "que piensas", "what do you think", "cuéntame", "cuentame",
    "hola", "hello", "gracias", "thanks", "ok", "bien",
})

# Patrones estructurales que FUERZAN clasificación como código
_CODE_STRUCTURAL_PATTERNS: list[re.Pattern] = [
    re.compile(r"```[\w]*\n"),                    # bloque de código markdown
    re.compile(r"def\s+\w+\s*\("),               # definición Python
    re.compile(r"class\s+\w+[\s:(]"),             # definición de clase
    re.compile(r"import\s+\w+"),                  # import statement
    re.compile(r"function\s+\w+\s*\("),           # JS/TS function
    re.compile(r"\bfor\s+\w+\s+in\b"),            # Python for loop
    re.compile(r"SELECT\s+.+FROM", re.IGNORECASE), # SQL query
    re.compile(r"@\w+\s*\n"),                     # decorador Python
    re.compile(r"\$\w+\s*="),                     # bash/PHP variable
    re.compile(r"async\s+def\s+\w+"),             # async Python
    re.compile(r"<[a-z]+\s[^>]*>"),              # HTML tag
    re.compile(r"\{[\s\S]{0,200}\}"),             # JSON/dict inline
]

# Umbral de score para considerar intent CODE o LOGIC
CODE_THRESHOLD  = 2.5
LOGIC_THRESHOLD = 2.0
DATA_THRESHOLD  = 2.0


class IntentClassifier:
    """
    Clasificador léxico + estructural de intención.
    
    Scoring:
      +1.0  por palabra clave CODE exacta
      +0.5  por palabra clave LOGIC exacta
      +0.5  por palabra clave DATA exacta
      +3.0  por patrón estructural de código
      -0.8  por señal de chat counter
      +0.3  por cada línea adicional (input multilínea sugiere código)
    """

    def classify(self, text: str) -> IntentType:
        score_code  = 0.0
        score_logic = 0.0
        score_data  = 0.0

        # Normalizar texto
        text_lower = text.lower()
        tokens = set(re.findall(r"[\w'\.]+", text_lower))

        # ── Scoring léxico ───────────────────────────────────────────────
        code_hits  = tokens & _CODE_KEYWORDS
        logic_hits = tokens & _LOGIC_KEYWORDS
        data_hits  = tokens & _DATA_KEYWORDS
        chat_hits  = tokens & _CHAT_COUNTER_SIGNALS

        score_code  += len(code_hits)  * 1.0
        score_logic += len(logic_hits) * 0.5
        score_data  += len(data_hits)  * 0.5

        # Multi-keyword en el mismo token: bonus por densidad
        if len(code_hits) >= 3:
            score_code += 1.5

        # Counter-signals reducen todos los scores
        counter_penalty = len(chat_hits) * 0.8
        score_code  = max(0.0, score_code  - counter_penalty)
        score_logic = max(0.0, score_logic - counter_penalty)
        score_data  = max(0.0, score_data  - counter_penalty)

        # ── Patrones estructurales ────────────────────────────────────────
        for pattern in _CODE_STRUCTURAL_PATTERNS:
            if pattern.search(text):
                score_code += 3.0
                break  # Un patrón es suficiente para +3

        # ── Heurística de longitud (input multilínea → probablemente código) ──
        lines = text.strip().split("\n")
        if len(lines) > 5:
            score_code  += 0.3 * min(len(lines) - 5, 10)
            score_logic += 0.15 * min(len(lines) - 5, 10)

        # ── Preguntas de "cómo hacer X" de múltiples pasos ────────────────
        if re.search(r"(cómo|como|how).{0,40}(paso|step|implement|construir|build)", text_lower):
            score_logic += 1.2

        # ── Decisión final ────────────────────────────────────────────────
        if score_code >= CODE_THRESHOLD:
            return IntentType.CODE
        if score_data >= DATA_THRESHOLD:
            return IntentType.DATA
        if score_logic >= LOGIC_THRESHOLD:
            return IntentType.LOGIC
        return IntentType.CHAT

    def explain(self, text: str) -> dict:
        """Versión debug que devuelve scores detallados."""
        text_lower = text.lower()
        tokens = set(re.findall(r"[\w'\.]+", text_lower))

        code_hits  = sorted(tokens & _CODE_KEYWORDS)
        logic_hits = sorted(tokens & _LOGIC_KEYWORDS)
        data_hits  = sorted(tokens & _DATA_KEYWORDS)
        chat_hits  = sorted(tokens & _CHAT_COUNTER_SIGNALS)
        structural = [p.pattern for p in _CODE_STRUCTURAL_PATTERNS if p.search(text)]
        intent     = self.classify(text)

        return {
            "intent":          intent.name,
            "code_hits":       code_hits,
            "logic_hits":      logic_hits,
            "data_hits":       data_hits,
            "chat_counter":    chat_hits,
            "structural_hits": structural,
        }
