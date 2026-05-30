"""
Base de datos SQLite - Usuarios, Sesiones, Chats, Archivos
===========================================================
Gestiona todo el estado persistente de la aplicacion:
- Usuarios (registro/login con hash de contrasena)
- Sesiones (tokens de 30 dias)
- Chats (conversaciones por usuario)
- Mensajes (historial de cada chat)
- Archivos (archivos subidos vinculados a cada chat)

OPTIMIZACIÓN: usa aiosqlite para operaciones async en el event loop de FastAPI,
más un pool de conexiones sincronas (sqlite3) para init_db y funciones de arranque.
"""
import sqlite3
import aiosqlite
import asyncio
import secrets
import json
import hashlib
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from passlib.context import CryptContext

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

DB_PATH = Path("data/analizador.db")


# ══════════════════════════════════════════════════════════════════════════
# CONEXIÓN — sync (solo para init_db / migraciones en arranque)
#            async (para todo lo demás desde los endpoints FastAPI)
# ══════════════════════════════════════════════════════════════════════════

@contextmanager
def get_db():
    """Context manager SÍNCRONO — solo para init_db y migraciones en arranque."""
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA cache_size=-32000")   # 32 MB de caché en RAM
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@asynccontextmanager
async def get_db_async():
    """Context manager ASYNC — para todos los endpoints FastAPI."""
    DB_PATH.parent.mkdir(exist_ok=True)
    async with aiosqlite.connect(str(DB_PATH)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA cache_size=-32000")
        try:
            yield conn
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise


def init_db():
    """Crea todas las tablas si no existen. Síncrono — llamado solo en lifespan."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                username        TEXT    UNIQUE NOT NULL,
                email           TEXT    UNIQUE NOT NULL,
                password_hash   TEXT    NOT NULL,
                salt            TEXT    NOT NULL,
                plan            TEXT    DEFAULT 'plan_free_limitado',
                plan_assigned_at TEXT,
                pioneer_number  INTEGER,
                selected_model  TEXT    DEFAULT 'nexo_coder',
                created_at      TEXT    DEFAULT (datetime('now'))
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

            CREATE TABLE IF NOT EXISTS api_keys (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                key_hash     TEXT    UNIQUE NOT NULL,
                key_prefix   TEXT    NOT NULL,
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

        _safe_alter(conn, "users", "plan",             "TEXT NOT NULL DEFAULT 'free_limited'")
        _safe_alter(conn, "users", "pioneer_number",   "INTEGER DEFAULT NULL")
        _safe_alter(conn, "users", "plan_assigned_at", "TEXT DEFAULT NULL")
        _safe_alter(conn, "users", "selected_model",   "TEXT DEFAULT 'nexo_coder'")

        _ensure_aerys_plan_max(conn)

        conn.executescript("""
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


# ══════════════════════════════════════════════════════════════════════════
# USUARIOS
# ══════════════════════════════════════════════════════════════════════════

def _hash_password(password: str, salt: str = None) -> tuple[str, str]:
    return _pwd_ctx.hash(password), ""


async def create_user(username: str, email: str, password: str) -> dict | None:
    if len(password) < 6:
        raise ValueError("La contraseña debe tener al menos 6 caracteres")
    if len(username) < 3:
        raise ValueError("El nombre de usuario debe tener al menos 3 caracteres")
    pw_hash, _ = _hash_password(password)
    try:
        async with get_db_async() as conn:
            cur = await conn.execute(
                "INSERT INTO users (username, email, password_hash, salt) VALUES (?, ?, ?, ?)",
                (username.strip(), email.strip().lower(), pw_hash, ""),
            )
            return {"id": cur.lastrowid, "username": username, "email": email}
    except aiosqlite.IntegrityError:
        return None


async def authenticate_user(username_or_email: str, password: str) -> dict | None:
    async with get_db_async() as conn:
        cursor = await conn.execute(
            "SELECT * FROM users WHERE username = ? OR email = ?",
            (username_or_email.strip(), username_or_email.strip().lower()),
        )
        user = await cursor.fetchone()
    if not user:
        return None
    user = dict(user)
    stored_hash = user["password_hash"]
    if stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"):
        if _pwd_ctx.verify(password, stored_hash):
            return user
        return None
    # Legacy SHA-256 → migrar a bcrypt
    legacy_hash = hashlib.sha256((password + user["salt"]).encode("utf-8")).hexdigest()
    if secrets.compare_digest(legacy_hash, stored_hash):
        new_hash = _pwd_ctx.hash(password)
        async with get_db_async() as conn:
            await conn.execute(
                "UPDATE users SET password_hash = ?, salt = '' WHERE id = ?",
                (new_hash, user["id"]),
            )
        return user
    return None


async def get_user_by_id(user_id: int) -> dict | None:
    async with get_db_async() as conn:
        cursor = await conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
    return dict(row) if row else None


# ══════════════════════════════════════════════════════════════════════════
# SESIONES
# ══════════════════════════════════════════════════════════════════════════

async def create_session(user_id: int) -> str:
    token = secrets.token_hex(32)
    expires_at = (datetime.utcnow() + timedelta(days=30)).isoformat()
    async with get_db_async() as conn:
        await conn.execute(
            "INSERT INTO sessions (user_id, token, expires_at) VALUES (?, ?, ?)",
            (user_id, token, expires_at),
        )
    return token


async def get_session_user(token: str) -> dict | None:
    if not token:
        return None
    async with get_db_async() as conn:
        cursor = await conn.execute(
            """SELECT u.id, u.username, u.email, u.plan, u.selected_model, u.created_at
               FROM users u
               JOIN sessions s ON s.user_id = u.id
               WHERE s.token = ? AND s.expires_at > datetime('now')""",
            (token,),
        )
        row = await cursor.fetchone()
    if not row:
        return None
    user = dict(row)
    if user.get("username", "").strip().lower() == ADMIN_USERNAME.lower():
        user["plan"] = PLAN_MAX
    return user


async def delete_session(token: str):
    async with get_db_async() as conn:
        await conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


async def cleanup_expired_sessions():
    async with get_db_async() as conn:
        await conn.execute("DELETE FROM sessions WHERE expires_at <= datetime('now')")


# ══════════════════════════════════════════════════════════════════════════
# CHATS
# ══════════════════════════════════════════════════════════════════════════

async def create_chat(user_id: int, title: str = "Chat nuevo") -> dict:
    async with get_db_async() as conn:
        cur = await conn.execute(
            "INSERT INTO chats (user_id, title) VALUES (?, ?)",
            (user_id, title[:80]),
        )
        chat_id = cur.lastrowid
        cursor = await conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,))
        row = await cursor.fetchone()
    return dict(row)


