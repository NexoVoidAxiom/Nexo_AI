"""
continuous_scraper.py — Scraper CONTINUO de PDFs v3.0
================================================================
Novedades en v3.0 (RAG + búsqueda dinámica):
  · --search "tema"        Búsqueda semántica en arXiv + Semantic Scholar
                           en tiempo real, no solo las URLs hardcodeadas
  · --index-rag            Indexa cada PDF descargado en ChromaDB al momento
  · --query "pregunta"     Abre sesión RAG interactiva al terminar de descargar
  · Fuentes confirmadas    arXiv (nuevos CS) + libros gratuitos verificados

Búsqueda dinámica (--search):
  - arXiv full-text search  → cualquier topic CS/ML/AI
  - Semantic Scholar API    → papers con PDF abierto
  - Combina ambas, deduplica y descarga los mejores

Indexado RAG automático (--index-rag):
  - Cada PDF descargado con éxito se indexa inmediatamente en ChromaDB
  - Requiere: pip install chromadb && ollama pull nomic-embed-text
  - El índice se usa luego con: python rag_query.py --interactive

Uso:
    # Modo continuo (igual que antes)
    python continuous_scraper.py

    # Búsqueda manual por tema (nueva funcionalidad RAG)
    python continuous_scraper.py --search "attention mechanism transformers"
    python continuous_scraper.py --search "operating systems memory management" --index-rag
    python continuous_scraper.py --search "python asyncio" --index-rag --query

    # Un solo ciclo y salir
    python continuous_scraper.py --once --index-rag

    # Solo fuentes confirmadas + indexar
    python continuous_scraper.py --once --index-rag --interval 0
"""

import os, re, json, time, random, sqlite3, hashlib, logging, argparse, subprocess, sys
import urllib.parse, urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timedelta
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("continuous_scraper.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("continuous_scraper")

# ── Config ───────────────────────────────────────────────────────────────────
KNOWLEDGE_BASE_DIR = Path(os.environ.get("KNOWLEDGE_BASE", "./knowledge_base")).resolve()
RAG_STORE_DIR      = Path(os.environ.get("RAG_STORE", "./rag_store")).resolve()
DB_FILE            = KNOWLEDGE_BASE_DIR / "pdf_scraper.db"
DELAY_BASE         = 2.0
MAX_FILE_SIZE_MB   = 60
SCAN_INTERVAL      = 600    # 10 min entre tandas automáticas

# Semantic Scholar API (gratis, sin autenticación)
SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1/paper/search"

_session = requests.Session()
_session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
    ),
})

# ── RAG Indexer ──────────────────────────────────────────────────────────────
# Ruta al script de indexación; se busca en el mismo directorio que este script
_THIS_DIR    = Path(__file__).parent
RAG_INDEXER  = _THIS_DIR / "rag_indexer.py"
RAG_QUERY_SC = _THIS_DIR / "rag_query.py"


# ═══════════════════════════════════════════════════════════════════════════════
# UTILIDADES HTTP
# ═══════════════════════════════════════════════════════════════════════════════

