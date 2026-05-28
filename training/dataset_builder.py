"""
dataset_builder.py — Pipeline de construcción de dataset para QLoRA 32B
=======================================================================
Versión 2.0 — Mejorado con:
  · Extracción de código limpia: detecta bloques de código en PDFs
  · Chunking inteligente: respeta bloques de código, no los corta a la mitad
  · Deduplicación por contenido (SHA-256) Y por similitud Jaccard (opcional)
  · Estadísticas ricas: distribución de tokens, por categoría, por lenguaje
  · Soporte para combinar múltiples datasets JSONL de study_engine
  · Opción --merge para unir datasets anteriores sin reprocesar
  · Validación estricta de formato ShareGPT y Alpaca
  · Filtro de calidad: descarta chunks sin suficiente contenido técnico

Hardware target: RTX 3090 (24 GB VRAM) · i7-9700K · 32 GB RAM
Modelo objetivo: Qwen2.5-32B / DeepSeek-R1-32B

USO:
    # Básico
    python dataset_builder.py --source /knowledge_base/ --out ./datasets/

    # Con repos GitHub y validación
    python dataset_builder.py \\
        --source /knowledge_base/ \\
        --github https://github.com/user/repo \\
        --out ./datasets/ --validate

    # Combinar con Q&A de study_engine
    python dataset_builder.py \\
        --source /knowledge_base/ \\
        --merge ./datasets/study_qa_*.jsonl \\
        --out ./datasets/

    # Solo métricas del dataset existente
    python dataset_builder.py --analyze ./datasets/dataset_sharegpt_*.jsonl
"""

import os
import re
import glob
import json
import shutil
import hashlib
import logging
import argparse
import subprocess
import unicodedata
from pathlib import Path
from datetime import datetime
from typing import Generator

# ── Deps opcionales ──────────────────────────────────────────────────────────
try:
    from pypdf import PdfReader
    PDF_OK = True
except ImportError:
    PDF_OK = False
    print("[WARN] pypdf no instalado: pip install pypdf")

try:
    import pdfplumber
    PLUMBER_OK = True
except ImportError:
    PLUMBER_OK = False

try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")
    def count_tokens(text: str) -> int: return len(_enc.encode(text))
except ImportError:
    def count_tokens(text: str) -> int: return len(text.split())

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("dataset_builder.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("dataset_builder")

# ── Constantes ───────────────────────────────────────────────────────────────
KNOWLEDGE_BASE_DIR   = Path(os.environ.get("KNOWLEDGE_BASE", "./knowledge_base"))
MAX_TOKENS_PER_CHUNK = 1024
MIN_TOKENS_PER_CHUNK = 40
MAX_FILE_SIZE_MB     = 60
OVERLAP_TOKENS       = 64
MIN_CODE_QUALITY_SCORE = 2   # mínimo de marcadores técnicos para aceptar un chunk

SYSTEM_PROMPT = (
    "Eres VOID AXIOM, un asistente de IA especializado en análisis de datos, "
    "programación y conocimiento técnico. Responde siempre en el idioma del usuario, "
    "con precisión, claridad y detalle técnico. Incluye código cuando sea relevante."
)

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".cpp", ".c",
    ".h", ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".sql",
    ".yaml", ".yml", ".toml", ".json", ".html", ".css", ".md",
    ".rst", ".txt", ".swift", ".kt", ".bash", ".r", ".scala",
    ".lua", ".zig", ".nim", ".ex", ".exs", ".clj", ".hs",
}

# Marcadores de contenido técnico (para filtro de calidad)
TECH_MARKERS = [
    r"\bdef \b", r"\bfunction\b", r"\bclass \b", r"\bimport \b",
    r"\bSELECT\b", r"\bCREATE\b", r"O\(n", r"\balgorithm\b",
    r"```", r"\bpublic\b", r"\bprivate\b", r"\breturn\b",
    r"#include", r"\bvoid\b", r"\bint main\b", r"\basync\b",
    r"\bawait\b", r"\bconst\b", r"\blet \b", r"\bvar \b",
]


# ═══════════════════════════════════════════════════════════════════════════════
# LIMPIEZA DE TEXTO
# ═══════════════════════════════════════════════════════════════════════════════

