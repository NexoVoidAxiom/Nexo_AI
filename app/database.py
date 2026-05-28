"""
Base de datos SQLite - Usuarios, Sesiones, Chats, Archivos
===========================================================
Gestiona todo el estado persistente de la aplicacion:
- Usuarios (registro/login con hash de contrasena)
- Sesiones (tokens de 30 dias)
- Chats (conversaciones por usuario)
- Mensajes (historial de cada chat)
- Archivos (archivos subidos vinculados a cada chat)
"""
import sqlite3
import secrets
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

# SEGURIDAD: bcrypt es resistente a fuerza bruta (SHA-256 no lo es).
# passlib[bcrypt] ya está en requirements.txt.
from passlib.context import CryptContext

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

DB_PATH = Path("data/analizador.db")


@contextmanager
def get_db():
    """Context manager para conexiones SQLite thread-safe."""
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")      # Mejor concurrencia
    conn.execute("PRAGMA synchronous=NORMAL")    # 2-3× más rápido que FULL, seguro con WAL
    conn.execute("PRAGMA foreign_keys=ON")       # Integridad referencial
    conn.execute("PRAGMA cache_size=-32000")     # 32 MB de caché de páginas en RAM
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Crea todas las tablas si no existen."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                username     TEXT    UNIQUE NOT NULL,
                email        TEXT    UNIQUE NOT NULL,
                password_hash TEXT   NOT NULL,
                salt         TEXT    NOT NULL,
                created_at   TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                token      TEXT    UNIQUE NOT NULL,
                created_at TEXT    DEFAULT (datetime('now')),
                expires_at TEXT    NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS chats (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                title      TEXT    DEFAULT 'Chat nuevo',
                created_at TEXT    DEFAULT (datetime('now')),
                updated_at TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    INTEGER NOT NULL,
                role       TEXT    NOT NULL,
                content    TEXT    NOT NULL,
                tokens     INTEGER DEFAULT 0,
                created_at TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS chat_files (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id      INTEGER NOT NULL,
                user_id      INTEGER NOT NULL,
                filename     TEXT    NOT NULL,
                text_content TEXT    NOT NULL,
                tokens       INTEGER DEFAULT 0,
                metadata_json TEXT   DEFAULT '{}',
                created_at   TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
            );

            -- ═══════════════════════════════════════════════════════════
            -- API KEYS — Acceso programático externo (curl, scripts…)
            -- ═══════════════════════════════════════════════════════════
            CREATE TABLE IF NOT EXISTS api_keys (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                key_hash     TEXT    UNIQUE NOT NULL,   -- SHA-256 de la key real
                key_prefix   TEXT    NOT NULL,          -- Primeros 12 chars para mostrar
                name         TEXT    NOT NULL DEFAULT 'Mi API Key',
                is_active    INTEGER NOT NULL DEFAULT 1,
                created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
                last_used_at TEXT    DEFAULT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_token   ON sessions(token);
            CREATE INDEX IF NOT EXISTS idx_chats_user       ON chats(user_id);
            CREATE INDEX IF NOT EXISTS idx_messages_chat    ON chat_messages(chat_id);
            CREATE INDEX IF NOT EXISTS idx_files_chat       ON chat_files(chat_id);
            CREATE INDEX IF NOT EXISTS idx_api_keys_hash    ON api_keys(key_hash);
            CREATE INDEX IF NOT EXISTS idx_api_keys_user    ON api_keys(user_id);
        """)

        # ── Migración segura: añadir columnas de plan a tabla existente ──────
        _safe_alter(conn, "users", "plan",             "TEXT NOT NULL DEFAULT 'free_limited'")
        _safe_alter(conn, "users", "pioneer_number",   "INTEGER DEFAULT NULL")
        _safe_alter(conn, "users", "plan_assigned_at", "TEXT DEFAULT NULL")

        # ── Garantía: Aerys siempre tiene plan_max, independiente del estado de la BD ──
        _ensure_aerys_plan_max(conn)

        conn.executescript("""

            -- ═══════════════════════════════════════════════════════════
            -- VOID AXIOM — Sesiones y mensajes de agentes A2A
            -- ═══════════════════════════════════════════════════════════
            CREATE TABLE IF NOT EXISTS agent_sessions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                task        TEXT    NOT NULL,
                status      TEXT    DEFAULT 'active',
                created_at  TEXT    DEFAULT (datetime('now')),
                updated_at  TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS agent_messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  INTEGER NOT NULL,
                agent_id    TEXT    NOT NULL,
                content     TEXT    NOT NULL,
                msg_type    TEXT    DEFAULT 'chat',
                created_at  TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (session_id) REFERENCES agent_sessions(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_agent_msgs ON agent_messages(session_id);
        """)


# ═══════════════════════════════════════════════════════════════
# USUARIOS
# ═══════════════════════════════════════════════════════════════

def _hash_password(password: str, salt: str = None) -> tuple[str, str]:
    """Hashea una contraseña con bcrypt (resistente a fuerza bruta).

    El parámetro 'salt' se ignora: bcrypt genera e incrusta su propio salt
    automáticamente. Se mantiene la firma (pw_hash, salt) para compatibilidad
    con el resto del código, devolviendo salt="" en su lugar.

    NOTA: Si la BD tiene usuarios antiguos con SHA-256, sus contraseñas
    seguirán funcionando hasta que vuelvan a iniciar sesión, momento en que
    se migrarán automáticamente a bcrypt (ver authenticate_user).
    """
    pw_hash = _pwd_ctx.hash(password)
    return pw_hash, ""  # bcrypt incrusta el salt en el hash


def create_user(username: str, email: str, password: str) -> dict | None:
    """Crea un usuario nuevo. Devuelve None si ya existe."""
    if len(password) < 6:
        raise ValueError("La contraseña debe tener al menos 6 caracteres")
    if len(username) < 3:
        raise ValueError("El nombre de usuario debe tener al menos 3 caracteres")

    pw_hash, _ = _hash_password(password)
    try:
        with get_db() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, email, password_hash, salt) VALUES (?, ?, ?, ?)",
                (username.strip(), email.strip().lower(), pw_hash, ""),
            )
            return {"id": cur.lastrowid, "username": username, "email": email}
    except sqlite3.IntegrityError:
        return None  # Usuario o email ya existe


def authenticate_user(username_or_email: str, password: str) -> dict | None:
    """Verifica credenciales. Devuelve el usuario o None.

    Migración automática SHA-256 → bcrypt: si el hash almacenado empieza por
    '$2b$' ya es bcrypt; si no, se verifica con el SHA-256 antiguo y, en caso
    de éxito, se re-hashea con bcrypt para futuros logins.
    """
    import hashlib as _hashlib  # solo para migración de hashes legacy

    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username = ? OR email = ?",
            (username_or_email.strip(), username_or_email.strip().lower()),
        ).fetchone()

    if not user:
        return None

    stored_hash = user["password_hash"]

    # ── Hash moderno (bcrypt) ──────────────────────────────────────────────
    if stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"):
        if _pwd_ctx.verify(password, stored_hash):
            return dict(user)
        return None

    # ── Hash legacy (SHA-256 + salt) — migración automática ───────────────
    legacy_hash = _hashlib.sha256((password + user["salt"]).encode("utf-8")).hexdigest()
    if secrets.compare_digest(legacy_hash, stored_hash):
        # Contraseña correcta: re-hashear con bcrypt y guardar
        new_hash = _pwd_ctx.hash(password)
        with get_db() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, salt = '' WHERE id = ?",
                (new_hash, user["id"]),
            )
        return dict(user)

    return None


def get_user_by_id(user_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


# ═══════════════════════════════════════════════════════════════
# SESIONES
# ═══════════════════════════════════════════════════════════════

def create_session(user_id: int) -> str:
    """Crea un token de sesion de 30 dias."""
    token = secrets.token_hex(32)
    expires_at = (datetime.utcnow() + timedelta(days=30)).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO sessions (user_id, token, expires_at) VALUES (?, ?, ?)",
            (user_id, token, expires_at),
        )
    return token


def get_session_user(token: str) -> dict | None:
    """Devuelve el usuario de una sesion valida, o None si expirada/invalida."""
    if not token:
        return None
    with get_db() as conn:
        row = conn.execute(
            """SELECT u.id, u.username, u.email, u.plan, u.created_at
                FROM users u
                JOIN sessions s ON s.user_id = u.id
                WHERE s.token = ? AND s.expires_at > datetime('now')""",
            (token,),
        ).fetchone()
    if not row:
        return None
    user = dict(row)
    # Aerys siempre tiene plan_max, sin excepción
    if user.get("username", "").strip().lower() == ADMIN_USERNAME.lower():
        user["plan"] = PLAN_MAX
    return user


def delete_session(token: str):
    """Invalida un token de sesion (logout)."""
    with get_db() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def cleanup_expired_sessions():
    """Limpia sesiones expiradas (llamar periodicamente)."""
    with get_db() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at <= datetime('now')")


# ═══════════════════════════════════════════════════════════════
# CHATS
# ═══════════════════════════════════════════════════════════════

def create_chat(user_id: int, title: str = "Chat nuevo") -> dict:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO chats (user_id, title) VALUES (?, ?)",
            (user_id, title[:80]),
        )
        chat_id = cur.lastrowid
        row = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
    return dict(row)


def get_user_chats(user_id: int) -> list[dict]:
    """Lista todos los chats del usuario con conteo de mensajes."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT c.id, c.title, c.created_at, c.updated_at,
                      COUNT(m.id) as message_count,
                      COUNT(f.id) as file_count
               FROM chats c
               LEFT JOIN chat_messages m ON m.chat_id = c.id
               LEFT JOIN chat_files    f ON f.chat_id = c.id
               WHERE c.user_id = ?
               GROUP BY c.id
               ORDER BY c.updated_at DESC""",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_chat(chat_id: int, user_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM chats WHERE id = ? AND user_id = ?",
            (chat_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def get_chat_by_id(chat_id: int) -> dict | None:
    """Devuelve un chat por su ID sin filtrar por user_id.

    Uso exclusivo para rutas de administrador que necesitan auditar
    el historial de cualquier usuario del sistema.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM chats WHERE id = ?",
            (chat_id,),
        ).fetchone()
    return dict(row) if row else None


def delete_chat(chat_id: int, user_id: int) -> bool:
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM chats WHERE id = ? AND user_id = ?",
            (chat_id, user_id),
        )
    return cur.rowcount > 0


def update_chat_title(chat_id: int, user_id: int, title: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE chats SET title = ?, updated_at = datetime('now') WHERE id = ? AND user_id = ?",
            (title[:80], chat_id, user_id),
        )


def touch_chat(chat_id: int):
    """Actualiza updated_at para que aparezca primero en el historial."""
    with get_db() as conn:
        conn.execute(
            "UPDATE chats SET updated_at = datetime('now') WHERE id = ?",
            (chat_id,),
        )


# ═══════════════════════════════════════════════════════════════
# MENSAJES
# ═══════════════════════════════════════════════════════════════

def add_message(chat_id: int, role: str, content: str, tokens: int = 0) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO chat_messages (chat_id, role, content, tokens) VALUES (?, ?, ?, ?)",
            (chat_id, role, content, tokens),
        )
        return cur.lastrowid