def get_bytes(url, timeout=60):
    try:
        time.sleep(DELAY_BASE)
        resp = _session.get(url, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200:
            return resp.content
    except Exception as e:
        log.warning(f"  ⚠ {e}")
    return None


def get_page(url):
    for attempt in range(2):
        try:
            time.sleep(3 + random.random() * 2)
            resp = _session.get(url, timeout=60)
            if resp.status_code == 200:
                return resp.text
            if resp.status_code == 429:
                log.warning("  Rate limited, esperando 15s…")
                time.sleep(15)
        except Exception:
            time.sleep(5)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# BASE DE DATOS DE DESCARGAS
# ═══════════════════════════════════════════════════════════════════════════════

class DownloadDB:
    def __init__(self):
        os.makedirs(str(KNOWLEDGE_BASE_DIR), exist_ok=True)
        self.conn = sqlite3.connect(str(DB_FILE), check_same_thread=False)
        self._init()

    def _init(self):
        for sql in [
            """CREATE TABLE IF NOT EXISTS downloads (
                url TEXT PRIMARY KEY, filename TEXT, source TEXT,
                sha256 TEXT, size_kb REAL, ts TEXT,
                category TEXT, language TEXT DEFAULT '', indexed_rag INTEGER DEFAULT 0
            )""",
            "CREATE TABLE IF NOT EXISTS failures (url TEXT PRIMARY KEY, reason TEXT, ts TEXT)",
        ]:
            self.conn.execute(sql)
        # Migración: añadir columna indexed_rag si no existe (bases de datos antiguas)
        try:
            self.conn.execute("ALTER TABLE downloads ADD COLUMN indexed_rag INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        self.conn.commit()

    def seen(self, url):
        return self.conn.execute(
            "SELECT 1 FROM downloads WHERE url=? UNION SELECT 1 FROM failures WHERE url=?",
            (url, url),
        ).fetchone() is not None

    def seen_hash(self, sha):
        return self.conn.execute(
            "SELECT 1 FROM downloads WHERE sha256=?", (sha,)
        ).fetchone() is not None

    def record(self, url, filename, source, sha256, size_kb, category, language=""):
        self.conn.execute(
            "INSERT OR REPLACE INTO downloads VALUES (?,?,?,?,?,?,?,?,0)",
            (url, filename, source, sha256, size_kb,
             datetime.utcnow().isoformat(), category, language),
        )
        self.conn.commit()

    def mark_indexed(self, url):
        self.conn.execute(
            "UPDATE downloads SET indexed_rag=1 WHERE url=?", (url,)
        )
        self.conn.commit()

    def fail(self, url, reason):
        self.conn.execute(
            "INSERT OR REPLACE INTO failures VALUES (?,?,?)",
            (url, reason, datetime.utcnow().isoformat()),
        )
        self.conn.commit()

    def get_stats(self):
        total = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size_kb),0) FROM downloads"
        ).fetchone()
        indexed = self.conn.execute(
            "SELECT COUNT(*) FROM downloads WHERE indexed_rag=1"
        ).fetchone()[0]
        return {
            "total_pdfs": total[0] or 0,
            "total_mb":   round((total[1] or 0) / 1024, 2),
            "indexed_rag": indexed,
        }

    def get_unindexed_paths(self) -> list[tuple[str, str]]:
        """Devuelve (filename, url) de PDFs no indexados en RAG."""
        rows = self.conn.execute(
            "SELECT filename, url FROM downloads WHERE indexed_rag=0"
        ).fetchall()
        return rows

    def close(self):
        self.conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDACIÓN Y DESCARGA DE PDFs
# ═══════════════════════════════════════════════════════════════════════════════

def is_valid_pdf(data): return data[:4] == b"%PDF"
def pdf_sha256(data):   return hashlib.sha256(data).hexdigest()

def safe_filename(text, max_len=80):
    text = re.sub(r"[^\w\s\-]", "", text)
    text = re.sub(r"\s+", "_", text.strip())
    return text[:max_len]


def download_pdf(url, dest_path, db, category="continuous", language="", auto_index=False):
    """
    Descarga un PDF y opcionalmente lo indexa en ChromaDB (auto_index=True).
    Devuelve True si se descargó con éxito.
    """
    if db.seen(url):
        return False
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    log.info(f"  ↓ {dest_path.name}")
    data = get_bytes(url)
    if not data:
        db.fail(url, "no_response")
        log.warning("  ✗ Sin respuesta")
        return False
    if not is_valid_pdf(data):
        db.fail(url, "not_pdf")
        log.warning("  ✗ No es PDF válido")
        return False
    size_mb = len(data) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        db.fail(url, "too_large")
        log.warning(f"  ✗ Demasiado grande ({size_mb:.1f} MB)")
        return False
    sha = pdf_sha256(data)
    if db.seen_hash(sha):
        db.fail(url, "duplicate")
        log.info("  ↷ Duplicado")
        return False
    try:
        dest_path.write_bytes(data)
    except Exception as e:
        log.warning(f"  ⚠ Error al guardar: {e}")
        db.fail(url, "write_error")
        return False

    db.record(url, dest_path.name, "continuous", sha, len(data) / 1024, category, language)
    log.info(f"  ✓ OK! ({size_mb:.1f} MB) — {language}")

    # Indexar en RAG inmediatamente si se solicitó
    if auto_index:
        indexed = _index_file_rag(dest_path)
        if indexed:
            db.mark_indexed(url)

    return True


