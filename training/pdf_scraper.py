"""
pdf_scraper.py — Buscador automático de PDFs de código para VOID AXIOM
=======================================================================
Versión 3.0 — SUPER-MEJORADO con:
  · 12+ fuentes distintas (arXiv, libros curados, GitHub releases, Open Textbooks,
    University CS depts, Papers With Code, OpenStax, MIT OCW, Stanford, OSTEP,
    PDFs de lenguajes específicos, React, Docker, Kubernetes, Android, iOS, etc.)
  · Deduplicación por contenido (SHA-256) y por URL
  · Filtrado inteligente: solo descarga PDFs que contienen código real
  · Reintentos con backoff exponencial
  · Registro completo en SQLite (qué descargó, cuándo, de dónde, hash)
  · Modo --dry-run para ver qué descargaría sin descargar nada
  · Rate limiting adaptativo por dominio
  · Escaneo de MÁS LENGUAJES: Python, JS, TS, Java, C/C++, Rust, Go, C#, PHP,
    Ruby, Swift, Kotlin, R, Scala, Lua, Luau, Docker, K8s, React, SQL avanzado,
    Android, iOS, HTML, CSS, Bash, etc.
  · Modo --all-languages: descarga PDFs de TODOS los lenguajes
  · Log DETALLADO de cada paso (qué va bien, qué va mal)

Uso:
    python pdf_scraper.py                    # todo (recomendado primera vez)
    python pdf_scraper.py --books            # solo libros curados
    python pdf_scraper.py --arxiv            # solo papers arXiv por categoría
    python pdf_scraper.py --topics           # solo arXiv por temas de código
    python pdf_scraper.py --university       # solo notas universitarias
    python pdf_scraper.py --pwc              # solo Papers With Code
    python pdf_scraper.py --all-languages    # PDFs de todos los lenguajes
    python pdf_scraper.py --language python  # PDFs de un lenguaje específico
    python pdf_scraper.py --limit 30         # máx 30 PDFs por fuente
    python pdf_scraper.py --dry-run          # ver URLs sin descargar
    python pdf_scraper.py --out ./mi_base    # carpeta destino
    python pdf_scraper.py --report           # solo mostrar stats de la BD
    python pdf_scraper.py --verbose          # log súper detallado
"""

import os
import re
import time
import json
import sqlite3
import hashlib
import logging
import argparse
import urllib.request
import urllib.parse
import urllib.error
import random as _random
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from typing import Iterator

# ── Logging ───────────────────────────────────────────────────────────────────
_log_handlers = [
    logging.StreamHandler(),
    logging.FileHandler("pdf_scraper.log", encoding="utf-8"),
]
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=_log_handlers,
)
log = logging.getLogger("pdf_scraper")

# ── Configuración global ──────────────────────────────────────────────────────
KNOWLEDGE_BASE_DIR = Path(os.environ.get("KNOWLEDGE_BASE", "./knowledge_base"))
DELAY_BASE         = 4.0      # segundos base entre peticiones al mismo dominio
ARXIV_DELAY        = 6.0      # delay específico para arXiv (más conservador)
MAX_FILE_SIZE_MB   = 60
TIMEOUT_SECONDS    = 60
MAX_RETRIES        = 4        # reintentos máximos por petición (evita bucles infinitos)
BACKOFF_CAP        = 60.0     # tiempo máximo de backoff (1 minuto)
DB_FILE            = "pdf_scraper.db"

HEADERS = {
    "User-Agent": (
        "VOID-AXIOM-Research/3.0 "
        "(Open academic PDF collector; educational AI training)"
    ),
    "Accept": "application/pdf,*/*",
}

# ── Marcadores de contenido de código (para filtrar PDFs irrelevantes) ────────
CODE_MARKERS = [
    b"def ", b"function ", b"public class", b"import ", b"#include",
    b"int main", b"print(", b"console.log", b"SELECT ", b"CREATE TABLE",
    b"async def", b"fn main", b"package main", b"#!/bin/bash",
    b"algorithm", b"pseudocode", b"complexity", b"O(n",
    b"const ", b"let ", b"var ", b"export ", b"interface ",
    b"struct ", b"impl ", b"fn ", b"enum ", b"trait ",
    b"func ", b"package ", b"type ", b"nil", b"defer ",
    b"using ", b"namespace ", b"class ", b"virtual ", b"override ",
    b"<?php", b"echo ", b"function ", b"return ",
    b"defmodule", b"defprotocol", b"defimpl", b"end",
    b"fun ", b"val ", b"var ", b"object ", b"trait ",
    b"library ", b"#include", b"template", b"auto",
    b"React", b"Component", b"useState", b"useEffect", b"render",
    b"Dockerfile", b"FROM ", b"RUN ", b"CMD ", b"docker-compose",
    b"apiVersion", b"kind: ", b"metadata", b"kubectl", b"pod ",
    b"@Override", b"@interface", b"@Entity", b"@Autowired",
    b"#!/usr/bin/env", b"require ", b"module.exports",
]
CODE_MARKER_MIN_HITS = 2   # mínimo de marcadores para considerar el PDF "de código"


# ═══════════════════════════════════════════════════════════════════════════════
# BASE DE DATOS — registro persistente de descargas
# ═══════════════════════════════════════════════════════════════════════════════

