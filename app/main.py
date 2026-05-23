"""
Plataforma de Analisis de Datos con IA Local
=============================================
FastAPI + SQLite + Streaming + Autenticacion por usuarios

Nuevo sistema:
- Registro/login de usuarios con sesiones SQLite
- Historial de chats por usuario (persistente)
- Archivos vinculados a cada chat (guardados en DB)
- Mensajes guardados en DB por chat
- Estado en memoria cacheado por (user_id, chat_id)

Hardware: GTX 1080 Ti (11GB) + i7-9700K + 32GB RAM
"""

import gc
import json
import re
import time
from pydantic import BaseModel
import sys
import asyncio
import ast
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.background import BackgroundTask
import uvicorn

from app.config import SERVER_CONFIG, UPLOAD_CONFIG, TOKEN_ESTIMATION, GC_CONFIG
from app.auth import AuthMiddleware, get_current_user, get_auth_page
from app import database as db
from app.data_processor import (
    process_file, estimate_tokens, save_upload, cleanup_file, truncate_to_token_limit,
)
from app.llm_handler import OllamaHandler
from app.void_activity import void_activity
from app.prompts import (
    build_architect_prompt,
    build_agent_a_prompt,
    build_agent_b_prompt,
    build_reviewer_prompt,
)

# ─── BÚSQUEDA WEB ─────────────────────────────────────────────────────────────
try:
    from duckduckgo_search import DDGS
    WEB_SEARCH_AVAILABLE = True
except ImportError:
    WEB_SEARCH_AVAILABLE = False

# Palabras clave que indican que el usuario quiere info actual de internet
_BUSQUEDA_KEYWORDS = [
    "noticias", "noticia", "ultimas", "ultima",
    "hoy", "ahora", "actual", "actualmente", "reciente", "recientes",
    "precio", "precios", "tiempo", "clima", "temperatura",
    "quien gano", "resultado", "resultados",
    "busca", "buscar", "busca en internet", "search", "internet",
    "este ano", "este mes", "esta semana", "ayer",
    "2024", "2025", "2026",
]

def _necesita_busqueda(prompt: str) -> bool:
    """Detecta si el prompt necesita busqueda en internet."""
    if not WEB_SEARCH_AVAILABLE:
        return False
    p = prompt.lower()
    return any(kw in p for kw in _BUSQUEDA_KEYWORDS)

# Cache simple en memoria: { query_key: (timestamp, resultado) }
_SEARCH_CACHE: dict = {}
_CACHE_TTL = 120  # segundos que dura el cache (2 minutos)
_SEARCH_CACHE_LOCK = asyncio.Lock()  # Evita race conditions al escribir en caché

# Feeds RSS de noticias en español (sin rate limit, sin API key)
_RSS_FEEDS = [
    ("BBC Mundo",     "https://feeds.bbci.co.uk/mundo/rss.xml"),
    ("20minutos",     "https://www.20minutos.es/rss/"),
    ("RTVE",          "https://www.rtve.es/api/noticias.rss"),
    ("El Pais",       "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada"),
    ("CNN Espanol",   "https://cnnespanol.cnn.com/feed/"),
    ("Europapress",   "https://www.europapress.es/rss/rss.aspx"),
]

_RSS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

def _buscar_rss(query: str, max_items: int = 8) -> str:
    """Obtiene noticias recientes de feeds RSS de periodicos espanoles.
    No tiene rate limit. Filtra por palabras clave del query si no es generico."""
    import xml.etree.ElementTree as ET
    import httpx, re, html

    query_lower = query.lower()
    # Si el query es generico (solo "noticias", "news"...) no filtrar
    palabras_genericas = {"noticias", "noticia", "news", "ultimas", "ultima", "recientes", "hoy"}
    tokens = set(re.split(r"\W+", query_lower)) - {""}
    es_generico = tokens.issubset(palabras_genericas) or len(tokens) <= 2

    all_items = []
    for source_name, feed_url in _RSS_FEEDS:
        try:
            r = httpx.get(feed_url, headers=_RSS_HEADERS, timeout=6, follow_redirects=True, verify=False)
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:5]:
                title = html.unescape(item.findtext("title", "") or "")
                desc  = html.unescape(item.findtext("description", "") or "")
                # Quitar HTML de la descripcion
                desc  = re.sub(r"<[^>]+>", "", desc).strip()[:200]
                pub   = item.findtext("pubDate", "") or item.findtext("dc:date", "")
                link  = item.findtext("link", "")
                source_tag = item.findtext("source", source_name)
                # Filtrar por relevancia si el query es especifico
                if not es_generico:
                    combined = (title + " " + desc).lower()
                    if not any(t in combined for t in tokens if len(t) > 3):
                        continue
                all_items.append({
                    "title": title, "body": desc,
                    "pub": pub, "url": link, "source": source_name
                })
            if len(all_items) >= max_items:
                break
        except Exception:
            continue

    if not all_items:
        return ""

    lines = ["[NOTICIAS RECIENTES obtenidas ahora mismo de feeds RSS]"]
    for i, r in enumerate(all_items[:max_items], 1):
        lines.append(f"\n[{i}] {r['title']}")
        meta = " | ".join(filter(None, [r["pub"][:22] if r["pub"] else "", r["source"]]))
        if meta:
            lines.append(meta)
        if r["body"]:
            lines.append(r["body"])
        if r["url"]:
            lines.append(f"Enlace: {r['url']}")
    return "\n".join(lines)


def _buscar_ddg_sync(query: str, max_results: int = 6) -> str:
    """Fallback: busqueda DuckDuckGo con reintentos."""
    import time
    news = []
    for intento in range(2):
        try:
            with DDGS() as ddgs:
                news = list(ddgs.news(query, max_results=max_results, region="es-es"))
            if news:
                break
        except Exception as e:
            if "ratelimit" in str(e).lower() or "429" in str(e):
                time.sleep(5 * (intento + 1))
            else:
                break
    if not news:
        return ""
    lines = ["[NOTICIAS via DuckDuckGo]"]
    for i, r in enumerate(news, 1):
        title = r.get("title", "")
        body  = r.get("body", "") or r.get("snippet", "")
        date  = r.get("date", "")
        src   = r.get("source", "")
        url   = r.get("url", r.get("href", ""))
        lines.append(f"\n[{i}] {title}")
        if date or src:
            lines.append(" | ".join(filter(None, [date, src])))
        if body:
            lines.append(body)
        if url:
            lines.append(f"Enlace: {url}")
    return "\n".join(lines)


def _buscar_web_sync(query: str, max_results: int = 8) -> str:
    """Busqueda principal: RSS primero, DuckDuckGo como fallback. Con cache."""
    import time
    cache_key = query.lower().strip()[:80]
    now = time.time()
    if cache_key in _SEARCH_CACHE:
        ts, cached = _SEARCH_CACHE[cache_key]
        if now - ts < _CACHE_TTL:
            return cached + "\n[Cache: resultado reciente]"

    # 1. Intentar RSS (rapido, sin limite)
    result = _buscar_rss(query, max_items=max_results)

    # 2. Fallback a DDG si RSS no dio resultados
    if not result:
        result = _buscar_ddg_sync(query, max_results=6)

    if not result:
        result = "[No se pudieron obtener noticias en este momento. Comprueba tu conexion a internet.]"

    _SEARCH_CACHE[cache_key] = (time.time(), result)
    return result

async def _buscar_web_async(query: str, max_results: int = 5) -> str:
    """Version async: ejecuta la búsqueda en un hilo y protege el caché con Lock."""
    cache_key = query.lower().strip()[:80]
    now = asyncio.get_event_loop().time()

    # Lectura del caché (sin lock, es solo lectura de dict)
    cached = _SEARCH_CACHE.get(cache_key)
    if cached:
        ts, result = cached
        if now - ts < _CACHE_TTL:
            return result + "\n[Cache: resultado reciente]"

    # Búsqueda en hilo para no bloquear el event loop
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _buscar_web_sync, query, max_results)

    # Escritura con lock para evitar race condition
    async with _SEARCH_CACHE_LOCK:
        _SEARCH_CACHE[cache_key] = (asyncio.get_event_loop().time(), result)

    return result

# ─── MODELOS PERMITIDOS ────────────────────────────────────────────────────────
ALLOWED_MODELS = [
    "qwen2.5-coder:3b",
    "qwen2.5-coder:7b",
    "qwen2.5-coder:14b",
]

# ─── DEPENDENCIA ADMIN ────────────────────────────────────────────────────────
async def get_admin_user(user: dict = Depends(get_current_user)) -> dict:
    """Solo permite acceso al administrador (Aerys)."""
    if not db.is_admin(user):
        raise HTTPException(status_code=403, detail="Acceso denegado: solo administradores")
    return user


# ─── ESTADO GLOBAL ────────────────────────────────────────────────────────────

class AppState:
    """Estado global de la aplicacion.

    Los archivos se persisten en SQLite.
    Aqui mantenemos un cache en memoria por (user_id, chat_id) para
    no releer la DB en cada mensaje del chat.
    """

    def __init__(self):
        self.llm = OllamaHandler()
        # Cache: key=(user_id, chat_id) → list of file dicts
        self._file_cache: dict[tuple, list] = {}

    def get_files(self, user_id: int, chat_id: int) -> list:
        key = (user_id, chat_id)
        if key not in self._file_cache:
            rows = db.get_chat_files(chat_id)
            self._file_cache[key] = [
                {
                    "filename": r["filename"],
                    "text": r["text_content"],
                    "tokens": r["tokens"],
                    "metadata": r["metadata"],
                }
                for r in rows
            ]
        return self._file_cache[key]

    def invalidate(self, user_id: int, chat_id: int):
        self._file_cache.pop((user_id, chat_id), None)

    def total_tokens(self, user_id: int, chat_id: int) -> int:
        return sum(f["tokens"] for f in self.get_files(user_id, chat_id))


app_state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ciclo de vida: inicializar DB y LLM al arrancar."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    db.init_db()
    print(f"[i] DB inicializada en data/analizador.db")
    print(f"[i] Ollama: {app_state.llm.base_url} | Modelo: {app_state.llm.model}")
    print(f"[i] Servidor: http://{SERVER_CONFIG['host']}:{SERVER_CONFIG['port']}")

    # Limpieza periodica de sesiones expiradas cada hora
    async def session_cleanup():
        while True:
            await asyncio.sleep(3600)
            db.cleanup_expired_sessions()

    task = asyncio.create_task(session_cleanup())
    yield
    task.cancel()
    await app_state.llm.close()
    try:
        from app.agent_chat import void_session

        await void_session.close()
    except Exception:
        pass
    print("[i] Servidor detenido.")


app = FastAPI(
    title="Analizador de Datos IA Local",
    version="2.0.0",
    lifespan=lifespan,
)