async def get_user_chats(user_id: int) -> list[dict]:
    async with get_db_async() as conn:
        cursor = await conn.execute(
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
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_chat(chat_id: int, user_id: int) -> dict | None:
    async with get_db_async() as conn:
        cursor = await conn.execute(
            "SELECT * FROM chats WHERE id = ? AND user_id = ?",
            (chat_id, user_id),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def get_chat_by_id(chat_id: int) -> dict | None:
    async with get_db_async() as conn:
        cursor = await conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,))
        row = await cursor.fetchone()
    return dict(row) if row else None


async def delete_chat(chat_id: int, user_id: int) -> bool:
    async with get_db_async() as conn:
        cur = await conn.execute(
            "DELETE FROM chats WHERE id = ? AND user_id = ?",
            (chat_id, user_id),
        )
    return cur.rowcount > 0


async def update_chat_title(chat_id: int, user_id: int, title: str):
    async with get_db_async() as conn:
        await conn.execute(
            "UPDATE chats SET title = ?, updated_at = datetime('now') WHERE id = ? AND user_id = ?",
            (title[:80], chat_id, user_id),
        )


async def touch_chat(chat_id: int):
    async with get_db_async() as conn:
        await conn.execute(
            "UPDATE chats SET updated_at = datetime('now') WHERE id = ?",
            (chat_id,),
        )


# ══════════════════════════════════════════════════════════════════════════
# MENSAJES
# ══════════════════════════════════════════════════════════════════════════

async def add_message(chat_id: int, role: str, content: str, tokens: int = 0) -> int:
    async with get_db_async() as conn:
        cur = await conn.execute(
            "INSERT INTO chat_messages (chat_id, role, content, tokens) VALUES (?, ?, ?, ?)",
            (chat_id, role, content, tokens),
        )
        return cur.lastrowid