# ═══════════════════════════════════════════════════════════════════════════════
# INDEXADO RAG
# ═══════════════════════════════════════════════════════════════════════════════

def _index_file_rag(path: Path) -> bool:
    """
    Llama a rag_indexer.py para indexar un archivo en ChromaDB.
    Devuelve True si el proceso terminó sin error.
    """
    if not RAG_INDEXER.exists():
        log.debug(f"  rag_indexer.py no encontrado en {RAG_INDEXER}")
        return False
    try:
        result = subprocess.run(
            [sys.executable, str(RAG_INDEXER),
             "--pdf", str(path),
             "--store", str(RAG_STORE_DIR)],
            timeout=180,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            log.info(f"  📚 RAG: '{path.name}' indexado en ChromaDB")
            return True
        else:
            # Mostrar solo la primera línea del error para no saturar el log
            err_line = result.stderr.strip().split("\n")[0]
            log.warning(f"  ⚠ RAG indexer: {err_line}")
            return False
    except subprocess.TimeoutExpired:
        log.warning("  ⚠ RAG indexer timeout (>3 min)")
        return False
    except Exception as e:
        log.warning(f"  ⚠ Error al invocar rag_indexer: {e}")
        return False


def index_pending_rag(db: DownloadDB) -> int:
    """Indexa en RAG todos los PDFs descargados que aún no están indexados."""
    pending = db.get_unindexed_paths()
    if not pending:
        log.info("  ✓ Todos los PDFs ya están indexados en RAG")
        return 0

    log.info(f"  📚 Indexando {len(pending)} PDFs pendientes en RAG…")
    indexed = 0
    for filename, url in pending:
        # Buscar el archivo en la knowledge base
        matches = list(KNOWLEDGE_BASE_DIR.rglob(filename))
        if not matches:
            log.warning(f"  ⚠ No encontrado en KB: {filename}")
            continue
        if _index_file_rag(matches[0]):
            db.mark_indexed(url)
            indexed += 1
    return indexed


# ═══════════════════════════════════════════════════════════════════════════════
# FUENTE 1 — arXiv (nuevos papers por categoría)
# ═══════════════════════════════════════════════════════════════════════════════

ARXIV_CATS = [
    "cs.PL", "cs.SE", "cs.LG", "cs.AI", "cs.DS", "cs.DC",
    "cs.CR", "cs.DB", "cs.NE", "cs.CV", "cs.OS", "cs.AR",
    "cs.LO", "cs.GT", "cs.RO", "cs.HC", "cs.IR", "cs.CY",
]


def _parse_arxiv_xml(text: str) -> list[dict]:
    """Parsea la respuesta XML de la API de arXiv."""
    papers = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return papers
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("atom:entry", ns):
        try:
            title = (entry.find("atom:title", ns).text or "").strip()
            pdf_url = None
            for link in entry.findall("atom:link", ns):
                if link.get("title") == "pdf":
                    pdf_url = link.get("href", "")
                    if pdf_url and not pdf_url.endswith(".pdf"):
                        pdf_url += ".pdf"
                    break
            if title and pdf_url:
                papers.append({"title": title[:120], "pdf_url": pdf_url})
        except Exception:
            continue
    return papers


def search_arxiv(max_results=15) -> list[dict]:
    """Búsqueda de arXiv por categorías predefinidas (modo continuo)."""
    papers = []
    per_cat = max(1, max_results // len(ARXIV_CATS))
    for cat in ARXIV_CATS:
        params = urllib.parse.urlencode({
            "search_query": f"cat:{cat}",
            "start": 0,
            "max_results": per_cat,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        })
        text = get_page(f"https://export.arxiv.org/api/query?{params}")
        if not text:
            continue
        for p in _parse_arxiv_xml(text):
            p["category"] = cat
            papers.append(p)
    return papers


def search_arxiv_dynamic(query: str, max_results: int = 20) -> list[dict]:
    """
    NUEVA: Búsqueda dinámica en arXiv por tema/query.
    Busca en todos los campos (título, abstract, cuerpo) — mucho más flexible
    que buscar solo por categoría.
    """
    log.info(f"  🔎 arXiv — búsqueda: '{query}'")
    params = urllib.parse.urlencode({
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    })
    text = get_page(f"https://export.arxiv.org/api/query?{params}")
    if not text:
        return []
    papers = []
    for p in _parse_arxiv_xml(text):
        p["category"] = "arxiv_search"
        papers.append(p)
    log.info(f"     → {len(papers)} papers encontrados en arXiv")
    return papers


def search_semantic_scholar(query: str, max_results: int = 15) -> list[dict]:
    """
    NUEVA: Busca papers con PDF abierto en Semantic Scholar (API gratuita, sin auth).
    Es complementaria a arXiv — cubre más fuentes (ACM, IEEE, NeurIPS, etc.)
    """
    log.info(f"  🔎 Semantic Scholar — búsqueda: '{query}'")
    try:
        resp = _session.get(
            SEMANTIC_SCHOLAR_API,
            params={
                "query": query,
                "limit": max_results,
                "fields": "title,openAccessPdf,year,venue",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            log.warning(f"  ⚠ Semantic Scholar: HTTP {resp.status_code}")
            return []

        papers = []
        for p in resp.json().get("data", []):
            pdf_info = p.get("openAccessPdf")
            if not pdf_info or not pdf_info.get("url"):
                continue
            title = p.get("title", "")[:120]
            year  = p.get("year", "")
            venue = p.get("venue", "")
            papers.append({
                "title":    f"{title} ({venue} {year})".strip(" ()"),
                "pdf_url":  pdf_info["url"],
                "category": "semantic_scholar",
            })

        log.info(f"     → {len(papers)} papers con PDF abierto")
        return papers

    except Exception as e:
        log.warning(f"  ⚠ Semantic Scholar: {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# FUENTE 2 — Libros GRATIS con URLs CONFIRMADAS
# ═══════════════════════════════════════════════════════════════════════════════

CONFIRMED_FREE_BOOKS = [
    # Python
    ("https://greenteapress.com/thinkpython2/thinkpython2.pdf",           "ThinkPython2.pdf",         "python"),
    ("https://greenteapress.com/thinkstats2/thinkstats2.pdf",             "ThinkStats2.pdf",           "python"),
    ("https://greenteapress.com/complexity2/thinkcomplexity2.pdf",        "ThinkComplexity2.pdf",      "python"),
    ("https://do1.dr-chuck.net/pythonlearn/EN_us/pythonlearn.pdf",        "PythonForEverybody.pdf",    "python"),
    # JavaScript
    ("https://eloquentjavascript.net/Eloquent_JavaScript.pdf",            "EloquentJavaScript.pdf",    "javascript"),
    # Linux / Bash
    ("https://tldp.org/LDP/abs/abs-guide.pdf",                            "AdvancedBashScripting.pdf", "linux"),
    ("https://tldp.org/LDP/Bash-Beginners-Guide/Bash-Beginners-Guide.pdf","BashBeginnersGuide.pdf",    "linux"),
    # Networking
    ("https://do1.dr-chuck.net/net-intro/EN_us/net-intro.pdf",            "IntroNetworking.pdf",       "networking"),
    # Databases
    ("https://www.db-book.com/slides-dir/PDF-dir/ch1.pdf",                "DB_Ch1.pdf",                "databases"),
    ("https://www.db-book.com/slides-dir/PDF-dir/ch3.pdf",                "DB_Ch3_SQL.pdf",            "databases"),
    ("https://www.db-book.com/slides-dir/PDF-dir/ch6.pdf",                "DB_Ch6_AdvancedSQL.pdf",    "databases"),
    # Security
    ("https://www.cl.cam.ac.uk/~rja14/Papers/SEv3-ch2-7sep.pdf",          "SecurityEngineering_Ch2.pdf","security"),
    # Deep Learning
    ("https://www.deeplearningbook.org/front_matter.pdf",                 "DeepLearning_FrontMatter.pdf","deep_learning"),
    # OSTEP — Sistemas Operativos
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/intro.pdf",                  "OSTEP_intro.pdf",           "systems"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/threads-intro.pdf",          "OSTEP_threads.pdf",         "systems"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/file-intro.pdf",             "OSTEP_files.pdf",           "systems"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/cpu-intro.pdf",              "OSTEP_cpu.pdf",             "systems"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/vm-intro.pdf",               "OSTEP_vm.pdf",              "systems"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/threads-locks.pdf",          "OSTEP_locks.pdf",           "systems"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/threads-cv.pdf",             "OSTEP_condvar.pdf",         "systems"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/file-implementation.pdf",    "OSTEP_fs_impl.pdf",         "systems"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/dist-intro.pdf",             "OSTEP_dist.pdf",            "systems"),
    # R
    ("https://cran.r-project.org/doc/manuals/r-release/R-intro.pdf",      "R_Intro.pdf",               "r"),
    ("https://cran.r-project.org/doc/manuals/r-release/R-lang.pdf",       "R_Lang.pdf",                "r"),
]


def scrape_confirmed_books(db, auto_index=False) -> int:
    total = 0
    log.info(f"\n  📚 Libros confirmados: {len(CONFIRMED_FREE_BOOKS)} disponibles")
    for url, filename, lang in CONFIRMED_FREE_BOOKS:
        if db.seen(url):
            continue
        dest = KNOWLEDGE_BASE_DIR / "confirmed_books" / filename
        if download_pdf(url, dest, db, category="confirmed", language=lang,
                        auto_index=auto_index):
            total += 1
            if total >= 5:
                break
    return total


# ═══════════════════════════════════════════════════════════════════════════════
# FUENTE 3 — arXiv por categorías (modo continuo)
# ═══════════════════════════════════════════════════════════════════════════════

def scrape_arxiv(db, auto_index=False) -> int:
    total = 0
    log.info("\n  📡 arXiv: Buscando papers recientes…")
    papers = search_arxiv(10)
    nuevos = [p for p in papers if not db.seen(p["pdf_url"])]
    log.info(f"     {len(papers)} encontrados, {len(nuevos)} sin descargar")
    for p in nuevos:
        if total >= 3:
            break
        safe = p.get("category", "cs").replace(".", "_")
        fn   = f"{safe}_{safe_filename(p['title'])}.pdf"
        dest = KNOWLEDGE_BASE_DIR / "arxiv" / "continuous" / fn
        if download_pdf(p["pdf_url"], dest, db,
                        category=f"arxiv_{p.get('category','cs')}",
                        language=p.get("category","cs").split(".")[-1],
                        auto_index=auto_index):
            total += 1
    return total


# ═══════════════════════════════════════════════════════════════════════════════
# FUENTE 4 (NUEVA) — Búsqueda dinámica por query
# ═══════════════════════════════════════════════════════════════════════════════

def scrape_dynamic_search(db, query: str, max_download: int = 10,
                          auto_index: bool = False) -> int:
    """
    Búsqueda dinámica: arXiv full-text + Semantic Scholar.
    Usa cuando se ejecuta manualmente con --search "tema".
    """
    log.info(f"\n  🔎 Búsqueda dinámica: '{query}'")
    log.info(f"  {'─' * 54}")

    papers = []

    # 1. arXiv full-text
    try:
        papers += search_arxiv_dynamic(query, max_results=15)
    except Exception as e:
        log.warning(f"  ⚠ arXiv dinámico: {e}")

    # Pausa cortés entre APIs
    time.sleep(2)

    # 2. Semantic Scholar
    try:
        papers += search_semantic_scholar(query, max_results=15)
    except Exception as e:
        log.warning(f"  ⚠ Semantic Scholar: {e}")

    # Deduplicar por URL
    seen_urls: set[str] = set()
    unique_papers = []
    for p in papers:
        url = p.get("pdf_url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_papers.append(p)

    # Filtrar los ya descargados
    nuevos = [p for p in unique_papers if not db.seen(p["pdf_url"])]
    log.info(f"\n  Total únicos: {len(unique_papers)} | Nuevos: {len(nuevos)}")

    if not nuevos:
        log.info("  ✓ No hay PDFs nuevos para este query")
        return 0

    total = 0
    for p in nuevos:
        if total >= max_download:
            break
        fn   = f"search_{safe_filename(p['title'])}.pdf"
        cat  = p.get("category", "dynamic_search")
        dest = KNOWLEDGE_BASE_DIR / "search_results" / fn
        if download_pdf(p["pdf_url"], dest, db, category=cat,
                        language="en", auto_index=auto_index):
            total += 1
            log.info(f"  [{total}/{max_download}] {p['title'][:60]}…")

    return total


# ═══════════════════════════════════════════════════════════════════════════════
# CICLO PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

def run_scan_cycle(db, auto_index=False) -> int:
    log.info("\n" + "=" * 56)
    log.info("  🚀 NUEVA TANDA")
    log.info(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 56)
    grand_total = 0

    # 1. arXiv categorías
    log.info(f"\n{'─' * 56}")
    try:
        n = scrape_arxiv(db, auto_index=auto_index)
        grand_total += n
        if n > 0:
            log.info(f"  ✓ arXiv: {n} descargados")
    except Exception as e:
        log.warning(f"  ⚠ arXiv: {e}")

    # 2. Libros confirmados
    log.info(f"\n{'─' * 56}")
    try:
        n = scrape_confirmed_books(db, auto_index=auto_index)
        grand_total += n
        if n > 0:
            log.info(f"  ✓ Libros: {n} descargados")
    except Exception as e:
        log.warning(f"  ⚠ Libros: {e}")

    stats = db.get_stats()
    log.info(f"\n{'=' * 56}")
    log.info(f"  📊 TANDA: {grand_total} nuevos")
    log.info(f"  Total: {stats['total_pdfs']} PDFs ({stats['total_mb']} MB)")
    log.info(f"  RAG indexados: {stats['indexed_rag']}")
    log.info(f"{'=' * 56}")
    return grand_total


# ═══════════════════════════════════════════════════════════════════════════════
# RAG QUERY INTERACTIVO
# ═══════════════════════════════════════════════════════════════════════════════

def launch_rag_interactive(model: str = ""):
    """Abre una sesión RAG interactiva con rag_query.py."""
    if not RAG_QUERY_SC.exists():
        log.error(f"  ✗ rag_query.py no encontrado en {RAG_QUERY_SC}")
        log.error("  Coloca rag_query.py en el mismo directorio que este script")
        return

    cmd = [sys.executable, str(RAG_QUERY_SC), "--interactive"]
    if model:
        cmd += ["--model", model]

    log.info("\n  🤖 Iniciando sesión RAG interactiva…")
    log.info(f"  (usa /salir para terminar)\n")
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="VOID AXIOM — Scraper CONTINUO v3.0 (RAG + búsqueda dinámica)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  # Modo continuo (igual que siempre)
  python continuous_scraper.py

  # Búsqueda manual por tema (NUEVA funcionalidad)
  python continuous_scraper.py --search "attention mechanism transformers"
  python continuous_scraper.py --search "python asyncio coroutines" --index-rag

  # Descargar + indexar + abrir chat RAG
  python continuous_scraper.py --search "OS scheduling" --index-rag --query

  # Un ciclo completo con indexado automático
  python continuous_scraper.py --once --index-rag

  # Indexar todos los PDFs descargados que faltan en RAG
  python continuous_scraper.py --reindex-all
        """,
    )
    # Modo de operación
    parser.add_argument("--once",         action="store_true",
                        help="Ejecutar un solo ciclo y salir")
    parser.add_argument("--interval",     type=int, default=SCAN_INTERVAL,
                        help=f"Segundos entre ciclos (default: {SCAN_INTERVAL})")

    # NUEVA: búsqueda dinámica
    parser.add_argument("--search",       type=str, default="",
                        help="Búsqueda dinámica por tema (arXiv + Semantic Scholar)")
    parser.add_argument("--max-search",   type=int, default=10,
                        help="Máx PDFs a descargar por búsqueda (default: 10)")

    # NUEVA: integración RAG
    parser.add_argument("--index-rag",    action="store_true",
                        help="Indexar en ChromaDB (RAG) cada PDF descargado")
    parser.add_argument("--reindex-all",  action="store_true",
                        help="Indexar en RAG todos los PDFs pendientes")
    parser.add_argument("--query",        action="store_true",
                        help="Abrir sesión RAG interactiva al terminar")
    parser.add_argument("--rag-model",    type=str, default="",
                        help="Modelo LLM para el chat RAG (default: env VOID_MODEL)")

    args = parser.parse_args()
    interval   = args.interval
    auto_index = args.index_rag

    db = DownloadDB()

    log.info("╔══════════════════════════════════════════════════════╗")
    log.info("║   VOID AXIOM — Scraper CONTINUO v3.0               ║")
    log.info("║   RAG + Búsqueda Dinámica (arXiv + Semantic Scholar)║")
    log.info("╚══════════════════════════════════════════════════════╝")
    log.info(f"  Destino     : {KNOWLEDGE_BASE_DIR.resolve()}")
    log.info(f"  RAG store   : {RAG_STORE_DIR.resolve()}")
    log.info(f"  Index RAG   : {'SI' if auto_index else 'NO'}")
    if args.search:
        log.info(f"  Búsqueda    : '{args.search}'")

    # ── Modo: reindexar pendientes ─────────────────────────────────────────
    if args.reindex_all:
        log.info("\n  📚 Reindexando PDFs pendientes en RAG…")
        n = index_pending_rag(db)
        log.info(f"  ✓ {n} PDFs indexados en esta sesión")
        if args.query:
            launch_rag_interactive(args.rag_model)
        db.close()
        return

    ciclo = 0
    try:
        while True:
            ciclo += 1
            log.info(f"\n{'█' * 56}")
            log.info(f"  🔄 CICLO #{ciclo}")
            log.info(f"{'█' * 56}\n")

            nuevos = 0

            # ── Búsqueda dinámica por query (si se especificó --search) ────
            if args.search:
                try:
                    n = scrape_dynamic_search(
                        db,
                        query=args.search,
                        max_download=args.max_search,
                        auto_index=auto_index,
                    )
                    nuevos += n
                    if n > 0:
                        log.info(f"  ✓ Búsqueda '{args.search}': {n} PDFs descargados")
                except Exception as e:
                    log.error(f"  ✗ Error en búsqueda dinámica: {e}")

            # ── Ciclo estándar (arXiv categorías + libros confirmados) ─────
            try:
                nuevos += run_scan_cycle(db, auto_index=auto_index)
            except Exception as e:
                log.error(f"  ✗ Error en ciclo estándar: {e}")

            # ── Abrir RAG interactivo al terminar (si --query) ─────────────
            if args.once or args.search:
                if args.query:
                    launch_rag_interactive(args.rag_model)
                break

            # ── Control de ciclos continuos ────────────────────────────────
            if nuevos > 0:
                log.info(f"\n  ✅ {nuevos} PDFs nuevos. Siguiente tanda inmediatamente…\n")
                continue

            log.info(f"\n  ⏳ Esperando {interval // 60} min hasta próxima tanda…")
            time.sleep(interval)

    except KeyboardInterrupt:
        log.info("\n  🛑 Detenido por el usuario")
    finally:
        try:
            stats = db.get_stats()
        except Exception:
            stats = {"total_pdfs": 0, "total_mb": 0, "indexed_rag": 0}
        db.close()
        log.info(
            f"\n  TOTAL FINAL: {stats['total_pdfs']} PDFs "
            f"({stats['total_mb']} MB) | "
            f"RAG indexados: {stats['indexed_rag']}"
        )


if __name__ == "__main__":
    main()