# ─── MIDDLEWARE ────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)
app.add_middleware(AuthMiddleware)


@app.middleware("http")
async def track_main_activity(request: Request, call_next):
    path = request.url.path
    is_chat_stream = (
        request.method.upper() == "POST"
        and path.startswith("/api/chats/")
        and path.endswith("/stream")
    )
    if is_chat_stream:
        void_activity.active_chat_streams += 1
        void_activity.mark_chat()

    if path == "/" or path.startswith(("/api/chats", "/api/upload", "/api/data", "/api/chat")):
        void_activity.mark_main()
    response = await call_next(request)

    if is_chat_stream:
        existing_background = response.background

        async def release_chat_stream() -> None:
            try:
                if existing_background is not None:
                    await existing_background()
            finally:
                void_activity.active_chat_streams = max(0, void_activity.active_chat_streams - 1)
                void_activity.last_chat_activity = time.monotonic()

        response.background = BackgroundTask(release_chat_stream)

    return response

# ─── ESTATICOS ────────────────────────────────────────────────────────────────
static_dir = Path("static")
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ─── RUTAS WEB ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = static_dir / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Error: static/index.html no encontrado</h1>", status_code=500)


@app.get("/auth", response_class=HTMLResponse)
async def auth_page(request: Request):
    """Pagina de login/registro. Si ya esta autenticado, redirige al inicio."""
    token = request.cookies.get("session_token")
    if db.get_session_user(token):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/")
    return HTMLResponse(get_auth_page())


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "ollama_available": await app_state.llm.check_available(),
        "model": app_state.llm.model,
    }


# ─── AUTENTICACION ────────────────────────────────────────────────────────────

@app.post("/api/auth/register")
async def api_register(data: dict):
    """Registro de nuevo usuario."""
    username = data.get("username", "").strip()
    email    = data.get("email", "").strip()
    password = data.get("password", "")

    if not all([username, email, password]):
        raise HTTPException(status_code=400, detail="Todos los campos son obligatorios")

    try:
        user = db.create_user(username, email, password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if user is None:
        raise HTTPException(status_code=409, detail="El nombre de usuario o email ya esta en uso")

    # Auto-login tras registro
    token = db.create_session(user["id"])
    response = JSONResponse({"status": "ok", "message": f"Bienvenido, {username}!"})
    response.set_cookie(
        key="session_token", value=token,
        max_age=86400 * 30, httponly=True, samesite="lax",
    )
    return response


@app.post("/api/auth/login")
async def api_login(data: dict):
    """Login con usuario/email y contrasena."""
    username_or_email = data.get("username_or_email", "").strip()
    password          = data.get("password", "")

    if not username_or_email or not password:
        raise HTTPException(status_code=400, detail="Usuario y contrasena requeridos")

    user = db.authenticate_user(username_or_email, password)
    if not user:
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

    token = db.create_session(user["id"])
    response = JSONResponse({
        "status": "ok",
        "message": f"Bienvenido, {user['username']}!",
        "user": {"id": user["id"], "username": user["username"], "email": user["email"]},
    })
    response.set_cookie(
        key="session_token", value=token,
        max_age=86400 * 30, httponly=True, samesite="lax",
    )
    return response


@app.post("/api/auth/logout")
async def api_logout(request: Request):
    """Logout: invalida la sesion y borra la cookie."""
    token = request.cookies.get("session_token")
    if token:
        db.delete_session(token)
    response = JSONResponse({"status": "ok"})
    response.delete_cookie("session_token")
    return response


@app.get("/api/auth/me")
async def api_me(user: dict = Depends(get_current_user)):
    """Devuelve info del usuario autenticado."""
    return {"id": user["id"], "username": user["username"], "email": user["email"]}


# ─── MODELOS ──────────────────────────────────────────────────────────────────

@app.get("/api/models")
async def list_models(user: dict = Depends(get_current_user)):
    """Devuelve los modelos permitidos que están disponibles en Ollama."""
    available_in_ollama = await app_state.llm.list_models()
    ollama_names = [m.get("name", "") for m in available_in_ollama]

    result = []
    for allowed in ALLOWED_MODELS:
        # Comprobar si está instalado en Ollama
        installed = any(
            allowed in name or name.startswith(allowed)
            for name in ollama_names
        )
        result.append({
            "name": allowed,
            "installed": installed,
            "active": allowed in app_state.llm.model or app_state.llm.model.startswith(allowed),
        })
    return {"models": result, "current": app_state.llm.model}


@app.post("/api/models/switch")
async def switch_model(data: dict, user: dict = Depends(get_current_user)):
    model_name = data.get("model", "").strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="Nombre del modelo vacio")

    # Validar que sea uno de los modelos permitidos
    matched = None
    for allowed in ALLOWED_MODELS:
        if model_name == allowed or model_name in allowed or allowed.startswith(model_name):
            matched = allowed
            break

    if not matched:
        raise HTTPException(
            status_code=400,
            detail=f"Modelo no permitido. Solo se admiten: {', '.join(ALLOWED_MODELS)}",
        )

    # Comprobar que esté disponible en Ollama
    available = await app_state.llm.list_models()
    ollama_names = [m.get("name", "") for m in available]
    found = next(
        (n for n in ollama_names if matched in n or n.startswith(matched)),
        None,
    )
    if not found:
        raise HTTPException(
            status_code=404,
            detail=f"Modelo '{matched}' no encontrado en Ollama. Descárgalo primero.",
        )

    app_state.llm.model = found
    return {"status": "ok", "model": app_state.llm.model}


# ─── ADMINISTRACIÓN ───────────────────────────────────────────────────────────

@app.get("/api/admin/stats")
async def admin_stats(admin: dict = Depends(get_admin_user)):
    """Estadísticas globales del sistema."""
    stats = db.get_global_stats()
    stats["current_model"] = app_state.llm.model
    stats["allowed_models"] = ALLOWED_MODELS
    return stats


@app.get("/api/admin/users")
async def admin_list_users(admin: dict = Depends(get_admin_user)):
    """Lista todos los usuarios registrados."""
    return {"users": db.get_all_users()}


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: int, admin: dict = Depends(get_admin_user)):
    """Elimina un usuario y todos sus datos."""
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="No puedes eliminarte a ti mismo")
    if not db.delete_user_admin(user_id):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return {"status": "ok", "message": f"Usuario {user_id} eliminado"}


@app.post("/api/admin/users/{user_id}/logout")
async def admin_force_logout(user_id: int, admin: dict = Depends(get_admin_user)):
    """Cierra todas las sesiones de un usuario (fuerza logout)."""
    count = db.delete_user_sessions_admin(user_id)
    return {"status": "ok", "sessions_closed": count}


@app.get("/api/admin/chats")
async def admin_list_chats(admin: dict = Depends(get_admin_user)):
    """Lista todos los chats de todos los usuarios."""
    return {"chats": db.get_all_chats_admin()}


@app.delete("/api/admin/chats/{chat_id}")
async def admin_delete_chat(chat_id: int, admin: dict = Depends(get_admin_user)):
    """Elimina cualquier chat."""
    if not db.delete_chat_admin(chat_id):
        raise HTTPException(status_code=404, detail="Chat no encontrado")
    return {"status": "ok", "message": f"Chat {chat_id} eliminado"}


@app.get("/api/admin/users/{user_id}/messages")
async def admin_user_messages(user_id: int, admin: dict = Depends(get_admin_user)):
    """Ver últimos mensajes de un usuario."""
    return {"messages": db.get_user_messages_admin(user_id)}


# ─── CHATS ────────────────────────────────────────────────────────────────────

@app.get("/api/chats")
async def list_chats(user: dict = Depends(get_current_user)):
    """Lista todos los chats del usuario autenticado."""
    chats = db.get_user_chats(user["id"])
    return {"chats": chats}


@app.post("/api/chats/new")
async def new_chat(data: dict = None, user: dict = Depends(get_current_user)):
    """Crea un nuevo chat vacio."""
    title = (data or {}).get("title", "Chat nuevo")
    chat = db.create_chat(user["id"], title)
    return {"chat": chat}


# ─── HELPER DE PERMISOS ───────────────────────────────────────────────────────

def _resolve_chat_for_user(chat_id: int, user: dict) -> dict:
    """Devuelve el chat si el usuario tiene permiso para acceder a él.

    Lógica de permisos:
      • Administrador  → puede acceder a cualquier chat del sistema (auditoría).
      • Usuario normal → solo puede acceder a sus propios chats.

    Lanza:
      • 403 Forbidden  si el chat existe pero pertenece a otro usuario.
      • 404 Not Found  si el chat no existe en absoluto.
    """
    if db.is_admin(user):
        # El admin usa la consulta sin filtro de user_id
        chat = db.get_chat_by_id(chat_id)
        if not chat:
            raise HTTPException(status_code=404, detail="Chat no encontrado")
        return chat

    # Usuario normal: filtrar estrictamente por su user_id
    chat = db.get_chat(chat_id, user["id"])
    if not chat:
        # Distinguir 404 real de un 403 (el chat existe pero es de otro)
        # para no revelar al usuario que ese chat_id sí existe en el sistema.
        if db.get_chat_by_id(chat_id):
            raise HTTPException(
                status_code=403,
                detail="Acceso denegado: este chat no te pertenece.",
            )
        raise HTTPException(status_code=404, detail="Chat no encontrado")

    return chat


@app.get("/api/chats/{chat_id}")
async def get_chat(chat_id: int, user: dict = Depends(get_current_user)):
    """Devuelve un chat con sus mensajes y archivos.

    Permisos:
      • Usuario normal → solo sus propios chats.
      • Administrador  → cualquier chat del sistema (modo auditoría).
    """
    chat = _resolve_chat_for_user(chat_id, user)

    messages     = db.get_chat_messages(chat_id)
    files        = db.get_chat_files(chat_id)
    total_tokens = sum(f["tokens"] for f in files)

    # Flag para que el frontend sepa que el admin está leyendo un chat ajeno
    is_audit = db.is_admin(user) and chat["user_id"] != user["id"]

    return {
        "chat":         chat,
        "messages":     messages,
        "files": [
            {
                "filename": f["filename"],
                "tokens":   f["tokens"],
                "metadata": f["metadata"],
            }
            for f in files
        ],
        "total_tokens": total_tokens,
        "is_audit":     is_audit,
    }


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: int, user: dict = Depends(get_current_user)):
    """Elimina un chat y todos sus mensajes/archivos.

    Permisos:
      • Usuario normal → solo puede borrar sus propios chats.
      • Administrador  → puede borrar cualquier chat del sistema.
    """
    chat = _resolve_chat_for_user(chat_id, user)

    if db.is_admin(user):
        success = db.delete_chat_admin(chat_id)
    else:
        success = db.delete_chat(chat_id, user["id"])

    if not success:
        raise HTTPException(status_code=404, detail="Chat no encontrado")

    app_state.invalidate(chat["user_id"], chat_id)
    return {"status": "ok", "message": "Chat eliminado"}


