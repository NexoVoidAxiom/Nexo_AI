"""
core/classifier.py — Clasificador de Intención de Void Axiom.
=============================================================
Estrategia: scoring léxico + patrones estructurales.
Zero-latency (sin llamadas a modelos): < 1ms por clasificación.

Resultado: IntentType.CODE → despacha a Coda
           IntentType.CHAT → despacha a Arch-7
"""
from __future__ import annotations

import re
from enum import Enum, auto


class IntentType(Enum):
    CHAT    = auto()   # Conversación → Arch-7
    CODE    = auto()   # Código, debugging → Coda
    LOGIC   = auto()   # Razonamiento formal → Coda
    DATA    = auto()   # SQL, análisis → Coda
    UNKNOWN = auto()   # Sin señal → Arch-7 (safe default)


_CODE_KEYWORDS = frozenset({
    "python", "javascript", "typescript", "rust", "go", "golang", "java",
    "c++", "cpp", "c#", "bash", "shell", "sql", "html", "css", "react",
    "fastapi", "django", "flask", "express", "código", "codigo", "code",
    "script", "función", "funcion", "function", "clase", "class", "método",
    "metodo", "implementa", "implement", "escribe", "write", "refactoriza",
    "refactor", "optimiza", "optimize", "arregla", "fix", "debug", "debuguea",
    "depura", "error", "bug", "excepción", "exception", "traceback",
    "import", "librería", "library", "api", "endpoint", "router", "middleware",
    "async", "await", "thread", "proceso", "process", "socket", "buffer",
    "heap", "stack", "algoritmo", "algorithm", "recursión", "recursion",
    "array", "lista", "list", "diccionario", "dictionary", "hash",
    "árbol", "arbol", "tree", "grafo", "graph", "pipeline", "docker",
    "kubernetes", "git", "test", "pytest", "mock", "orm", "schema",
    "query", "regex", "parser", "websocket", "cuda", "tensor", "model",
    "embedding", "vector", "numpy", "pandas", "pytorch",
})

_LOGIC_KEYWORDS = frozenset({
    "demuestra", "demuestre", "prove", "proof", "teorema", "theorem",
    "ecuación", "ecuacion", "equation", "integral", "derivada", "derivate",
    "límite", "limite", "limit", "matriz", "matrix", "probabilidad",
    "probability", "estadística", "statistics", "hipótesis", "hypothesis",
    "analiza", "analyze", "compara", "compare", "diseña", "design",
    "arquitectura", "architecture", "sistema", "system", "patrón", "pattern",
    "optimización", "optimization", "escalabilidad", "scalability",
    "rendimiento", "performance", "latencia", "latency", "throughput",
    "paso a paso", "step by step", "system design", "software architecture",
})

_DATA_KEYWORDS = frozenset({
    "select", "from", "where", "join", "group by", "order by",
    "insert", "update", "delete", "create table", "dataframe", "csv",
    "excel", "parquet", "etl", "spark", "bigquery", "análisis de datos",
    "data analysis", "visualización", "visualization", "matplotlib",
    "seaborn", "plotly", "tableau",
})

_CHAT_COUNTER = frozenset({
    "qué es", "que es", "what is", "quién es", "quien es", "who is",
    "cuándo", "cuando", "when", "dónde", "donde", "where", "por qué",
    "porque", "why", "qué piensas", "que piensas", "cuéntame", "cuentame",
    "hola", "hello", "gracias", "thanks", "ok", "bien",
})

_CODE_STRUCT_RE: list[re.Pattern] = [
    re.compile(r"```[\w]*\n"),
    re.compile(r"def\s+\w+\s*\("),
    re.compile(r"class\s+\w+[\s:(]"),
    re.compile(r"import\s+\w+"),
    re.compile(r"function\s+\w+\s*\("),
    re.compile(r"\bfor\s+\w+\s+in\b"),
    re.compile(r"SELECT\s+.+FROM", re.IGNORECASE),
    re.compile(r"async\s+def\s+\w+"),
    re.compile(r"<[a-z]+\s[^>]*>"),
    re.compile(r"\$\w+\s*="),
]

CODE_THRESHOLD  = 2.5
LOGIC_THRESHOLD = 2.0
DATA_THRESHOLD  = 2.0


class IntentClassifier:
    def classify(self, text: str) -> IntentType:
        tl = text.lower()
        tokens = set(re.findall(r"[\w'\.]+", tl))

        sc = len(tokens & _CODE_KEYWORDS) * 1.0
        sl = len(tokens & _LOGIC_KEYWORDS) * 0.5
        sd = len(tokens & _DATA_KEYWORDS) * 0.5
        penalty = len(tokens & _CHAT_COUNTER) * 0.8

        if len(tokens & _CODE_KEYWORDS) >= 3:
            sc += 1.5

        sc = max(0.0, sc - penalty)
        sl = max(0.0, sl - penalty)
        sd = max(0.0, sd - penalty)

        for pat in _CODE_STRUCT_RE:
            if pat.search(text):
                sc += 3.0
                break

        lines = text.strip().split("\n")
        if len(lines) > 5:
            sc += 0.3 * min(len(lines) - 5, 10)
            sl += 0.15 * min(len(lines) - 5, 10)

        if re.search(r"(cómo|como|how).{0,40}(paso|step|implement|construir|build)", tl):
            sl += 1.2

        if sc >= CODE_THRESHOLD:
            return IntentType.CODE
        if sd >= DATA_THRESHOLD:
            return IntentType.DATA
        if sl >= LOGIC_THRESHOLD:
            return IntentType.LOGIC
        return IntentType.CHAT
