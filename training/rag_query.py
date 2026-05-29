"""
rag_query.py — Motor de consultas RAG en tiempo real v1.0
==========================================================
Recupera chunks semánticamente relevantes de ChromaDB e inyecta el contexto
en el prompt de Ollama. La búsqueda es vectorial (similitud coseno), no
por palabras clave, por lo que encuentra respuestas aunque la pregunta use
vocabulario diferente al del documento.

Flujo completo:
  Pregunta → embedding → ChromaDB (top-k chunks) → prompt con contexto → Ollama → respuesta

Requiere:
    pip install chromadb requests
    ollama pull nomic-embed-text   (mismo modelo que usó rag_indexer)
    python rag_indexer.py          (debe ejecutarse antes al menos una vez)

Uso:
    python rag_query.py "¿Cómo funciona el GIL de Python?"      # consulta directa
    python rag_query.py --interactive                            # chat interactivo
    python rag_query.py --top-k 8 --show-sources "threads"      # ver fuentes
    python rag_query.py --no-llm "sorting algorithms"           # solo retrieval
    python rag_query.py --category arxiv "neural networks"      # filtrar fuente
    python rag_query.py --model qwen2.5-coder:14b "SQL joins"   # modelo diferente
"""

import os, json, sys, logging, argparse
from pathlib import Path
import requests

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("rag_query")