@app.patch("/api/chats/{chat_id}/title")
async def rename_chat(chat_id: int, data: dict, user: dict = Depends(get_current_user)):
    """Renombra un chat.

    Permisos:
      • Usuario normal → solo puede renombrar sus propios chats.
      • Administrador  → puede renombrar cualquier chat (moderación).
    """
    title = data.get("title", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Titulo vacio")

    chat = _resolve_chat_for_user(chat_id, user)

    # update_chat_title filtra internamente por user_id; usamos el propietario real
    db.update_chat_title(chat_id, chat["user_id"], title)
    return {"status": "ok"}


# ─── ARCHIVOS ─────────────────────────────────────────────────────────────────

@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """Sube un archivo a un chat especifico.
    El chat_id viene en la query string: /api/upload?chat_id=X
    """
    from fastapi import Request as FR
    return await _do_upload_file(file, user)


async def _do_upload_file(file: UploadFile, user: dict, chat_id: int = None):
    ext = Path(file.filename).suffix.lower()
    if ext not in UPLOAD_CONFIG["allowed_extensions"]:
        raise HTTPException(status_code=400, detail=f"Extension no permitida: {ext}")

    content  = await file.read()
    filepath = save_upload(content, file.filename)

    try:
        text, tokens, metadata = process_file(filepath)
        max_tokens = TOKEN_ESTIMATION["max_tokens"]
        if tokens > max_tokens:
            text, tokens = truncate_to_token_limit(text, max_tokens)
            metadata["truncated"] = True
        else:
            metadata["truncated"] = False

        if chat_id:
            db.add_chat_file(chat_id, user["id"], file.filename, text, tokens, metadata)
            app_state.invalidate(user["id"], chat_id)

        return {
            "status": "ok",
            "filename": file.filename,
            "tokens": tokens,
            "metadata": metadata,
            "message": f"Archivo: {tokens:,} tokens",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cleanup_file(filepath)


@app.post("/api/chats/{chat_id}/upload")
async def upload_file_to_chat(
    chat_id: int,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """Sube un archivo vinculado a un chat especifico."""
    chat = db.get_chat(chat_id, user["id"])
    if not chat:
        raise HTTPException(status_code=404, detail="Chat no encontrado")
    return await _do_upload_file(file, user, chat_id)


@app.post("/api/chats/{chat_id}/upload/multiple")
async def upload_multiple_to_chat(
    chat_id: int,
    files: list[UploadFile] = File(...),
    user: dict = Depends(get_current_user),
):
    """Sube multiples archivos a un chat."""
    chat = db.get_chat(chat_id, user["id"])
    if not chat:
        raise HTTPException(status_code=404, detail="Chat no encontrado")

    results = []
    total_tokens = 0
    for file in files:
        ext = Path(file.filename).suffix.lower()
        if ext not in UPLOAD_CONFIG["allowed_extensions"]:
            continue
        content  = await file.read()
        filepath = save_upload(content, file.filename)
        try:
            text, tokens, metadata = process_file(filepath)
            max_tokens = TOKEN_ESTIMATION["max_tokens"]
            if tokens > max_tokens:
                text, tokens = truncate_to_token_limit(text, max_tokens)
                metadata["truncated"] = True
            db.add_chat_file(chat_id, user["id"], file.filename, text, tokens, metadata)
            total_tokens += tokens
            results.append({"filename": file.filename, "tokens": tokens})
        except Exception:
            pass
        finally:
            cleanup_file(filepath)

    app_state.invalidate(user["id"], chat_id)
    return {
        "status": "ok",
        "files": len(results),
        "tokens": total_tokens,
        "message": f"{len(results)} archivos: {total_tokens:,} tokens totales",
    }


@app.post("/api/chats/{chat_id}/upload/text")
async def upload_text_to_chat(
    chat_id: int,
    data: dict,
    user: dict = Depends(get_current_user),
):
    """Carga texto pegado como archivo en un chat."""
    chat = db.get_chat(chat_id, user["id"])
    if not chat:
        raise HTTPException(status_code=404, detail="Chat no encontrado")

    text = data.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Texto vacio")

    tokens = estimate_tokens(text)
    max_tokens = TOKEN_ESTIMATION["max_tokens"]
    metadata = {"source": "texto_pegado", "chars": len(text), "truncated": False}

    if tokens > max_tokens:
        from app.data_processor import truncate_to_token_limit as ttl
        text, tokens = ttl(text, max_tokens)
        metadata["truncated"] = True

    # Nombre unico para el texto pegado
    existing = db.get_chat_files(chat_id)
    existing_names = {f["filename"] for f in existing}
    n = 1
    while f"texto_pegado_{n}.txt" in existing_names:
        n += 1
    filename = f"texto_pegado_{n}.txt"

    db.add_chat_file(chat_id, user["id"], filename, text, tokens, metadata)
    app_state.invalidate(user["id"], chat_id)

    return {
        "status": "ok",
        "tokens": tokens,
        "metadata": metadata,
        "message": f"Texto cargado: {tokens:,} tokens",
    }


@app.post("/api/chats/{chat_id}/files/remove")
async def remove_file_from_chat(
    chat_id: int,
    data: dict,
    user: dict = Depends(get_current_user),
):
    """Elimina un archivo especifico de un chat."""
    chat = db.get_chat(chat_id, user["id"])
    if not chat:
        raise HTTPException(status_code=404, detail="Chat no encontrado")

    filename = data.get("filename", "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="Nombre de archivo vacio")

    db.remove_chat_file(chat_id, filename)
    app_state.invalidate(user["id"], chat_id)
    return {"status": "ok", "message": f"Archivo '{filename}' eliminado"}


@app.post("/api/chats/{chat_id}/files/clear")
async def clear_files_from_chat(
    chat_id: int,
    user: dict = Depends(get_current_user),
):
    """Elimina todos los archivos de un chat."""
    chat = db.get_chat(chat_id, user["id"])
    if not chat:
        raise HTTPException(status_code=404, detail="Chat no encontrado")

    db.clear_chat_files(chat_id)
    app_state.invalidate(user["id"], chat_id)
    return {"status": "ok", "message": "Archivos eliminados"}


@app.get("/api/chats/{chat_id}/status")
async def chat_status(chat_id: int, user: dict = Depends(get_current_user)):
    """Estado de archivos de un chat (para sincronizar la barra de tokens)."""
    chat = db.get_chat(chat_id, user["id"])
    if not chat:
        raise HTTPException(status_code=404, detail="Chat no encontrado")

    files = app_state.get_files(user["id"], chat_id)
    total_tokens = sum(f["tokens"] for f in files)

    return {
        "loaded": len(files) > 0,
        "files": [
            {"filename": f["filename"], "tokens": f["tokens"], "metadata": f["metadata"]}
            for f in files
        ],
        "total_tokens": total_tokens,
    }


@app.get("/api/chats/{chat_id}/summary")
async def chat_summary(chat_id: int, user: dict = Depends(get_current_user)):
    chat = _resolve_chat_for_user(chat_id, user)
    files = app_state.get_files(chat["user_id"], chat_id)
    messages = db.get_chat_messages(chat_id)
    user_msgs = [m for m in messages if m["role"] == "user"]
    assistant_msgs = [m for m in messages if m["role"] == "assistant"]
    total_tokens = sum(m.get("tokens", 0) or 0 for m in messages) + sum(f["tokens"] for f in files)
    top_files = sorted(files, key=lambda f: f["tokens"], reverse=True)[:6]
    return {
        "chat_id": chat_id,
        "title": chat.get("title"),
        "message_count": len(messages),
        "user_messages": len(user_msgs),
        "assistant_messages": len(assistant_msgs),
        "file_count": len(files),
        "total_tokens": total_tokens,
        "last_user_prompt": user_msgs[-1]["content"][:500] if user_msgs else "",
        "top_files": [
            {"filename": f["filename"], "tokens": f["tokens"], "metadata": f.get("metadata", {})}
            for f in top_files
        ],
    }


@app.get("/api/chats/{chat_id}/export")
async def chat_export(chat_id: int, user: dict = Depends(get_current_user)):
    chat = _resolve_chat_for_user(chat_id, user)
    files = app_state.get_files(chat["user_id"], chat_id)
    messages = db.get_chat_messages(chat_id)
    return {
        "exported_at": _now_iso(),
        "chat": chat,
        "messages": messages,
        "files": [
            {
                "filename": f["filename"],
                "tokens": f["tokens"],
                "metadata": f["metadata"],
                "text": f["text"],
            }
            for f in files
        ],
    }


@app.get("/api/chats/{chat_id}/files/insights")
async def chat_file_insights(chat_id: int, user: dict = Depends(get_current_user)):
    chat = _resolve_chat_for_user(chat_id, user)
    files = app_state.get_files(chat["user_id"], chat_id)
    insights = []
    stop_words = {"para", "con", "una", "por", "que", "del", "los", "las", "the", "and", "este", "esta"}
    for f in files:
        text = f.get("text") or ""
        lines = text.splitlines()
        words = re.findall(r"\b[\wÁÉÍÓÚÜÑáéíóúüñ-]{3,}\b", text.lower())
        freq: dict[str, int] = {}
        for word in words[:50000]:
            if word in stop_words:
                continue
            freq[word] = freq.get(word, 0) + 1
        top_terms = sorted(freq.items(), key=lambda item: item[1], reverse=True)[:8]
        insights.append({
            "filename": f["filename"],
            "tokens": f["tokens"],
            "lines": len(lines),
            "chars": len(text),
            "metadata": f.get("metadata", {}),
            "top_terms": [{"term": term, "count": count} for term, count in top_terms],
            "preview": "\n".join(lines[:5])[:600],
        })
    return {"chat_id": chat_id, "files": insights}


# ─── CHAT CON STREAMING ───────────────────────────────────────────────────────

@app.post("/api/chats/{chat_id}/stream")
async def chat_stream(
    chat_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Chat con streaming SSE con detección de desconexión del cliente.

    Solución al WinError 10054 en Windows (ProactorEventLoop):
    El generador comprueba `await request.is_disconnected()` en cada
    iteración. Si el cliente cierra la pestaña, se rompe el bucle
    limpiamente, liberando la GPU de Ollama y evitando que Uvicorn
    se quede congelado.
    """
    chat = db.get_chat(chat_id, user["id"])
    if not chat:
        raise HTTPException(status_code=404, detail="Chat no encontrado")

    body             = await request.json()
    prompt           = body.get("prompt", "").strip()
    use_max_context  = body.get("use_max_context", False)

    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt vacio")

    files = app_state.get_files(user["id"], chat_id)
    context_text = ""
    if files:
        parts = [f"===== {f['filename']} =====\n{f['text']}" for f in files]
        context_text = "\n\n".join(parts)

    # Búsqueda web automática si el prompt lo requiere
    is_web_search = False
    web_results_text = ""
    if _necesita_busqueda(prompt):
        is_web_search = True
        web_results_text = await _buscar_web_async(prompt)

    # Guardar mensaje del usuario en DB
    prompt_tokens = estimate_tokens(prompt)
    db.add_message(chat_id, "user", prompt, prompt_tokens)

    # Auto-titulo con IA: si es el primer mensaje, pedir al modelo 3B que genere un título
    messages_count = len(db.get_chat_messages(chat_id))
    if messages_count <= 2 and chat["title"] in ("Chat nuevo", ""):
        asyncio.create_task(_auto_title(chat_id, user["id"], prompt))

    # Buffer para acumular la respuesta completa
    full_response = []

    async def stream_response():
        system_prompt = (
            "Eres Nexo, un asistente de IA local creado por Aerys, un desarrollador de 13 años. "
            "IMPORTANTE: NO eres Claude, NO eres ChatGPT ni ningun otro asistente externo. "
            "Responde siempre en espanol de forma clara y concisa."
        )

        # Si hay resultados web, construir el prompt con los datos integrados
        final_prompt = prompt
        final_context = context_text
        if is_web_search and web_results_text:
            final_prompt = (
                f"Pregunta del usuario: {prompt}\n\n"
                f"A continuacion tienes los RESULTADOS REALES obtenidos de internet AHORA MISMO "
                f"usando DuckDuckGo. Usa estos datos para responder. "
                f"NO digas que no puedes buscar en internet, porque YA se ha hecho la busqueda por ti:\n\n"
                f"{web_results_text}\n\n"
                f"Con esa informacion, responde a la pregunta del usuario en espanol."
            )
            final_context = ""  # Los resultados ya van en el prompt, no como contexto separado
        elif is_web_search:
            final_prompt = (
                f"{prompt}\n\n"
                f"(Nota: se intento buscar en internet pero no se obtuvieron resultados. "
                f"Responde con lo que sepas e indica que la busqueda no devolvio resultados.)"
            )

        # ── BUCLE PRINCIPAL CON DETECCIÓN DE DESCONEXIÓN ──────────────────────
        # En Windows, cerrar la pestaña a mitad del stream lanza un
        # ConnectionResetError (WinError 10054) que congela el ProactorEventLoop.
        # La solución es comprobar is_disconnected() antes de cada yield y
        # capturar cualquier excepción de red para salir limpiamente.
        try:
            async for chunk in app_state.llm.generate_stream(
                prompt=final_prompt,
                context_text=final_context,
                system_prompt=system_prompt,
                use_max_context=use_max_context,
            ):
                # Comprobar si el cliente cerró la conexión antes de enviar el chunk
                if await request.is_disconnected():
                    print(
                        f"\n💢 HIJO DE TU MADRE (user_id={user['id']}, chat_id={chat_id}) — "
                        f"Porque cierras la web mientras procesa?! >:(\n"
                        f"   Cancelando stream y liberando GPU...\n",
                        flush=True,
                    )
                    return  # Salida limpia: Ollama para, GPU libre, Uvicorn no se congela

                full_response.append(chunk)
                yield f"data: {json.dumps({'text': chunk})}\n\n"

        except (asyncio.CancelledError, GeneratorExit):
            # CancelledError y GeneratorExit heredan de BaseException, NO de Exception.
            # Uvicorn los lanza cuando el cliente cierra la conexión — por eso
            # el bloque "except Exception" anterior nunca los capturaba.
            print(
                f"\n💢 HIJO DE TU MADRE (user_id={user['id']}, chat_id={chat_id}) — "
                f"Porque cierras la web mientras procesa?! >:(\n"
                f"   [CancelledError/GeneratorExit] Cancelando stream y liberando GPU...\n",
                flush=True,
            )
            return  # Salida limpia desde el generador

        except Exception as exc:
            # Captura ConnectionResetError (WinError 10054) y cualquier otro
            # error de red que llegue antes de que is_disconnected() lo detecte.
            _DISCONNECT_ERRORS = (
                ConnectionResetError,
                ConnectionAbortedError,
                BrokenPipeError,
            )
            es_desconexion = (
                isinstance(exc, _DISCONNECT_ERRORS)
                or "10054" in str(exc)
                or "10053" in str(exc)
                or "Broken pipe" in str(exc)
            )
            if es_desconexion:
                print(
                    f"\n💢 HIJO DE TU MADRE (user_id={user['id']}, chat_id={chat_id}) — "
                    f"Porque cierras la web mientras procesa?! >:(\n"
                    f"   [{type(exc).__name__}] Cancelando stream y liberando GPU...\n",
                    flush=True,
                )
            else:
                logger.warning(
                    "Error de red en stream (chat_id=%s): %s — %s",
                    chat_id, type(exc).__name__, exc,
                )
            return  # Salir sin propagar: Uvicorn cierra la conexión limpiamente

        # Guardar respuesta completa en DB (solo si el stream terminó sin cortes)
        complete = "".join(full_response).replace("\n[DONE]", "").strip()
        if complete:
            resp_tokens = estimate_tokens(complete)
            db.add_message(chat_id, "assistant", complete, resp_tokens)
            db.touch_chat(chat_id)

    return StreamingResponse(
        stream_response(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ─── HELPER: TÍTULO AUTOMÁTICO CON 3B ────────────────────────────────────────

async def _auto_title(chat_id: int, user_id: int, first_message: str):
    """Tarea en background: genera un título con qwen2.5-coder:3b."""
    try:
        title = await app_state.llm.generate_title(first_message)
        if title:
            db.update_chat_title(chat_id, user_id, title)
    except Exception:
        pass  # Si falla, el chat mantiene el título por defecto


# ─── GC ───────────────────────────────────────────────────────────────────────

@app.post("/api/gc")
async def force_gc(user: dict = Depends(get_current_user)):
    collected = gc.collect()
    cuda_freed = False
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            cuda_freed = True
    except ImportError:
        pass
    return {"status": "ok", "objects_collected": collected, "cuda_cache_cleared": cuda_freed}


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# GENERADOR DE CÓDIGO CON IA LOCAL (Ollama)


# ─────────────────────────────────────────────────────────────────────────────
#  GENERADOR DE CÓDIGO — SISTEMA MULTI-AGENTE (4 FASES)
#
#  FASE 1 — Arquitecto (14b):
#    Diseña la estructura COMPLETA: carpetas, archivos, contrato de interfaces,
#    lanzadores .bat, README, requirements.txt. Mínimo 10 archivos.
#
#  FASE 2 — Dual Subagente (7b × 7b por archivo, en diálogo):
#    • Agente A (Implementador): escribe el código inicial completo (mín. 150 líneas).
#    • Agente B (Crítico/Enriquecedor): lee el código de A, lo critica y lo
#      REESCRIBE completo con más features, mejor gestión de errores y más líneas.
#    • El output final de cada archivo es el código de Agente B.
#    → Los dos agentes se comunican a través del texto: B siempre ve el código de A.
#
#  FASE 3 — Revisor (14b):
#    Lee TODOS los archivos, detecta imports rotos, métodos inexistentes,
#    variables no definidas y rutas de carpeta incorrectas, y los corrige.
# ─────────────────────────────────────────────────────────────────────────────

GENERADOR_ARCHITECT_MODEL  = "qwen2.5-coder:14b"
GENERADOR_AGENT_A_MODEL    = "qwen2.5-coder:7b"   # Implementador
GENERADOR_AGENT_B_MODEL    = "qwen2.5-coder:7b"   # Crítico / Enriquecedor
GENERADOR_REVIEWER_MODEL   = "qwen2.5-coder:14b"


class GeneradorRequest(BaseModel):
    prompt:   str
    language: str = ""
    size:     str = "4 to 8"
    options:  dict = {}


class GeneratedFilePayload(BaseModel):
    path: str
    content: str = ""


class GeneradorFilesRequest(BaseModel):
    project_name: str = "proyecto"
    prompt: str = ""
    language: str = ""
    stack: str = ""
    options: dict = {}
    files: list[GeneratedFilePayload] = []


class RegenerateFileRequest(GeneradorFilesRequest):
    target_path: str
    instructions: str = ""


GENERADOR_ALLOWED_SIZES = {"2 to 4", "4 to 8", "8 to 14"}
GENERADOR_MAX_PROMPT_CHARS = 8000
GENERADOR_MAX_LANGUAGE_CHARS = 80
GENERADOR_HISTORY_LIMIT = 50
GENERADOR_HISTORY_PATH = Path("data") / "generador_history.json"
GENERADOR_EXPORT_DIR = Path("generated_projects")


def _safe_project_name(value: str | None) -> str:
    """Devuelve un nombre de carpeta/zip seguro para el proyecto generado."""
    text = (value or "proyecto").strip().lower()
    text = re.sub(r"[^a-z0-9_-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._-")
    return (text or "proyecto")[:64]


def _safe_generated_path(value: str | None, fallback: str) -> str:
    """Normaliza rutas creadas por el modelo para evitar rutas absolutas o traversal."""
    raw = (value or fallback).replace("\\", "/").replace("\x00", "").strip()
    raw = re.sub(r"^[a-zA-Z]:", "", raw).lstrip("/")

    safe_parts: list[str] = []
    for part in raw.split("/"):
        part = part.strip()
        if not part or part in (".", ".."):
            continue
        clean = re.sub(r"[^A-Za-z0-9._ -]+", "_", part).strip(" ._")
        if clean:
            safe_parts.append(clean[:80])

    if not safe_parts:
        return fallback
    return "/".join(safe_parts)[:240]


def _dedupe_path(path: str, used: set[str]) -> str:
    if path not in used:
        used.add(path)
        return path

    stem, dot, ext = path.rpartition(".")
    base = stem if dot else path
    suffix = f".{ext}" if dot else ""
    n = 2
    while True:
        candidate = f"{base}_{n}{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        n += 1


def _sanitize_plan_files(files: list, max_files: int) -> list[dict]:
    """Copia y sanea la lista de archivos del plan antes de generar contenido."""
    safe_files: list[dict] = []
    used_paths: set[str] = set()

    for idx, item in enumerate(files[:max_files]):
        if not isinstance(item, dict):
            item = {}
        finfo = dict(item)
        fallback = f"file_{idx + 1}.txt"
        safe_path = _safe_generated_path(str(finfo.get("path") or ""), fallback)
        finfo["path"] = _dedupe_path(safe_path, used_paths)

        for key in ("description",):
            finfo[key] = str(finfo.get(key) or "")[:800]
        for key in ("key_features", "exports", "imports_needed"):
            value = finfo.get(key, [])
            if not isinstance(value, list):
                value = []
            finfo[key] = [str(v)[:500] for v in value if isinstance(v, (str, int, float))]

        safe_files.append(finfo)

    return safe_files


def _sanitize_generated_files(files: list[GeneratedFilePayload] | list[dict]) -> list[dict]:
    safe_files: list[dict] = []
    used_paths: set[str] = set()
    for idx, item in enumerate(files or []):
        if isinstance(item, GeneratedFilePayload):
            raw_path = item.path
            content = item.content
        elif isinstance(item, dict):
            raw_path = str(item.get("path") or "")
            content = str(item.get("content") or "")
        else:
            continue
        safe_path = _safe_generated_path(raw_path, f"file_{idx + 1}.txt")
        safe_files.append({
            "path": _dedupe_path(safe_path, used_paths),
            "content": content,
        })
    return safe_files


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_generation_history() -> list[dict]:
    try:
        if GENERADOR_HISTORY_PATH.exists():
            data = json.loads(GENERADOR_HISTORY_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def _save_generation_history(rows: list[dict]) -> None:
    GENERADOR_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    GENERADOR_HISTORY_PATH.write_text(
        json.dumps(rows[:GENERADOR_HISTORY_LIMIT], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _history_summary(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "created_at": row.get("created_at"),
        "project_name": row.get("project_name"),
        "prompt": row.get("prompt", "")[:260],
        "language": row.get("language", ""),
        "stack": row.get("stack", ""),
        "file_count": row.get("file_count", 0),
        "total_lines": row.get("total_lines", 0),
    }


def _save_generation_record(
    user: dict,
    req: GeneradorRequest,
    project_name: str,
    stack: str,
    generated: list[dict],
    total_lines: int,
) -> str:
    history_id = uuid.uuid4().hex[:12]
    row = {
        "id": history_id,
        "user_id": user["id"],
        "created_at": _now_iso(),
        "project_name": project_name,
        "prompt": req.prompt,
        "language": req.language,
        "size": req.size,
        "options": req.options or {},
        "stack": stack,
        "file_count": len(generated),
        "total_lines": total_lines,
        "files": generated,
    }
    rows = _load_generation_history()
    rows.insert(0, row)
    _save_generation_history(rows)
    return history_id


def _find_generation_record(history_id: str, user: dict) -> dict | None:
    for row in _load_generation_history():
        if row.get("id") == history_id and (row.get("user_id") == user["id"] or db.is_admin(user)):
            return row
    return None


def _verify_generated_files(files: list[dict]) -> dict:
    issues: list[dict] = []
    safe_files = _sanitize_generated_files(files)
    paths = [f["path"] for f in safe_files]
    path_set = set(paths)

    if len(paths) != len(path_set):
        issues.append({"severity": "error", "path": "", "message": "Hay rutas duplicadas en el proyecto."})

    py_exports: dict[str, set[str]] = {}
    for f in safe_files:
        path = f["path"]
        content = f["content"]
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if not content.strip():
            issues.append({"severity": "error", "path": path, "message": "Archivo vacío."})
            continue
        if len(content.strip()) < 20 and ext in {"py", "js", "ts", "html", "css"}:
            issues.append({"severity": "warning", "path": path, "message": "Archivo sospechosamente corto."})
        if re.search(r"\b(TODO|pass\s*$|NotImplementedError)\b", content, re.MULTILINE):
            issues.append({"severity": "warning", "path": path, "message": "Puede contener stubs o TODOs pendientes."})

        if ext == "py":
            module = path[:-3].replace("/", ".")
            try:
                tree = ast.parse(content)
                exports = {
                    node.name for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                }
                exports.update(
                    target.id
                    for node in tree.body
                    if isinstance(node, ast.Assign)
                    for target in node.targets
                    if isinstance(target, ast.Name)
                )
                py_exports[module] = exports
            except SyntaxError as exc:
                issues.append({
                    "severity": "error",
                    "path": path,
                    "message": f"Python no compila: línea {exc.lineno}, {exc.msg}.",
                })
        elif path.endswith("requirements.txt"):
            bad = [
                line for line in content.splitlines()
                if line.strip() and not re.match(r"^[A-Za-z0-9_.-]+([<>=!~]=?|==)[A-Za-z0-9_.!*+-]+", line.strip())
            ]
            if bad:
                issues.append({"severity": "warning", "path": path, "message": "requirements.txt contiene líneas no estándar o sin versión."})
        elif ext == "bat":
            real_cmd = any(
                line.strip().lower().startswith(("python ", "py ", "pip ", "uvicorn ", "npm ", "node "))
                for line in content.splitlines()
            )
            if not real_cmd:
                issues.append({"severity": "warning", "path": path, "message": "Batch sin comando ejecutable claro."})

    for f in safe_files:
        if not f["path"].endswith(".py"):
            continue
        try:
            tree = ast.parse(f["content"])
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                module_path = node.module.replace(".", "/") + ".py"
                if module_path in path_set:
                    exports = py_exports.get(node.module, set())
                    missing = [
                        alias.name for alias in node.names
                        if alias.name != "*" and alias.name not in exports
                    ]
                    if missing:
                        issues.append({
                            "severity": "error",
                            "path": f["path"],
                            "message": f"Import roto desde {node.module}: {', '.join(missing)}.",
                        })

    errors = sum(1 for issue in issues if issue["severity"] == "error")
    warnings = sum(1 for issue in issues if issue["severity"] == "warning")
    score = max(0, 100 - errors * 25 - warnings * 7)
    return {
        "score": score,
        "errors": errors,
        "warnings": warnings,
        "issues": issues,
        "file_count": len(safe_files),
        "total_lines": sum(len(f["content"].splitlines()) for f in safe_files),
    }


def _write_project_to_disk(user: dict, project_name: str, files: list[dict]) -> Path:
    safe_name = _safe_project_name(project_name)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = (GENERADOR_EXPORT_DIR / str(user["id"]) / f"{safe_name}_{stamp}").resolve()
    base.mkdir(parents=True, exist_ok=True)

    for f in _sanitize_generated_files(files):
        rel = Path(f["path"])
        target = (base / rel).resolve()
        if not str(target).startswith(str(base)):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f["content"], encoding="utf-8", newline="")
    return base


def _build_advanced_prompt(prompt: str, options: dict) -> str:
    if not options:
        return prompt
    additions: list[str] = []
    if options.get("mode"):
        additions.append(f"Modo de generación: {options['mode']}.")
    if options.get("include_tests"):
        additions.append("Incluye tests automatizados apropiados para el stack.")
    if options.get("include_docker"):
        additions.append("Incluye Dockerfile y/o docker-compose si encaja con el proyecto.")
    if options.get("include_ci"):
        additions.append("Incluye workflow de CI básico si el stack lo permite.")
    if options.get("comments"):
        additions.append(f"Nivel de comentarios/docstrings: {options['comments']}.")
    if options.get("style"):
        additions.append(f"Estilo de arquitectura/código preferido: {options['style']}.")
    if options.get("extra"):
        additions.append(f"Requisitos extra: {str(options['extra'])[:1000]}")
    if not additions:
        return prompt
    return prompt + "\n\nREQUISITOS AVANZADOS:\n- " + "\n- ".join(additions)


async def _ollama_generate_stream_events(
    base_url: str,
    client,
    model: str,
    prompt: str,
    max_tokens: int = 2000,
    temperature: float = 0.05,
    num_ctx: int = 4096,
    cancel_check=None,
):
    """Como _ollama_generate pero hace yield de eventos thinking_token en tiempo real.
    Al finalizar hace yield del texto completo como cadena en el último elemento (str).
    """
    safe_max_tokens = min(max_tokens, int(num_ctx * 0.65))
    payload = {
        "model": model,
        "prompt": prompt,
        "options": {
            "num_ctx":        num_ctx,
            "num_predict":    safe_max_tokens,
            "temperature":    temperature,
            "top_p":          0.9,
            "repeat_penalty": 1.05,
            "num_thread":     7,
            "keep_alive":     "600s",
        },
        "stream": True,
    }
    full_response = ""
    try:
        async with client.stream(
            "POST", f"{base_url}/api/generate", json=payload, timeout=1200.0
        ) as resp:
            if resp.status_code != 200:
                raise HTTPException(502, f"Error Ollama {resp.status_code}")
            async for line in resp.aiter_lines():
                if cancel_check and await cancel_check():
                    raise asyncio.CancelledError()
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                    token = chunk.get("response", "")
                    if token:
                        full_response += token
                        yield json.dumps({"type": "thinking_token", "text": token}, ensure_ascii=False) + "\n"
                    if chunk.get("done"):
                        break
                except Exception:
                    continue
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"Ollama no disponible: {e}")
    # Último yield: el texto completo (tipo str, no dict)
    yield full_response.strip()


async def _ollama_generate(
    base_url: str,
    client,
    model: str,
    prompt: str,
    max_tokens: int = 2000,
    temperature: float = 0.05,
    num_ctx: int = 4096,
    cancel_check=None,
) -> str:
    # Seguridad: num_predict no puede superar num_ctx (dejamos margen para el prompt)
    safe_max_tokens = min(max_tokens, int(num_ctx * 0.65))
    payload = {
        "model": model,
        "prompt": prompt,
        "options": {
            "num_ctx":        num_ctx,
            "num_predict":    safe_max_tokens,
            "temperature":    temperature,
            "top_p":          0.9,
            "repeat_penalty": 1.05,
            "num_thread":     7,
            "keep_alive":     "600s",
        },
        "stream": True,   # ← CLAVE: evita el timeout de lectura
    }
    try:
        full_response = ""
        async with client.stream(
            "POST", f"{base_url}/api/generate", json=payload, timeout=1200.0
        ) as resp:
            if resp.status_code != 200:
                raise HTTPException(502, f"Error Ollama {resp.status_code}")
            async for line in resp.aiter_lines():
                if cancel_check and await cancel_check():
                    raise asyncio.CancelledError()
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                    full_response += chunk.get("response", "")
                    if chunk.get("done"):
                        break
                except Exception:
                    continue
        return full_response.strip()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"Ollama no disponible: {e}")
    if resp.status_code != 200:
        raise HTTPException(502, f"Error Ollama {resp.status_code}: {resp.text[:300]}")
    return resp.json().get("response", "").strip()


def _extract_json(raw: str) -> dict | None:
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$",          "", raw, flags=re.MULTILINE)
    raw = raw.strip()
    try:
        return json.loads(raw)
    except Exception:
        # buscar el primer objeto JSON completo
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return None


def _clean_code(raw: str, file_path: str = "") -> str:
    """Limpia el output del modelo: quita fences markdown y texto suelto.
    NO recorta líneas de código válidas (bug anterior corregido).
    """
    raw = raw.strip()
    # Eliminar fence de apertura: ```python, ```py, ```html, ```bat, etc.
    raw = re.sub(r"^```[\w+\-#]*\s*\n", "", raw, flags=re.MULTILINE)
    # Eliminar fence de cierre
    raw = re.sub(r"\n```\s*$", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"^```\s*$", "", raw, flags=re.MULTILINE)

    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    prose_safe_exts = {
        "bat", "cmd", "css", "csv", "html", "htm", "ini", "json", "md",
        "toml", "txt", "xml", "yml", "yaml",
    }
    if ext in prose_safe_exts:
        return raw.strip()

    lines = raw.splitlines()
    CODE_SIGNALS = {
        "=", "(", ":", "{", "[", '"', "'", ";", "//", "/*", "-->",
        "import", "from", "def ", "class ", "var ", "function ",
        "const ", "let ", "#!", "<?", "<!", "@", "->", "=>",
    }

    # Eliminar prosa pura al inicio
    first_code = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if any(sig in stripped for sig in CODE_SIGNALS):
            first_code = i
            break

    lines = lines[first_code:]

    # Eliminar prosa pura al final (máx 3 líneas de explicación)
    result_lines = []
    trailing_prose: list[str] = []
    for line in lines:
        stripped = line.strip()
        is_code = any(sig in stripped for sig in CODE_SIGNALS) \
                  or stripped == "" \
                  or stripped.startswith("#") \
                  or stripped.startswith("//")
        if is_code:
            result_lines.extend(trailing_prose)
            trailing_prose = []
            result_lines.append(line)
        else:
            trailing_prose.append(line)
            if len(trailing_prose) > 3:
                break  # prosa larga al final → descartar

    return "\n".join(result_lines).strip()


@app.get("/generador", response_class=HTMLResponse)
async def generador_page():
    html_path = static_dir / "generador.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Error: static/generador.html no encontrado</h1>", status_code=500)


@app.get("/api/generador/history")
async def api_generador_history(user: dict = Depends(get_current_user)):
    rows = [
        _history_summary(row)
        for row in _load_generation_history()
        if row.get("user_id") == user["id"] or db.is_admin(user)
    ]
    return {"items": rows}


@app.get("/api/generador/history/{history_id}")
async def api_generador_history_detail(history_id: str, user: dict = Depends(get_current_user)):
    row = _find_generation_record(history_id, user)
    if not row:
        raise HTTPException(status_code=404, detail="Generación no encontrada")
    return row


@app.delete("/api/generador/history/{history_id}")
async def api_generador_history_delete(history_id: str, user: dict = Depends(get_current_user)):
    rows = _load_generation_history()
    kept = [
        row for row in rows
        if not (row.get("id") == history_id and (row.get("user_id") == user["id"] or db.is_admin(user)))
    ]
    if len(kept) == len(rows):
        raise HTTPException(status_code=404, detail="Generación no encontrada")
    _save_generation_history(kept)
    return {"status": "ok"}


@app.post("/api/generador/verificar")
async def api_generador_verificar(req: GeneradorFilesRequest, user: dict = Depends(get_current_user)):
    files = _sanitize_generated_files(req.files)
    return _verify_generated_files(files)


@app.post("/api/generador/exportar")
async def api_generador_exportar(req: GeneradorFilesRequest, user: dict = Depends(get_current_user)):
    files = _sanitize_generated_files(req.files)
    if not files:
        raise HTTPException(status_code=400, detail="No hay archivos para exportar")
    target = _write_project_to_disk(user, req.project_name, files)
    return {
        "status": "ok",
        "path": str(target),
        "file_count": len(files),
    }


@app.post("/api/generador/regenerar-file")
async def api_generador_regenerar_file(
    req: RegenerateFileRequest,
    user: dict = Depends(get_current_user),
):
    files = _sanitize_generated_files(req.files)
    target_path = _safe_generated_path(req.target_path, "file.txt")
    current = next((f for f in files if f["path"] == target_path), None)
    if not current:
        raise HTTPException(status_code=404, detail="Archivo objetivo no encontrado")

    context = "\n".join(f"- {f['path']} ({len(f['content'].splitlines())} líneas)" for f in files[:80])
    prompt = (
        "Reescribe por completo un único archivo de un proyecto generado.\n"
        "Devuelve SOLO el contenido crudo del archivo, sin markdown ni explicación.\n\n"
        f"Proyecto: {req.project_name}\n"
        f"Stack: {req.stack}\n"
        f"Solicitud original: {_build_advanced_prompt(req.prompt, req.options or {})}\n"
        f"Archivo objetivo: {target_path}\n"
        f"Instrucciones de regeneración: {req.instructions or 'Mejora calidad, corrige errores y mantén compatibilidad.'}\n\n"
        f"Mapa de archivos:\n{context}\n\n"
        f"Contenido actual de {target_path}:\n{current['content'][:12000]}\n\n"
        "Nuevo contenido completo:"
    )
    try:
        raw = await _ollama_generate(
            app_state.llm.base_url,
            app_state.llm.client,
            GENERADOR_AGENT_B_MODEL,
            prompt,
            max_tokens=4500,
            temperature=0.06,
            num_ctx=8192,
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"No se pudo regenerar con Ollama: {e}")

    content = _clean_code(raw, target_path)
    updated = [{"path": f["path"], "content": content if f["path"] == target_path else f["content"]} for f in files]
    return {
        "path": target_path,
        "content": content,
        "lines": len(content.splitlines()),
        "verification": _verify_generated_files(updated),
    }


@app.post("/api/generador/generar")
async def api_generador_generar(
    req:  GeneradorRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """
    Generación en 3 fases con streaming NDJSON.

    Eventos emitidos:
        {"type":"phase",       "phase":N, "msg":"..."}
        {"type":"plan",        "project_name":"...", "stack":"...", "files":[...], "interface":"..."}
        {"type":"file_start",  "index":N, "total":N, "path":"..."}
        {"type":"file_done",   "index":N, "path":"...", "content":"...", "lines":N}
        {"type":"review_start","msg":"..."}
        {"type":"fix",         "path":"...", "content":"...", "lines":N, "changes":"..."}
        {"type":"done",        "project_name":"...", "files":[...], "msg":"..."}
        {"type":"error",       "msg":"..."}
    """
    req.prompt = (req.prompt or "").strip()
    req.language = (req.language or "").strip()[:GENERADOR_MAX_LANGUAGE_CHARS]
    if not req.prompt:
        raise HTTPException(status_code=400, detail="La descripción del proyecto es obligatoria")
    if len(req.prompt) > GENERADOR_MAX_PROMPT_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"La descripción es demasiado larga ({len(req.prompt)} caracteres). Máximo: {GENERADOR_MAX_PROMPT_CHARS}.",
        )
    if req.size not in GENERADOR_ALLOWED_SIZES:
        req.size = "4 to 8"

    # Remapeo interno: los valores del selector siguen igual en la UI
    # pero internamente exigimos MÁS archivos para conseguir proyectos completos
    # Rangos reales mostrados en la UI — el arquitecto apunta al punto medio.
    # UI:  Pequeño=7..11, Mediano=12..18, Grande=20..28
    # target_f = punto medio que el arquitecto DEBE alcanzar.
    size_map = {
        "2 to 4":  (7,  11,  9),   # UI: "Pequeño"  — objetivo 9
        "4 to 8":  (12, 18, 15),   # UI: "Mediano"  — objetivo 15
        "8 to 14": (20, 28, 24),   # UI: "Grande"   — objetivo 24
    }
    min_f, max_f, target_f = size_map.get(req.size, (12, 18, 15))
    lang_hint = f"Primary language: {req.language}.\n" if req.language else ""
    model_prompt = _build_advanced_prompt(req.prompt, req.options or {})

    async def stream():
        base_url = app_state.llm.base_url
        client   = app_state.llm.client

        async def client_disconnected() -> bool:
            try:
                return await request.is_disconnected()
            except RuntimeError:
                return False

        def ndjson(payload: dict) -> str:
            return json.dumps(payload, ensure_ascii=False) + "\n"

        # ══════════════════════════════════════════════════════════════════
        #  FASE 1 — ARQUITECTO (14b): plan + contrato de interfaces completo
        # ══════════════════════════════════════════════════════════════════
        architect_prompt = build_architect_prompt(
            req_prompt=model_prompt,
            lang_hint=lang_hint,
            min_f=min_f,
            max_f=max_f,
            target_f=target_f,
        )

        try:
            yield ndjson({"type":"phase","phase":1,
                "msg":f"🏗️ Arquitecto ({GENERADOR_ARCHITECT_MODEL}) diseñando estructura, carpetas e interfaces…"})

            # Pre-calentar modelo: si no está en VRAM Ollama lo carga ahora.
            # Emitimos eventos de progreso para que la UI no parezca congelada.
            yield ndjson({"type":"phase","phase":1,
                "msg":f"⏳ Cargando {GENERADOR_ARCHITECT_MODEL} en memoria…"})
            try:
                await _ollama_generate(
                    base_url, client, GENERADOR_ARCHITECT_MODEL,
                    "hi", max_tokens=1, temperature=0.0, num_ctx=512,
                    cancel_check=client_disconnected,
                )
            except Exception:
                pass  # Si falla el ping lo intentamos igualmente con la llamada real

            yield ndjson({"type":"phase","phase":1,
                "msg":f"✏️ Generando plan detallado del proyecto…"})
            yield ndjson({"type":"thinking_start", "msg": "📐 Arquitecto pensando…"})

            raw_plan = None
            async for event in _ollama_generate_stream_events(
                base_url, client, GENERADOR_ARCHITECT_MODEL,
                architect_prompt, max_tokens=5500, temperature=0.05,
                num_ctx=8192, cancel_check=client_disconnected,
            ):
                if isinstance(event, str) and not event.startswith("{"):
                    # Último elemento: texto completo acumulado
                    raw_plan = event
                else:
                    # Evento thinking_token — reenviar al cliente
                    yield event
            if raw_plan is None:
                raise Exception("No se recibió respuesta del arquitecto")
        except (asyncio.CancelledError, GeneratorExit):
            return
        except Exception as e:
            yield ndjson({"type":"error","msg":f"Error en arquitecto: {e}"})
            return

        yield ndjson({"type":"thinking_end"})

        plan = _extract_json(raw_plan)
        if not plan or not plan.get("files"):
            yield ndjson({"type":"error",
                "msg":"El arquitecto no generó un plan válido. Añade más detalle a la descripción."})
            return

        project_name = _safe_project_name(plan.get("project_name", "proyecto"))
        raw_plan_files = plan.get("files", [])
        truncated_files = max(0, len(raw_plan_files) - max_f) if isinstance(raw_plan_files, list) else 0
        plan_files = _sanitize_plan_files(raw_plan_files if isinstance(raw_plan_files, list) else [], max_f)

        # ── Guardia de archivos mínimos ──────────────────────────────────
        # Solo rechazamos si el arquitecto generó MENOS archivos del mínimo.
        # No exigimos nombres concretos: el arquitecto puede llamar al punto
        # de entrada "snake.py", "app.py", "game.py", etc. según el proyecto.
        if len(plan_files) < min_f:
            detail = (
                f"Solo generó {len(plan_files)} archivo(s) (mínimo esperado: {min_f}). "
                "El modelo probablemente truncó el JSON. "
                "Intenta con una descripción más corta o un proyecto más pequeño."
            )
            yield ndjson({"type":"error", "msg": f"Plan incompleto — {detail}"})
            return
        stack        = plan.get("stack", "")
        contract     = plan.get("interface_contract", {})
        contract_str      = json.dumps(contract, ensure_ascii=False, indent=2)
        responsibility_map = plan.get("responsibility_map", {})
        responsibility_str = json.dumps(responsibility_map, ensure_ascii=False, indent=2)
        import_root        = plan.get("import_root", "")

        yield ndjson({"type":"plan","project_name":project_name,"stack":stack,
            "files":plan_files,"interface":contract_str,
            "msg":f"📋 Plan listo — {len(plan_files)} archivos en subcarpetas · {stack}"})

        if truncated_files:
            yield ndjson({
                "type": "review_start",
                "msg": f"⚠ El plan traía {truncated_files} archivo(s) extra; se limitó a {max_f} para evitar una generación interminable.",
            })

        # ══════════════════════════════════════════════════════════════════
        #  GUARDIA DE CROSS-IMPORTS: verifica que cada path importado exista
        #  en el plan. Si un archivo importa de game/utils.py pero el
        #  arquitecto no planificó game/utils.py → error temprano.
        # ══════════════════════════════════════════════════════════════════
        import re as _re

        def _module_to_path(import_line: str) -> str | None:
            """Convierte 'from game.utils import X' → 'game/utils.py'."""
            m = _re.match(r"from\s+([\w.]+)\s+import", import_line.strip())
            if not m:
                return None
            module = m.group(1)
            # Solo procesar imports del proyecto (descartar stdlib/terceros)
            # Heurística: si empieza por el import_root del plan, es del proyecto
            if import_root and not module.startswith(import_root):
                return None
            return module.replace(".", "/") + ".py"

        plan_paths = {f.get("path", "") for f in plan_files}
        ghost_imports: list[tuple[str, str]] = []  # (archivo_origen, import_roto)

        for finfo in plan_files:
            fpath_check = finfo.get("path", "")
            for imp_line in finfo.get("imports_needed", []):
                src_path = _module_to_path(imp_line)
                if src_path and src_path not in plan_paths:
                    ghost_imports.append((fpath_check, imp_line))

        if ghost_imports:
            ghost_details = "; ".join(
                f"{fp} importa '{imp}' pero el archivo fuente no está en el plan"
                for fp, imp in ghost_imports[:5]  # mostrar máx 5
            )
            yield ndjson({
                "type": "error",
                "msg": (
                    f"Plan inválido — {len(ghost_imports)} import(s) fantasma detectado(s): "
                    f"{ghost_details}. "
                    "El arquitecto planificó imports de archivos que no va a generar. "
                    "Intenta de nuevo con una descripción más concisa."
                ),
            })
            return

        yield ndjson({
            "type": "review_start",
            "msg": f"✅ Cross-imports verificados — todos los {len(plan_files)} archivos son coherentes.",
        })

        # ══════════════════════════════════════════════════════════════════
        #  FASE 2 — DUAL SUBAGENTE (Agente A + Agente B por archivo)
        #
        #  Agente A (qwen2.5-coder:7b) → escribe implementación inicial
        #  Agente B (qwen2.5-coder:7b) → lee código de A, critica y REESCRIBE
        #                                 con más features, mejor código y más líneas
        #  Output final = código de Agente B
        # ══════════════════════════════════════════════════════════════════
        yield ndjson({"type":"phase","phase":2,
            "msg":f"🤖🤖 Lanzando Dual-Subagente ({GENERADOR_AGENT_A_MODEL} × 2) — diálogo A→B por cada archivo…"})

        generated: list[dict] = []

        for i, finfo in enumerate(plan_files):
            if await client_disconnected():
                return
            fpath          = finfo.get("path", f"file_{i}.txt")
            fdesc          = finfo.get("description","")
            feats          = finfo.get("key_features",[])
            exports        = finfo.get("exports",[])
            imp_map        = finfo.get("imports_from",{})
            imports_needed = finfo.get("imports_needed", [])  # nuevo campo del arquitecto

            yield ndjson({"type":"file_start","index":i,"total":len(plan_files),
                "path":fpath,"model":f"A:{GENERADOR_AGENT_A_MODEL}→B:{GENERADOR_AGENT_B_MODEL}",
                "msg":f"⚙️ [{i+1}/{len(plan_files)}] Agente A → {fpath}"})

            feats_str = "\n".join(f"  - {f}" for f in feats)

            # ── Detectar tipo de archivo ─────────────────────────────────
            ext = fpath.rsplit(".", 1)[-1].lower() if "." in fpath else ""
            is_bat  = ext == "bat"
            is_json = ext == "json"
            is_md   = ext == "md"
            is_txt  = ext in ("txt", "cfg", "ini", "yml", "yaml")
            is_html = ext in ("html", "htm")

            if is_bat:
                min_lines_hint = "at least 20 lines with real commands (HARD MINIMUM)"
            elif is_json:
                min_lines_hint = "complete and realistic JSON data"
            elif is_md:
                min_lines_hint = "at least 80 lines covering all sections (HARD MINIMUM)"
            elif is_html:
                min_lines_hint = "at least 180 lines (HARD MINIMUM — writing fewer is a FAILURE)"
            else:
                min_lines_hint = "at least 250 lines (HARD MINIMUM — writing fewer is a FAILURE, aim for 300+"

            # ── AGENTE A: Implementador ──────────────────────────────────
            agent_a_prompt = build_agent_a_prompt(
                req_prompt        = model_prompt,
                lang_hint         = lang_hint,
                fpath             = fpath,
                fdesc             = fdesc,
                feats_str         = feats_str,
                exports           = exports,
                imp_map           = imp_map,
                imports_needed    = imports_needed,
                contract_str      = contract_str,
                responsibility_map= responsibility_str,
                is_bat            = is_bat,
                is_json           = is_json,
                is_md             = is_md,
                is_txt            = is_txt,
                is_html           = is_html,
                min_lines_hint    = min_lines_hint,
            )

            try:
                raw_a = await _ollama_generate(
                    base_url, client, GENERADOR_AGENT_A_MODEL,
                    agent_a_prompt, max_tokens=4000, temperature=0.07,
                    num_ctx=6144,   # 7b: 4.5GB modelo + 2.4GB KV@6144 = 6.9GB < 11GB VRAM
                    cancel_check=client_disconnected,
                )
                code_a = _clean_code(raw_a, fpath)
            except Exception as e:
                code_a = f"# ⚠ Agent A error generating {fpath}: {e}"

            lines_a = len(code_a.splitlines())

            # ── AGENTE B: Crítico / Enriquecedor ────────────────────────
            yield ndjson({"type":"file_start","index":i,"total":len(plan_files),
                "path":fpath,"model":GENERADOR_AGENT_B_MODEL,
                "msg":f"🔄 [{i+1}/{len(plan_files)}] Agente B enriqueciendo {fpath} ({lines_a} líneas de A)…"})

            if is_bat or is_json or is_md or is_txt:
                enrichment_goal = "Improve clarity, completeness, and correctness."
                min_b_lines     = "longer and more complete than Agent A's version"
            else:
                enrichment_goal = (
                    "Rewrite with MORE features, MORE error handling, "
                    "MORE helper methods. Target 300+ lines."
                )
                min_b_lines = "at least 50 more lines than Agent A's version, aiming for 300+ total"

            # ── AGENTE B: Crítico / Enriquecedor ────────────────────────
            agent_b_prompt = build_agent_b_prompt(
                req_prompt        = model_prompt,
                fpath             = fpath,
                fdesc             = fdesc,
                code_a            = code_a,
                contract_str      = contract_str,
                responsibility_map= responsibility_str,
                imports_needed    = imports_needed,
                is_bat            = is_bat,
                is_json           = is_json,
                is_md             = is_md,
                is_txt            = is_txt,
                enrichment_goal   = enrichment_goal,
                min_b_lines       = min_b_lines,
            )

            try:
                raw_b = await _ollama_generate(
                    base_url, client, GENERADOR_AGENT_B_MODEL,
                    agent_b_prompt, max_tokens=4000, temperature=0.07,
                    num_ctx=6144,   # 7b: 4.5GB modelo + 2.4GB KV@6144 = 6.9GB < 11GB VRAM
                    cancel_check=client_disconnected,
                )
                content = _clean_code(raw_b, fpath)
            except Exception as e:
                # Fallback: usar código de Agente A
                content = code_a
                yield ndjson({"type":"review_start",
                    "msg":f"⚠ Agente B falló en {fpath} ({e}), usando código de A."})

            lines_final = len(content.splitlines())
            generated.append({"path": fpath, "content": content})

            yield ndjson({
                "type":    "file_done",
                "index":   i,
                "path":    fpath,
                "content": content,
                "lines":   lines_final,
                "lines_a": lines_a,
                "model":   f"{GENERADOR_AGENT_A_MODEL}→{GENERADOR_AGENT_B_MODEL}",
                "msg":     f"✅ {fpath} — A:{lines_a}→B:{lines_final} líneas (+{lines_final-lines_a})",
            })

        # ══════════════════════════════════════════════════════════════════
        #  FASE 3 — REVISOR (14b): coherencia cruzada entre todos los archivos
        # ══════════════════════════════════════════════════════════════════
        if await client_disconnected():
            return
        yield ndjson({"type":"phase","phase":3,
            "msg":f"🔍 Revisor ({GENERADOR_REVIEWER_MODEL}) verificando coherencia entre {len(generated)} archivos…"})
        yield ndjson({"type":"review_start",
            "msg":"Analizando imports, rutas de módulos, métodos y variables entre todos los archivos…"})

        # Snapshot: solo archivos .py / .js / .ts para el revisor (sin assets binarios)
        code_files = [f for f in generated
                      if f["path"].endswith((".py",".js",".ts",".jsx",".tsx",".html",".bat"))]
        files_snapshot = "\n\n".join(
            f"### FILE: {f['path']} ###\n{f['content'][:4000]}" for f in code_files
        )

        reviewer_prompt = build_reviewer_prompt(
            req_prompt        = model_prompt,
            stack             = stack,
            files_snapshot    = files_snapshot,
            contract_str      = contract_str,
            responsibility_map= responsibility_str,
        )

        try:
            raw_review = await _ollama_generate(
                base_url, client, GENERADOR_REVIEWER_MODEL,
                reviewer_prompt, max_tokens=2500, temperature=0.0,
                num_ctx=4096,   # 14b: mantiene calidad con contexto seguro para 11GB VRAM
                cancel_check=client_disconnected,
            )
            review = _extract_json(raw_review)
        except Exception as e:
            review = None
            yield ndjson({"type":"review_start",
                "msg":f"⚠ Revisor falló ({e}), usando archivos originales."})

        if review and review.get("fixes"):
            fixes_map = {
                fix.get("path", ""): fix
                for fix in review["fixes"]
                if isinstance(fix, dict) and fix.get("path") and fix.get("fixed_content")
            }
            for fix_path, fix in fixes_map.items():
                fix_path = _safe_generated_path(fix_path, "file_fixed.txt")
                corrected = _clean_code(fix["fixed_content"], fix_path)
                issue     = fix.get("issue","corrección aplicada")
                for gf in generated:
                    if gf["path"] == fix_path:
                        gf["content"] = corrected
                        break
                yield ndjson({"type":"fix","path":fix_path,
                    "content":corrected,"lines":len(corrected.splitlines()),
                    "changes":issue,
                    "msg":f"🔧 Corregido {fix_path}: {issue}"})
        else:
            yield ndjson({"type":"review_start",
                "msg":"✅ Revisión completada — no se encontraron errores cruzados."})

        # ══════════════════════════════════════════════════════════════════
        #  DONE
        # ══════════════════════════════════════════════════════════════════
        total_lines = sum(len(f["content"].splitlines()) for f in generated)
        verification = _verify_generated_files(generated)
        history_id = _save_generation_record(user, req, project_name, stack, generated, total_lines)
        yield ndjson({
            "type":             "done",
            "project_name":     project_name,
            "stack":            stack,
            "model_architect":  GENERADOR_ARCHITECT_MODEL,
            "model_subagent":   f"{GENERADOR_AGENT_A_MODEL} + {GENERADOR_AGENT_B_MODEL}",
            "model_reviewer":   GENERADOR_REVIEWER_MODEL,
            "files":            generated,
            "history_id":       history_id,
            "verification":     verification,
            "total_lines":      total_lines,
            "msg":              f"🎉 Proyecto listo — {len(generated)} archivos · {total_lines:,} líneas totales",
        })

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

def main():
    print("=" * 60)
    print("  == ANALIZADOR DE DATOS CON IA LOCAL v2.0 ==")
    print("=" * 60)
    print(f"  Sistema: Registro + Historial de chats")
    print(f"  GPU:     GTX 1080 Ti (11GB VRAM)")
    print(f"  Modelo:  {app_state.llm.model}")
    print(f"  Web:     http://localhost:{SERVER_CONFIG['port']}")
    print("=" * 60)

    uvicorn.run(
        "app.main:app",
        host=SERVER_CONFIG["host"],
        port=SERVER_CONFIG["port"],
        reload=SERVER_CONFIG["reload"],
        workers=SERVER_CONFIG["workers"],
        log_level="info",
    )


@app.get("/api/debug/search")
def debug_search(q: str = "noticias"):
    """Endpoint de diagnostico para la busqueda web (sincrono)."""
    import xml.etree.ElementTree as ET
    import httpx as _httpx
    import traceback

    results = {"query": q, "rss": {}, "ddg": "no probado", "final": ""}
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
    }

    for name, url in _RSS_FEEDS:
        try:
            r = _httpx.get(url, headers=hdrs, timeout=8, follow_redirects=True, verify=False)
            if r.status_code == 200:
                root = ET.fromstring(r.content)
                items = root.findall(".//item")
                titulo = items[0].findtext("title", "") if items else ""
                results["rss"][name] = f"OK - {len(items)} items - primer titulo: '{titulo[:60]}'"
            else:
                results["rss"][name] = f"HTTP {r.status_code}"
        except Exception as e:
            results["rss"][name] = f"ERROR {type(e).__name__}: {str(e)[:120]}"

    if WEB_SEARCH_AVAILABLE:
        try:
            with DDGS() as ddgs:
                news = list(ddgs.news(q, max_results=2, region="es-es"))
            results["ddg"] = f"OK - {len(news)} resultados - '{news[0].get('title','') if news else ''}'"
        except Exception as e:
            results["ddg"] = f"ERROR: {str(e)[:120]}"

    try:
        results["final"] = _buscar_web_sync(q, 3)
    except Exception as e:
        results["final"] = f"ERROR: {traceback.format_exc()}"

    return results



# ═══════════════════════════════════════════════════════════════════════════════
# VOID AXIOM — Rutas del sistema A2A
# ═══════════════════════════════════════════════════════════════════════════════

from app.agent_chat import void_session, AGENTS


# ─── Página principal de Void Axiom ──────────────────────────────────────────

@app.get("/void", response_class=HTMLResponse)
async def void_axiom_page(user: dict = Depends(get_current_user)):
    """Página del sistema A2A — accesible solo para usuarios autenticados."""
    html_path = Path("static/void_axiom.html")
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>void_axiom.html no encontrado</h1>", status_code=404)


# ─── SSE — Stream de mensajes en tiempo real ─────────────────────────────────

@app.get("/api/void/stream")
async def void_stream(request: Request, user: dict = Depends(get_current_user)):
    """
    Server-Sent Events: transmite mensajes del canal A2A en tiempo real.
    Todos los usuarios autenticados pueden escuchar.
    """
    queue = void_session.subscribe()

    async def event_generator():
        # Enviar estado actual al conectarse
        init_payload = {
            "event": "init",
            "data": {
                "session_id": void_session.session_id,
                "task": void_session.task,
                "is_active": void_session.is_active,
                "is_paused": void_session.is_paused,
                "agents": {
                    k: {
                        "display_name": v["display_name"],
                        "role": v["role"],
                        "color": v["color"],
                        "icon": v["icon"],
                        "model": v["model"],
                    }
                    for k, v in AGENTS.items()
                },
                "history": void_session.message_history[-80:],
            },
        }
        yield f"data: {json.dumps(init_payload, ensure_ascii=False)}\n\n"

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    # Keepalive
                    yield ": keepalive\n\n"
        finally:
            void_session.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ─── Control de sesión (solo Aerys) ──────────────────────────────────────────

class VoidStartRequest(BaseModel):
    task: str


@app.post("/api/void/start")
async def void_start(body: VoidStartRequest, admin: dict = Depends(get_admin_user)):
    """Inicia una nueva sesión de trabajo para los agentes."""
    task = body.task.strip()
    if not task:
        raise HTTPException(status_code=400, detail="La tarea no puede estar vacia")

    if void_session.is_active:
        previous_session_id = void_session.session_id
        void_session.stop()
        if previous_session_id:
            db.update_agent_session_status(previous_session_id, "stopped")
        await asyncio.sleep(0.3)

    session_id = db.create_agent_session(task)
    void_session.start_session(task, session_id)

    # Lanzar el loop de agentes como tarea de fondo
    void_session._loop_task = asyncio.create_task(void_session.run_agent_loop())

    return {
        "status": "started",
        "session_id": session_id,
        "task": task,
    }


@app.post("/api/void/pause")
async def void_pause(admin: dict = Depends(get_admin_user)):
    """Pausa el loop de agentes."""
    void_session.pause()
    return {"status": "paused"}


@app.post("/api/void/resume")
async def void_resume(admin: dict = Depends(get_admin_user)):
    """Reanuda el loop de agentes."""
    void_session.resume()
    return {"status": "resumed"}


@app.post("/api/void/stop")
async def void_stop(admin: dict = Depends(get_admin_user)):
    """Detiene la sesión actual."""
    void_session.stop()
    if void_session.session_id:
        db.update_agent_session_status(void_session.session_id, "stopped")
    return {"status": "stopped"}


# ─── Intervención de Aerys ────────────────────────────────────────────────────

class VoidInterventionRequest(BaseModel):
    message: str


class VoidPrivateRequest(BaseModel):
    agent_id: str
    message: str
    include_context: bool = True


@app.post("/api/void/intervene")
async def void_intervene(
    body: VoidInterventionRequest,
    admin: dict = Depends(get_admin_user),
):
    """
    Aerys cruza el umbral e inyecta un mensaje al canal.
    Los agentes reaccionan según sus personalidades.
    """
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="El mensaje no puede estar vacio")
    asyncio.create_task(void_session.aerys_intervene(message))
    return {"status": "intervention_sent", "message": message}


@app.post("/api/void/private")
async def void_private(
    body: VoidPrivateRequest,
    admin: dict = Depends(get_admin_user),
):
    """Consulta privada a un agente, sin emitirla al canal A2A público."""
    if body.agent_id not in AGENTS:
        raise HTTPException(status_code=400, detail="Agente no válido")
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="El mensaje no puede estar vacio")
    context = void_session.build_context_snapshot(limit=5, max_chars=160) if body.include_context else ""
    context = f"{context}\n[AERYS_PRIVADO]: {message}"
    response = await void_session._generate_agent_response(
        body.agent_id,
        context,
        extra_instruction=(
            "CANAL PRIVADO: responde solo a Aerys. No emitas órdenes al canal público. "
            "Sé concreto, técnico y útil."
        ),
        conversation_mode="technical",
    )
    return {
        "agent_id": body.agent_id,
        "message": message,
        "response": response,
        "created_at": _now_iso(),
    }


# ─── Estado y sesiones ────────────────────────────────────────────────────────

@app.get("/api/void/status")
async def void_status(user: dict = Depends(get_current_user)):
    """Estado actual del canal A2A."""
    return {
        "is_active": void_session.is_active,
        "is_paused": void_session.is_paused,
        "session_id": void_session.session_id,
        "task": void_session.task,
        "message_count": len(void_session.message_history),
        "subscribers": len(void_session.subscribers),
        "is_admin": db.is_admin(user),
        "memory_mode": "extended_55k" if void_session._extended_context_enabled else "standard_16k",
        "activity": void_activity.snapshot(),
    }


@app.get("/api/void/sessions")
async def void_sessions(admin: dict = Depends(get_admin_user)):
    """Lista histórica de sesiones A2A."""
    return {"sessions": db.get_all_agent_sessions()}


@app.get("/api/void/history")
async def void_history(user: dict = Depends(get_current_user)):
    """Historial de mensajes de la sesión actual en memoria."""
    return {
        "session_id": void_session.session_id,
        "task": void_session.task,
        "messages": void_session.message_history,
    }


@app.get("/api/void/export")
async def void_export(admin: dict = Depends(get_admin_user)):
    return {
        "exported_at": _now_iso(),
        "session_id": void_session.session_id,
        "task": void_session.task,
        "is_active": void_session.is_active,
        "is_paused": void_session.is_paused,
        "rolling_summary": void_session.rolling_summary,
        "messages": void_session.message_history,
    }


if __name__ == "__main__":
    main()
