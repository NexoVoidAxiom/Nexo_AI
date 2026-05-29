"""
rag_indexer.py — Indexador RAG para VOID AXIOM v1.0
====================================================
Convierte PDFs y código de la knowledge_base en embeddings vectoriales
almacenados en ChromaDB. Soporta indexación incremental (solo archivos nuevos
o modificados), por lotes y de un solo archivo.

Requiere:
    pip install chromadb pypdf requests

Modelo de embeddings (Ollama debe estar corriendo):
    ollama pull nomic-embed-text

Uso:
    python rag_indexer.py                              # indexar toda la KB
    python rag_indexer.py --pdf libro.pdf             # un archivo concreto
    python rag_indexer.py --reset                     # borrar y reindexar todo
    python rag_indexer.py --stats                     # estadísticas del índice
    python rag_indexer.py --embed-model mxbai-embed-large
    python rag_indexer.py --source /ruta/kb --store /ruta/rag_store
"""

import os, re, json, time, sqlite3, hashlib, logging, argparse, sys
from pathlib import Path
from datetime import datetime
import requests

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("rag_indexer.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("rag_indexer")

# ── Config ───────────────────────────────────────────────────────────────────
KNOWLEDGE_BASE_DIR  = Path(os.environ.get("KNOWLEDGE_BASE", "./knowledge_base")).resolve()
RAG_STORE_DIR       = Path(os.environ.get("RAG_STORE", "./rag_store")).resolve()
OLLAMA_BASE_URL     = os.environ.get("VOID_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
DEFAULT_EMBED_MODEL = os.environ.get("VOID_EMBED_MODEL", "nomic-embed-text")
COLLECTION_NAME     = "void_axiom_kb"
CHUNK_CHARS         = 1200   # ~300 tokens — contexto generoso por chunk
CHUNK_OVERLAP_CHARS = 150    # solapamiento para no perder contexto en bordes
UPSERT_BATCH_SIZE   = 50     # chunks por lote de upsert en ChromaDB
MAX_FILE_SIZE_MB    = 60

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".cpp", ".c", ".h",
    ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".bash", ".sql",
    ".lua", ".swift", ".kt", ".r", ".R", ".scala", ".ex", ".exs",
    ".yml", ".yaml", ".toml", ".md", ".rst",
}

# ── Deps opcionales ──────────────────────────────────────────────────────────
try:
    import chromadb
    CHROMA_OK = True
except ImportError:
    CHROMA_OK = False
    log.warning("ChromaDB no instalado: pip install chromadb")

try:
    from pypdf import PdfReader
    PDF_OK = True
    _PDF_LIB = "pypdf"
except ImportError:
    try:
        import pdfplumber
        PDF_OK = True
        _PDF_LIB = "pdfplumber"
    except ImportError:
        PDF_OK = False
        _PDF_LIB = None
        log.warning("Sin librería PDF: pip install pypdf")


# ═══════════════════════════════════════════════════════════════════════════════
# EMBEDDINGS VÍA OLLAMA
# ═══════════════════════════════════════════════════════════════════════════════

_embed_model_active = DEFAULT_EMBED_MODEL
_embed_dim: int | None = None   # se detecta automáticamente en el primer uso


def get_embedding(text: str, model: str | None = None) -> list[float] | None:
    """
    Obtiene embedding de texto vía Ollama.
    Intenta el endpoint moderno /api/embed (Ollama ≥ 0.3) y el legacy /api/embeddings.
    """
    global _embed_dim
    m = model or _embed_model_active
    text = text.strip()
    if not text:
        return None

    endpoints = [
        ("/api/embed",       {"model": m, "input": text}),
        ("/api/embeddings",  {"model": m, "prompt": text}),
    ]
    for path, payload in endpoints:
        try:
            resp = requests.post(
                f"{OLLAMA_BASE_URL}{path}",
                json=payload,
                timeout=30
            )
            if resp.status_code == 200:
                data = resp.json()
                # /api/embed devuelve {"embeddings": [[...]]}
                # /api/embeddings devuelve {"embedding": [...]}
                raw = data.get("embeddings") or [data.get("embedding")]
                emb = raw[0] if raw else None
                if emb:
                    if _embed_dim is None:
                        _embed_dim = len(emb)
                        log.info(f"  Dimensión de embedding detectada: {_embed_dim}")
                    return emb
        except requests.exceptions.ConnectionError:
            log.error(f"  ✗ Ollama no accesible en {OLLAMA_BASE_URL}")
            return None
        except Exception:
            continue
    return None


def check_embed_model(model: str) -> bool:
    """Verifica que el modelo de embeddings esté disponible en Ollama."""
    log.info(f"  Verificando modelo de embeddings '{model}'…")
    emb = get_embedding("test de conectividad", model)
    if emb:
        log.info(f"  ✓ '{model}' disponible ({len(emb)}-dim)")
        return True
    log.error(f"  ✗ '{model}' no disponible. Solución: ollama pull {model}")
    return False


class OllamaEmbeddingFunction:
    """
    Función de embeddings compatible con la interfaz de ChromaDB.
    Llama a Ollama en lotes para no saturar el servidor.
    """
    def __call__(self, input: list[str]) -> list[list[float]]:
        results = []
        fallback_dim = _embed_dim or 768
        for text in input:
            emb = get_embedding(text)
            if emb is None:
                # Vector cero como fallback (el chunk se almacena pero tiene baja recuperabilidad)
                emb = [0.0] * fallback_dim
            results.append(emb)
        return results


# ═══════════════════════════════════════════════════════════════════════════════
# BASE DE DATOS DE ÍNDICE (tracking de archivos ya indexados)
# ═══════════════════════════════════════════════════════════════════════════════

class IndexDB:
    """SQLite para rastrear qué archivos están indexados y su hash SHA-256."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._init_schema()

    def _init_schema(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS indexed_files (
                path        TEXT PRIMARY KEY,
                sha256      TEXT NOT NULL,
                chunks      INTEGER DEFAULT 0,
                embed_model TEXT,
                ts          TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS index_stats (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        self.conn.commit()

    def is_indexed(self, path: Path, sha256: str) -> bool:
        """True si el archivo ya está indexado con el mismo hash (sin cambios)."""
        row = self.conn.execute(
            "SELECT sha256 FROM indexed_files WHERE path=?", (str(path),)
        ).fetchone()
        return row is not None and row[0] == sha256

    def record(self, path: Path, sha256: str, chunks: int, model: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO indexed_files VALUES (?,?,?,?,?)",
            (str(path), sha256, chunks, model, datetime.utcnow().isoformat())
        )
        self.conn.commit()

    def remove(self, path: Path):
        self.conn.execute("DELETE FROM indexed_files WHERE path=?", (str(path),))
        self.conn.commit()

    def reset(self):
        self.conn.execute("DELETE FROM indexed_files")
        self.conn.commit()

    def get_stats(self) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(chunks),0) FROM indexed_files"
        ).fetchone()
        return {
            "indexed_files": row[0] or 0,
            "total_local_chunks": row[1] or 0,
        }

    def close(self):
        self.conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# EXTRACCIÓN DE TEXTO
# ═══════════════════════════════════════════════════════════════════════════════

def extract_pdf(path: Path) -> str:
    if not PDF_OK:
        return ""
    try:
        if _PDF_LIB == "pypdf":
            from pypdf import PdfReader
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
        else:
            import pdfplumber
            parts = []
            with pdfplumber.open(str(path)) as pdf:
                for page in pdf.pages:
                    t = page.extract_text()
                    if t:
                        parts.append(t)
            raw = "\n\n".join(parts)

        # Limpiar artefactos comunes de PDFs
        raw = re.sub(r"-\n(\w)", r"\1", raw)                    # palabras partidas al final de línea
        raw = re.sub(r"^\s*\d+\s*$", "", raw, flags=re.MULTILINE)  # números de página solos
        raw = re.sub(r" {2,}", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()

    except Exception as e:
        log.error(f"  Error leyendo PDF {path.name}: {e}")
        return ""


def extract_code(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception as e:
        log.error(f"  Error leyendo {path.name}: {e}")
        return ""


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


# ═══════════════════════════════════════════════════════════════════════════════
# CHUNKING CON OVERLAP
# ═══════════════════════════════════════════════════════════════════════════════

def chunk_text(
    text: str,
    source: str,
    chunk_size: int = CHUNK_CHARS,
    overlap: int = CHUNK_OVERLAP_CHARS,
) -> list[dict]:
    """
    Divide texto en chunks con overlap semántico.
    Prioriza cortar en límites de párrafo > oración > palabra.
    """
    text = text.strip()
    if not text:
        return []

    chunks = []
    start = 0
    chunk_id = 0

    while start < len(text):
        end = start + chunk_size

        if end >= len(text):
            body = text[start:]
        else:
            # Buscar límite natural: párrafo > oración > espacio
            cut = end
            for delimiter in ["\n\n", "\n", ". ", "? ", "! ", " "]:
                pos = text.rfind(delimiter, start + chunk_size // 2, end)
                if pos > 0:
                    cut = pos + len(delimiter)
                    break
            body = text[start:cut]
            end = cut

        body = body.strip()
        if len(body) >= 80:   # mínimo de contenido útil
            uid = hashlib.md5(f"{source}:{chunk_id}:{body[:32]}".encode()).hexdigest()
            chunks.append({
                "id":      uid,
                "text":    body,
                "source":  source,
                "chunk_n": chunk_id,
            })
            chunk_id += 1

        # Retroceder 'overlap' caracteres para mantener contexto
        start = max(end - overlap, start + 1)

    return chunks


# ═══════════════════════════════════════════════════════════════════════════════
# INDEXER PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

class RAGIndexer:
    def __init__(
        self,
        embed_model: str = DEFAULT_EMBED_MODEL,
        store_dir: Path = RAG_STORE_DIR,
        reset: bool = False,
    ):
        if not CHROMA_OK:
            raise RuntimeError("ChromaDB no instalado: pip install chromadb")

        global _embed_model_active
        _embed_model_active = embed_model
        self.embed_model = embed_model

        store_dir.mkdir(parents=True, exist_ok=True)
        self.db = IndexDB(store_dir / "rag_index.db")

        # Inicializar ChromaDB persistente
        chroma_path = store_dir / "chroma"
        chroma_path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(chroma_path))
        self.ef = OllamaEmbeddingFunction()

        if reset:
            log.info("  🗑  Reset: eliminando colección y índice local…")
            try:
                self.client.delete_collection(COLLECTION_NAME)
            except Exception:
                pass
            self.db.reset()
            log.info("  ✓ Reset completado")

        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=self.ef,
            metadata={"hnsw:space": "cosine"},   # similitud coseno para texto
        )
        log.info(f"  Colección '{COLLECTION_NAME}': {self.collection.count()} chunks en ChromaDB")

    # ── Indexar un archivo ──────────────────────────────────────────────────

    def index_file(self, path: Path) -> int:
        """Indexa un archivo. Devuelve el número de chunks nuevos insertados."""
        suffix = path.suffix.lower()
        if suffix not in {".pdf"} | CODE_EXTENSIONS:
            return 0

        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > MAX_FILE_SIZE_MB:
            log.warning(f"  ⚠ Saltando (muy grande {size_mb:.1f} MB): {path.name}")
            return 0

        sha = file_sha256(path)
        if self.db.is_indexed(path, sha):
            log.debug(f"  ↷ Sin cambios, saltando: {path.name}")
            return 0

        log.info(f"  → Indexando: {path.name} ({size_mb:.1f} MB)")

        # Extraer texto
        if suffix == ".pdf":
            text = extract_pdf(path)
        else:
            text = extract_code(path)

        if not text or len(text.strip()) < 100:
            log.warning(f"  ⚠ Texto insuficiente en: {path.name}")
            return 0

        # Etiqueta de fuente relativa a la KB (o nombre de archivo)
        try:
            source_label = str(path.relative_to(KNOWLEDGE_BASE_DIR))
        except ValueError:
            source_label = path.name

        # Categoría desde la ruta (primer subdirectorio bajo KB)
        try:
            rel = path.relative_to(KNOWLEDGE_BASE_DIR)
            category = rel.parts[0] if len(rel.parts) > 1 else "general"
        except ValueError:
            category = "imported"

        # Chunking
        chunks = chunk_text(text, source=source_label)
        if not chunks:
            return 0

        # Upsert en ChromaDB por lotes
        inserted = 0
        for i in range(0, len(chunks), UPSERT_BATCH_SIZE):
            batch = chunks[i : i + UPSERT_BATCH_SIZE]
            try:
                self.collection.upsert(
                    ids       = [c["id"]   for c in batch],
                    documents = [c["text"] for c in batch],
                    metadatas = [
                        {
                            "source":   c["source"],
                            "chunk_n":  c["chunk_n"],
                            "category": category,
                            "filename": path.name,
                            "sha256":   sha[:12],
                        }
                        for c in batch
                    ],
                )
                inserted += len(batch)
            except Exception as e:
                log.error(f"  ✗ Error upsert lote {i//UPSERT_BATCH_SIZE}: {e}")

        if inserted > 0:
            self.db.record(path, sha, inserted, self.embed_model)
            log.info(f"  ✓ {path.name}: {inserted} chunks → ChromaDB")

        return inserted

    # ── Indexar directorio ──────────────────────────────────────────────────

    def index_directory(self, base_dir: Path, limit: int = 0) -> tuple[int, int]:
        """Indexa todos los PDFs/código de un directorio. Devuelve (files, chunks)."""
        if not base_dir.exists():
            log.warning(f"  Directorio no existe: {base_dir}")
            return 0, 0

        files = []
        for p in sorted(base_dir.rglob("*")):
            if not p.is_file():
                continue
            if any(part.startswith(".") or part == "__pycache__" for part in p.parts):
                continue
            if p.suffix.lower() in {".pdf"} | CODE_EXTENSIONS:
                files.append(p)

        log.info(f"  {len(files)} archivos encontrados en {base_dir}")
        if limit > 0:
            files = files[:limit]
            log.info(f"  (limitado a {limit})")

        total_files = total_chunks = 0
        for f in files:
            n = self.index_file(f)
            if n > 0:
                total_files += 1
                total_chunks += n

        return total_files, total_chunks

    def get_stats(self) -> dict:
        db_stats = self.db.get_stats()
        return {
            **db_stats,
            "chroma_total_chunks": self.collection.count(),
            "collection_name": COLLECTION_NAME,
        }

    def close(self):
        self.db.close()


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="VOID AXIOM — RAG Indexer v1.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python rag_indexer.py                              # indexar toda la KB
  python rag_indexer.py --pdf ./knowledge_base/libro.pdf
  python rag_indexer.py --reset                      # borrar y reindexar todo
  python rag_indexer.py --stats                      # estadísticas del índice
  python rag_indexer.py --embed-model mxbai-embed-large
  python rag_indexer.py --source /mi/kb --store /mi/rag_store --limit 50
        """,
    )
    parser.add_argument("--source", type=Path, default=KNOWLEDGE_BASE_DIR,
                        help="Directorio de la knowledge base")
    parser.add_argument("--store", type=Path, default=RAG_STORE_DIR,
                        help="Directorio ChromaDB (rag_store)")
    parser.add_argument("--pdf", type=Path, default=None,
                        help="Indexar un solo archivo")
    parser.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL,
                        help=f"Modelo de embeddings Ollama (default: {DEFAULT_EMBED_MODEL})")
    parser.add_argument("--batch-size", type=int, default=UPSERT_BATCH_SIZE,
                        help="Chunks por lote de upsert")
    parser.add_argument("--limit", type=int, default=0,
                        help="Máximo archivos (0=todos)")
    parser.add_argument("--reset", action="store_true",
                        help="Borrar el índice y reindexar desde cero")
    parser.add_argument("--stats", action="store_true",
                        help="Mostrar estadísticas del índice RAG")
    args = parser.parse_args()

    global UPSERT_BATCH_SIZE
    UPSERT_BATCH_SIZE = args.batch_size

    log.info("╔══════════════════════════════════════════════════════╗")
    log.info("║   VOID AXIOM — RAG Indexer v1.0                    ║")
    log.info("╚══════════════════════════════════════════════════════╝")
    log.info(f"  Knowledge Base : {args.source.resolve()}")
    log.info(f"  ChromaDB store : {args.store.resolve()}")
    log.info(f"  Embed model    : {args.embed_model}")

    # Verificar modelo de embeddings antes de empezar
    if not check_embed_model(args.embed_model):
        log.error("  Abortando — modelo no disponible")
        sys.exit(1)

    indexer = RAGIndexer(
        embed_model=args.embed_model,
        store_dir=args.store.resolve(),
        reset=args.reset,
    )

    try:
        if args.stats:
            stats = indexer.get_stats()
            log.info("\n  📊 Estadísticas RAG:")
            log.info(f"     Archivos indexados : {stats['indexed_files']}")
            log.info(f"     Chunks locales     : {stats['total_local_chunks']}")
            log.info(f"     Chunks en ChromaDB : {stats['chroma_total_chunks']}")
            log.info(f"     Colección          : {stats['collection_name']}")

        elif args.pdf:
            path = args.pdf.resolve()
            if not path.exists():
                log.error(f"  ✗ Archivo no existe: {path}")
                sys.exit(1)
            n = indexer.index_file(path)
            log.info(f"\n  ✓ {n} chunks indexados de {path.name}")

        else:
            source = args.source.resolve()
            files, chunks = indexer.index_directory(source, limit=args.limit)
            stats = indexer.get_stats()
            log.info("\n  ✅ Indexación completada")
            log.info(f"     Archivos nuevos    : {files}")
            log.info(f"     Chunks nuevos      : {chunks}")
            log.info(f"     Total en ChromaDB  : {stats['chroma_total_chunks']}")
    finally:
        indexer.close()


if __name__ == "__main__":
    main()
