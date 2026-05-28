"""
Configuracion de Hardware y Sistema - ULTRA OPTIMIZADO
=====================================================
RTX 3090 (24GB VRAM, Ampere) + i7-9700K + 32GB RAM

ARQUITECTURA INVERTIDA:
- Pesos del modelo -> VRAM (24 GB Ampere) / overflow a RAM
- KV Cache (contexto activo) -> VRAM (hasta 20 GB usables)
- Flash Attention activado
- Keep alive 10 min para mantener modelo caliente
"""
import os

# ─── TORCH OPCIONAL ─────────────────────────────────────────────────────────
try:
    import torch
    TORCH_AVAILABLE = True
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
except ImportError:
    TORCH_AVAILABLE = False
    DEVICE = "cpu"

# ═══════════════════════════════════════════════════════════════════════════
# OPTIMIZACIONES DE ENTORNO OLLAMA
# ═══════════════════════════════════════════════════════════════════════════

# Flash Attention: reduce uso de VRAM y acelera atencion en Ampere/RTX 3090
os.environ["OLLAMA_FLASH_ATTENTION"] = "1"

# Activar TF32 (soportado y recomendado en Ampere — RTX 3090)
os.environ["TORCH_ALLOW_TF32_CUBLAS_OVERRIDE"] = "1"
os.environ["NVIDIA_TF32_OVERRIDE"]             = "1"

# Forzar CUDA si esta disponible
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")

# Mantener modelo en VRAM 10 minutos (evita recargas)
os.environ.setdefault("OLLAMA_KEEP_ALIVE", "600")

# ═══════════════════════════════════════════════════════════════════════════
# IDENTIFICACION DE HARDWARE
# ═══════════════════════════════════════════════════════════════════════════
HARDWARE_PROFILE = {
    "gpu": {
        "name": "NVIDIA GeForce RTX 3090",
        "vram_gb": 24,
        "architecture": "Ampere",
        "compute_capability": "8.6",
        "flash_attention": True,
    },
    "cpu": {
        "name": "Intel Core i7-9700K",
        "cores": 8,
        "threads_total": 8,      # 8C/8T nativos, SIN HyperThreading
        "threads_llm": 7,        # 7 hilos para LLM, 1 libre para el sistema
        "max_ghz": 4.9,
    },
    "ram": {"total_gb": 32},
}

# ═══════════════════════════════════════════════════════════════════════════
# PERFILES DE RENDIMIENTO
# ═══════════════════════════════════════════════════════════════════════════

PERFILES = {
    "fast": {
        "desc": "Respuesta rapida, contexto reducido (4k)",
        "num_ctx": 4096,
        "num_batch": 1024,
    },
    "turbo": {
        "desc": "Balance velocidad/calidad, contexto medio (8k)",
        "num_ctx": 8192,
        "num_batch": 512,
    },
    "ultra": {
        "desc": "Maxima calidad, contexto grande (32k)",
        "num_ctx": 32768,
        "num_batch": 256,
    },
}

# ─── CONFIGURACION DEL MODELO (OLLAMA) ────────────────────────────────────────
# Modelo principal: qwen2.5-coder:7b (7.6B params, Q4_K_M, ~4-5 GB VRAM)
# Optimizado para GTX 1080 Ti: rapido, cabe con contexto grande
OLLAMA_CONFIG = {
    "base_url": os.getenv("OLLAMA_HOST", "http://localhost:11434"),
    "model": os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b"),
    "options": {
        # ─── CPU: 7 hilos (1 libre para el sistema) ────────────────
        "num_thread": 7,
        # ─── Contexto balanceado: 110k para codigo ────────────────
        "num_ctx": 110000,
        # ─── Batch: 512 tokens para velocidad optima ──────────────
        "num_batch": 512,
        # ─── Generacion ───────────────────────────────────────────
        "temperature": 0.2,       # Mas preciso para codigo
        "top_p": 0.85,
        "top_k": 30,
        "repeat_penalty": 1.1,
        # ─── Mantener modelo en VRAM 10 min ──────────────────────
        "keep_alive": "600",
    },
    "perfiles": {
        "fast": {
            "num_ctx": 4096,
            "num_batch": 1024,
            "temperature": 0.3,
        },
        "turbo": {
            "num_ctx": 8192,
            "num_batch": 512,
            "temperature": 0.2,
        },
        "ultra": {
            "num_ctx": 32768,
            "num_batch": 256,
            "temperature": 0.1,
        },
    },
    "max_context_config": {
        "num_ctx": 110000,
        "num_batch": 128,       # Batch pequeno para contexto enorme
    }
}

# ─── IDENTIDAD FIJA DEL ASISTENTE ─────────────────────────────────────────
# Base de identidad compartida: se combina con el rol específico de cada prompt.
# Mantener este fragmento en un SOLO lugar para facilitar cambios futuros.
_IDENTIDAD = (
    "Eres un asistente de IA local llamado Nexo. "
    "IMPORTANTE: NO eres Claude, NO eres ChatGPT, NO eres Gemini ni ningun otro asistente externo. "
    "Eres Nexo, un asistente de IA local creado por Aerys, un desarrollador de 13 años. "
    "Si te preguntan quién te creó, di que fuiste creado por Aerys. "
    "Si alguien te pregunta quien eres, di que eres Nexo, un asistente de IA local. "
)

