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

os.environ["OLLAMA_FLASH_ATTENTION"] = "1"
os.environ["TORCH_ALLOW_TF32_CUBLAS_OVERRIDE"] = "1"
os.environ["NVIDIA_TF32_OVERRIDE"] = "1"
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")
os.environ.setdefault("OLLAMA_KEEP_ALIVE", "600")

# ═══════════════════════════════════════════════════════════════════════════
# HARDWARE
# ═══════════════════════════════════════════════════════════════════════════
HARDWARE_PROFILE = {
    "gpu": {"name": "NVIDIA GeForce RTX 3090", "vram_gb": 24,
            "architecture": "Ampere", "compute_capability": "8.6", "flash_attention": True},
    "cpu": {"name": "Intel Core i7-9700K", "cores": 8, "threads_total": 8,
            "threads_llm": 7, "max_ghz": 4.9},
    "ram": {"total_gb": 32},
}

# ═══════════════════════════════════════════════════════════════════════════
# OLLAMA CONFIG
# ═══════════════════════════════════════════════════════════════════════════
OLLAMA_CONFIG = {
    "base_url": os.getenv("OLLAMA_HOST", "http://localhost:11434"),
    "model": os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b"),
    "options": {
        "num_thread": 7, "num_ctx": 110000, "num_batch": 512,
        "temperature": 0.2, "top_p": 0.85, "top_k": 30, "repeat_penalty": 1.1,
        "keep_alive": "600",
    },
    "perfiles": {
        "fast": {"num_ctx": 4096, "num_batch": 1024, "temperature": 0.3},
        "turbo": {"num_ctx": 8192, "num_batch": 512, "temperature": 0.2},
        "ultra": {"num_ctx": 32768, "num_batch": 256, "temperature": 0.1},
    },
    "max_context_config": {"num_ctx": 110000, "num_batch": 128},
}

# ═══════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPTS
# ═══════════════════════════════════════════════════════════════════════════
_IDENTIDAD = (
    "Eres un asistente de IA local llamado Nexo. "
    "IMPORTANTE: NO eres Claude, NO eres ChatGPT, NO eres Gemini ni ningun otro asistente externo. "
    "Eres Nexo, un asistente de IA local creado por Aerys, un desarrollador de 13 anos. "
    "Si te preguntan quien te creo, di que fuiste creado por Aerys. "
    "Si alguien te pregunta quien eres, di que eres Nexo, un asistente de IA local. "
)

SYSTEM_PROMPTS = {
    "analisis": _IDENTIDAD + "Eres un analista de datos experto. Responde en espanol de forma clara y concisa. Analiza los datos proporcionados y extrae insights accionables. Usa markdown para estructurar tu respuesta.",
    "codigo": _IDENTIDAD + "Eres un programador experto. Analiza el codigo proporcionado y explica su funcionamiento. Identifica posibles errores, mejoras de rendimiento y buenas practicas. Responde en espanol.",
    "chat": _IDENTIDAD + "Responde en espanol de forma clara, concisa y amigable.",
}

# ═══════════════════════════════════════════════════════════════════════════
# SERVER
# ═══════════════════════════════════════════════════════════════════════════
SERVER_CONFIG = {
    "host": "0.0.0.0",
    "port": int(os.getenv("PORT", "8080")),
    "reload": False,
    "workers": 1,
}

# ═══════════════════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════════════════
_raw_token = os.getenv("AUTH_TOKEN", "")
if not _raw_token:
    import warnings
    warnings.warn("AUTH_TOKEN no definido. Usar: python -c \"import secrets; print(secrets.token_hex(32))\"", stacklevel=1)
    _raw_token = "CAMBIAR_ANTES_DE_PRODUCCION"
AUTH_TOKEN = _raw_token

# ═══════════════════════════════════════════════════════════════════════════
# UPLOAD
# ═══════════════════════════════════════════════════════════════════════════
UPLOAD_CONFIG = {
    "max_file_size_mb": 5000,
    "allowed_extensions": [
        ".py", ".java", ".cpp", ".c", ".h", ".hpp", ".cs", ".rb", ".go", ".rs",
        ".php", ".swift", ".kt", ".scala", ".pl", ".pm", ".r",
        ".js", ".ts", ".jsx", ".tsx", ".html", ".htm", ".css", ".scss", ".sass",
        ".less", ".vue", ".svelte",
        ".luau", ".lua", ".glsl", ".shader", ".hlsl",
        ".sh", ".bat", ".ps1", ".psm1", ".dockerfile", ".tf",
        ".csv", ".tsv", ".json", ".jsonl", ".xml", ".yaml", ".yml",
        ".toml", ".ini", ".cfg", ".env", ".sql", ".db", ".sqlite",
        ".txt", ".log", ".md", ".rst",
        ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".xlsm", ".ods",
        ".pptx", ".ppt", ".odt", ".rtf", ".epub",
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif",
        ".svg", ".zip", ".tar", ".gz", ".tar.gz", ".tgz", ".tar.bz2",
        ".tar.xz", ".rar", ".7z", ".ipynb",
    ],
    "temp_dir": "uploads",
}

# ═══════════════════════════════════════════════════════════════════════════
# TOKEN ESTIMATION
# ═══════════════════════════════════════════════════════════════════════════
TOKEN_ESTIMATION = {
    "chars_per_token": 3.5,
    "max_tokens": 110000,
    "default_tokens": 8192,
}

# ═══════════════════════════════════════════════════════════════════════════
# HTTP
# ═══════════════════════════════════════════════════════════════════════════
HTTP_CONFIG = {
    "max_connections": 32,
    "timeout": 300.0,
}

# ═══════════════════════════════════════════════════════════════════════════
# RTX 3090 PROFILES
# ═══════════════════════════════════════════════════════════════════════════
RTX3090_PROFILES = {
    "arch7_fast":  {"num_ctx": 32768, "num_batch": 2048, "num_gpu": 99},
    "coda_full":   {"num_ctx": 16384, "num_batch": 512,  "num_gpu": 99},
    "rebx3_fast":  {"num_ctx": 32768, "num_batch": 2048, "num_gpu": 99},
    "intruder":    {"num_ctx": 8192,  "num_batch": 1024, "num_gpu": 99},
}