def get_chat_messages(chat_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM chat_messages WHERE chat_id = ? ORDER BY created_at ASC",
            (chat_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════
# ARCHIVOS DE CHAT
# ═══════════════════════════════════════════════════════════════

def add_chat_file(
    chat_id: int, user_id: int,
    filename: str, text_content: str,
    tokens: int, metadata: dict
) -> int:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO chat_files
               (chat_id, user_id, filename, text_content, tokens, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (chat_id, user_id, filename, text_content, tokens, json.dumps(metadata)),
        )
        return cur.lastrowid


def get_chat_files(chat_id: int) -> list[dict]:
    """Devuelve los archivos de un chat con metadata deserializada."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM chat_files WHERE chat_id = ? ORDER BY created_at ASC",
            (chat_id,),
        ).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        try:
            d["metadata"] = json.loads(d.get("metadata_json") or "{}")
        except Exception:
            d["metadata"] = {}
        result.append(d)
    return result


def remove_chat_file(chat_id: int, filename: str) -> bool:
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM chat_files WHERE chat_id = ? AND filename = ?",
            (chat_id, filename),
        )
    return cur.rowcount > 0


def clear_chat_files(chat_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM chat_files WHERE chat_id = ?", (chat_id,))


def get_total_chat_tokens(chat_id: int) -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(tokens), 0) as total FROM chat_files WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
    return row["total"] if row else 0


# ═══════════════════════════════════════════════════════════════
# ADMIN
# ═══════════════════════════════════════════════════════════════

ADMIN_USERNAME = "Aerys"


def _ensure_aerys_plan_max(conn) -> None:
    """
    Migración en caliente: si Aerys existe en la BD y no tiene plan_max,
    lo actualiza. Se llama durante init_db() para garantizar el estado correcto
    aunque la cuenta se haya creado antes de esta lógica.
    """
    conn.execute(
        """UPDATE users
           SET plan = ?, plan_assigned_at = COALESCE(plan_assigned_at, datetime('now'))
           WHERE LOWER(username) = LOWER(?) AND plan != ?""",
        (PLAN_MAX, ADMIN_USERNAME, PLAN_MAX),
    )


def is_admin(user: dict) -> bool:
    """Devuelve True si el usuario es el administrador."""
    return user.get("username", "").strip().lower() == ADMIN_USERNAME.lower()


def get_global_stats() -> dict:
    """Estadísticas globales del sistema para el panel de admin."""
    with get_db() as conn:
        users   = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        chats   = conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
        msgs    = conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
        files   = conn.execute("SELECT COUNT(*) FROM chat_files").fetchone()[0]
        sessions = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE expires_at > datetime('now')"
        ).fetchone()[0]
    return {
        "total_users": users,
        "total_chats": chats,
        "total_messages": msgs,
        "total_files": files,
        "active_sessions": sessions,
    }


def get_all_users() -> list[dict]:
    """Lista todos los usuarios (sin password_hash ni salt)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, username, email, created_at FROM users ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def delete_user_admin(user_id: int) -> bool:
    """Elimina un usuario y en cascada sus chats/sesiones/archivos."""
    with get_db() as conn:
        cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return cur.rowcount > 0


def delete_user_sessions_admin(user_id: int) -> int:
    """Cierra (elimina) todas las sesiones activas de un usuario."""
    with get_db() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return cur.rowcount


def get_all_chats_admin() -> list[dict]:
    """Lista todos los chats de todos los usuarios con nombre de usuario."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT c.id, c.title, c.created_at, c.updated_at,
                      u.username,
                      COUNT(m.id) as message_count
               FROM chats c
               JOIN users u ON u.id = c.user_id
               LEFT JOIN chat_messages m ON m.chat_id = c.id
               GROUP BY c.id
               ORDER BY c.updated_at DESC""",
        ).fetchall()
    return [dict(r) for r in rows]


def delete_chat_admin(chat_id: int) -> bool:
    """Elimina cualquier chat sin comprobar user_id."""
    with get_db() as conn:
        cur = conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    return cur.rowcount > 0


def get_user_messages_admin(user_id: int, limit: int = 100) -> list[dict]:
    """Devuelve los últimos mensajes de un usuario concreto."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT m.id, m.role, m.content, m.tokens, m.created_at,
                      c.title as chat_title
               FROM chat_messages m
               JOIN chats c ON c.id = m.chat_id
               WHERE c.user_id = ?
               ORDER BY m.created_at DESC
               LIMIT ?""",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]

