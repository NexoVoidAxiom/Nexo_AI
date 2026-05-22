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
    conn.execute("PRAGMA journal_mode=WAL")   # Mejor concurrencia
    conn.execute("PRAGMA foreign_keys=ON")    # Integridad referencial
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
# ADMINISTRACIÓN (solo Aerys / elgatosuperpitzzero@gmail.com)
# ═══════════════════════════════════════════════════════════════

ADMIN_EMAIL = "elgatosuperpitzzero@gmail.com"
ADMIN_USERNAME = "Aerys"


def is_admin(user: dict) -> bool:
    """Comprueba si el usuario es administrador."""
    return user.get("email", "").lower() == ADMIN_EMAIL


def get_all_users() -> list[dict]:
    """Lista todos los usuarios con estadísticas."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT u.id, u.username, u.email, u.created_at,
                   COUNT(DISTINCT c.id)  AS chat_count,
                   COUNT(DISTINCT m.id)  AS message_count,
                   COUNT(DISTINCT s.id)  AS active_sessions
            FROM users u
            LEFT JOIN chats         c ON c.user_id = u.id
            LEFT JOIN chat_messages m ON m.chat_id = c.id
            LEFT JOIN sessions      s ON s.user_id = u.id
                                      AND s.expires_at > datetime('now')
            GROUP BY u.id
            ORDER BY u.created_at DESC
        """).fetchall()
    return [dict(r) for r in rows]


def get_all_chats_admin() -> list[dict]:
    """Lista todos los chats de todos los usuarios."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   u.username, u.email, u.id AS user_id,
                   COUNT(DISTINCT m.id) AS message_count,
                   COUNT(DISTINCT f.id) AS file_count
            FROM chats c
            JOIN  users         u ON u.id = c.user_id
            LEFT JOIN chat_messages m ON m.chat_id = c.id
            LEFT JOIN chat_files    f ON f.chat_id = c.id
            GROUP BY c.id
            ORDER BY c.updated_at DESC
        """).fetchall()
    return [dict(r) for r in rows]


def delete_user_admin(user_id: int) -> bool:
    """Elimina un usuario y todos sus datos (CASCADE)."""
    with get_db() as conn:
        cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return cur.rowcount > 0


def delete_chat_admin(chat_id: int) -> bool:
    """Elimina un chat sin importar de quién sea."""
    with get_db() as conn:
        cur = conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    return cur.rowcount > 0


def delete_user_sessions_admin(user_id: int) -> int:
    """Cierra todas las sesiones de un usuario (fuerza logout)."""
    with get_db() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return cur.rowcount


def get_global_stats() -> dict:
    """Estadísticas globales del sistema."""
    with get_db() as conn:
        users    = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        chats    = conn.execute("SELECT COUNT(*) AS n FROM chats").fetchone()["n"]
        messages = conn.execute("SELECT COUNT(*) AS n FROM chat_messages").fetchone()["n"]
        sessions = conn.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE expires_at > datetime('now')"
        ).fetchone()["n"]
        files    = conn.execute("SELECT COUNT(*) AS n FROM chat_files").fetchone()["n"]
        tok_sum  = conn.execute(
            "SELECT COALESCE(SUM(tokens), 0) AS n FROM chat_files"
        ).fetchone()["n"]
    return {
        "users": users,
        "chats": chats,
        "messages": messages,
        "active_sessions": sessions,
        "files": files,
        "total_tokens_stored": tok_sum,
    }


def get_user_messages_admin(user_id: int) -> list[dict]:
    """Devuelve todos los mensajes de un usuario concreto."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT m.id, m.chat_id, m.role, m.content, m.created_at, c.title
            FROM chat_messages m
            JOIN chats c ON c.id = m.chat_id
            WHERE c.user_id = ?
            ORDER BY m.created_at DESC
            LIMIT 200
        """, (user_id,)).fetchall()
    return [dict(r) for r in rows]