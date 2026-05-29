"""
study_engine.py — Motor de estudio v3.0 turbo: PDFs → pares Q&A para QLoRA
===========================================================================
Lee PDFs/código de /knowledge_base/ y genera pares pregunta/respuesta de alta
calidad usando Ollama local. Concurrencia real, checkpoint/resume, reintentos.

Uso:
    python study_engine.py                              # todo /knowledge_base/
    python study_engine.py --model void-coda:latest      # modelo específico
    python study_engine.py --limit 50                    # máx 50 archivos
    python study_engine.py --pdf libro.pdf               # un PDF concreto
    python study_engine.py --dry-run                     # solo listar
    python study_engine.py --workers 4                   # 4 chunks en paralelo
"""

import os, re, json, time, random, hashlib, logging, argparse
import concurrent.futures
from pathlib import Path
from datetime import datetime

# ── Deps opcionales ──────────────────────────────────────────────────────────
try:
    from pypdf import PdfReader
    PDF_OK = True
except ImportError:
    PDF_OK = False

try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")
    def count_tokens(t: str) -> int: return len(_enc.encode(t))
except ImportError:
    def count_tokens(t: str) -> int: return len(t.split())

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("study_engine.log", encoding="utf-8")],
)
log = logging.getLogger("study_engine")

# ── Config ───────────────────────────────────────────────────────────────────
KNOWLEDGE_BASE_DIR = Path(os.environ.get("KNOWLEDGE_BASE", "./knowledge_base"))
OLLAMA_BASE_URL    = os.environ.get("VOID_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL      = os.environ.get("VOID_STUDY_MODEL", "qwen2.5-coder:14b")
MAX_CHUNK_TOKENS   = 800
MIN_CHUNK_TOKENS   = 40
OVERLAP_SENTENCES  = 2
QA_PER_CHUNK       = 3
TIMEOUT_INFERENCE  = 300   # 5 min (GTX 1080 Ti es lenta con 14B)
TEMPERATURE        = 0.4
MAX_RETRIES        = 3     # reintentos por inferencia fallida
WORKERS_DEFAULT    = 2     # chunks en paralelo

SYSTEM_PROMPT_STUDY = """Eres VOID AXIOM, un experto en programación y ciencias de la computación.
Tu tarea es estudiar fragmentos de libros y papers técnicos y generar preguntas y respuestas
de alta calidad para el entrenamiento de IA en programación.

REGLAS:
- Genera preguntas específicas y técnicas, no genéricas.
- Las respuestas deben incluir código cuando sea posible.
- Usa el lenguaje o tecnología del fragmento (Python, C++, SQL, etc.)
- Si el fragmento muestra código, explica qué hace y cómo mejorarlo.
- Responde en el mismo idioma del fragmento (español o inglés).
- Nunca inventes información que no esté en el fragmento."""

QA_PROMPT_TEMPLATE = """Dado este fragmento de {source}:

---
{chunk}
---

Genera exactamente {n} pares de pregunta/respuesta técnica en formato JSON.
Cada par debe tener:
- "q": pregunta técnica específica sobre el contenido
- "a": respuesta completa y técnica (incluye código si aplica)
- "type": uno de ["explanation", "implementation", "comparison", "bugfix", "exercise"]

Responde SOLO con un array JSON válido, sin texto adicional:
[
  {{"q": "...", "a": "...", "type": "..."}},
  ...
]"""

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".cpp", ".c", ".h",
    ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".bash",
    ".sql", ".swift", ".kt", ".kts", ".r", ".R", ".scala", ".sc",
    ".lua", ".luau", ".zig", ".nim", ".ex", ".exs", ".clj", ".cljs",
    ".hs", ".erl", ".hrl", ".ml", ".mli", ".vue", ".svelte", ".astro",
    ".pl", ".pm", ".t", ".ps1", ".psm1", ".bat", ".cmd", ".dockerfile",
    ".yml", ".yaml", ".toml", ".json", ".html", ".htm", ".css", ".scss",
    ".sass", ".less", ".md", ".rst", ".tex", ".makefile",
}


# ═══════════════════════════════════════════════════════════════════════════════
# EXTRACCIÓN
# ═══════════════════════════════════════════════════════════════════════════════