# ═══════════════════════════════════════════════════════════════
# VOID AXIOM — Funciones de base de datos para agentes A2A
# ═══════════════════════════════════════════════════════════════

def create_agent_session(task: str) -> int:
    """Crea una nueva sesión de agentes y devuelve su ID."""
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO agent_sessions (task, status) VALUES (?, 'active')",
            (task,),
        )
        return cur.lastrowid


def get_agent_session(session_id: int) -> dict | None:
    """Obtiene una sesión de agentes por ID."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM agent_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def get_latest_agent_session() -> dict | None:
    """Obtiene la sesión más reciente."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM agent_sessions ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def update_agent_session_status(session_id: int, status: str):
    """Actualiza el estado de una sesión."""
    with get_db() as conn:
        conn.execute(
            "UPDATE agent_sessions SET status=?, updated_at=datetime('now') WHERE id=?",
            (status, session_id),
        )


def save_agent_message(
    session_id: int,
    agent_id: str,
    content: str,
    msg_type: str = "chat",
) -> int:
    """Persiste un mensaje de agente en la BD."""
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO agent_messages (session_id, agent_id, content, msg_type) VALUES (?,?,?,?)",
            (session_id, agent_id, content, msg_type),
        )
        return cur.lastrowid


def get_agent_messages(session_id: int, limit: int = 200) -> list[dict]:
    """Obtiene mensajes de una sesión."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, agent_id, content, msg_type, created_at
               FROM agent_messages WHERE session_id=?
               ORDER BY id ASC LIMIT ?""",
            (session_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_agent_sessions(limit: int = 20) -> list[dict]:
    """Lista todas las sesiones de agentes."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT s.id, s.task, s.status, s.created_at,
                      COUNT(m.id) as message_count
               FROM agent_sessions s
               LEFT JOIN agent_messages m ON m.session_id = s.id
               GROUP BY s.id
               ORDER BY s.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════
# ALPHA PIONERO — Lógica de plan por orden de registro
# Los primeros 50 usuarios obtienen "plan_max" gratis.
# Del 51 en adelante, "free_limited".
# ═══════════════════════════════════════════════════════════════════════════

PIONEER_LIMIT   = 50
PLAN_MAX        = "plan_max"
PLAN_FREE       = "free_limited"
PLAN_ADMIN      = "admin"


def _safe_alter(conn, table: str, column: str, definition: str) -> None:
    """Añade una columna si no existe. SQLite no soporta ADD COLUMN IF NOT EXISTS."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except Exception:
        pass  # La columna ya existe


def get_pioneer_count() -> int:
    """Devuelve el número de usuarios con plan_max asignado (excl. admin)."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM users WHERE plan = ? AND username != 'aerys'",
            (PLAN_MAX,)
        ).fetchone()
        return int(row[0]) if row else 0


def assign_pioneer_plan(user_id: int) -> dict:
    """
    Asigna el plan correcto al usuario recién registrado.

    Regla Alpha_Pionero:
    - Aerys (admin): plan_admin gratis, siempre.
    - Usuarios 1–50 (excl. admin): plan_max gratis, pioneer_number = N
    - Usuario 51+: free_limited

    Devuelve: {"plan": str, "pioneer_number": int|None, "is_pioneer": bool}
    """
    with get_db() as conn:
        # Comprobar si es el admin (Aerys) — le asignamos plan_admin siempre
        user_row = conn.execute(
            "SELECT username FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if user_row and user_row["username"].strip().lower() == ADMIN_USERNAME.lower():
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE users SET plan=?, pioneer_number=NULL, plan_assigned_at=? WHERE id=?",
                (PLAN_ADMIN, now, user_id)
            )
            return {"plan": PLAN_ADMIN, "pioneer_number": None, "is_pioneer": True}

        # Contar pioneros actuales (sin contar al admin)
        count_row = conn.execute(
            "SELECT COUNT(*) FROM users WHERE plan = ? AND username != 'aerys'",
            (PLAN_MAX,)
        ).fetchone()
        current_count = int(count_row[0]) if count_row else 0

        if current_count < PIONEER_LIMIT:
            pioneer_number = current_count + 1
            plan           = PLAN_MAX
        else:
            pioneer_number = None
            plan           = PLAN_FREE

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            "UPDATE users SET plan=?, pioneer_number=?, plan_assigned_at=? WHERE id=?",
            (plan, pioneer_number, now, user_id)
        )
        return {
            "plan":            plan,
            "pioneer_number":  pioneer_number,
            "is_pioneer":      pioneer_number is not None,
        }


def get_user_plan(user_id: int) -> dict:
    """Devuelve el plan y número pionero de un usuario."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT username, plan, pioneer_number, plan_assigned_at FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
        if not row:
            return {"plan": PLAN_FREE, "pioneer_number": None, "is_pioneer": False}
        # Aerys siempre tiene plan_max, sin excepción
        plan = PLAN_MAX if row["username"].strip().lower() == ADMIN_USERNAME.lower() else row["plan"]
        return {
            "plan":           plan,
            "pioneer_number": row["pioneer_number"],
            "is_pioneer":     row["pioneer_number"] is not None,
        }


def get_pioneer_leaderboard(limit: int = 60) -> list[dict]:
    """Lista de pioneros ordenados por número de registro."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, username, plan, pioneer_number, created_at
               FROM users
               WHERE plan = ? AND username != 'aerys'
               ORDER BY pioneer_number ASC
               LIMIT ?""",
            (PLAN_MAX, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def is_plan_max(user: dict) -> bool:
    """Predicate: ¿tiene el usuario plan_max o admin?"""
    return user.get("plan") in (PLAN_MAX, PLAN_ADMIN)


# ═══════════════════════════════════════════════════════════════════════════
# CÓDIGOS DE REDENCIÓN (admin genera → usuario activa Plan MAX)
# ═══════════════════════════════════════════════════════════════════════════

def _ensure_redeem_table() -> None:
    """Crea la tabla de códigos si no existe."""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS redeem_codes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT    NOT NULL UNIQUE,
                plan        TEXT    NOT NULL DEFAULT 'plan_max',
                note        TEXT,
                used        INTEGER NOT NULL DEFAULT 0,
                used_by     INTEGER REFERENCES users(id),
                used_at     TEXT,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)


def create_redeem_code(code: str, plan: str = "plan_max", note: str = "") -> bool:
    """Crea un código de redención. Devuelve False si el código ya existe."""
    _ensure_redeem_table()
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO redeem_codes (code, plan, note) VALUES (?, ?, ?)",
                (code.strip().upper(), plan, note),
            )
        return True
    except Exception:
        return False