# ── Config ───────────────────────────────────────────────────────────────────
RAG_STORE_DIR       = Path(os.environ.get("RAG_STORE", "./rag_store")).resolve()
OLLAMA_BASE_URL     = os.environ.get("VOID_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
DEFAULT_LLM_MODEL   = os.environ.get("VOID_MODEL", "void-axiom-32b")
DEFAULT_EMBED_MODEL = os.environ.get("VOID_EMBED_MODEL", "nomic-embed-text")
COLLECTION_NAME     = "void_axiom_kb"
DEFAULT_TOP_K       = 5

SYSTEM_PROMPT = (
    "Eres VOID AXIOM, un experto técnico en programación y ciencias de la computación. "
    "Cuando respondas, usa principalmente el contexto recuperado de la base de conocimiento. "
    "Si el contexto no cubre completamente la pregunta, complementa con tu conocimiento. "
    "Incluye código cuando sea útil. Menciona las fuentes relevantes."
)

# ── ChromaDB ─────────────────────────────────────────────────────────────────
try:
    import chromadb
    CHROMA_OK = True
except ImportError:
    CHROMA_OK = False
    log.warning("ChromaDB no instalado: pip install chromadb")

_embed_dim: int | None = None
_embed_model_active = DEFAULT_EMBED_MODEL


# ═══════════════════════════════════════════════════════════════════════════════
# EMBEDDINGS
# ═══════════════════════════════════════════════════════════════════════════════

def get_embedding(text: str) -> list[float] | None:
    """Embedding de texto vía Ollama. Mismo código que rag_indexer para coherencia."""
    global _embed_dim
    endpoints = [
        ("/api/embed",      {"model": _embed_model_active, "input": text}),
        ("/api/embeddings", {"model": _embed_model_active, "prompt": text}),
    ]
    for path, payload in endpoints:
        try:
            resp = requests.post(
                f"{OLLAMA_BASE_URL}{path}",
                json=payload,
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                raw = data.get("embeddings") or [data.get("embedding")]
                emb = raw[0] if raw else None
                if emb:
                    if _embed_dim is None:
                        _embed_dim = len(emb)
                    return emb
        except requests.exceptions.ConnectionError:
            log.error(f"  Ollama no accesible en {OLLAMA_BASE_URL}")
            return None
        except Exception:
            continue
    return None


class OllamaEmbeddingFunction:
    def __call__(self, input: list[str]) -> list[list[float]]:
        return [get_embedding(t) or [0.0] * (_embed_dim or 768) for t in input]


# ═══════════════════════════════════════════════════════════════════════════════
# GENERACIÓN CON OLLAMA (streaming)
# ═══════════════════════════════════════════════════════════════════════════════

def ollama_generate(
    prompt: str,
    model: str,
    system: str = "",
    stream: bool = True,
) -> str:
    """
    Envía un prompt a Ollama y retorna la respuesta.
    Con stream=True imprime en tiempo real mientras genera.
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={"model": model, "messages": messages, "stream": stream},
            timeout=600,
            stream=stream,
        )
        if resp.status_code != 200:
            log.error(f"  ✗ Ollama error {resp.status_code}: {resp.text[:200]}")
            return ""

        full = ""
        if stream:
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    token = data.get("message", {}).get("content", "")
                    print(token, end="", flush=True)
                    full += token
                    if data.get("done"):
                        print()   # salto de línea final
                        break
                except json.JSONDecodeError:
                    continue
        else:
            full = resp.json().get("message", {}).get("content", "")

        return full

    except requests.exceptions.ConnectionError:
        log.error(f"  ✗ Ollama no accesible en {OLLAMA_BASE_URL}")
        return ""
    except Exception as e:
        log.error(f"  ✗ Error en generación: {e}")
        return ""


# ═══════════════════════════════════════════════════════════════════════════════
# RAG QUERY ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class RAGQuery:
    def __init__(
        self,
        embed_model: str = DEFAULT_EMBED_MODEL,
        store: Path = RAG_STORE_DIR,
    ):
        if not CHROMA_OK:
            raise RuntimeError("ChromaDB no instalado: pip install chromadb")

        global _embed_model_active
        _embed_model_active = embed_model

        chroma_path = store / "chroma"
        if not chroma_path.exists():
            raise FileNotFoundError(
                f"ChromaDB no encontrado en {chroma_path}\n"
                f"Ejecuta primero: python rag_indexer.py"
            )

        self.client = chromadb.PersistentClient(path=str(chroma_path))

        try:
            self.collection = self.client.get_collection(
                name=COLLECTION_NAME,
                embedding_function=OllamaEmbeddingFunction(),
            )
        except Exception:
            raise RuntimeError(
                f"Colección '{COLLECTION_NAME}' no encontrada.\n"
                f"Ejecuta primero: python rag_indexer.py"
            )

        n = self.collection.count()
        log.info(f"  ✓ Colección cargada: {n:,} chunks indexados")

    # ── Recuperación semántica ──────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        category: str = "",
    ) -> list[dict]:
        """
        Recupera los chunks más relevantes semánticamente.
        Devuelve lista ordenada por score (mayor = más relevante).
        """
        query_emb = get_embedding(query)
        if query_emb is None:
            log.error("  ✗ No se pudo obtener embedding para la query")
            return []

        n_total = self.collection.count()
        if n_total == 0:
            log.warning("  ⚠ ChromaDB vacío — ejecuta rag_indexer.py primero")
            return []

        kwargs = dict(
            query_embeddings=[query_emb],
            n_results=min(top_k, n_total),
            include=["documents", "metadatas", "distances"],
        )
        if category:
            kwargs["where"] = {"category": category}

        try:
            results = self.collection.query(**kwargs)
        except Exception as e:
            log.error(f"  ✗ Error en ChromaDB query: {e}")
            return []

        chunks = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            chunks.append({
                "text":     doc,
                "source":   meta.get("source", ""),
                "filename": meta.get("filename", "?"),
                "category": meta.get("category", ""),
                "chunk_n":  meta.get("chunk_n", 0),
                "score":    round(1.0 - dist, 4),    # convertir distancia coseno a similitud
            })

        # Ordenar de mayor a menor similitud
        return sorted(chunks, key=lambda c: c["score"], reverse=True)

    # ── Query completa (retrieval + generación) ─────────────────────────────

    def query(
        self,
        question: str,
        model: str = DEFAULT_LLM_MODEL,
        top_k: int = DEFAULT_TOP_K,
        show_sources: bool = False,
        no_llm: bool = False,
        category: str = "",
    ) -> str:
        """
        Flujo RAG completo:
        1. Embede la pregunta
        2. Recupera top-k chunks de ChromaDB
        3. Construye prompt con contexto
        4. Genera respuesta con Ollama (streaming)
        """
        log.info(f"\n  🔍 Recuperando contexto para: {question[:90]}…")

        chunks = self.retrieve(question, top_k=top_k, category=category)

        # ── Mostrar fuentes ─────────────────────────────────────────────────
        if chunks:
            print(f"\n  📚 {len(chunks)} fuentes recuperadas:")
            for i, ch in enumerate(chunks, 1):
                print(f"  [{i}] {ch['filename']} | score={ch['score']:.3f} | cat={ch['category']}")
                if show_sources:
                    preview = ch["text"][:300].replace("\n", " ")
                    print(f"      ↳ {preview}…\n")
        else:
            print("  ⚠ No se encontraron chunks relevantes — respondiendo sin contexto RAG")

        if no_llm:
            return "\n\n---\n\n".join(c["text"] for c in chunks)

        # ── Construir prompt con contexto RAG ───────────────────────────────
        if chunks:
            context_sections = []
            for i, ch in enumerate(chunks, 1):
                context_sections.append(
                    f"[Fuente {i} — {ch['filename']} | relevancia: {ch['score']:.2f}]\n{ch['text']}"
                )
            context = "\n\n" + ("─" * 60) + "\n\n".join(context_sections)

            prompt = (
                f"CONTEXTO RECUPERADO DE LA BASE DE CONOCIMIENTO:\n"
                f"{context}\n\n"
                f"{'─' * 60}\n\n"
                f"PREGUNTA: {question}\n\n"
                f"Responde técnicamente, citando las fuentes cuando uses "
                f"información específica del contexto."
            )
        else:
            # Sin contexto RAG — respuesta directa del modelo
            prompt = question

        # ── Generación ──────────────────────────────────────────────────────
        print(f"\n  🤖 Generando con {model}…\n")
        print("─" * 60)
        response = ollama_generate(prompt, model=model, system=SYSTEM_PROMPT, stream=True)
        print("─" * 60)

        return response

    # ── Modo interactivo ────────────────────────────────────────────────────

    def interactive(
        self,
        model: str = DEFAULT_LLM_MODEL,
        top_k: int = DEFAULT_TOP_K,
    ):
        """
        Chat RAG interactivo.
        Comandos disponibles:
          /salir          — salir
          /fuentes        — toggle mostrar texto de las fuentes
          /categoria X   — filtrar por categoría (arxiv, python, systems…)
          /categoria     — quitar filtro de categoría
          /top X         — cambiar número de chunks recuperados
          /info          — estado actual
        """
        n_chunks = self.collection.count()
        print("\n╔══════════════════════════════════════════════════════╗")
        print("║   VOID AXIOM — RAG Query Interactivo v1.0          ║")
        print(f"║   Modelo       : {model:<36}║")
        print(f"║   Chunks en KB : {n_chunks:<36,}║")
        print(f"║   Top-K        : {top_k:<36}║")
        print("║   Comandos: /salir /fuentes /categoria /top /info  ║")
        print("╚══════════════════════════════════════════════════════╝\n")

        show_sources = False
        category = ""
        current_top_k = top_k

        while True:
            try:
                user_input = input("\n💬 > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n\n  👋 Hasta luego")
                break

            if not user_input:
                continue

            # ── Comandos ────────────────────────────────────────────────────
            lower = user_input.lower()

            if lower in ("/salir", "/exit", "/quit"):
                print("  👋 Hasta luego")
                break

            elif lower == "/fuentes":
                show_sources = not show_sources
                print(f"  🔍 Mostrar texto de fuentes: {'ON' if show_sources else 'OFF'}")
                continue

            elif lower.startswith("/categoria ") or lower.startswith("/category "):
                category = user_input.split(" ", 1)[1].strip()
                print(f"  📂 Filtrando por categoría: '{category}'")
                continue

            elif lower in ("/categoria", "/category"):
                category = ""
                print("  📂 Sin filtro de categoría")
                continue

            elif lower.startswith("/top "):
                try:
                    current_top_k = int(user_input.split()[1])
                    print(f"  🔢 Top-K cambiado a {current_top_k}")
                except (ValueError, IndexError):
                    print("  ⚠ Uso: /top 8")
                continue

            elif lower == "/info":
                print(f"  Modelo     : {model}")
                print(f"  Top-K      : {current_top_k}")
                print(f"  Categoría  : '{category}' (todas si vacío)")
                print(f"  Fuentes    : {'visible' if show_sources else 'ocultas'}")
                print(f"  Chunks KB  : {self.collection.count():,}")
                continue

            # ── Query RAG ───────────────────────────────────────────────────
            self.query(
                user_input,
                model=model,
                top_k=current_top_k,
                show_sources=show_sources,
                category=category,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="VOID AXIOM — RAG Query en tiempo real v1.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python rag_query.py "¿Cómo funciona el GIL de Python?"
  python rag_query.py --interactive
  python rag_query.py --top-k 8 --show-sources "algoritmos de sorting"
  python rag_query.py --no-llm "transformers attention"   # solo retrieval, sin LLM
  python rag_query.py --category arxiv "neural networks"  # solo papers arXiv
  python rag_query.py --model qwen2.5-coder:14b "SQL"    # modelo más rápido
        """,
    )
    parser.add_argument("question", nargs="?", default="",
                        help="Pregunta o consulta (omitir si --interactive)")
    parser.add_argument("--interactive", "-i", action="store_true",
                        help="Modo chat interactivo")
    parser.add_argument("--model", default=DEFAULT_LLM_MODEL,
                        help=f"Modelo LLM Ollama (default: {DEFAULT_LLM_MODEL})")
    parser.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL,
                        help=f"Modelo de embeddings (default: {DEFAULT_EMBED_MODEL})")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K,
                        help=f"Chunks a recuperar (default: {DEFAULT_TOP_K})")
    parser.add_argument("--show-sources", "-s", action="store_true",
                        help="Mostrar texto completo de las fuentes recuperadas")
    parser.add_argument("--no-llm", action="store_true",
                        help="Solo recuperar chunks, sin generar respuesta con LLM")
    parser.add_argument("--category", default="",
                        help="Filtrar por categoría (arxiv, python, systems, etc.)")
    parser.add_argument("--store", type=Path, default=RAG_STORE_DIR,
                        help=f"Directorio ChromaDB (default: {RAG_STORE_DIR})")
    args = parser.parse_args()

    global _embed_model_active
    _embed_model_active = args.embed_model

    log.info("╔══════════════════════════════════════════════════════╗")
    log.info("║   VOID AXIOM — RAG Query v1.0                      ║")
    log.info("╚══════════════════════════════════════════════════════╝")

    try:
        rag = RAGQuery(embed_model=args.embed_model, store=args.store.resolve())
    except (FileNotFoundError, RuntimeError) as e:
        log.error(f"\n  ✗ {e}")
        log.error("  Solución: python rag_indexer.py")
        sys.exit(1)

    if args.interactive:
        rag.interactive(model=args.model, top_k=args.top_k)
    elif args.question:
        rag.query(
            args.question,
            model=args.model,
            top_k=args.top_k,
            show_sources=args.show_sources,
            no_llm=args.no_llm,
            category=args.category,
        )
    else:
        parser.print_help()
        print(
            "\n  CONSEJO: Para indexar primero ejecuta:\n"
            "    python rag_indexer.py\n\n"
            "  Luego puedes preguntar:\n"
            "    python rag_query.py --interactive\n"
        )


if __name__ == "__main__":
    main()
