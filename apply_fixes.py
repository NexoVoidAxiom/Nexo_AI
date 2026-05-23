"""
apply_fixes.py — Copia los archivos corregidos al proyecto Void Axiom.

Uso (desde la raíz del proyecto):
    python apply_fixes.py [--project /ruta/al/proyecto]

Por defecto asume que este script está en la misma carpeta que el zip extraído.
"""
import shutil
import argparse
from pathlib import Path

FIXED_FILES = [
    "void_agents.py",
    "void_memory.py",
    "void_ollama.py",
    "config.py",
    "database.py",
    "main.py",
]

def apply(project_root: str, fixed_dir: str) -> None:
    app_dir  = Path(project_root) / "app"
    fix_dir  = Path(fixed_dir)

    if not app_dir.exists():
        print(f"[ERROR] No se encontró el directorio app/ en {project_root}")
        return

    print(f"\nAplicando fixes a: {app_dir.resolve()}\n")
    for fname in FIXED_FILES:
        src  = fix_dir / fname
        dest = app_dir / fname
        if not src.exists():
            print(f"  [SKIP] {fname} — archivo fuente no encontrado")
            continue
        # Backup del original
        backup = dest.with_suffix(dest.suffix + ".bak")
        if dest.exists():
            shutil.copy2(str(dest), str(backup))
        shutil.copy2(str(src), str(dest))
        print(f"  ✓ {fname} actualizado  (backup: {backup.name})")

    # Copy migration script
    src_migrate  = fix_dir.parent / "migrate_db.py"
    dest_migrate = Path(project_root) / "migrate_db.py"
    if src_migrate.exists():
        shutil.copy2(str(src_migrate), str(dest_migrate))
        print(f"  ✓ migrate_db.py copiado a raíz del proyecto")

    print("""
╔══════════════════════════════════════════════════════════════╗
║  Fixes aplicados. Pasos siguientes:                          ║
║                                                              ║
║  1. Si tienes una DB existente (data/analizador.db):         ║
║     python migrate_db.py --db data/analizador.db             ║
║                                                              ║
║  2. Reinicia el servidor:                                     ║
║     uvicorn app.main:app --reload                            ║
║                                                              ║
║  3. Verifica que no hay ImportError en el arranque.          ║
╚══════════════════════════════════════════════════════════════╝
""")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=".", help="Raíz del proyecto")
    parser.add_argument("--fixed",   default="fixed",  help="Carpeta con archivos corregidos")
    args = parser.parse_args()
    apply(args.project, args.fixed)