def use_redeem_code(user_id: int, code: str) -> dict:
    """
    Intenta canjear un código para el usuario dado.
    Devuelve {"ok": bool, "plan": str|None, "error": str|None}
    """
    _ensure_redeem_table()
    code = code.strip().upper()
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, plan, used FROM redeem_codes WHERE code = ?", (code,)
        ).fetchone()
        if not row:
            return {"ok": False, "plan": None, "error": "Código no válido."}
        if row["used"]:
            return {"ok": False, "plan": None, "error": "Este código ya fue usado."}
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE redeem_codes SET used=1, used_by=?, used_at=? WHERE code=?",
            (user_id, now, code),
        )
        conn.execute(
            "UPDATE users SET plan=?, plan_assigned_at=? WHERE id=?",
            (row["plan"], now, user_id),
        )
        return {"ok": True, "plan": row["plan"], "error": None}


def list_redeem_codes() -> list[dict]:
    """Lista todos los códigos (para el panel admin)."""
    _ensure_redeem_table()
    with get_db() as conn:
        rows = conn.execute("""
            SELECT rc.id, rc.code, rc.plan, rc.note, rc.used,
                   u.username AS used_by_name, rc.used_at, rc.created_at
            FROM redeem_codes rc
            LEFT JOIN users u ON u.id = rc.used_by
            ORDER BY rc.created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════
# API KEYS — Acceso programático externo (curl, scripts, integraciones…)
# ═══════════════════════════════════════════════════════════════════════════
# Formato de key: ak_<64 chars hex>  (66 chars totales)
# Se almacena únicamente el SHA-256 de la key, nunca el valor original.
# El prefix (ej. "ak_a1b2c3d4e5f6") se guarda solo para mostrar al usuario.
# ═══════════════════════════════════════════════════════════════════════════

_API_KEY_PREFIX = "ak_"
_API_KEY_BYTES  = 32   # → 64 chars hex


def _hash_api_key(key: str) -> str:
    """Devuelve el SHA-256 hex de la API key."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    """Genera una nueva API key con formato ak_<64 hex chars>."""
    return _API_KEY_PREFIX + secrets.token_hex(_API_KEY_BYTES)


def create_api_key(user_id: int, name: str) -> dict:
    """
    Genera y persiste una nueva API key para el usuario.

    Devuelve:
        {
          "id":         int,
          "key":        str,   ← solo se devuelve UNA vez, aquí
          "key_prefix": str,   ← los primeros 12 chars (para mostrar luego)
          "name":       str,
          "created_at": str,
        }

    Límite: máximo 10 API keys activas por usuario.
    """
    # Verificar límite de keys activas
    with get_db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM api_keys WHERE user_id = ? AND is_active = 1",
            (user_id,)
        ).fetchone()[0]
        if count >= 10:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=400,
                detail="Límite alcanzado: máximo 10 API keys activas por usuario.",
            )

    raw_key    = generate_api_key()
    key_hash   = _hash_api_key(raw_key)
    key_prefix = raw_key[:12]   # "ak_a1b2c3d4e" — primeros 12 chars

    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO api_keys (user_id, key_hash, key_prefix, name)
               VALUES (?, ?, ?, ?)""",
            (user_id, key_hash, key_prefix, name.strip()[:64]),
        )
        key_id = cur.lastrowid
        row = conn.execute(
            "SELECT id, key_prefix, name, created_at FROM api_keys WHERE id = ?",
            (key_id,)
        ).fetchone()

    return {
        "id":         row["id"],
        "key":        raw_key,        # ← devolver UNA sola vez al usuario
        "key_prefix": row["key_prefix"],
        "name":       row["name"],
        "created_at": row["created_at"],
    }