def extract_pdf_text(path: Path) -> str:
    if not PDF_OK:
        log.warning("pypdf no instalado: pip install pypdf")
        return ""
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
        raw = "\n\n".join(parts)
        raw = re.sub(r"-\n(\w)", r"\1", raw)
        raw = re.sub(r"^\s*\d+\s*$", "", raw, flags=re.MULTILINE)
        raw = re.sub(r" {2,}", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()
    except Exception as e:
        log.error(f"Error leyendo PDF {path}: {e}")
        return ""

def extract_code_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        log.error(f"Error leyendo {path}: {e}")
        return ""

def detect_language(text: str, path: Path) -> str:
    ext_map = {
        ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
        ".java": "Java", ".cpp": "C++", ".c": "C", ".cs": "C#",
        ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP",
        ".sh": "Bash", ".sql": "SQL",
    }
    if path.suffix in ext_map:
        return ext_map[path.suffix]
    markers = {
        "Python": ["def ", "import ", "print(", "elif ", "self."],
        "JavaScript": ["function ", "const ", "let ", "=>", "console.log"],
        "Java": ["public class", "void ", "System.out", "import java"],
        "C++": ["#include", "std::", "cout <<", "int main()"],
        "SQL": ["SELECT ", "FROM ", "WHERE ", "CREATE TABLE"],
        "Bash": ["#!/bin/bash", "echo ", "if [", "fi\n"],
    }
    sample = text[:3000]
    scores = {lang: sum(1 for m in marks if m in sample) for lang, marks in markers.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "programming"


# ═══════════════════════════════════════════════════════════════════════════════
# CHUNKING
# ═══════════════════════════════════════════════════════════════════════════════

def smart_chunks(text: str, source: str) -> list[dict]:
    chunks = []
    paragraphs = re.split(r"\n{2,}", text)
    current: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        is_code_block = (
            para.startswith("    ") or
            para.startswith("\t") or
            bool(re.match(r"^(def |class |function |int |void |public )", para))
        )
        para_tok = count_tokens(para)

        if para_tok > MAX_CHUNK_TOKENS and not is_code_block:
            sentences = re.split(r"(?<=[.!?])\s+", para)
            for sent in sentences:
                s_tok = count_tokens(sent)
                if current_tokens + s_tok > MAX_CHUNK_TOKENS and current:
                    body = "\n\n".join(current)
                    if count_tokens(body) >= MIN_CHUNK_TOKENS:
                        chunks.append({"text": body, "source": source})
                    current = current[-OVERLAP_SENTENCES:]
                    current_tokens = count_tokens("\n\n".join(current))
                current.append(sent)
                current_tokens += s_tok
            continue

        if current_tokens + para_tok > MAX_CHUNK_TOKENS and current:
            body = "\n\n".join(current)
            if count_tokens(body) >= MIN_CHUNK_TOKENS:
                chunks.append({"text": body, "source": source})
            current = current[-OVERLAP_SENTENCES:]
            current_tokens = count_tokens("\n\n".join(current))

        current.append(para)
        current_tokens += para_tok

    if current:
        body = "\n\n".join(current)
        if count_tokens(body) >= MIN_CHUNK_TOKENS:
            chunks.append({"text": body, "source": source})
    return chunks


# ═══════════════════════════════════════════════════════════════════════════════
# INFERENCIA MEJORADA (timeout socket + reintentos)
# ═══════════════════════════════════════════════════════════════════════════════

def _ollama_infer(model: str, prompt: str, system: str, timeout: int) -> str | None:
    """Una llamada a Ollama con timeout explícito."""
    import http.client
    from urllib.parse import urlparse

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {"temperature": TEMPERATURE, "num_ctx": 4096},
    })

    parsed = urlparse(OLLAMA_BASE_URL)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 11434

    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("POST", "/api/chat", body=payload, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()
        return data.get("message", {}).get("content", "")
    except Exception as e:
        log.debug(f"  Ollama call failed: {e}")
        return None


def ollama_generate(model: str, prompt: str, system: str) -> str | None:
    """Llama a Ollama con reintentos automáticos y backoff."""
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        result = _ollama_infer(model, prompt, system, TIMEOUT_INFERENCE)
        if result is not None:
            return result

        if attempt < MAX_RETRIES:
            wait = attempt * 5  # backoff: 5s, 10s, 15s
            log.warning(f"  ⚠ Intento {attempt}/{MAX_RETRIES} falló, reintento en {wait}s...")
            time.sleep(wait)
        else:
            last_err = f"falló tras {MAX_RETRIES} intentos"
    log.error(f"  ✗ Inferencia fallida: {last_err}")
    return None


def parse_qa_response(text: str) -> list[dict]:
    if not text:
        return []
    json_match = re.search(r"\[[\s\S]*\]", text)
    if not json_match:
        return []
    try:
        items = json.loads(json_match.group())
        valid = []
        for item in items:
            if isinstance(item, dict) and "q" in item and "a" in item:
                valid.append({
                    "q":    str(item["q"]).strip(),
                    "a":    str(item["a"]).strip(),
                    "type": str(item.get("type", "explanation")),
                })
        return valid
    except json.JSONDecodeError:
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# FORMATO DATASET
# ═══════════════════════════════════════════════════════════════════════════════

SYSTEM_VOID = (
    "Eres VOID AXIOM, un asistente de IA especializado en programación "
    "y ciencias de la computación. Responde con precisión técnica, "
    "incluye código cuando sea útil, y explica el razonamiento paso a paso."
)

def qa_to_sharegpt(qa: dict, source: str, language: str) -> dict:
    return {
        "conversations": [
            {"from": "system", "value": SYSTEM_VOID},
            {"from": "human",  "value": qa["q"]},
            {"from": "gpt",    "value": qa["a"]},
        ],
        "source": source,
        "language": language,
        "qa_type": qa.get("type", "explanation"),
        "generated_by": "study_engine_v3",
    }

def entry_hash(entry: dict) -> str:
    return hashlib.sha256(entry["conversations"][-1]["value"].encode()).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════════
# CHECKPOINT / RESUME
# ═══════════════════════════════════════════════════════════════════════════════

CHECKPOINT_DIR = Path("./datasets/.checkpoints")

def save_checkpoint(checkpoint_path: Path, done_files: list[Path], seen_hashes: set,
                    stats: dict, entries: list[dict]):
    """Guarda checkpoint para poder reanudar."""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "done_files": [str(p) for p in done_files],
        "seen_hashes": list(seen_hashes),
        "stats": stats,
        "entries": entries,
        "timestamp": datetime.now().isoformat(),
    }
    with open(checkpoint_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def load_checkpoint(checkpoint_path: Path, base_dirs: list[Path]):
    """Carga checkpoint. Retorna (done_files_set, seen_hashes, stats, entries) o None."""
    if not checkpoint_path.exists():
        return None
    try:
        with open(checkpoint_path, encoding="utf-8") as f:
            data = json.load(f)

        # Convertir rutas guardadas a objetos Path
        done_files = {Path(p) for p in data.get("done_files", [])}
        seen_hashes = set(data.get("seen_hashes", []))
        stats = data.get("stats", {})
        entries = data.get("entries", [])

        log.info(f"  ♻ Checkpoint cargado: {len(done_files)} archivos ya procesados, "
                 f"{len(entries)} Q&As generados")
        return done_files, seen_hashes, stats, entries
    except Exception as e:
        log.warning(f"  ⚠ No se pudo cargar checkpoint: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

class StudyEngine:
    def __init__(self, model: str, out_dir: Path, dry_run: bool = False,
                 workers: int = WORKERS_DEFAULT):
        self.model   = model
        self.out_dir = out_dir
        self.dry_run = dry_run
        self.workers = workers
        self.seen_hashes: set[str] = set()
        self.stats = {
            "files_processed": 0, "chunks_total": 0,
            "qa_generated": 0, "qa_skipped_dup": 0, "inference_errors": 0,
        }
        self._entries: list[dict] = []
        out_dir.mkdir(parents=True, exist_ok=True)
        self._check_ollama()

    def _check_ollama(self):
        if self.dry_run:
            return
        try:
            import urllib.request
            req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags")
            with urllib.request.urlopen(req, timeout=5):
                log.info(f"  ✓ Ollama accesible en {OLLAMA_BASE_URL}")
        except Exception:
            log.warning(f"  ⚠ Ollama no accesible en {OLLAMA_BASE_URL}")

    def _process_single_chunk(self, chunk: dict, language: str) -> list[dict]:
        """Procesa un chunk (independiente, para paralelizar)."""
        prompt = QA_PROMPT_TEMPLATE.format(
            source=chunk["source"], chunk=chunk["text"], n=QA_PER_CHUNK,
        )
        response = ollama_generate(self.model, prompt, SYSTEM_PROMPT_STUDY)
        if response is None:
            return None  # señal de error

        qas = parse_qa_response(response)
        entries = []
        for qa in qas:
            entry = qa_to_sharegpt(qa, chunk["source"], language)
            h = entry_hash(entry)
            if h not in self.seen_hashes:  # chequeo rápido fuera del lock
                entries.append((h, entry))
        return entries

    def process_file(self, path: Path) -> list[dict]:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            text = extract_pdf_text(path)
        elif suffix in CODE_EXTENSIONS:
            text = extract_code_text(path)
        else:
            return []

        if not text or len(text.strip()) < 100:
            return []

        language = detect_language(text, path)
        source = path.name
        chunks = smart_chunks(text, source)

        log.info(f"  📄 {path.name} — {len(chunks)} chunks ({language})")
        self.stats["files_processed"] += 1
        self.stats["chunks_total"] += len(chunks)

        if self.dry_run:
            for i, ch in enumerate(chunks[:3]):
                log.info(f"    [chunk {i}] {ch['text'][:120]}…")
            return []

        # Procesar TODOS los chunks en paralelo (ya no samplear)
        entries_out: list[dict] = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {executor.submit(self._process_single_chunk, ch, language): ch for ch in chunks}

            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result is None:
                    self.stats["inference_errors"] += 1
                    continue

                for h, entry in result:
                    if h in self.seen_hashes:
                        self.stats["qa_skipped_dup"] += 1
                        continue
                    self.seen_hashes.add(h)
                    entries_out.append(entry)
                    self.stats["qa_generated"] += 1

        return entries_out

    def run(self, source_dirs: list[Path], specific_pdf: Path | None = None,
            limit: int = 0, resume: bool = False):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = self.out_dir / f"study_qa_{timestamp}.jsonl"
        ckpt_path = CHECKPOINT_DIR / f"study_ckpt_{timestamp}.json"

        # ── Reunir archivos ──────────────────────────────────────────────────
        if specific_pdf:
            files = [specific_pdf]
        else:
            files = []
            for d in source_dirs:
                if not d.exists():
                    log.warning(f"  Directorio no existe: {d}")
                    continue
                for ext in ["*.pdf"] + [f"*{e}" for e in CODE_EXTENSIONS]:
                    files.extend(d.rglob(ext))

        files = sorted(files, key=lambda p: p.stat().st_size)
        log.info(f"  Archivos totales encontrados: {len(files)}")

        # ── Resume? ──────────────────────────────────────────────────────────
        done_files: set[Path] = set()
        if resume:
            ckpt = load_checkpoint(ckpt_path, source_dirs)
            if ckpt:
                done_files, self.seen_hashes, self.stats, self._entries = ckpt
                log.info(f"  Reanudando: {len(done_files)}/{len(files)} archivos ya procesados")

        # ── Filtrar ──────────────────────────────────────────────────────────
        remaining = [f for f in files if f not in done_files]
        if limit > 0:
            remaining = remaining[:limit]
            # Si hay resume, sumar los ya hechos para mostrar progreso real
            total = len(done_files) + len(remaining)
        else:
            total = len(files)

        log.info(f"\n  Archivos a procesar: {len(remaining)} ({total} total con checkpoint)")
        log.info(f"  Modelo Ollama      : {self.model}")
        log.info(f"  Q&A por chunk      : {QA_PER_CHUNK}")
        log.info(f"  Workers paralelos   : {self.workers}")

        done_count = len(done_files)
        for i, f in enumerate(remaining, 1):
            idx = done_count + i
            log.info(f"\n[{idx}/{total}] Procesando: {f.name} ({f.stat().st_size // 1024} KB)")
            entries = self.process_file(f)
            self._entries.extend(entries)
            done_files.add(f)

            # Checkpoint cada 3 archivos
            if i % 3 == 0 and self._entries and not self.dry_run:
                save_checkpoint(ckpt_path, done_files, self.seen_hashes,
                                self.stats, self._entries)
                self._flush(out_path, self._entries)
                log.info(f"  💾 Checkpoint + flush: {len(self._entries)} Q&As guardados")

        # Guardar final
        if self._entries and not self.dry_run:
            save_checkpoint(ckpt_path, done_files, self.seen_hashes,
                            self.stats, self._entries)
            self._flush(out_path, self._entries)

        # Limpiar checkpoint si todo OK
        if ckpt_path.exists() and not self.dry_run:
            try:
                ckpt_path.unlink()
                log.info("  🧹 Checkpoint eliminado (proceso completado)")
            except Exception:
                pass

        self._print_stats(out_path)

    def _flush(self, path: Path, entries: list[dict]):
        with open(path, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _print_stats(self, out_path: Path):
        sep = "═" * 56
        elapsed = "N/A"  # podríamos trackear tiempo real
        log.info(f"\n{sep}")
        log.info("  ESTUDIO COMPLETADO — VOID AXIOM v3.0")
        log.info(sep)
        log.info(f"  Archivos procesados : {self.stats['files_processed']}")
        log.info(f"  Chunks analizados   : {self.stats['chunks_total']}")
        log.info(f"  Q&A generados       : {self.stats['qa_generated']}")
        log.info(f"  Duplicados omitidos : {self.stats['qa_skipped_dup']}")
        log.info(f"  Errores inferencia  : {self.stats['inference_errors']}")
        if not self.dry_run and self.stats['qa_generated'] > 0:
            log.info(f"  Dataset guardado    : {out_path}")
            size_kb = out_path.stat().st_size / 1024
            log.info(f"  Tamaño             : {size_kb:.1f} KB")
            avg = self.stats['qa_generated'] / max(self.stats['files_processed'], 1)
            log.info(f"  Q&A promedio/arch  : {avg:.1f}")
        log.info(sep)
        if not self.dry_run:
            log.info("  Siguiente paso:")
            log.info("    python training/train_qlora.py \\")
            log.info(f"      --dataset {out_path} \\")
            log.info("      --output ./lora_adapters/")
        log.info(sep)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    global QA_PER_CHUNK

    parser = argparse.ArgumentParser(
        description="VOID AXIOM — Study Engine v3.0: PDFs → Q&A turbo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python study_engine.py                                    # procesar todo
  python study_engine.py --model void-coda:latest --workers 4  # más paralelo
  python study_engine.py --limit 10 --resume                # reanudar
  python study_engine.py --dry-run                          # solo listar
        """,
    )
    parser.add_argument("--source", action="append", type=Path,
                        default=[KNOWLEDGE_BASE_DIR],
                        help="Directorio(s) con PDFs/código")
    parser.add_argument("--pdf", type=Path, default=None,
                        help="Procesar un solo archivo")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Modelo Ollama (default: {DEFAULT_MODEL})")
    parser.add_argument("--out", type=Path, default=Path("./datasets"),
                        help="Carpeta de salida")
    parser.add_argument("--limit", type=int, default=0,
                        help="Máximo archivos (0=todos)")
    parser.add_argument("--qa-per-chunk", type=int, default=QA_PER_CHUNK,
                        help=f"Q&A por chunk (default: {QA_PER_CHUNK})")
    parser.add_argument("--workers", type=int, default=WORKERS_DEFAULT,
                        help=f"Chunks en paralelo (default: {WORKERS_DEFAULT})")
    parser.add_argument("--resume", action="store_true",
                        help="Reanudar desde checkpoint")
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo listar chunks sin generar Q&A")
    args = parser.parse_args()

    QA_PER_CHUNK = args.qa_per_chunk

    log.info("╔══════════════════════════════════════════════════════╗")
    log.info("║   VOID AXIOM — Study Engine v3.0 TURBO             ║")
    log.info("╚══════════════════════════════════════════════════════╝")

    engine = StudyEngine(
        model=args.model,
        out_dir=args.out,
        dry_run=args.dry_run,
        workers=args.workers,
    )

    engine.run(
        source_dirs=args.source,
        specific_pdf=args.pdf,
        limit=args.limit,
        resume=args.resume,
    )


if __name__ == "__main__":
    main()