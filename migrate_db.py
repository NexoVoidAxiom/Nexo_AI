"""
migrate_db.py — Migración segura de la base de datos existente.

Ejecutar UNA SOLA VEZ sobre una DB antigua (antes del fix) para añadir
las columnas de plan/pioneer sin perder datos existentes.

Uso:
    python migrate_db.py [--db path/to/analizador.db]
"""
import sqlite3
import argparse
from pathlib import Path

PIONEER_LIMIT = 50
PLAN_MAX      = "plan_max"
PLAN_FREE     = "free_limited"
PLAN_ADMIN    = "admin"


def migrate(db_path: str) -> None:
    p = Path(db_path)
    if not p.exists():
        print(f"[ERROR] DB no encontrada: {db_path}")
        return

    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    def safe_alter(table: str, column: str, definition: str) -> bool:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            conn.commit()
            print(f"  ✓ Columna '{column}' añadida a '{table}'")
            return True
        except Exception:
            print(f"  · Columna '{column}' ya existe en '{table}' — omitida")
            return False

    print("\n[1/4] Añadiendo columnas de plan a la tabla users...")
    safe_alter("users", "plan",             "TEXT NOT NULL DEFAULT 'free_limited'")
    safe_alter("users", "pioneer_number",   "INTEGER DEFAULT NULL")
    safe_alter("users", "plan_assigned_at", "TEXT DEFAULT NULL")

    print("\n[2/4] Asignando plan ADMIN al usuario 'aerys'...")
    conn.execute(
        "UPDATE users SET plan=? WHERE username='aerys'",
        (PLAN_ADMIN,)
    )
    conn.commit()

    print("\n[3/4] Asignando plan_max a los primeros 50 usuarios (excl. aerys)...")
    users = conn.execute(
        "SELECT id, username FROM users WHERE username != 'aerys' ORDER BY id ASC"
    ).fetchall()

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    for idx, user in enumerate(users, start=1):
        if idx <= PIONEER_LIMIT:
            conn.execute(
                "UPDATE users SET plan=?, pioneer_number=?, plan_assigned_at=? WHERE id=?",
                (PLAN_MAX, idx, now, user["id"])
            )
            print(f"  ✓ [{idx:02d}] {user['username']} → plan_max (Pioneer #{idx})")
        else:
            conn.execute(
                "UPDATE users SET plan=?, plan_assigned_at=? WHERE id=?",
                (PLAN_FREE, now, user["id"])
            )
            print(f"  · [{idx:02d}] {user['username']} → free_limited")

    conn.commit()

    print("\n[4/4] Verificación final...")
    total    = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    pioneers = conn.execute(f"SELECT COUNT(*) FROM users WHERE plan='{PLAN_MAX}'").fetchone()[0]
    free     = conn.execute(f"SELECT COUNT(*) FROM users WHERE plan='{PLAN_FREE}'").fetchone()[0]
    admin    = conn.execute(f"SELECT COUNT(*) FROM users WHERE plan='{PLAN_ADMIN}'").fetchone()[0]
    print(f"  Total usuarios: {total}")
    print(f"  · plan_max (pioneros): {pioneers}")
    print(f"  · free_limited:        {free}")
    print(f"  · admin:               {admin}")

    conn.close()
    print("\n[OK] Migración completada sin pérdida de datos.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/analizador.db", help="Ruta a la DB SQLite")
    args = parser.parse_args()
    migrate(args.db)