def get_user_by_api_key(raw_key: str) -> dict | None:
    """
    Verifica una API key y devuelve el usuario propietario.
    Actualiza last_used_at. Devuelve None si la key es inválida o inactiva.
    """
    if not raw_key or not raw_key.startswith(_API_KEY_PREFIX):
        return None

    key_hash = _hash_api_key(raw_key)

    with get_db() as conn:
        row = conn.execute(
            """SELECT u.id, u.username, u.email, u.plan, u.created_at,
                      ak.id AS key_id, ak.is_active
               FROM api_keys ak
               JOIN users u ON u.id = ak.user_id
               WHERE ak.key_hash = ?""",
            (key_hash,),
        ).fetchone()

        if not row or not row["is_active"]:
            return None

        # Actualizar last_used_at de forma no bloqueante
        conn.execute(
            "UPDATE api_keys SET last_used_at = datetime('now') WHERE id = ?",
            (row["key_id"],),
        )

    user = {k: row[k] for k in ("id", "username", "email", "plan", "created_at")}
    # Aerys siempre plan_max
    if user["username"].strip().lower() == ADMIN_USERNAME.lower():
        user["plan"] = PLAN_MAX
    user["auth_method"] = "api_key"
    return user


def list_user_api_keys(user_id: int) -> list[dict]:
    """Lista las API keys activas del usuario (sin exponer el hash)."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, key_prefix, name, created_at, last_used_at
               FROM api_keys
               WHERE user_id = ? AND is_active = 1
               ORDER BY created_at DESC""",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def revoke_api_key(key_id: int, user_id: int) -> bool:
    """
    Revoca (desactiva) una API key del usuario.
    Devuelve True si se encontró y revocó, False si no existe o pertenece a otro.
    """
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE api_keys SET is_active = 0 WHERE id = ? AND user_id = ?",
            (key_id, user_id),
        )
    return cur.rowcount > 0