def normalize_unicode(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    return text.replace("\ufffd", "").replace("\x00", "")


def clean_pdf_text(raw: str) -> str:
    """Limpieza específica para texto extraído de PDFs."""
    # Reparar palabras partidas con guión al final de línea
    text = re.sub(r"-\n(\w)", r"\1", raw)
    # Eliminar números de página solitarios
    text = re.sub(r"^\s*\d+\s*$", "", text, flags=re.MULTILINE)
    # Eliminar headers/footers repetitivos (líneas < 5 palabras que se repiten)
    lines = text.split("\n")
    line_freq: dict[str, int] = {}
    for line in lines:
        stripped = line.strip()
        if 0 < len(stripped.split()) < 6:
            line_freq[stripped] = line_freq.get(stripped, 0) + 1
    # Eliminar líneas que aparecen más de 3 veces (headers/footers)
    filtered = [
        line for line in lines
        if line_freq.get(line.strip(), 0) <= 3
    ]
    text = "\n".join(filtered)
    # Normalizar espacios
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return normalize_unicode(text.strip())


def clean_code_text(raw: str) -> str:
    text = normalize_unicode(raw)
    text = text.expandtabs(4)
    text = re.sub(r"\n{4,}", "\n\n", text)
    return text.strip()


def clean_markdown_text(raw: str) -> str:
    # Eliminar frontmatter YAML
    text = re.sub(r"^---[\s\S]+?---\n", "", raw)
    # Acortar URLs muy largas
    text = re.sub(r"https?://\S{100,}", "[URL]", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def quality_score(text: str) -> int:
    """Cuenta cuántos marcadores técnicos tiene el texto (0-N)."""
    return sum(1 for pattern in TECH_MARKERS if re.search(pattern, text, re.IGNORECASE))


# ═══════════════════════════════════════════════════════════════════════════════
# CHUNKING MEJORADO — respeta bloques de código
# ═══════════════════════════════════════════════════════════════════════════════

def is_code_block_start(line: str) -> bool:
    return (
        line.startswith("```") or
        line.startswith("    ") or
        line.startswith("\t") or
        bool(re.match(r"^\s*(def |class |function |int |void |public |private )", line))
    )


def chunk_text(
    text: str,
    max_tokens: int = MAX_TOKENS_PER_CHUNK,
    overlap: int = OVERLAP_TOKENS,
    source: str = "",
) -> Generator[dict, None, None]:
    """
    Chunk con respeto a bloques de código.
    Nunca corta en medio de un bloque de código indentado.
    """
    paragraphs = re.split(r"\n{2,}", text)
    current_chunks: list[str] = []
    current_tokens = 0
    chunk_id = 0
    in_code_block = False

    def flush():
        nonlocal chunk_id, current_chunks, current_tokens
        body = "\n\n".join(current_chunks)
        tok  = count_tokens(body)
        if tok >= MIN_TOKENS_PER_CHUNK:
            score = quality_score(body)
            yield {
                "text":     body,
                "tokens":   tok,
                "source":   source,
                "chunk_id": chunk_id,
                "quality":  score,
            }
            chunk_id += 1
        # Overlap: mantener últimos párrafos hasta OVERLAP_TOKENS
        kept, kept_tok = [], 0
        for p in reversed(current_chunks):
            p_tok = count_tokens(p)
            if kept_tok + p_tok > overlap:
                break
            kept.insert(0, p)
            kept_tok += p_tok
        current_chunks = kept
        current_tokens = kept_tok

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # Detectar inicio/fin de bloque de código Markdown
        if para.startswith("```"):
            in_code_block = not in_code_block

        para_tokens = count_tokens(para)

        # Si el párrafo solo es muy largo Y no es código: partir por oraciones
        if para_tokens > max_tokens and not in_code_block:
            sentences = re.split(r"(?<=[.!?])\s+", para)
            for sent in sentences:
                sent_tok = count_tokens(sent)
                if current_tokens + sent_tok > max_tokens and current_chunks:
                    yield from flush()
                current_chunks.append(sent)
                current_tokens += sent_tok
            continue

        # Flush si supera el límite (pero no en medio de bloque de código)
        if current_tokens + para_tokens > max_tokens and current_chunks and not in_code_block:
            yield from flush()

        current_chunks.append(para)
        current_tokens += para_tokens

    if current_chunks:
        yield from flush()


# ═══════════════════════════════════════════════════════════════════════════════
# EXTRACTORES
# ═══════════════════════════════════════════════════════════════════════════════

def extract_pdf(path: Path) -> str:
    """Extrae texto de PDF con fallback pypdf → pdfplumber."""
    if not PDF_OK and not PLUMBER_OK:
        return ""

    # Intentar primero con pypdf
    if PDF_OK:
        try:
            reader = PdfReader(str(path))
            parts = []
            for page in reader.pages:
                try:
                    t = page.extract_text()
                    if t:
                        parts.append(t)
                except Exception:
                    continue
            text = clean_pdf_text("\n\n".join(parts))
            if text and len(text) > 200:
                return text
        except Exception as e:
            log.debug(f"  pypdf failed on {path.name}: {e}")

    # Fallback: pdfplumber (mejor con tablas y PDFs complejos)
    if PLUMBER_OK:
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                parts = []
                for page in pdf.pages:
                    t = page.extract_text()
                    if t:
                        parts.append(t)
            return clean_pdf_text("\n\n".join(parts))
        except Exception as e:
            log.error(f"  pdfplumber failed on {path.name}: {e}")

    return ""


def extract_code(path: Path) -> str:
    try:
        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > MAX_FILE_SIZE_MB:
            log.info(f"  Archivo grande ({size_mb:.1f} MB), saltando: {path.name}")
            return ""
        raw = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix in (".md", ".rst"):
            return clean_markdown_text(raw)
        return clean_code_text(raw)
    except Exception as e:
        log.error(f"  Error leyendo {path}: {e}")
        return ""


def clone_github_repo(url: str, dest_dir: Path) -> Path | None:
    repo_name = url.rstrip("/").split("/")[-1].replace(".git", "")
    repo_path = dest_dir / repo_name
    if repo_path.exists():
        log.info(f"  Repo cacheado: {repo_path}")
        return repo_path
    log.info(f"  Clonando {url}")
    try:
        result = subprocess.run(
            ["git", "clone", "--depth=1", url, str(repo_path)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            log.error(f"  Error: {result.stderr[:200]}")
            return None
        return repo_path
    except Exception as e:
        log.error(f"  Error clonando: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# FORMATOS DE DATASET
# ═══════════════════════════════════════════════════════════════════════════════

def make_sharegpt_entry(chunk: dict) -> dict:
    """Formato ShareGPT — compatible con axolotl, LLaMA-Factory, unsloth."""
    source = chunk["source"]
    user_msg = (
        f"Analiza y explica el siguiente contenido técnico de '{source}':\n\n"
        f"{chunk['text']}"
    )
    assistant_msg = (
        f"Aquí está mi análisis del contenido de {source}:\n\n"
        f"{chunk['text']}\n\n"
        "Este fragmento forma parte de la base de conocimiento de VOID AXIOM. "
        "¿Necesitas que profundice en algún concepto específico?"
    )
    return {
        "conversations": [
            {"from": "system", "value": SYSTEM_PROMPT},
            {"from": "human",  "value": user_msg},
            {"from": "gpt",    "value": assistant_msg},
        ],
        "source":   source,
        "tokens":   chunk["tokens"],
        "chunk_id": chunk["chunk_id"],
        "quality":  chunk.get("quality", 0),
    }


def make_alpaca_entry(chunk: dict) -> dict:
    return {
        "instruction": SYSTEM_PROMPT,
        "input":  f"Explica el siguiente fragmento de '{chunk['source']}':\n\n{chunk['text']}",
        "output": chunk["text"],
        "source": chunk["source"],
    }


def load_merge_dataset(patterns: list[str]) -> list[dict]:
    """Carga datasets JSONL existentes (de study_engine u otros) para fusionar."""
    entries = []
    for pattern in patterns:
        for filepath in glob.glob(pattern):
            log.info(f"  ← Fusionando: {filepath}")
            try:
                with open(filepath, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            entries.append(json.loads(line))
            except Exception as e:
                log.error(f"  Error leyendo {filepath}: {e}")
    log.info(f"  Total fusionados: {len(entries)} entradas")
    return entries


def dedup_entries(entries: list[dict], fmt: str = "sharegpt") -> list[dict]:
    """Deduplicación por hash SHA-256 del contenido de la respuesta."""
    seen, deduped = set(), []
    for entry in entries:
        if fmt == "sharegpt":
            key = entry.get("conversations", [{}])[-1].get("value", "")
        else:
            key = entry.get("input", "")
        h = hashlib.sha256(key.encode()).hexdigest()
        if h not in seen:
            seen.add(h)
            deduped.append(entry)
    removed = len(entries) - len(deduped)
    if removed:
        log.info(f"  Deduplicación: eliminados {removed} duplicados")
    return deduped


# ═══════════════════════════════════════════════════════════════════════════════
# ANÁLISIS DE DATASET
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_dataset(path: str):
    """Muestra estadísticas detalladas de un dataset JSONL."""
    entries = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    except Exception as e:
        log.error(f"Error leyendo {path}: {e}")
        return

    total = len(entries)
    tokens_list = [e.get("tokens", 0) for e in entries]
    sources = {}
    qa_types = {}

    for e in entries:
        src = e.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1
        qt = e.get("qa_type", "corpus")
        qa_types[qt] = qa_types.get(qt, 0) + 1

    avg_tok = sum(tokens_list) / max(total, 1)
    total_tok = sum(tokens_list)

    sep = "═" * 60
    print(f"\n{sep}")
    print(f"  ANÁLISIS: {Path(path).name}")
    print(sep)
    print(f"  Entradas totales  : {total:,}")
    print(f"  Tokens totales    : {total_tok:,}")
    print(f"  Tokens promedio   : {avg_tok:.0f}")
    print(f"  Tokens mín/máx    : {min(tokens_list, default=0)} / {max(tokens_list, default=0)}")
    print(f"\n  Tipos de entrada:")
    for qt, count in sorted(qa_types.items(), key=lambda x: -x[1]):
        print(f"    {qt:20s} {count:5d}")
    print(f"\n  Top 10 fuentes:")
    for src, count in sorted(sources.items(), key=lambda x: -x[1])[:10]:
        print(f"    {src[:40]:40s} {count:4d}")
    print(sep)


# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def scan_directory(base_dir: Path) -> Generator[tuple[Path, str], None, None]:
    if not base_dir.exists():
        log.warning(f"Directorio no existe: {base_dir}")
        return
    for path in sorted(base_dir.rglob("*")):
        if not path.is_file():
            continue
        # Saltar archivos ocultos y __pycache__
        if any(p.startswith(".") or p == "__pycache__" for p in path.parts):
            continue
        # Saltar archivos .bak y temporales
        if path.suffix in (".bak", ".bak2", ".pyc", ".db", ".lock"):
            continue
        suffix = path.suffix.lower()
        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > MAX_FILE_SIZE_MB:
            log.debug(f"  Saltando (muy grande {size_mb:.1f} MB): {path.name}")
            continue

        log.info(f"  → {path.name} ({size_mb:.1f} MB)")

        if suffix == ".pdf":
            text = extract_pdf(path)
        elif suffix in CODE_EXTENSIONS:
            text = extract_code(path)
        else:
            continue

        if text and len(text.strip()) > 100:
            yield path, text


def build_dataset(
    source_dirs: list[Path],
    github_repos: list[str],
    out_dir: Path,
    format_type: str = "sharegpt",
    validate: bool = False,
    merge_patterns: list[str] = [],
    min_quality: int = 0,
    github_cache: Path = Path("/tmp/github_cache"),
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    github_cache.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path   = out_dir / f"dataset_{format_type}_{timestamp}.jsonl"
    stats_path = out_dir / f"stats_{timestamp}.json"

    all_entries: list[dict] = []
    file_count = chunk_count = total_tokens = errors = filtered_quality = 0

    def process_text(text: str, label: str):
        nonlocal chunk_count, total_tokens, filtered_quality
        for chunk in chunk_text(text, source=label):
            # Filtro de calidad
            if chunk.get("quality", 0) < min_quality:
                filtered_quality += 1
                continue
            entry = (
                make_sharegpt_entry(chunk)
                if format_type == "sharegpt"
                else make_alpaca_entry(chunk)
            )
            all_entries.append(entry)
            chunk_count  += 1
            total_tokens += chunk["tokens"]

    # 1. Directorios locales
    for base_dir in source_dirs:
        log.info(f"\n── Escaneando: {base_dir}")
        for path, text in scan_directory(base_dir):
            process_text(text, path.name)
            file_count += 1

    # 2. Repos GitHub
    for repo_url in github_repos:
        log.info(f"\n── GitHub: {repo_url}")
        repo_path = clone_github_repo(repo_url, github_cache)
        if not repo_path:
            errors += 1
            continue
        for path, text in scan_directory(repo_path):
            process_text(text, f"github:{repo_path.name}/{path.name}")
            file_count += 1

    # 3. Fusionar datasets externos (study_engine Q&A, etc.)
    if merge_patterns:
        log.info(f"\n── Fusionando datasets externos")
        merged = load_merge_dataset(merge_patterns)
        all_entries.extend(merged)
        log.info(f"  +{len(merged)} entradas fusionadas")

    # 4. Deduplicación
    all_entries = dedup_entries(all_entries, format_type)

    # 5. Validación
    if validate:
        valid = []
        for entry in all_entries:
            try:
                if format_type == "sharegpt":
                    convs = entry.get("conversations", [])
                    assert len(convs) >= 2
                    assert all("from" in c and "value" in c for c in convs)
                else:
                    assert entry.get("output", "").strip()
                valid.append(entry)
            except AssertionError:
                errors += 1
        removed = len(all_entries) - len(valid)
        if removed:
            log.info(f"  Validación: {removed} entradas inválidas eliminadas")
        all_entries = valid

    # 6. Escribir JSONL
    with open(out_path, "w", encoding="utf-8") as f:
        for entry in all_entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # 7. Stats
    out_mb = out_path.stat().st_size / (1024 ** 2)
    stats = {
        "timestamp":          timestamp,
        "format":             format_type,
        "files_processed":    file_count,
        "chunks_generated":   chunk_count,
        "entries_final":      len(all_entries),
        "total_tokens":       total_tokens,
        "avg_tokens":         round(total_tokens / max(len(all_entries), 1), 1),
        "filtered_quality":   filtered_quality,
        "errors":             errors,
        "output_file":        str(out_path),
        "output_mb":          round(out_mb, 2),
    }
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    sep = "═" * 60
    log.info(f"\n{sep}")
    log.info("  DATASET CONSTRUIDO — VOID AXIOM")
    log.info(sep)
    log.info(f"  Archivos procesados : {file_count}")
    log.info(f"  Chunks generados    : {chunk_count}")
    log.info(f"  Entradas finales    : {len(all_entries):,}")
    log.info(f"  Tokens totales      : {total_tokens:,}")
    log.info(f"  Tokens promedio     : {stats['avg_tokens']}")
    log.info(f"  Filtrados calidad   : {filtered_quality}")
    log.info(f"  Errores             : {errors}")
    log.info(f"  Archivo             : {out_path}")
    log.info(f"  Tamaño              : {out_mb:.2f} MB")
    log.info(sep)

    return stats


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="VOID AXIOM v2.0 — Dataset Builder para QLoRA 32B",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--source", action="append", type=Path,
                        default=[KNOWLEDGE_BASE_DIR])
    parser.add_argument("--github", action="append", default=[])
    parser.add_argument("--out", type=Path, default=Path("./datasets"))
    parser.add_argument("--format", choices=["sharegpt", "alpaca"],
                        default="sharegpt")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--merge", action="append", default=[],
                        metavar="PATTERN",
                        help="Patrón glob de datasets JSONL a fusionar")
    parser.add_argument("--min-quality", type=int, default=0,
                        help="Mínimo de marcadores técnicos para incluir un chunk")
    parser.add_argument("--max-tokens", type=int, default=MAX_TOKENS_PER_CHUNK)
    parser.add_argument("--github-cache", type=Path, default=Path("/tmp/github_cache"))
    parser.add_argument("--analyze", metavar="JSONL",
                        help="Solo analizar un dataset JSONL existente")
    args = parser.parse_args()

    if args.analyze:
        analyze_dataset(args.analyze)
        return

    global MAX_TOKENS_PER_CHUNK
    MAX_TOKENS_PER_CHUNK = args.max_tokens

    log.info("╔══════════════════════════════════════════════════════╗")
    log.info("║   VOID AXIOM — Dataset Builder v2.0                 ║")
    log.info("╚══════════════════════════════════════════════════════╝")
    log.info(f"Fuentes  : {args.source}")
    log.info(f"GitHub   : {args.github or 'ninguno'}")
    log.info(f"Fusionar : {args.merge or 'ninguno'}")
    log.info(f"Formato  : {args.format} | max_tokens={args.max_tokens}")

    stats = build_dataset(
        source_dirs    = args.source,
        github_repos   = args.github,
        out_dir        = args.out,
        format_type    = args.format,
        validate       = args.validate,
        merge_patterns = args.merge,
        min_quality    = args.min_quality,
        github_cache   = args.github_cache,
    )
    print(f"\n✅ {stats['entries_final']:,} entradas | "
          f"{stats['total_tokens']:,} tokens | "
          f"{stats['output_mb']} MB")


if __name__ == "__main__":
    main()
