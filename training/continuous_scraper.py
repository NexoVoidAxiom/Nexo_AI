"""
continuous_scraper.py — Scraper CONTINUO de PDFs
================================================================
Versión 2.2 — Busca PDFs solo en fuentes CONFIRMADAS:
  · arXiv (nuevos papers CS)
  · Libros clásicos gratuitos (URLs verificadas)
  · Documentación técnica gratuita
"""

import os, re, json, time, random, sqlite3, hashlib, logging, argparse
import urllib.request, urllib.parse, urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timedelta
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("continuous_scraper.log", encoding="utf-8")],
)
log = logging.getLogger("continuous_scraper")

KNOWLEDGE_BASE_DIR = Path(os.environ.get("KNOWLEDGE_BASE", "./knowledge_base")).resolve()
DB_FILE = KNOWLEDGE_BASE_DIR / "pdf_scraper.db"
DELAY_BASE = 2.0
MAX_FILE_SIZE_MB = 60
SCAN_INTERVAL = 600  # 10 min entre tandas

_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
})

def get_bytes(url, timeout=60):
    try:
        time.sleep(DELAY_BASE)
        resp = _session.get(url, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200:
            return resp.content
    except Exception as e:
        log.warning(f"  ⚠ {e}")
    return None

class DownloadDB:
    def __init__(self):
        os.makedirs(str(KNOWLEDGE_BASE_DIR), exist_ok=True)
        self.conn = sqlite3.connect(str(DB_FILE), check_same_thread=False)
        self._init()
    def _init(self):
        for sql in [
            "CREATE TABLE IF NOT EXISTS downloads (url TEXT PRIMARY KEY, filename TEXT, source TEXT, sha256 TEXT, size_kb REAL, ts TEXT, category TEXT, language TEXT DEFAULT '')",
            "CREATE TABLE IF NOT EXISTS failures (url TEXT PRIMARY KEY, reason TEXT, ts TEXT)",
        ]: self.conn.execute(sql)
        self.conn.commit()
    def seen(self, url):
        return self.conn.execute("SELECT 1 FROM downloads WHERE url=? UNION SELECT 1 FROM failures WHERE url=?", (url, url)).fetchone() is not None
    def seen_hash(self, sha):
        return self.conn.execute("SELECT 1 FROM downloads WHERE sha256=?", (sha,)).fetchone() is not None
    def record(self, url, filename, source, sha256, size_kb, category, language=""):
        self.conn.execute("INSERT OR REPLACE INTO downloads VALUES (?,?,?,?,?,?,?,?)", (url, filename, source, sha256, size_kb, datetime.utcnow().isoformat(), category, language))
        self.conn.commit()
    def fail(self, url, reason):
        self.conn.execute("INSERT OR REPLACE INTO failures VALUES (?,?,?)", (url, reason, datetime.utcnow().isoformat()))
        self.conn.commit()
    def get_stats(self):
        total = self.conn.execute("SELECT COUNT(*), COALESCE(SUM(size_kb),0) FROM downloads").fetchone()
        return {"total_pdfs": total[0] or 0, "total_mb": round((total[1] or 0)/1024, 2)}
    def close(self):
        self.conn.close()

def is_valid_pdf(data):
    return data[:4] == b"%PDF"

def pdf_sha256(data):
    return hashlib.sha256(data).hexdigest()

def safe_filename(text, max_len=80):
    text = re.sub(r"[^\w\s\-]", "", text)
    text = re.sub(r"\s+", "_", text.strip())
    return text[:max_len]

def download_pdf(url, dest_path, db, category="continuous", language=""):
    if db.seen(url):
        return False
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    log.info(f"  ↓ {dest_path.name}")
    data = get_bytes(url)
    if not data:
        db.fail(url, "no_response")
        log.warning(f"  ✗ Sin respuesta")
        return False
    if not is_valid_pdf(data):
        db.fail(url, "not_pdf")
        log.warning(f"  ✗ No es PDF válido")
        return False
    size_mb = len(data) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        db.fail(url, f"too_large")
        log.warning(f"  ✗ Demasiado grande ({size_mb:.1f} MB)")
        return False
    sha = pdf_sha256(data)
    if db.seen_hash(sha):
        db.fail(url, "duplicate")
        log.info(f"  ↷ Duplicado")
        return False
    try:
        dest_path.write_bytes(data)
    except Exception as e:
        log.warning(f"  ⚠ Error al guardar: {e}")
        db.fail(url, f"write_error")
        return False
    db.record(url, dest_path.name, "continuous", sha, len(data)/1024, category, language)
    log.info(f"  ✓ OK! ({size_mb:.1f} MB) — {language}")
    return True

# ═══════════════════════════════════════════════════════════════════════════════
# FUENTE 1 — arXiv
# ═══════════════════════════════════════════════════════════════════════════════

ARXIV_CATS = ["cs.PL","cs.SE","cs.LG","cs.AI","cs.DS","cs.DC","cs.CR","cs.DB",
    "cs.NE","cs.CV","cs.OS","cs.AR","cs.LO","cs.GT","cs.RO","cs.HC","cs.IR","cs.CY"]

def get_page(url):
    for attempt in range(2):
        try:
            time.sleep(3 + random.random() * 2)
            resp = _session.get(url, timeout=60)
            if resp.status_code == 200: return resp.text
            if resp.status_code == 429: time.sleep(10)
        except:
            time.sleep(5)
    return None

def search_arxiv(max_results=15):
    papers = []
    per_cat = max(1, max_results // len(ARXIV_CATS))
    for cat in ARXIV_CATS:
        params = urllib.parse.urlencode({"search_query": f"cat:{cat}", "start": 0,
            "max_results": per_cat, "sortBy": "submittedDate", "sortOrder": "descending"})
        text = get_page(f"https://export.arxiv.org/api/query?{params}")
        if not text: continue
        try: root = ET.fromstring(text)
        except: continue
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("atom:entry", ns):
            try:
                title = (entry.find("atom:title", ns).text or "").strip()
                pdf_url = None
                for link in entry.findall("atom:link", ns):
                    if link.get("title") == "pdf":
                        pdf_url = link.get("href", "")
                        if pdf_url and not pdf_url.endswith(".pdf"): pdf_url += ".pdf"
                        break
                if pdf_url: papers.append({"title": title[:120], "pdf_url": pdf_url, "category": cat})
            except: continue
    return papers

def scrape_arxiv(db):
    total = 0
    log.info("\n  📡 arXiv: Buscando...")
    papers = search_arxiv(10)
    nuevos = [p for p in papers if not db.seen(p["pdf_url"])]
    log.info(f"     {len(papers)} encontrados, {len(nuevos)} sin descargar")
    for p in nuevos:
        if total >= 3: break
        safe = p["category"].replace(".", "_")
        fn = f"{safe}_{safe_filename(p['title'])}.pdf"
        if download_pdf(p["pdf_url"], KNOWLEDGE_BASE_DIR / "arxiv" / "continuous" / fn, db,
                        category=f"arxiv_{p['category']}", language=p["category"].split(".")[-1]):
            total += 1
    return total

# ═══════════════════════════════════════════════════════════════════════════════
# FUENTE 2 — Libros GRATIS con URLs CONFIRMADAS que funcionan
# ═══════════════════════════════════════════════════════════════════════════════

CONFIRMED_FREE_BOOKS = [
    # Python
    ("https://greenteapress.com/thinkpython2/thinkpython2.pdf", "ThinkPython2.pdf", "python"),
    ("https://greenteapress.com/thinkstats2/thinkstats2.pdf", "ThinkStats2.pdf", "python"),
    ("https://greenteapress.com/complexity2/thinkcomplexity2.pdf", "ThinkComplexity2.pdf", "python"),
    ("https://do1.dr-chuck.net/pythonlearn/EN_us/pythonlearn.pdf", "PythonForEverybody.pdf", "python"),
    # JavaScript
    ("https://eloquentjavascript.net/Eloquent_JavaScript.pdf", "EloquentJavaScript.pdf", "javascript"),
    # Linux/Bash
    ("https://tldp.org/LDP/abs/abs-guide.pdf", "AdvancedBashScripting.pdf", "linux"),
    ("https://tldp.org/LDP/Bash-Beginners-Guide/Bash-Beginners-Guide.pdf", "BashBeginnersGuide.pdf", "linux"),
    # Networking
    ("https://do1.dr-chuck.net/net-intro/EN_us/net-intro.pdf", "IntroNetworking.pdf", "networking"),
    # Databases
    ("https://www.db-book.com/slides-dir/PDF-dir/ch1.pdf", "DB_Ch1.pdf", "databases"),
    ("https://www.db-book.com/slides-dir/PDF-dir/ch3.pdf", "DB_Ch3_SQL.pdf", "databases"),
    ("https://www.db-book.com/slides-dir/PDF-dir/ch6.pdf", "DB_Ch6_AdvancedSQL.pdf", "databases"),
    # Security
    ("https://www.cl.cam.ac.uk/~rja14/Papers/SEv3-ch2-7sep.pdf", "SecurityEngineering_Ch2.pdf", "security"),
    # Deep Learning
    ("https://www.deeplearningbook.org/front_matter.pdf", "DeepLearning_FrontMatter.pdf", "deep_learning"),
    # OSTEP (sistemas operativos)
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/intro.pdf", "OSTEP_intro.pdf", "systems"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/threads-intro.pdf", "OSTEP_threads.pdf", "systems"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/file-intro.pdf", "OSTEP_files.pdf", "systems"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/cpu-intro.pdf", "OSTEP_cpu.pdf", "systems"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/vm-intro.pdf", "OSTEP_vm.pdf", "systems"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/threads-locks.pdf", "OSTEP_locks.pdf", "systems"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/threads-cv.pdf", "OSTEP_condvar.pdf", "systems"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/file-implementation.pdf", "OSTEP_fs_impl.pdf", "systems"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/dist-intro.pdf", "OSTEP_dist.pdf", "systems"),
    # R
    ("https://cran.r-project.org/doc/manuals/r-release/R-intro.pdf", "R_Intro.pdf", "r"),
    ("https://cran.r-project.org/doc/manuals/r-release/R-lang.pdf", "R_Lang.pdf", "r"),
]

def scrape_confirmed_books(db):
    """Descarga libros GRATIS con URLs CONFIRMADAS que funcionan."""
    total = 0
    log.info(f"\n  📚 Libros confirmados: {len(CONFIRMED_FREE_BOOKS)} disponibles")
    for url, filename, lang in CONFIRMED_FREE_BOOKS:
        if db.seen(url):
            continue
        if download_pdf(url, KNOWLEDGE_BASE_DIR / "confirmed_books" / filename, db, category="confirmed", language=lang):
            total += 1
            if total >= 5: break  # máx 5 por tanda
    return total

# ═══════════════════════════════════════════════════════════════════════════════
# TANDA PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

def run_scan_cycle(db):
    log.info("\n" + "=" * 56)
    log.info("  🚀 NUEVA TANDA")
    log.info(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 56)
    grand_total = 0

    # 1. arXiv
    log.info(f"\n{'─' * 56}")
    try:
        n = scrape_arxiv(db)
        grand_total += n
        if n > 0: log.info(f"  ✓ arXiv: {n}")
    except Exception as e:
        log.warning(f"  ⚠ arXiv: {e}")

    # 2. Libros confirmados
    log.info(f"\n{'─' * 56}")
    try:
        n = scrape_confirmed_books(db)
        grand_total += n
        if n > 0: log.info(f"  ✓ Libros: {n}")
    except Exception as e:
        log.warning(f"  ⚠ Libros: {e}")

    stats = db.get_stats()
    log.info(f"\n{'=' * 56}")
    log.info(f"  📊 TANDA: {grand_total} nuevos")
    log.info(f"  Total: {stats['total_pdfs']} PDFs ({stats['total_mb']} MB)")
    log.info(f"{'=' * 56}")
    return grand_total

def main():
    parser = argparse.ArgumentParser(description="VOID AXIOM — Scraper CONTINUO v2.2")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=SCAN_INTERVAL)
    args = parser.parse_args()
    interval = args.interval
    db = DownloadDB()
    log.info("╔══════════════════════════════════════════════════════╗")
    log.info("║   VOID AXIOM — Scraper CONTINUO v2.2               ║")
    log.info("║   Solo fuentes CONFIRMADAS (sin errores)           ║")
    log.info("╚══════════════════════════════════════════════════════╝")
    log.info(f"  Destino: {KNOWLEDGE_BASE_DIR.resolve()}")
    ciclo = 0
    try:
        while True:
            ciclo += 1
            log.info(f"\n{'█' * 56}")
            log.info(f"  🔄 CICLO #{ciclo}")
            log.info(f"{'█' * 56}\n")
            try:
                nuevos = run_scan_cycle(db)
            except Exception as e:
                log.error(f"  Error: {e}")
                nuevos = 0
            if args.once: break
            if nuevos > 0:
                log.info(f"\n  ✅ {nuevos} PDFs. Siguiente inmediatamente...\n")
                continue
            log.info(f"\n  ⏳ Esperando {interval//60} min...")
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("\n  🛑 Detenido.")
    finally:
        try: stats = db.get_stats()
        except: stats = {"total_pdfs": 0, "total_mb": 0}
        db.close()
        log.info(f"\n TOTAL: {stats['total_pdfs']} PDFs ({stats['total_mb']} MB)")

if __name__ == "__main__":
    main()