def revoke_all_user_api_keys(user_id: int) -> int:
    """Revoca todas las API keys de un usuario. Devuelve el número revocadas."""
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE api_keys SET is_active = 0 WHERE user_id = ? AND is_active = 1",
            (user_id,),
        )
    return cur.rowcount
    
    """
PARCHE PARA database.py
=======================
Añade las dos funciones que le faltan y que pioneers.py intenta importar.
Copia el contenido de este archivo al FINAL de app/database.py
(después de la función revoke_all_user_api_keys).

FUNCIONES AÑADIDAS:
  1. get_user_by_token(token)       — alias de get_session_user (mismo token de sesión)
  2. check_daily_message_limit(...) — cuenta mensajes de hoy y compara con el límite del plan
"""

# ═══════════════════════════════════════════════════════════════
# COMPATIBILIDAD CON pioneers.py
# ═══════════════════════════════════════════════════════════════

MAX_DAILY_MESSAGES: dict[str, int] = {
    "plan_max":           -1,   # ilimitado
    "plan_free_limitado": 20,
    "free_limited":       20,   # mismo nombre que PLAN_FREE en database.py
}


def get_user_by_token(token: str) -> dict | None:
    """
    Alias de get_session_user() para compatibilidad con pioneers.py.
    pioneers.py usa Authorization: Bearer <token> donde el token
    es el mismo session_token que emite el sistema de auth principal.
    """
    return get_session_user(token)


