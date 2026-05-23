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
import hashlib
import secrets
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

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

            CREATE INDEX IF NOT EXISTS idx_sessions_token   ON sessions(token);
            CREATE INDEX IF NOT EXISTS idx_chats_user       ON chats(user_id);
            CREATE INDEX IF NOT EXISTS idx_messages_chat    ON chat_messages(chat_id);
            CREATE INDEX IF NOT EXISTS idx_files_chat       ON chat_files(chat_id);
        """)

        # ── Migración segura: añadir columnas de plan a tabla existente ──────
        _safe_alter(conn, "users", "plan",             "TEXT NOT NULL DEFAULT 'free_limited'")
        _safe_alter(conn, "users", "pioneer_number",   "INTEGER DEFAULT NULL")
        _safe_alter(conn, "users", "plan_assigned_at", "TEXT DEFAULT NULL")
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
    """Hashea una contrasena con SHA-256 + salt aleatorio."""
    if salt is None:
        salt = secrets.token_hex(32)
    pw_hash = hashlib.sha256((password + salt).encode("utf-8")).hexdigest()
    return pw_hash, salt


def create_user(username: str, email: str, password: str) -> dict | None:
    """Crea un usuario nuevo. Devuelve None si ya existe."""
    if len(password) < 6:
        raise ValueError("La contrasena debe tener al menos 6 caracteres")
    if len(username) < 3:
        raise ValueError("El nombre de usuario debe tener al menos 3 caracteres")

    pw_hash, salt = _hash_password(password)
    try:
        with get_db() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, email, password_hash, salt) VALUES (?, ?, ?, ?)",
                (username.strip(), email.strip().lower(), pw_hash, salt),
            )
            return {"id": cur.lastrowid, "username": username, "email": email}
    except sqlite3.IntegrityError:
        return None  # Usuario o email ya existe


def authenticate_user(username_or_email: str, password: str) -> dict | None:
    """Verifica credenciales. Devuelve el usuario o None."""
    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username = ? OR email = ?",
            (username_or_email.strip(), username_or_email.strip().lower()),
        ).fetchone()

    if not user:
        return None

    pw_hash, _ = _hash_password(password, user["salt"])
    if secrets.compare_digest(pw_hash, user["password_hash"]):
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
            """SELECT u.id, u.username, u.email, u.created_at
               FROM users u
               JOIN sessions s ON s.user_id = u.id
               WHERE s.token = ? AND s.expires_at > datetime('now')""",
            (token,),
        ).fetchone()
    return dict(row) if row else None


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
    - Usuarios 1–50 (excl. admin): plan_max gratis, pioneer_number = N
    - Usuario 51+: free_limited

    Devuelve: {"plan": str, "pioneer_number": int|None, "is_pioneer": bool}
    """
    with get_db() as conn:
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
            "SELECT plan, pioneer_number, plan_assigned_at FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
        if not row:
            return {"plan": PLAN_FREE, "pioneer_number": None, "is_pioneer": False}
        return {
            "plan":           row["plan"],
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