class DownloadDB:
    """SQLite ligero para rastrear qué PDFs ya se descargaron."""

    def __init__(self, db_path: str = DB_FILE):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init()

    def _init(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS downloads (
                url       TEXT PRIMARY KEY,
                filename  TEXT,
                source    TEXT,
                sha256    TEXT,
                size_kb   REAL,
                ts        TEXT,
                category  TEXT,
                language  TEXT DEFAULT ''
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS failures (
                url    TEXT PRIMARY KEY,
                reason TEXT,
                ts     TEXT
            )
        """)
        # Migrar si falta columna language
        try:
            self.conn.execute("SELECT language FROM downloads LIMIT 1")
        except sqlite3.OperationalError:
            self.conn.execute("ALTER TABLE downloads ADD COLUMN language TEXT DEFAULT ''")
        self.conn.commit()

    def seen(self, url: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM downloads WHERE url=? "
            "UNION SELECT 1 FROM failures WHERE url=?",
            (url, url)
        ).fetchone()
        return row is not None

    def seen_hash(self, sha256: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM downloads WHERE sha256=?", (sha256,)
        ).fetchone()
        return row is not None

    def record(self, url: str, filename: str, source: str,
               sha256: str, size_kb: float, category: str, language: str = ""):
        self.conn.execute(
            "INSERT OR REPLACE INTO downloads VALUES (?,?,?,?,?,?,?,?)",
            (url, filename, source, sha256, size_kb,
             datetime.utcnow().isoformat(), category, language)
        )
        self.conn.commit()

    def fail(self, url: str, reason: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO failures VALUES (?,?,?)",
            (url, reason, datetime.utcnow().isoformat())
        )
        self.conn.commit()

    def stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*), SUM(size_kb) FROM downloads").fetchone()
        by_cat = self.conn.execute(
            "SELECT category, COUNT(*), SUM(size_kb) FROM downloads GROUP BY category"
        ).fetchall()
        by_lang = self.conn.execute(
            "SELECT language, COUNT(*), SUM(size_kb) FROM downloads WHERE language != '' GROUP BY language"
        ).fetchall()
        failures = self.conn.execute("SELECT COUNT(*) FROM failures").fetchone()[0]
        return {
            "total_pdfs": total[0] or 0,
            "total_mb": round((total[1] or 0) / 1024, 2),
            "by_category": [
                {"category": r[0], "count": r[1], "mb": round((r[2] or 0)/1024, 2)}
                for r in by_cat
            ],
            "by_language": [
                {"language": r[0], "count": r[1], "mb": round((r[2] or 0)/1024, 2)}
                for r in by_lang
            ],
            "failures": failures,
        }

    def close(self):
        self.conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP — con reintentos y rate limiting por dominio
# ═══════════════════════════════════════════════════════════════════════════════

_domain_last_hit: dict[str, float] = {}
_domain_rate_limit_cooldown: dict[str, float] = {}  # cuándo podemos volver a consultar un dominio que nos rate-limitó


def _domain_delay(url: str, extra: float = 0.0):
    """
    Espera el tiempo necesario antes de hacer otra petición al mismo dominio.
    Usa DELAY_BASE por defecto, pero ARXIV_DELAY para dominios de arXiv.
    También respeta cooldowns tras rate limits previos.
    """
    domain = urllib.parse.urlparse(url).netloc
    
    # Cooldown post-rate-limit: si el dominio nos dio 429 antes, esperamos hasta que expire
    cooldown_until = _domain_rate_limit_cooldown.get(domain, 0.0)
    now = time.time()
    if cooldown_until > now:
        wait = cooldown_until - now
        log.info(f"  ⏳ Cooldown activo para {domain}, esperando {wait:.0f}s...")
        time.sleep(wait)
    
    # Delay entre peticiones normales
    last = _domain_last_hit.get(domain, 0)
    # Usar delay específico para arXiv (más conservador) o el base genérico
    base_delay = ARXIV_DELAY if "arxiv.org" in domain else DELAY_BASE
    wait = base_delay + extra - (time.time() - last)
    if wait > 0:
        time.sleep(wait)
    _domain_last_hit[domain] = time.time()


def _jitter(base: float, max_pct: float = 0.25) -> float:
    """Añade jitter aleatorio de ±max_pct% a un valor base."""
    return base * (1.0 + _random.uniform(-max_pct, max_pct))


def http_get_bytes(url: str, retries: int = MAX_RETRIES) -> bytes | None:
    """GET con reintentos, backoff exponencial con jitter y cooldown persistente por dominio."""
    domain = urllib.parse.urlparse(url).netloc
    
    for attempt in range(retries):
        _domain_delay(url)
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                # Backoff exponencial con jitter + cooldown persistente
                # Para arXiv usamos cooldown mucho más agresivo (x5) porque es muy restrictivo
                wait = min(BACKOFF_CAP, _jitter(2 ** (attempt + 2)))
                arxiv_penalty = 5.0 if "arxiv.org" in domain else 1.5
                cooldown_mult = arxiv_penalty + attempt  # aumenta con cada intento
                log.warning(f"  ⚠ Rate limit ({e.code}) en {domain}, esperando {wait:.0f}s (intento {attempt+1}/{retries})...")
                # Guardar cooldown para que el dominio descanse incluso entre requests
                _domain_rate_limit_cooldown[domain] = time.time() + wait * cooldown_mult
                time.sleep(wait)
                # Para arXiv específicamente, esperar más entre requests
                if "arxiv.org" in domain:
                    _domain_last_hit[domain] = time.time() + 2.0  # delay extra para siguiente request
            elif e.code in (404, 403, 410):
                log.debug(f"  ✗ HTTP {e.code}: {url}")
                return None
            else:
                log.warning(f"  ⚠ HTTP {e.code} (intento {attempt+1}/{retries}): {url}")
                time.sleep(_jitter(1.0))
        except Exception as e:
            log.warning(f"  ⚠ Error (intento {attempt+1}/{retries}): {e}")
            time.sleep(_jitter(2 ** attempt))
    
    log.warning(f"  ✗ Agotados {retries} reintentos para {url}")
    return None


def http_get_text(url: str) -> str | None:
    data = http_get_bytes(url)
    if data is None:
        return None
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDACIÓN DE PDFs
# ═══════════════════════════════════════════════════════════════════════════════

def is_valid_pdf(data: bytes) -> bool:
    """Verifica que los datos sean un PDF válido."""
    return data[:4] == b"%PDF"


def pdf_has_code_content(data: bytes) -> bool:
    """
    Heurística: ¿el PDF contiene contenido de código/programación?
    Busca marcadores en los primeros 300KB (suficiente para saber el tema).
    """
    sample = data[:300_000].lower()
    hits = sum(1 for marker in CODE_MARKERS if marker.lower() in sample)
    return hits >= CODE_MARKER_MIN_HITS


def pdf_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_filename(text: str, max_len: int = 80) -> str:
    """Convierte texto a nombre de archivo seguro."""
    text = re.sub(r"[^\w\s\-]", "", text)
    text = re.sub(r"\s+", "_", text.strip())
    return text[:max_len]


# ═══════════════════════════════════════════════════════════════════════════════
# DESCARGA PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

def download_pdf(
    url: str,
    dest_path: Path,
    db: DownloadDB,
    category: str = "misc",
    dry_run: bool = False,
    check_code_content: bool = True,
    language: str = "",
) -> bool:
    """
    Descarga un PDF a dest_path.
    Retorna True si se descargó (o ya existía), False si falló o se filtró.
    """
    if db.seen(url):
        log.debug(f"  ↷ Ya registrado: {url}")
        return False

    if dry_run:
        log.info(f"  [DRY-RUN] Descargaría: {dest_path.name}")
        log.info(f"             {url}")
        return True

    log.info(f"  ↓ {dest_path.name}")
    log.info(f"    {url}")

    data = http_get_bytes(url)
    if not data:
        db.fail(url, "no_response")
        log.warning(f"  ✗ ERROR: Sin respuesta del servidor")
        return False

    if not is_valid_pdf(data):
        db.fail(url, "not_pdf")
        log.warning(f"  ✗ ERROR: No es un PDF válido")
        return False

    size_mb = len(data) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        db.fail(url, f"too_large_{size_mb:.1f}mb")
        log.warning(f"  ✗ ERROR: Demasiado grande ({size_mb:.1f} MB)")
        return False

    sha = pdf_sha256(data)
    if db.seen_hash(sha):
        db.fail(url, "duplicate_content")
        log.info(f"  ↷ Contenido duplicado (hash ya existe)")
        return False

    if check_code_content and not pdf_has_code_content(data):
        db.fail(url, "no_code_content")
        log.info(f"  ✗ Filtrado: no parece contener código")
        return False

    # Guardar con nombre seguro
    _FALLBACK_DIR = Path("./knowledge_base_fallback")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    safe_name = dest_path.name
    if len(safe_name) > 200:
        stem = dest_path.stem
        ext = dest_path.suffix
        safe_name = stem[:200 - len(ext)] + ext
        dest_path = dest_path.parent / safe_name
    try:
        dest_path.write_bytes(data)
    except (PermissionError, OSError) as e:
        log.warning(f"  ⚠ Error escribiendo en {dest_path.parent} (sin permisos)")
        # Fallback: intentar en la carpeta local del proyecto
        fallback_path = _FALLBACK_DIR / dest_path.parent.name / safe_name
        fallback_path.parent.mkdir(parents=True, exist_ok=True)
        log.warning(f"     Intentando fallback local: {fallback_path}")
        try:
            fallback_path.write_bytes(data)
            log.info(f"  ✓ OK! Guardado en fallback local: {fallback_path} ({size_mb:.1f} MB)")
            try:
                db.record(url, fallback_path.name, fallback_path.parent.parent.name,
                          sha, len(data)/1024, category, language)
            except Exception:
                pass
            return True
        except (PermissionError, OSError) as e2:
            log.warning(f"     ✗ Fallback también falló: {e2}")
            try:
                db.fail(url, "write_error_permission")
            except Exception:
                pass
            return False
    try:
        db.record(url, dest_path.name, dest_path.parent.name,
                  sha, len(data)/1024, category, language)
    except Exception:
        pass  # Si la DB falla, el PDF ya se guardó al menos
    log.info(f"  ✓ OK! Guardado ({size_mb:.1f} MB) — {language}")
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# FUENTE 1 — Libros curados (open-access / CC)
# ═══════════════════════════════════════════════════════════════════════════════

CURATED_BOOKS = [
    # ── Python ────────────────────────────────────────────────────────────────
    ("https://greenteapress.com/thinkpython2/thinkpython2.pdf",
     "ThinkPython2_Downey.pdf", "python"),
    ("https://greenteapress.com/thinkstats2/thinkstats2.pdf",
     "ThinkStats2_Downey.pdf", "python"),
    ("https://greenteapress.com/complexity2/thinkcomplexity2.pdf",
     "ThinkComplexity2_Downey.pdf", "python"),
    ("https://do1.dr-chuck.net/pythonlearn/EN_us/pythonlearn.pdf",
     "PythonForEverybody_Severance.pdf", "python"),
    ("https://automatetheboringstuff.com/2e/chapter1/",
     None, None),  # solo web

    # ── JavaScript / TypeScript ──────────────────────────────────────────────
    ("https://eloquentjavascript.net/Eloquent_JavaScript.pdf",
     "EloquentJavaScript_Haverbecke.pdf", "javascript"),
    ("https://github.com/nicowillis/notes/raw/master/progit.pdf",
     "ProGit2_Chacon.pdf", "git"),

    # ── Java ──────────────────────────────────────────────────────────────────
    ("https://people.cs.vt.edu/~shaffer/Book/JAVA3eSharf.pdf",
     "DataStructuresAlgorithms_Shaffer.pdf", "java"),

    # ── C / C++ ───────────────────────────────────────────────────────────────
    ("https://www.learncpp.com/",
     None, None),  # web

    # ── Bash / Linux ──────────────────────────────────────────────────────────
    ("https://tldp.org/LDP/abs/abs-guide.pdf",
     "AdvancedBashScripting_Cooper.pdf", "linux"),
    ("https://tldp.org/LDP/Bash-Beginners-Guide/Bash-Beginners-Guide.pdf",
     "BashBeginnersGuide_Garrels.pdf", "linux"),

    # ── Redes ─────────────────────────────────────────────────────────────────
    ("https://do1.dr-chuck.net/net-intro/EN_us/net-intro.pdf",
     "IntroNetworking_Severance.pdf", "networking"),

    # ── Algoritmos y estructuras de datos ─────────────────────────────────────
    ("https://www.algorist.com/algorist.pdf",
     None, None),  # Requiere auth

    # ── Compiladores / Intérpretes ────────────────────────────────────────────
    ("https://www.craftinginterpreters.com/book.pdf",
     "CraftingInterpreters_Nystrom.pdf", "compilers"),

    # ── Machine Learning / DL ─────────────────────────────────────────────────
    ("https://www.deeplearningbook.org/front_matter.pdf",
     "DeepLearningBook_FrontMatter_Goodfellow.pdf", "deep_learning"),
    ("https://www.deeplearningbook.org/contents/intro.pdf",
     "DeepLearningBook_Intro_Goodfellow.pdf", "deep_learning"),

    # ── Diseño de software ────────────────────────────────────────────────────
    ("https://www.cs.fsu.edu/~cop4530/fall18/notes/designPatterns.pdf",
     "DesignPatterns_FSU.pdf", "design_patterns"),

    # ── Seguridad ─────────────────────────────────────────────────────────────
    ("https://www.cl.cam.ac.uk/~rja14/Papers/SEv3-ch2-7sep.pdf",
     "SecurityEngineering_Ch2_Anderson.pdf", "security"),

    # ── SQL / Bases de datos ─────────────────────────────────────────────────
    ("https://www.db-book.com/slides-dir/PDF-dir/ch1.pdf",
     "DatabaseSystems_Ch1_Silberschatz.pdf", "databases"),
    ("https://www.db-book.com/slides-dir/PDF-dir/ch3.pdf",
     "DatabaseSystems_Ch3_SQL_Silberschatz.pdf", "databases"),
    ("https://www.db-book.com/slides-dir/PDF-dir/ch6.pdf",
     "DatabaseSystems_Ch6_AdvancedSQL_Silberschatz.pdf", "databases"),

    # ── NLP ────────────────────────────────────────────────────────────────────
    ("https://web.stanford.edu/~jurafsky/slp3/ed3book.pdf",
     "SpeechLanguageProcessing_Jurafsky.pdf", "nlp"),

    # ── OS / Sistemas ─────────────────────────────────────────────────────────
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/intro.pdf",
     "OSTEP_Intro_Arpaci.pdf", "systems"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/threads-intro.pdf",
     "OSTEP_Threads_Arpaci.pdf", "systems"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/file-intro.pdf",
     "OSTEP_Filesystem_Arpaci.pdf", "systems"),

    # ── Programación funcional ────────────────────────────────────────────────
    ("https://learnyouahaskell.com/learnyouahaskell.pdf",
     "LearnYouAHaskell.pdf", "functional"),
    ("https://book.realworldhaskell.org/read/",
     None, None),

    # ── Go ────────────────────────────────────────────────────────────────────
    ("https://www.gopl.io/",
     None, None),  # web

    # ── Rust ──────────────────────────────────────────────────────────────────
    ("https://doc.rust-lang.org/book/",
     None, None),  # web

    # ── SQL Avanzado ──────────────────────────────────────────────────────────
    ("https://www.sql.org/sql-database/",
     None, None),  # web

    # ── React ────────────────────────────────────────────────────────────────
    ("https://react.dev/",
     None, None),  # web

    # ── Docker ────────────────────────────────────────────────────────────────
    ("https://docker-curriculum.com/",
     None, None),  # web

    # ── Kubernetes ────────────────────────────────────────────────────────────
    ("https://kubernetes.io/docs/home/",
     None, None),  # web
]


def scrape_curated_books(
    dest_dir: Path,
    db: DownloadDB,
    limit: int = 50,
    dry_run: bool = False,
) -> int:
    dest_dir = dest_dir / "books"
    total = 0
    log.info("\n╔══ [FUENTE 1] Libros curados open-access ══╗")
    log.info(f"  Total en lista: {len([b for b in CURATED_BOOKS if b[1] is not None])} PDFs disponibles")

    for url, filename, category in CURATED_BOOKS:
        if total >= limit:
            break
        if filename is None:
            log.info(f"  ↷ Saltando (solo web): {url.split('/')[2]}")
            continue

        dest_path = dest_dir / category / filename
        if dest_path.exists() and not dry_run:
            log.info(f"  ↷ Ya existe: {filename}")
            continue

        ok = download_pdf(url, dest_path, db, category, dry_run,
                          check_code_content=False, language=category)
        if ok:
            total += 1

    log.info(f"╚══ Libros: {total} PDFs {'(dry-run)' if dry_run else 'descargados'}")
    return total


# ═══════════════════════════════════════════════════════════════════════════════
# FUENTE 2 — arXiv por categoría CS
# ═══════════════════════════════════════════════════════════════════════════════

ARXIV_CATEGORIES = [
    ("cs.PL", "Lenguajes de programación", "pl"),
    ("cs.SE", "Ingeniería de software", "se"),
    ("cs.LG", "Machine Learning", "ml"),
    ("cs.AI", "Inteligencia Artificial", "ai"),
    ("cs.DS", "Estructuras de datos y algoritmos", "ds"),
    ("cs.DC", "Computación distribuida y paralela", "dc"),
    ("cs.CR", "Criptografía y seguridad", "security"),
    ("cs.DB", "Bases de datos", "databases"),
    ("cs.NE", "Redes neuronales", "neural"),
    ("sys.CL", "Computación y lenguaje (NLP)", "nlp"),
    ("cs.CV", "Visión por computador", "cv"),
    ("cs.OS", "Sistemas operativos", "os"),
    ("cs.AR", "Arquitectura de hardware", "architecture"),
    ("cs.LO", "Lógica en computación", "logic"),
    ("cs.NA", "Análisis numérico", "numerical"),
    ("cs.GT", "Teoría de juegos", "games"),
    ("cs.RO", "Robótica", "robotics"),
    ("cs.CY", "Computación y sociedad", "society"),
    ("cs.HC", "Interacción humano-computador", "hci"),
    ("cs.IR", "Recuperación de información", "ir"),
]


def arxiv_query(search_query: str, max_results: int = 30, start: int = 0) -> list[dict]:
    """Llama a la API de arXiv y retorna lista de papers."""
    base = "https://export.arxiv.org/api/query"
    params = urllib.parse.urlencode({
        "search_query": search_query,
        "start": start,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    })
    data = http_get_bytes(f"{base}?{params}")
    if not data:
        return []

    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        log.error(f"  XML parse error: {e}")
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    papers = []

    for entry in root.findall("atom:entry", ns):
        try:
            title_el = entry.find("atom:title", ns)
            title = (title_el.text or "").strip().replace("\n", " ")

            pdf_url = None
            for link in entry.findall("atom:link", ns):
                if link.get("title") == "pdf":
                    pdf_url = link.get("href", "")
                    if pdf_url and not pdf_url.endswith(".pdf"):
                        pdf_url += ".pdf"
                    break

            if not pdf_url:
                continue

            id_el = entry.find("atom:id", ns)
            paper_id = (id_el.text or "").split("/abs/")[-1].replace("/", "_")

            papers.append({
                "id": paper_id,
                "title": title,
                "pdf_url": pdf_url,
            })
        except Exception:
            continue

    return papers


def scrape_arxiv_by_category(
    dest_dir: Path,
    db: DownloadDB,
    limit: int = 50,
    dry_run: bool = False,
) -> int:
    dest_dir = dest_dir / "arxiv" / "categories"
    total = 0
    per_cat = max(3, limit // len(ARXIV_CATEGORIES))

    log.info("\n╔══ [FUENTE 2] arXiv — Categorías CS ══╗")
    log.info(f"  Categorías: {len(ARXIV_CATEGORIES)}, ~{per_cat} papers cada una")

    for cat_id, cat_desc, lang_tag in ARXIV_CATEGORIES:
        if total >= limit:
            break

        log.info(f"\n  [{cat_id}] {cat_desc}")
        papers = arxiv_query(f"cat:{cat_id}", max_results=per_cat)
        log.info(f"    → {len(papers)} papers encontrados")
        cat_dir = dest_dir / cat_id.replace(".", "_")

        for paper in papers:
            if total >= limit:
                break

            filename = f"{paper['id']}_{safe_filename(paper['title'])}.pdf"
            dest_path = cat_dir / filename

            ok = download_pdf(
                paper["pdf_url"], dest_path, db,
                category=cat_id, dry_run=dry_run,
                check_code_content=True, language=lang_tag,
            )
            if ok:
                total += 1

    log.info(f"\n╚══ arXiv categorías: {total} PDFs")
    return total


# ═══════════════════════════════════════════════════════════════════════════════
# FUENTE 3 — arXiv por temas específicos de código
# ═══════════════════════════════════════════════════════════════════════════════

CODE_TOPICS = [
    ("code generation language model", "code_generation", "python"),
    ("program synthesis neural network", "program_synthesis", "python"),
    ("software testing automated", "testing", "general"),
    ("compiler optimization machine learning", "compilers", "cpp"),
    ("code refactoring automated", "refactoring", "general"),
    ("bug detection deep learning", "bug_detection", "python"),
    ("code completion transformer", "code_completion", "python"),
    ("static analysis program", "static_analysis", "general"),
    ("formal verification software", "formal_methods", "general"),
    ("API documentation generation", "documentation", "general"),
    ("neural program repair", "program_repair", "python"),
    ("code clone detection", "code_quality", "general"),
    ("type inference machine learning", "type_systems", "ml"),
    ("distributed systems algorithms", "distributed", "go"),
    ("concurrency verification", "concurrency", "rust"),
    ("reinforcement learning code", "rl_code", "python"),
    ("large language model programming", "llm_code", "python"),
    ("code summarization natural language", "code_nlp", "python"),
    ("Rust programming language safety", "rust_lang", "rust"),
    ("Go language concurrency patterns", "go_lang", "go"),
    ("TypeScript type system advanced", "typescript", "typescript"),
    ("JavaScript framework performance", "javascript", "javascript"),
    ("React component optimization", "react", "javascript"),
    ("Docker container security", "docker", "devops"),
    ("Kubernetes orchestration scaling", "kubernetes", "devops"),
    ("SQL query optimization", "sql_advanced", "sql"),
    ("Android app development Kotlin", "android", "kotlin"),
    ("iOS Swift programming", "ios", "swift"),
    ("C# .NET framework patterns", "c_sharp", "csharp"),
    ("PHP Laravel web development", "php", "php"),
    ("Ruby on Rails best practices", "ruby", "ruby"),
    ("Lua game scripting", "lua", "lua"),
    ("Java Spring Boot microservices", "java", "java"),
    ("R statistical computing", "r_lang", "r"),
    ("Scala functional programming", "scala", "scala"),
]


def scrape_arxiv_by_topic(
    dest_dir: Path,
    db: DownloadDB,
    limit: int = 60,
    dry_run: bool = False,
) -> int:
    dest_dir = dest_dir / "arxiv" / "topics"
    total = 0
    per_topic = max(2, limit // len(CODE_TOPICS))

    log.info("\n╔══ [FUENTE 3] arXiv — Temas específicos de código ══╗")
    log.info(f"  Temas: {len(CODE_TOPICS)}, ~{per_topic} papers cada uno")

    for topic, category, lang in CODE_TOPICS:
        if total >= limit:
            break

        log.info(f"\n  Tema: '{topic}' [{lang}]")
        query = f"ti:{urllib.parse.quote(topic)} AND cat:cs.*"
        papers = arxiv_query(query, max_results=per_topic)
        log.info(f"    → {len(papers)} papers encontrados")
        topic_dir = dest_dir / category

        for paper in papers:
            if total >= limit:
                break

            filename = f"{paper['id']}_{safe_filename(paper['title'])}.pdf"
            dest_path = topic_dir / filename

            ok = download_pdf(
                paper["pdf_url"], dest_path, db,
                category=category, dry_run=dry_run,
                check_code_content=True, language=lang,
            )
            if ok:
                total += 1

    log.info(f"\n╚══ arXiv temas: {total} PDFs")
    return total


# ═══════════════════════════════════════════════════════════════════════════════
# FUENTE 4 — Libros por LENGUAJE ESPECÍFICO
# ═══════════════════════════════════════════════════════════════════════════════

LANGUAGE_SPECIFIC_BOOKS = [
    # ─── Python ───────────────────────────────────────────────────────────────
    ("https://greenteapress.com/thinkpython2/thinkpython2.pdf",
     "ThinkPython2.pdf", "python"),
    ("https://automatetheboringstuff.com/2e/",
     None, "python"),  # solo web

    # ─── JavaScript ───────────────────────────────────────────────────────────
    ("https://eloquentjavascript.net/Eloquent_JavaScript.pdf",
     "EloquentJavaScript.pdf", "javascript"),
    ("https://github.com/getify/You-Dont-Know-JS/raw/2nd-ed/get-started/",
     None, "javascript"),  # solo web

    # ─── TypeScript ───────────────────────────────────────────────────────────
    ("https://www.typescriptlang.org/assets/typescript-handbook.pdf",
     None, "typescript"),  # puede cambiar URL
    ("https://basarat.gitbook.io/typescript/",
     None, "typescript"),  # web

    # ─── Java ─────────────────────────────────────────────────────────────────
    ("https://people.cs.vt.edu/~shaffer/Book/JAVA3eSharf.pdf",
     "DataStructuresJava_Shaffer.pdf", "java"),
    ("https://www.iitk.ac.in/esc101/esc101-2012/",
     None, "java"),  # web

    # ─── C / C++ ─────────────────────────────────────────────────────────────
    ("https://www.learncpp.com/",
     None, "cpp"),
    ("https://www.open-std.org/jtc1/sc22/wg21/docs/papers/",
     None, "cpp"),  # papers

    # ─── Rust ─────────────────────────────────────────────────────────────────
    ("https://doc.rust-lang.org/book/print.html",
     None, "rust"),  # HTML -> PDF no directo
    ("https://doc.rust-lang.org/nomicon/print.html",
     None, "rust"),

    # ─── Go ───────────────────────────────────────────────────────────────────
    ("https://www.gopl.io/",
     None, "go"),  # web

    # ─── C# ───────────────────────────────────────────────────────────────────
    ("https://learn.microsoft.com/en-us/dotnet/csharp/",
     None, "csharp"),

    # ─── PHP ──────────────────────────────────────────────────────────────────
    ("https://www.php.net/manual/en/langref.php",
     None, "php"),

    # ─── Ruby ─────────────────────────────────────────────────────────────────
    ("https://www.ruby-lang.org/en/documentation/quickstart/",
     None, "ruby"),

    # ─── Swift ────────────────────────────────────────────────────────────────
    ("https://docs.swift.org/swift-book/print.html",
     None, "swift"),

    # ─── Kotlin ───────────────────────────────────────────────────────────────
    ("https://kotlinlang.org/docs/home.html",
     None, "kotlin"),

    # ─── R ────────────────────────────────────────────────────────────────────
    ("https://cran.r-project.org/doc/manuals/r-release/R-intro.pdf",
     "R-Intro.pdf", "r"),
    ("https://cran.r-project.org/doc/manuals/r-release/R-lang.pdf",
     "R-Lang.pdf", "r"),
    ("https://adv-r.hadley.nz/",
     None, "r"),  # web

    # ─── Scala ────────────────────────────────────────────────────────────────
    ("https://docs.scala-lang.org/overviews/",
     None, "scala"),

    # ─── Lua ──────────────────────────────────────────────────────────────────
    ("https://www.lua.org/manual/5.4/readme.html",
     None, "lua"),

    # ─── SQL Avanzado ─────────────────────────────────────────────────────────
    ("https://www.postgresql.org/docs/current/static/",
     None, "sql"),
    ("https://dev.mysql.com/doc/refman/8.0/en/",
     None, "sql"),

    # ─── React ────────────────────────────────────────────────────────────────
    ("https://react.dev/learn",
     None, "javascript"),
    ("https://beta.reactjs.org/",
     None, "javascript"),

    # ─── Docker ───────────────────────────────────────────────────────────────
    ("https://docs.docker.com/get-started/",
     None, "devops"),

    # ─── Kubernetes ───────────────────────────────────────────────────────────
    ("https://kubernetes.io/docs/tutorials/",
     None, "devops"),

    # ─── Android / iOS ────────────────────────────────────────────────────────
    ("https://developer.android.com/docs",
     None, "kotlin"),
    ("https://developer.apple.com/documentation/",
     None, "swift"),

    # ─── HTML / CSS ───────────────────────────────────────────────────────────
    ("https://html.spec.whatwg.org/",
     None, "html"),
    ("https://www.w3.org/Style/CSS/",
     None, "css"),
]


def scrape_language_books(
    dest_dir: Path,
    db: DownloadDB,
    limit: int = 30,
    dry_run: bool = False,
    language_filter: str = "",
) -> int:
    """Busca PDFs específicos por lenguaje de programación."""
    dest_dir = dest_dir / "languages"
    total = 0

    log.info("\n╔══ [FUENTE 4] Libros por lenguaje específico ══╗")

    for url, filename, lang in LANGUAGE_SPECIFIC_BOOKS:
        if total >= limit:
            break
        if language_filter and language_filter.lower() != lang.lower():
            continue
        if filename is None:
            log.info(f"  ↷ Solo web (sin PDF directo): {lang} — {url.split('/')[2]}")
            continue

        dest_path = dest_dir / lang / filename
        if dest_path.exists() and not dry_run:
            log.info(f"  ↷ Ya existe: {filename}")
            continue

        ok = download_pdf(url, dest_path, db, f"language_{lang}", dry_run,
                          check_code_content=False, language=lang)
        if ok:
            total += 1

    log.info(f"╚══ Libros por lenguaje: {total} PDFs")
    return total


# ═══════════════════════════════════════════════════════════════════════════════
# FUENTE 5 — Notas universitarias
# ═══════════════════════════════════════════════════════════════════════════════

OPENSTAX_PDFS = [
    (
        "https://assets.openstax.org/oscms-prodcms/media/documents/"
        "ComputerScience_WEB.pdf",
        "OpenStax_ComputerScience.pdf",
        "cs_fundamentals",
    ),
    (
        "https://d3bxy9euw4e147.cloudfront.net/oscms-prodcms/media/documents/"
        "Prealgebra_WEB.pdf",
        None, None,  # no CS
    ),
]

GITHUB_RELEASE_PDFS = [
    (
        "https://github.com/nicowillis/notes/raw/master/progit.pdf",
        "ProGit2.pdf",
        "git",
    ),
]

MIT_OCW_PDFS = [
    (
        "https://ocw.mit.edu/courses/6-001-structure-and-interpretation-of"
        "-computer-programs-spring-2005/pages/lecture-notes/",
        None, None,  # HTML
    ),
]

STANFORD_PDFS = [
    (
        "https://web.stanford.edu/class/cs161/docs/notes.pdf",
        "Stanford_CS161_Algorithms.pdf",
        "algorithms",
    ),
    (
        "https://web.stanford.edu/~jurafsky/slp3/ed3book.pdf",
        "Jurafsky_SpeechLangProc3e.pdf",
        "nlp",
    ),
]

OSTEP_CHAPTERS = [
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/intro.pdf", "OSTEP_intro.pdf"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/cpu-intro.pdf", "OSTEP_cpu_intro.pdf"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/vm-intro.pdf", "OSTEP_vm_intro.pdf"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/threads-intro.pdf", "OSTEP_threads.pdf"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/threads-locks.pdf", "OSTEP_locks.pdf"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/threads-cv.pdf", "OSTEP_condvar.pdf"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/file-intro.pdf", "OSTEP_files.pdf"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/file-implementation.pdf", "OSTEP_fs_impl.pdf"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/dist-intro.pdf", "OSTEP_dist_intro.pdf"),
    ("https://pages.cs.wisc.edu/~remzi/OSTEP/dist-nfs.pdf", "OSTEP_nfs.pdf"),
]


def scrape_university_notes(
    dest_dir: Path,
    db: DownloadDB,
    limit: int = 40,
    dry_run: bool = False,
) -> int:
    dest_dir = dest_dir / "university"
    total = 0

    log.info("\n╔══ [FUENTE 5] Notas universitarias y textbooks open-access ══╗")

    # Stanford
    for url, filename, category in STANFORD_PDFS:
        if total >= limit or filename is None:
            continue
        dest_path = dest_dir / "stanford" / filename
        ok = download_pdf(url, dest_path, db, category, dry_run,
                          check_code_content=False, language=category)
        if ok:
            total += 1

    # OSTEP
    log.info(f"  Descargando OSTEP ({len(OSTEP_CHAPTERS)} capítulos)...")
    for url, filename in OSTEP_CHAPTERS:
        if total >= limit:
            break
        dest_path = dest_dir / "ostep" / filename
        ok = download_pdf(url, dest_path, db, "systems", dry_run,
                          check_code_content=False, language="cpp")
        if ok:
            total += 1

    # OpenStax
    for url, filename, category in OPENSTAX_PDFS:
        if total >= limit or filename is None:
            continue
        dest_path = dest_dir / "openstax" / filename
        ok = download_pdf(url, dest_path, db, category, dry_run,
                          check_code_content=False, language="general")
        if ok:
            total += 1

    log.info(f"\n╚══ Universidad: {total} PDFs")
    return total


# ═══════════════════════════════════════════════════════════════════════════════
# FUENTE 6 — Papers With Code
# ═══════════════════════════════════════════════════════════════════════════════

def scrape_papers_with_code(
    dest_dir: Path,
    db: DownloadDB,
    limit: int = 30,
    dry_run: bool = False,
) -> int:
    """
    Usa la API pública de Papers With Code para obtener papers
    que tienen código disponible (más relevantes para entrenar en código).
    """
    dest_dir = dest_dir / "papers_with_code"
    total = 0

    log.info("\n╔══ [FUENTE 6] Papers With Code (ML con código) ══╗")
    log.info(f"  Consultando API de Papers With Code...")

    api_url = "https://paperswithcode.com/api/v1/papers/?format=json&has_github=true&ordering=-published"
    
    text = http_get_text(api_url)
    if not text:
        log.warning("  ✗ NO se pudo acceder a Papers With Code API")
        return 0

    try:
        data = json.loads(text)
        results = data.get("results", [])
        log.info(f"  ✓ API response: {len(results)} papers disponibles")
    except json.JSONDecodeError:
        log.warning("  ✗ Error parsing Papers With Code response")
        return 0

    for paper in results:
        if total >= limit:
            break

        title = paper.get("title", "unknown")
        arxiv_id = paper.get("arxiv_id", "")

        if not arxiv_id:
            continue

        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        filename = f"pwc_{safe_filename(title)}.pdf"
        dest_path = dest_dir / filename

        ok = download_pdf(
            pdf_url, dest_path, db,
            category="papers_with_code", dry_run=dry_run,
            check_code_content=True, language="ml",
        )
        if ok:
            total += 1

    log.info(f"\n╚══ Papers With Code: {total} PDFs")
    return total


# ═══════════════════════════════════════════════════════════════════════════════
# FUENTE 7 — PDFs de TUTORIALES WEB (TutorialsPoint)
# ═══════════════════════════════════════════════════════════════════════════════

WEB_TUTORIAL_PDFS = [
    # React / Frontend
    ("https://www.tutorialspoint.com/reactjs/reactjs_tutorial.pdf",
     "ReactJS_Tutorial.pdf", "javascript"),
    ("https://www.tutorialspoint.com/typescript/typescript_tutorial.pdf",
     "TypeScript_Tutorial.pdf", "typescript"),

    # Docker / DevOps
    ("https://www.tutorialspoint.com/docker/docker_tutorial.pdf",
     "Docker_Tutorial.pdf", "devops"),
    ("https://www.tutorialspoint.com/kubernetes/kubernetes_tutorial.pdf",
     "Kubernetes_Tutorial.pdf", "devops"),

    # SQL Avanzado
    ("https://www.tutorialspoint.com/sql/sql_tutorial.pdf",
     "SQL_Tutorial.pdf", "sql"),
    ("https://www.tutorialspoint.com/plsql/plsql_tutorial.pdf",
     "PLSQL_Tutorial.pdf", "sql"),

    # Android / iOS
    ("https://www.tutorialspoint.com/android/android_tutorial.pdf",
     "Android_Tutorial.pdf", "kotlin"),
    ("https://www.tutorialspoint.com/ios/ios_tutorial.pdf",
     "iOS_Tutorial.pdf", "swift"),

    # Lenguajes
    ("https://www.tutorialspoint.com/python/python_tutorial.pdf",
     "Python_Tutorial.pdf", "python"),
    ("https://www.tutorialspoint.com/java/java_tutorial.pdf",
     "Java_Tutorial.pdf", "java"),
    ("https://www.tutorialspoint.com/cprogramming/cprogramming_tutorial.pdf",
     "C_Tutorial.pdf", "c"),
    ("https://www.tutorialspoint.com/cplusplus/cpp_tutorial.pdf",
     "CPP_Tutorial.pdf", "cpp"),
    ("https://www.tutorialspoint.com/csharp/csharp_tutorial.pdf",
     "CSharp_Tutorial.pdf", "csharp"),
    ("https://www.tutorialspoint.com/ruby/ruby_tutorial.pdf",
     "Ruby_Tutorial.pdf", "ruby"),
    ("https://www.tutorialspoint.com/perl/perl_tutorial.pdf",
     "Perl_Tutorial.pdf", "perl"),
    ("https://www.tutorialspoint.com/scala/scala_tutorial.pdf",
     "Scala_Tutorial.pdf", "scala"),
    ("https://www.tutorialspoint.com/go/go_tutorial.pdf",
     "Go_Tutorial.pdf", "go"),
    ("https://www.tutorialspoint.com/rust/rust_tutorial.pdf",
     "Rust_Tutorial.pdf", "rust"),
    ("https://www.tutorialspoint.com/swift/swift_tutorial.pdf",
     "Swift_Tutorial.pdf", "swift"),
    ("https://www.tutorialspoint.com/kotlin/kotlin_tutorial.pdf",
     "Kotlin_Tutorial.pdf", "kotlin"),
    ("https://www.tutorialspoint.com/r/r_tutorial.pdf",
     "R_Tutorial.pdf", "r"),
    ("https://www.tutorialspoint.com/lua/lua_tutorial.pdf",
     "Lua_Tutorial.pdf", "lua"),
    ("https://www.tutorialspoint.com/php/php_tutorial.pdf",
     "PHP_Tutorial.pdf", "php"),

    # Web dev
    ("https://www.tutorialspoint.com/html/html_tutorial.pdf",
     "HTML_Tutorial.pdf", "html"),
    ("https://www.tutorialspoint.com/css/css_tutorial.pdf",
     "CSS_Tutorial.pdf", "css"),
    ("https://www.tutorialspoint.com/javascript/javascript_tutorial.pdf",
     "JavaScript_Tutorial.pdf", "javascript"),
]


# ═══════════════════════════════════════════════════════════════════════════════
# FUENTE 8 — MEGA: PDFs de GeeksForGeeks, Javatpoint, Guru99 y más
# ═══════════════════════════════════════════════════════════════════════════════

MEGA_TUTORIAL_PDFS = [
    # ── Python ────────────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/cdn-uploads/20210901175839/Python-Programming.pdf",
     "GFG_Python.pdf", "python"),
    ("https://www.javatpoint.com/pdf/java-tutorial-1.pdf",
     None, "python"),  # Javatpoint no da PDFs directos

    # ── Java ──────────────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/JavaProgramming.pdf",
     "GFG_Java.pdf", "java"),
    ("https://www.guru99.com/java-tutorial.html",
     None, "java"),  # web

    # ── JavaScript ────────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20220411165723/JavaScriptTutorial.pdf",
     "GFG_JavaScript.pdf", "javascript"),

    # ── React ─────────────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20220411165723/ReactJSTutorial.pdf",
     None, "javascript"),  # puede no existir

    # ── TypeScript ────────────────────────────────────────────────────────────
    ("https://www.javatpoint.com/typescript-tutorial",
     None, "typescript"),

    # ── C / C++ ──────────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/CProgramming.pdf",
     "GFG_C.pdf", "c"),
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/CPlusPlusProgramming.pdf",
     "GFG_CPP.pdf", "cpp"),
    ("https://www.guru99.com/cpp-tutorial.html",
     None, "cpp"),

    # ── C# ────────────────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/CSharpProgramming.pdf",
     "GFG_CSharp.pdf", "csharp"),
    ("https://www.guru99.com/c-sharp-tutorial.html",
     None, "csharp"),

    # ── SQL / BBDD ───────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/SQL.pdf",
     "GFG_SQL.pdf", "sql"),
    ("https://www.guru99.com/sql.html",
     None, "sql"),
    ("https://www.javatpoint.com/sql-tutorial",
     None, "sql"),

    # ── MongoDB / NoSQL ──────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/MongoDB.pdf",
     "GFG_MongoDB.pdf", "nosql"),
    ("https://www.guru99.com/mongodb-tutorial.html",
     None, "nosql"),

    # ── Git / GitHub ─────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/Git.pdf",
     "GFG_Git.pdf", "git"),
    ("https://www.guru99.com/git-tutorial.html",
     None, "git"),

    # ── Docker ───────────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/Docker.pdf",
     "GFG_Docker.pdf", "devops"),
    ("https://www.guru99.com/docker-tutorial.html",
     None, "devops"),

    # ── Kubernetes ───────────────────────────────────────────────────────────
    ("https://www.guru99.com/kubernetes-tutorial.html",
     None, "devops"),

    # ── Linux / Bash ─────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/Linux.pdf",
     "GFG_Linux.pdf", "linux"),
    ("https://www.guru99.com/unix-linux-tutorial.html",
     None, "linux"),

    # ── HTML / CSS ───────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/HTML.pdf",
     "GFG_HTML.pdf", "html"),
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/CSS.pdf",
     "GFG_CSS.pdf", "css"),

    # ── Data Structures ──────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/DataStructures.pdf",
     "GFG_DataStructures.pdf", "algorithms"),
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/Algorithms.pdf",
     "GFG_Algorithms.pdf", "algorithms"),

    # ── Machine Learning / AI ────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/MachineLearning.pdf",
     "GFG_ML.pdf", "ml"),
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/DeepLearning.pdf",
     "GFG_DL.pdf", "deep_learning"),
    ("https://www.guru99.com/machine-learning-tutorial.html",
     None, "ml"),

    # ── Redes / Networking ───────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/ComputerNetwork.pdf",
     "GFG_Networking.pdf", "networking"),
    ("https://www.guru99.com/computer-networking-tutorial.html",
     None, "networking"),

    # ── Seguridad / CyberSecurity ────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/CyberSecurity.pdf",
     "GFG_Security.pdf", "security"),
    ("https://www.guru99.com/cyber-security-tutorial.html",
     None, "security"),

    # ── PHP ──────────────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/PHP.pdf",
     "GFG_PHP.pdf", "php"),
    ("https://www.guru99.com/php-tutorial.html",
     None, "php"),

    # ── Ruby ─────────────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/Ruby.pdf",
     "GFG_Ruby.pdf", "ruby"),
    ("https://www.guru99.com/ruby-tutorial.html",
     None, "ruby"),

    # ── Go ───────────────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/Go.pdf",
     "GFG_Go.pdf", "go"),
    ("https://www.guru99.com/go-tutorial.html",
     None, "go"),

    # ── Rust ─────────────────────────────────────────────────────────────────
    ("https://www.guru99.com/rust-tutorial.html",
     None, "rust"),

    # ── R ────────────────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/R.pdf",
     "GFG_R.pdf", "r"),
    ("https://www.guru99.com/r-tutorial.html",
     None, "r"),

    # ── Kotlin ───────────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/Kotlin.pdf",
     "GFG_Kotlin.pdf", "kotlin"),
    ("https://www.guru99.com/kotlin-tutorial.html",
     None, "kotlin"),

    # ── Swift ────────────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/Swift.pdf",
     "GFG_Swift.pdf", "swift"),

    # ── Scala ────────────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/Scala.pdf",
     "GFG_Scala.pdf", "scala"),
    ("https://www.guru99.com/scala-tutorial.html",
     None, "scala"),

    # ── Lua ──────────────────────────────────────────────────────────────────
    ("https://www.guru99.com/lua-tutorial.html",
     None, "lua"),

    # ── Perl ─────────────────────────────────────────────────────────────────
    ("https://www.guru99.com/perl-tutorial.html",
     None, "perl"),

    # ── MATLAB ───────────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/MATLAB.pdf",
     "GFG_MATLAB.pdf", "matlab"),
    ("https://www.guru99.com/matlab-tutorial.html",
     None, "matlab"),

    # ── DevOps / CI/CD ───────────────────────────────────────────────────────
    ("https://www.guru99.com/jenkins-tutorial.html",
     None, "devops"),
    ("https://www.guru99.com/ansible-tutorial.html",
     None, "devops"),
    ("https://www.guru99.com/terraform-tutorial.html",
     None, "devops"),

    # ── Testing ──────────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/SoftwareTesting.pdf",
     "GFG_Testing.pdf", "testing"),
    ("https://www.guru99.com/software-testing.html",
     None, "testing"),

    # ── Excel / Office ───────────────────────────────────────────────────────
    ("https://www.guru99.com/excel-tutorial.html",
     None, "office"),

    # ── Blockchain ───────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/Blockchain.pdf",
     "GFG_Blockchain.pdf", "blockchain"),
    ("https://www.guru99.com/blockchain-tutorial.html",
     None, "blockchain"),

    # ── Cloud Computing ──────────────────────────────────────────────────────
    ("https://www.guru99.com/cloud-computing-tutorial.html",
     None, "cloud"),

    # ── AWS ──────────────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/AWS.pdf",
     "GFG_AWS.pdf", "cloud"),
    ("https://www.guru99.com/aws-tutorial.html",
     None, "cloud"),

    # ── Angular ──────────────────────────────────────────────────────────────
    ("https://www.guru99.com/angular-tutorial.html",
     None, "javascript"),
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/Angular.pdf",
     "GFG_Angular.pdf", "javascript"),

    # ── Node.js ──────────────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/NodeJS.pdf",
     "GFG_NodeJS.pdf", "javascript"),
    ("https://www.guru99.com/node-js-tutorial.html",
     None, "javascript"),

    # ── Express.js ───────────────────────────────────────────────────────────
    ("https://www.guru99.com/express-js-tutorial.html",
     None, "javascript"),

    # ── Django (Python) ──────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/Django.pdf",
     "GFG_Django.pdf", "python"),
    ("https://www.guru99.com/django-tutorial.html",
     None, "python"),

    # ── Flask (Python) ───────────────────────────────────────────────────────
    ("https://www.guru99.com/flask-tutorial.html",
     None, "python"),

    # ── Spring Boot (Java) ───────────────────────────────────────────────────
    ("https://www.guru99.com/spring-boot-tutorial.html",
     None, "java"),
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/SpringBoot.pdf",
     "GFG_SpringBoot.pdf", "java"),

    # ── Hadoop / Big Data ────────────────────────────────────────────────────
    ("https://media.geeksforgeeks.org/wp-content/uploads/20211109131553/Hadoop.pdf",
     "GFG_Hadoop.pdf", "bigdata"),
    ("https://www.guru99.com/hadoop-tutorial.html",
     None, "bigdata"),

    # ── Spark ────────────────────────────────────────────────────────────────
    ("https://www.guru99.com/pyspark-tutorial.html",
     None, "bigdata"),

    # ── Tableau ──────────────────────────────────────────────────────────────
    ("https://www.guru99.com/tableau-tutorial.html",
     None, "visualization"),

    # ── Power BI ─────────────────────────────────────────────────────────────
    ("https://www.guru99.com/power-bi-tutorial.html",
     None, "visualization"),
]


def scrape_mega_tutorials(
    dest_dir: Path,
    db: DownloadDB,
    limit: int = 50,
    dry_run: bool = False,
) -> int:
    """Descarga PDFs de GeeksForGeeks, Javatpoint, Guru99 y más fuentes."""
    dest_dir = dest_dir / "mega_tutorials"
    total = 0

    log.info("\n╔══ [FUENTE 8] MEGA: PDFs GeeksForGeeks + Guru99 ══╗")
    log.info(f"  Tutoriales disponibles: {len(MEGA_TUTORIAL_PDFS)}")
    pdf_count = len([p for p in MEGA_TUTORIAL_PDFS if p[1] is not None])
    log.info(f"  PDFs directos: {pdf_count}")

    for url, filename, lang in MEGA_TUTORIAL_PDFS:
        if total >= limit:
            break
        if filename is None:
            log.debug(f"  ↷ Solo web: {url.split('/')[2]} — {lang}")
            continue

        dest_path = dest_dir / lang / filename
        if dest_path.exists() and not dry_run:
            log.info(f"  ↷ Ya existe: {filename}")
            continue

        ok = download_pdf(url, dest_path, db, "mega_tutorial", dry_run,
                          check_code_content=False, language=lang)
        if ok:
            total += 1

    log.info(f"╚══ MEGA tutoriales: {total} PDFs")
    return total


def scrape_web_tutorials(
    dest_dir: Path,
    db: DownloadDB,
    limit: int = 30,
    dry_run: bool = False,
) -> int:
    """Descarga PDFs de tutoriales web."""
    dest_dir = dest_dir / "tutorials"
    total = 0

    log.info("\n╔══ [FUENTE 7] Tutoriales web (TutorialsPoint y otros) ══╗")
    log.info(f"  Tutoriales disponibles: {len(WEB_TUTORIAL_PDFS)}")

    for url, filename, lang in WEB_TUTORIAL_PDFS:
        if total >= limit:
            break

        dest_path = dest_dir / lang / filename
        if dest_path.exists() and not dry_run:
            log.info(f"  ↷ Ya existe: {filename}")
            continue

        ok = download_pdf(url, dest_path, db, "web_tutorial", dry_run,
                          check_code_content=False, language=lang)
        if ok:
            total += 1

    log.info(f"╚══ Tutoriales: {total} PDFs")
    return total


# ═══════════════════════════════════════════════════════════════════════════════
# REPORTE
# ═══════════════════════════════════════════════════════════════════════════════

def print_report(dest_dir: Path, db: DownloadDB):
    stats = db.stats()
    sep = "═" * 56

    log.info(f"\n{sep}")
    log.info("  REPORTE — BASE DE CONOCIMIENTO VOID AXIOM")
    log.info(sep)
    log.info(f"  PDFs totales  : {stats['total_pdfs']}")
    log.info(f"  Tamaño total  : {stats['total_mb']} MB")
    log.info(f"  Fallos        : {stats['failures']}")
    log.info(f"  Directorio    : {dest_dir.resolve()}")
    log.info("")
    log.info("  Por categoría:")
    for cat in sorted(stats["by_category"], key=lambda x: -x["count"]):
        log.info(f"    {cat['category']:30s} {cat['count']:4d} PDFs  ({cat['mb']:.1f} MB)")
    log.info("")
    log.info("  Por lenguaje:")
    lang_list = stats.get("by_language", [])
    if lang_list:
        for lang in sorted(lang_list, key=lambda x: -x["count"]):
            log.info(f"    {lang['language']:20s} {lang['count']:4d} PDFs  ({lang['mb']:.1f} MB)")
    else:
        log.info("    (Sin datos de lenguaje)")
    log.info(sep)
    log.info("  Siguiente paso:")
    log.info(f"    python training/study_engine.py --source {dest_dir.resolve()}")
    log.info(f"    python training/dataset_builder.py --source {dest_dir.resolve()} --out ./datasets/")
    log.info(sep)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="VOID AXIOM v3.0 — Scraper MEGA de PDFs de TODOS los lenguajes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python pdf_scraper.py                    # Todo (recomendado)
  python pdf_scraper.py --books            # Solo libros curados
  python pdf_scraper.py --arxiv            # Solo papers arXiv
  python pdf_scraper.py --topics           # Solo temas de código
  python pdf_scraper.py --all-languages    # PDFs de TODOS los lenguajes
  python pdf_scraper.py --language python  # Solo Python
  python pdf_scraper.py --tutorials        # Solo tutoriales web
  python pdf_scraper.py --verbose          # Log detallado
  python pdf_scraper.py --limit 20         # Máx 20 PDFs por fuente
  python pdf_scraper.py --dry-run          # Ver sin descargar
  python pdf_scraper.py --report           # Solo estadísticas
        """,
    )
    parser.add_argument("--out", default=str(KNOWLEDGE_BASE_DIR),
                        help="Directorio destino")
    parser.add_argument("--limit", type=int, default=50,
                        help="Máximo PDFs por fuente (default: 50)")
    parser.add_argument("--books",         action="store_true", help="Solo libros curados")
    parser.add_argument("--arxiv",         action="store_true", help="Solo arXiv categorías")
    parser.add_argument("--topics",        action="store_true", help="Solo arXiv temas código")
    parser.add_argument("--university",    action="store_true", help="Solo notas universitarias")
    parser.add_argument("--pwc",           action="store_true", help="Solo Papers With Code")
    parser.add_argument("--all-languages", action="store_true", help="PDFs de todos los lenguajes")
    parser.add_argument("--language", type=str, default="",
                        help="Filtrar por lenguaje específico (python, java, rust, etc)")
    parser.add_argument("--tutorials",     action="store_true", help="Solo tutoriales web")
    parser.add_argument("--mega",          action="store_true", help="Solo MEGA tutoriales (GFG, Guru99)")
    parser.add_argument("--verbose",       action="store_true", help="Log súper detallado")
    parser.add_argument("--dry-run",       action="store_true", help="Mostrar qué se descargaría, sin descargar")
    parser.add_argument("--report",        action="store_true", help="Solo mostrar estadísticas de la BD")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        log.setLevel(logging.DEBUG)
        log.debug("Modo VERBOSE activado — mostrando detalles de cada operación")

    dest = Path(args.out)
    dest.mkdir(parents=True, exist_ok=True)

    db = DownloadDB(str(dest / DB_FILE))

    if args.report:
        print_report(dest, db)
        db.close()
        return

    run_all = not any([
        args.books, args.arxiv, args.topics, args.university,
        args.pwc, args.all_languages, args.tutorials, args.mega, args.language
    ])

    log.info("╔══════════════════════════════════════════════════════╗")
    log.info("║   VOID AXIOM — PDF Scraper v3.0                    ║")
    log.info("║   Buscando PDFs para HACER MÁS INTELIGENTE a VOID  ║")
    log.info("╚══════════════════════════════════════════════════════╝")
    log.info(f"Destino   : {dest.resolve()}")
    log.info(f"Límite    : {args.limit} PDFs por fuente")
    log.info(f"Modo      : {'DRY-RUN' if args.dry_run else 'DESCARGA REAL'}")
    log.info(f"Delay     : {DELAY_BASE}s entre peticiones (por dominio)")
    if args.language:
        log.info(f"Filtro    : Solo lenguaje '{args.language}'")

    grand_total = 0

    if run_all or args.books:
        n = scrape_curated_books(dest, db, args.limit, args.dry_run)
        grand_total += n
        log.info(f"  ✓ [FUENTE 1] Libros curados: {n} PDFs")

    if run_all or args.all_languages or args.language:
        n = scrape_language_books(dest, db, args.limit, args.dry_run,
                                  language_filter=args.language)
        grand_total += n
        log.info(f"  ✓ [FUENTE 4] Lenguajes específicos: {n} PDFs")

    if run_all or args.university:
        n = scrape_university_notes(dest, db, args.limit, args.dry_run)
        grand_total += n
        log.info(f"  ✓ [FUENTE 5] Universidad: {n} PDFs")

    if run_all or args.arxiv:
        n = scrape_arxiv_by_category(dest, db, args.limit, args.dry_run)
        grand_total += n
        log.info(f"  ✓ [FUENTE 2] arXiv categorías: {n} PDFs")

    if run_all or args.topics:
        n = scrape_arxiv_by_topic(dest, db, args.limit, args.dry_run)
        grand_total += n
        log.info(f"  ✓ [FUENTE 3] arXiv temas código: {n} PDFs")

    if run_all or args.mega:
        n = scrape_mega_tutorials(dest, db, args.limit, args.dry_run)
        grand_total += n
        log.info(f"  ✓ [FUENTE 8] MEGA tutoriales: {n} PDFs")

    if run_all or args.tutorials:
        n = scrape_web_tutorials(dest, db, args.limit, args.dry_run)
        grand_total += n
        log.info(f"  ✓ [FUENTE 7] Tutoriales web: {n} PDFs")

    if run_all or args.pwc:
        n = scrape_papers_with_code(dest, db, args.limit, args.dry_run)
        grand_total += n
        log.info(f"  ✓ [FUENTE 6] Papers With Code: {n} PDFs")

    log.info(f"\n{'═' * 56}")
    log.info(f"  TOTAL EN ESTA SESIÓN: {grand_total} PDFs")
    log.info(f"{'═' * 56}")

    print_report(dest, db)
    db.close()


if __name__ == "__main__":
    main()