def check_daily_message_limit(
    user_id: int,
    plan: str,
) -> tuple[bool, int, int]:
    """
    Comprueba si el usuario puede enviar un mensaje hoy.

    Devuelve: (allowed: bool, used_today: int, max_allowed: int)
      - allowed     → True si puede enviar, False si alcanzó el límite
      - used_today  → mensajes enviados hoy (solo rol 'user')
      - max_allowed → límite diario del plan (-1 = ilimitado)

    Lógica:
      • Plan max (plan_max) → siempre permitido, max = -1
      • Plan free           → contar mensajes 'user' de hoy y comparar con MAX_DAILY_MESSAGES
    """
    max_allowed = MAX_DAILY_MESSAGES.get(plan, 20)

    # Plan max: sin límite
    if max_allowed == -1:
        return True, 0, -1

    # Contar mensajes enviados hoy por este usuario (solo rol 'user')
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(m.id) as cnt
            FROM chat_messages m
            JOIN chats c ON c.id = m.chat_id
            WHERE c.user_id = ?
              AND m.role    = 'user'
              AND date(m.created_at) = date('now')
            """,
            (user_id,),
        ).fetchone()

    used_today = row["cnt"] if row else 0
    allowed = used_today < max_allowed
    return allowed, used_today, max_allowed