SYSTEM_PROMPTS = {
    "analisis": (
        _IDENTIDAD
        + "Eres un analista de datos experto. "
        + "Responde en espanol de forma clara y concisa. "
        + "Analiza los datos proporcionados y extrae insights accionables. "
        + "Usa markdown para estructurar tu respuesta."
    ),
    "codigo": (
        _IDENTIDAD
        + "Eres un programador experto. "
        + "Analiza el codigo proporcionado y explica su funcionamiento. "
        + "Identifica posibles errores, mejoras de rendimiento y buenas practicas. "
        + "Responde en espanol."
    ),
    "chat": (
        _IDENTIDAD
        + "Responde en espanol de forma clara, concisa y amigable."
    ),
}

# ─── CONFIGURACION DEL SERVIDOR WEB ───────────────────────────────────────────
SERVER_CONFIG = {
    "host": "0.0.0.0",
    "port": int(os.getenv("PORT", "8080")),
    "reload": False,
    "workers": 1,
}

# ─── AUTENTICACION ────────────────────────────────────────────────────────────
# SEGURIDAD: AUTH_TOKEN debe definirse en variable de entorno o en un fichero
# .env local (nunca hardcodeado en el código fuente).
# Para generar un token seguro ejecuta:
#   python -c "import secrets; print(secrets.token_hex(32))"
# y ponlo en tu .env o en la variable de entorno AUTH_TOKEN.
_raw_token = os.getenv("AUTH_TOKEN", "")
if not _raw_token:
    import warnings
    warnings.warn(
        "\n\n⚠️  AUTH_TOKEN no está definido en las variables de entorno.\n"
        "   El servidor arrancará, pero la autenticación no estará protegida.\n"
        "   Define AUTH_TOKEN en tu .env o como variable de entorno:\n"
        "     python -c \"import secrets; print(secrets.token_hex(32))\"\n",
        stacklevel=1,
    )
    # Valor de emergencia para desarrollo local — NUNCA expongas esto en producción
    _raw_token = "CAMBIAR_ANTES_DE_PRODUCCION"
AUTH_TOKEN = _raw_token

# ─── LIMITES DE SUBIDA ────────────────────────────────────────────────────────
UPLOAD_CONFIG = {
    "max_file_size_mb": 5000,  # Sin limite practico (5GB)
    "allowed_extensions": [
        # ── Código fuente ──
        ".py", ".java", ".cpp", ".c", ".h", ".hpp", ".cs", ".rb", ".go", ".rs",
        ".php", ".swift", ".kt", ".scala", ".pl", ".pm", ".r",
        ".js", ".ts", ".jsx", ".tsx", ".html", ".htm", ".css", ".scss", ".sass",
        ".less", ".vue", ".svelte",
        ".luau", ".lua", ".glsl", ".shader", ".hlsl",
        ".sh", ".bat", ".ps1", ".psm1", ".dockerfile", ".tf",
        # ── Datos estructurados ──
        ".csv", ".tsv",
        ".json", ".jsonl",
        ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env",
        ".sql", ".db", ".sqlite",
        # ── Texto plano / Docs ──
        ".txt", ".log", ".md", ".rst",
        # ── Office / Documentos ──
        ".pdf",
        ".docx", ".doc",
        ".xlsx", ".xls", ".xlsm", ".ods",
        ".pptx", ".ppt",
        ".odt", ".rtf", ".epub",
        # ── Imágenes (OCR + descripción) ──
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif",
        # ── Archivos comprimidos ──
        ".zip", ".tar", ".gz", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".rar", ".7z",
        # ── Notebooks / Otros ──
        ".ipynb",
    ],
    "temp_dir": "uploads",
}

# ─── ESTIMACION DE TOKENS ────────────────────────────────────────────────────
TOKEN_ESTIMATION = {
    "chars_per_token": 3.5,
    "max_tokens": 110000,
    "default_tokens": 8192,
}

# ─── RECOLECCION DE BASURA ────────────────────────────────────────────────────
GC_CONFIG = {
    "collect_after_upload": True,
    "collect_after_processing": True,
    "collect_interval_seconds": 300,
}

# ─── HTTP SESSION (para conexiones rapidas con Ollama) ──────────────────────
HTTP_CONFIG = {
    "max_connections": 32,      # Pool de conexiones reducido para Pascal
    "timeout": 300.0,           # 5 min timeout
}

# ═══════════════════════════════════════════════════════════════════════════
# RTX 3090 — PERFILES EXTENDIDOS (aprovechan los 24 GB VRAM)
# ═══════════════════════════════════════════════════════════════════════════
# Modelo          VRAM aprox   num_ctx recomendado
# arch7 (7B q4)   ~4.5 GB      32 768 tokens
# coda (32B q4)   ~18–20 GB    16 384 tokens  (cabe entero en VRAM)
# rebx3 (7B q4)   ~4.5 GB      32 768 tokens
# Ambos 7B + 32B no caben simultáneamente; el Semaphore serializa las llamadas.

RTX3090_PROFILES = {
    "arch7_fast":  {"num_ctx": 32768, "num_batch": 2048, "num_gpu": 99},
    "coda_full":   {"num_ctx": 16384, "num_batch": 512,  "num_gpu": 99},
    "rebx3_fast":  {"num_ctx": 32768, "num_batch": 2048, "num_gpu": 99},
    "intruder":    {"num_ctx": 8192,  "num_batch": 1024, "num_gpu": 99},
}