async def get_chat_messages(chat_id: int) -> list[dict]:
    async with get_db_async() as conn:
        cursor = await conn.execute(
            "SELECT * FROM chat_messages WHERE chat_id = ? ORDER BY created_at ASC",
            (chat_id,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════
# ARCHIVOS DE CHAT
# ══════════════════════════════════════════════════════════════════════════

async def add_chat_file(chat_id: int, user_id: int, filename: str, text_content: str,
                        tokens: int, metadata: dict) -> int:
    async with get_db_async() as conn:
        cur = await conn.execute(
            "INSERT INTO chat_files (chat_id, user_id, filename, text_content, tokens, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, user_id, filename, text_content, tokens, json.dumps(metadata)),
        )
        return cur.lastrowid


async def get_chat_files(chat_id: int) -> list[dict]:
    async with get_db_async() as conn:
        cursor = await conn.execute(
            "SELECT * FROM chat_files WHERE chat_id = ? ORDER BY created_at ASC",
            (chat_id,),
        )
        rows = await cursor.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["metadata"] = json.loads(d.get("metadata_json") or "{}")
        except Exception:
            d["metadata"] = {}
        result.append(d)
    return result


async def remove_chat_file(chat_id: int, filename: str) -> bool:
    async with get_db_async() as conn:
        cur = await conn.execute(
            "DELETE FROM chat_files WHERE chat_id = ? AND filename = ?",
            (chat_id, filename),
        )
    return cur.rowcount > 0


async def clear_chat_files(chat_id: int):
    async with get_db_async() as conn:
        await conn.execute("DELETE FROM chat_files WHERE chat_id = ?", (chat_id,))


async def get_total_chat_tokens(chat_id: int) -> int:
    async with get_db_async() as conn:
        cursor = await conn.execute(
            "SELECT COALESCE(SUM(tokens), 0) as total FROM chat_files WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
    return row["total"] if row else 0


# ══════════════════════════════════════════════════════════════════════════
# ADMIN
# ══════════════════════════════════════════════════════════════════════════

ADMIN_USERNAME = "Aerys"


def _ensure_aerys_plan_max(conn) -> None:
    conn.execute(
        """UPDATE users
           SET plan = ?, plan_assigned_at = COALESCE(plan_assigned_at, datetime('now'))
           WHERE LOWER(username) = LOWER(?) AND plan != ?""",
        (PLAN_MAX, ADMIN_USERNAME, PLAN_MAX),
    )


async def is_admin(user: dict) -> bool:
    return user.get("username", "").strip().lower() == ADMIN_USERNAME.lower()


async def get_global_stats() -> dict:
    async with get_db_async() as conn:
        c = await conn.execute("SELECT COUNT(*) FROM users")
        users = (await c.fetchone())[0]
        c = await conn.execute("SELECT COUNT(*) FROM chats")
        chats = (await c.fetchone())[0]
        c = await conn.execute("SELECT COUNT(*) FROM chat_messages")
        msgs = (await c.fetchone())[0]
        c = await conn.execute("SELECT COUNT(*) FROM chat_files")
        files = (await c.fetchone())[0]
        c = await conn.execute("SELECT COUNT(*) FROM sessions WHERE expires_at > datetime('now')")
        sessions = (await c.fetchone())[0]
    return {"total_users": users, "total_chats": chats, "total_messages": msgs,
            "total_files": files, "active_sessions": sessions}


async def get_all_users() -> list[dict]:
    async with get_db_async() as conn:
        cursor = await conn.execute(
            "SELECT id, username, email, created_at FROM users ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def delete_user_admin(user_id: int) -> bool:
    async with get_db_async() as conn:
        cur = await conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return cur.rowcount > 0


async def delete_user_sessions_admin(user_id: int) -> int:
    async with get_db_async() as conn:
        cur = await conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return cur.rowcount


async def get_all_chats_admin() -> list[dict]:
    async with get_db_async() as conn:
        cursor = await conn.execute(
            """SELECT c.id, c.title, c.created_at, c.updated_at, u.username,
                      COUNT(m.id) as message_count
               FROM chats c JOIN users u ON u.id = c.user_id
               LEFT JOIN chat_messages m ON m.chat_id = c.id
               GROUP BY c.id ORDER BY c.updated_at DESC""",
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def delete_chat_admin(chat_id: int) -> bool:
    async with get_db_async() as conn:
        cur = await conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    return cur.rowcount > 0


async def get_user_messages_admin(user_id: int, limit: int = 100) -> list[dict]:
    async with get_db_async() as conn:
        cursor = await conn.execute(
            """SELECT m.id, m.role, m.content, m.tokens, m.created_at, c.title as chat_title
               FROM chat_messages m JOIN chats c ON c.id = m.chat_id
               WHERE c.user_id = ? ORDER BY m.created_at DESC LIMIT ?""",
            (user_id, limit),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════
# VOID AXIOM
# ══════════════════════════════════════════════════════════════════════════

async def create_agent_session(task: str) -> int:
    async with get_db_async() as conn:
        cur = await conn.execute(
            "INSERT INTO agent_sessions (task, status) VALUES (?, 'active')", (task,))
        return cur.lastrowid


async def get_agent_session(session_id: int) -> dict | None:
    async with get_db_async() as conn:
        cursor = await conn.execute(
            "SELECT * FROM agent_sessions WHERE id = ?", (session_id,))
        row = await cursor.fetchone()
    return dict(row) if row else None


async def get_latest_agent_session() -> dict | None:
    async with get_db_async() as conn:
        cursor = await conn.execute(
            "SELECT * FROM agent_sessions ORDER BY created_at DESC LIMIT 1")
        row = await cursor.fetchone()
    return dict(row) if row else None


async def update_agent_session_status(session_id: int, status: str):
    async with get_db_async() as conn:
        await conn.execute(
            "UPDATE agent_sessions SET status=?, updated_at=datetime('now') WHERE id=?",
            (status, session_id),
        )


async def save_agent_message(session_id: int, agent_id: str, content: str, msg_type: str = "chat") -> int:
    async with get_db_async() as conn:
        cur = await conn.execute(
            "INSERT INTO agent_messages (session_id, agent_id, content, msg_type) VALUES (?,?,?,?)",
            (session_id, agent_id, content, msg_type),
        )
        return cur.lastrowid


async def get_agent_messages(session_id: int, limit: int = 200) -> list[dict]:
    async with get_db_async() as conn:
        cursor = await conn.execute(
            "SELECT id, agent_id, content, msg_type, created_at FROM agent_messages WHERE session_id=? ORDER BY id ASC LIMIT ?",
            (session_id, limit),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_all_agent_sessions(limit: int = 20) -> list[dict]:
    async with get_db_async() as conn:
        cursor = await conn.execute(
            """SELECT s.id, s.task, s.status, s.created_at, COUNT(m.id) as message_count
               FROM agent_sessions s LEFT JOIN agent_messages m ON m.session_id = s.id
               GROUP BY s.id ORDER BY s.created_at DESC LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════
# ALPHA PIONERO
# ══════════════════════════════════════════════════════════════════════════

PIONEER_LIMIT = 50
PLAN_MAX  = "plan_max"
PLAN_FREE = "free_limited"
PLAN_ADMIN = "admin"
PLAN_STUDENT = "plan_student"


def _safe_alter(conn, table: str, column: str, definition: str) -> None:
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except Exception:
        pass


async def get_pioneer_count() -> int:
    async with get_db_async() as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM users WHERE plan = ? AND username != 'aerys'",
            (PLAN_MAX,)
        )
        row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def assign_pioneer_plan(user_id: int) -> dict:
    async with get_db_async() as conn:
        cursor = await conn.execute(
            "SELECT username FROM users WHERE id = ?", (user_id,))
        user_row = await cursor.fetchone()
        if user_row and user_row["username"].strip().lower() == ADMIN_USERNAME.lower():
            now = datetime.utcnow().isoformat()
            await conn.execute(
                "UPDATE users SET plan=?, pioneer_number=NULL, plan_assigned_at=? WHERE id=?",
                (PLAN_ADMIN, now, user_id),
            )
            return {"plan": PLAN_ADMIN, "pioneer_number": None, "is_pioneer": True}
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM users WHERE plan = ? AND username != 'aerys'",
            (PLAN_MAX,))
        count_row = await cursor.fetchone()
        current_count = int(count_row[0]) if count_row else 0
        if current_count < PIONEER_LIMIT:
            pioneer_number = current_count + 1
            plan = PLAN_MAX
        else:
            pioneer_number = None
            plan = PLAN_FREE
        now = datetime.utcnow().isoformat()
        await conn.execute(
            "UPDATE users SET plan=?, pioneer_number=?, plan_assigned_at=? WHERE id=?",
            (plan, pioneer_number, now, user_id),
        )
        return {"plan": plan, "pioneer_number": pioneer_number,
                "is_pioneer": pioneer_number is not None}


async def get_user_plan(user_id: int) -> dict:
    async with get_db_async() as conn:
        cursor = await conn.execute(
            "SELECT username, plan, pioneer_number, plan_assigned_at FROM users WHERE id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
    if not row:
        return {"plan": PLAN_FREE, "pioneer_number": None, "is_pioneer": False}
    row = dict(row)
    plan = PLAN_MAX if row["username"].strip().lower() == ADMIN_USERNAME.lower() else row["plan"]
    return {"plan": plan, "pioneer_number": row["pioneer_number"],
            "is_pioneer": row["pioneer_number"] is not None}


async def get_pioneer_leaderboard(limit: int = 60) -> list[dict]:
    async with get_db_async() as conn:
        cursor = await conn.execute(
            "SELECT id, username, plan, pioneer_number, created_at FROM users WHERE plan = ? AND username != 'aerys' ORDER BY pioneer_number ASC LIMIT ?",
            (PLAN_MAX, limit),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


def is_plan_max(user: dict) -> bool:
    return user.get("plan") in (PLAN_MAX, PLAN_ADMIN)


# ══════════════════════════════════════════════════════════════════════════
# CÓDIGOS DE REDENCIÓN
# ══════════════════════════════════════════════════════════════════════════

async def _ensure_redeem_table() -> None:
    async with get_db_async() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS redeem_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE,
                plan TEXT NOT NULL DEFAULT 'plan_max', note TEXT,
                used INTEGER NOT NULL DEFAULT 0, used_by INTEGER REFERENCES users(id),
                used_at TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)


async def create_redeem_code(code: str, plan: str = "plan_max", note: str = "") -> bool:
    await _ensure_redeem_table()
    try:
        async with get_db_async() as conn:
            await conn.execute(
                "INSERT INTO redeem_codes (code, plan, note) VALUES (?, ?, ?)",
                (code.strip().upper(), plan, note),
            )
        return True
    except Exception:
        return False


async def use_redeem_code(user_id: int, code: str) -> dict:
    await _ensure_redeem_table()
    code = code.strip().upper()
    async with get_db_async() as conn:
        cursor = await conn.execute(
            "SELECT id, plan, used FROM redeem_codes WHERE code = ?", (code,))
        row = await cursor.fetchone()
        if not row:
            return {"ok": False, "plan": None, "error": "Código no válido."}
        if row["used"]:
            return {"ok": False, "plan": None, "error": "Este código ya fue usado."}
        now = datetime.utcnow().isoformat()
        await conn.execute("UPDATE redeem_codes SET used=1, used_by=?, used_at=? WHERE code=?",
                           (user_id, now, code))
        await conn.execute("UPDATE users SET plan=?, plan_assigned_at=? WHERE id=?",
                           (row["plan"], now, user_id))
        return {"ok": True, "plan": row["plan"], "error": None}


async def list_redeem_codes() -> list[dict]:
    await _ensure_redeem_table()
    async with get_db_async() as conn:
        cursor = await conn.execute("""
            SELECT rc.id, rc.code, rc.plan, rc.note, rc.used, u.username AS used_by_name,
                   rc.used_at, rc.created_at FROM redeem_codes rc
            LEFT JOIN users u ON u.id = rc.used_by ORDER BY rc.created_at DESC
        """)
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════
# API KEYS
# ══════════════════════════════════════════════════════════════════════════

_API_KEY_PREFIX = "ak_"
_API_KEY_BYTES  = 32


def _hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    return _API_KEY_PREFIX + secrets.token_hex(_API_KEY_BYTES)


async def create_api_key(user_id: int, name: str) -> dict:
    async with get_db_async() as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM api_keys WHERE user_id = ? AND is_active = 1",
            (user_id,))
        count = (await cursor.fetchone())[0]
        if count >= 10:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Máximo 10 API keys activas.")
    raw_key   = generate_api_key()
    key_hash  = _hash_api_key(raw_key)
    key_prefix = raw_key[:12]
    async with get_db_async() as conn:
        cur = await conn.execute(
            "INSERT INTO api_keys (user_id, key_hash, key_prefix, name) VALUES (?, ?, ?, ?)",
            (user_id, key_hash, key_prefix, name.strip()[:64]),
        )
        key_id = cur.lastrowid
        cursor = await conn.execute(
            "SELECT id, key_prefix, name, created_at FROM api_keys WHERE id = ?",
            (key_id,))
        row = await cursor.fetchone()
    return {"id": row["id"], "key": raw_key, "key_prefix": row["key_prefix"],
            "name": row["name"], "created_at": row["created_at"]}


async def get_user_by_api_key(raw_key: str) -> dict | None:
    if not raw_key or not raw_key.startswith(_API_KEY_PREFIX):
        return None
    key_hash = _hash_api_key(raw_key)
    async with get_db_async() as conn:
        cursor = await conn.execute(
            """SELECT u.id, u.username, u.email, u.plan, u.created_at, ak.id AS key_id, ak.is_active
               FROM api_keys ak JOIN users u ON u.id = ak.user_id WHERE ak.key_hash = ?""",
            (key_hash,))
        row = await cursor.fetchone()
        if not row or not row["is_active"]:
            return None
        await conn.execute("UPDATE api_keys SET last_used_at = datetime('now') WHERE id = ?",
                           (row["key_id"],))
    user = {k: row[k] for k in ("id", "username", "email", "plan", "created_at")}
    if user["username"].strip().lower() == ADMIN_USERNAME.lower():
        user["plan"] = PLAN_MAX
    user["auth_method"] = "api_key"
    return user


async def list_user_api_keys(user_id: int) -> list[dict]:
    async with get_db_async() as conn:
        cursor = await conn.execute(
            "SELECT id, key_prefix, name, created_at, last_used_at FROM api_keys WHERE user_id = ? AND is_active = 1 ORDER BY created_at DESC",
            (user_id,))
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def revoke_api_key(key_id: int, user_id: int) -> bool:
    async with get_db_async() as conn:
        cur = await conn.execute(
            "UPDATE api_keys SET is_active = 0 WHERE id = ? AND user_id = ?",
            (key_id, user_id))
    return cur.rowcount > 0


async def revoke_all_user_api_keys(user_id: int) -> int:
    async with get_db_async() as conn:
        cur = await conn.execute(
            "UPDATE api_keys SET is_active = 0 WHERE user_id = ? AND is_active = 1",
            (user_id,))
    return cur.rowcount


# ══════════════════════════════════════════════════════════════════════════
# MODELO SELECCIONADO POR USUARIO
# ══════════════════════════════════════════════════════════════════════════

async def get_user_selected_model(user_id: int) -> str:
    async with get_db_async() as conn:
        cursor = await conn.execute(
            "SELECT selected_model FROM users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
    if row and row["selected_model"]:
        return row["selected_model"]
    return "nexo_coder"


async def set_user_selected_model(user_id: int, model_id: str) -> bool:
    valid_models = {"nexo_lite", "nexo_coder", "nexo_pro"}
    if model_id not in valid_models:
        return False
    async with get_db_async() as conn:
        cur = await conn.execute(
            "UPDATE users SET selected_model = ? WHERE id = ?",
            (model_id, user_id))
    return cur.rowcount > 0


# ══════════════════════════════════════════════════════════════════════════
# COMPATIBILIDAD CON pioneers.py
# ══════════════════════════════════════════════════════════════════════════

MAX_DAILY_MESSAGES: dict[str, int] = {
    "plan_max": -1,
    "plan_free_limitado": 20,
    "free_limited": 20,
    "plan_student": 50,
}


async def get_user_by_token(token: str) -> dict | None:
    return await get_session_user(token)


async def check_daily_message_limit(user_id: int, plan: str) -> tuple[bool, int, int]:
    max_allowed = MAX_DAILY_MESSAGES.get(plan, 20)
    if max_allowed == -1:
        return True, 0, -1
    async with get_db_async() as conn:
        cursor = await conn.execute(
            """SELECT COUNT(m.id) as cnt FROM chat_messages m
               JOIN chats c ON c.id = m.chat_id
               WHERE c.user_id = ? AND m.role = 'user' AND date(m.created_at) = date('now')""",
            (user_id,))
        row = await cursor.fetchone()
    used_today = row["cnt"] if row else 0
    return used_today < max_allowed, used_today, max_allowed