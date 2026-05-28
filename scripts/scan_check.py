"""
scan_check.py — Escner completo de VOID AXIOM
===============================================
Ejecutar: python scripts/scan_check.py
"""

import os
import sys
import json
import socket
import importlib
import subprocess
from pathlib import Path

def check(label, condition, detail=""):
    if condition:
        print(f"  [OK] {label}")
        if detail:
            print(f"        {detail}")
        return True
    else:
        print(f"  [FAIL] {label}")
        if detail:
            print(f"        {detail}")
        return False

def warning(label, detail=""):
    print(f"  [WARN] {label}")
    if detail:
        print(f"        {detail}")

def info(label):
    print(f"    [i] {label}")

def step(num, label):
    print(f"\n[{num}] {label}")
    print(f"  " + "-" * 40)

def scan_all():
    print("=" * 50)
    print("  VOID AXIOM - ESCANER COMPLETO DEL SISTEMA")
    print("  Comprobando modulos, conexiones y configuracion...")
    print("=" * 50)
    print()

    BASE = Path.cwd()
    results = {"ok": 0, "fail": 0, "warn": 0}

    step(1, "ESTRUCTURA DEL PROYECTO")
    for d in ["app", "training", "static", "data", "core", "agents", "scripts"]:
        ok = check(f"Directorio {d}/", (BASE / d).exists())
        results["ok" if ok else "fail"] += 1
    for f in ["main.py", "requirements.txt", "apply_fixes.py", "migrate_db.py"]:
        ok = check(f"Archivo {f}", (BASE / f).exists())
        results["ok" if ok else "fail"] += 1

    step(2, "PYTHON Y DEPENDENCIAS")
    check("Python instalado", True, f"Version: {sys.version.split()[0]}")
    results["ok"] += 1
    
    critical = {"fastapi": "Servidor web", "uvicorn": "Servidor ASGI", "pydantic": "Validacion", "sqlite3": "Base datos"}
    for mod, desc in critical.items():
        try:
            importlib.import_module(mod)
            ok = check(f"{desc} ({mod})", True); results["ok"] += 1
        except ImportError:
            ok = check(f"{desc} ({mod})", False, f"pip install {mod}"); results["fail"] += 1
    
    optional = {"pypdf": "Lectura PDFs", "httpx": "HTTP async", "tiktoken": "Tokens", "duckduckgo_search": "Busqueda web"}
    for mod, desc in optional.items():
        try:
            importlib.import_module(mod)
            check(f"Opcional: {desc}", True); results["ok"] += 1
        except ImportError:
            warning(f"Opcional no instalado: {desc}"); results["warn"] += 1

    step(3, "OLLAMA - MOTOR DE IA LOCAL")
    import urllib.request
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = data.get("models", [])
            check("Ollama accesible", True, "URL: http://127.0.0.1:11434")
            results["ok"] += 1
            if models:
                info(f"Modelos: {len(models)} instalados")
                for m in models:
                    info(f"  - {m.get('name','?')}")
            else:
                warning("No hay modelos instalados"); results["warn"] += 1
    except Exception as e:
        check("Ollama accesible", False); results["fail"] += 1
        warning(f"Error: {e}", "Ejecuta 'ollama serve'"); results["warn"] += 1

    step(4, "BASE DE CONOCIMIENTO")
    kb = BASE / "knowledge_base"
    if kb.exists():
        pdfs = len(list(kb.rglob("*.pdf")))
        codes = sum(len(list(kb.rglob(f"*{e}"))) for e in [".py",".js",".ts",".java",".cpp",".c",".rs",".go"])
        check("knowledge_base existe", True, f"{pdfs+codes} archivos ({pdfs} PDFs, {codes} codigo)")
        results["ok"] += 1
    else:
        check("knowledge_base existe", False, "Crea el directorio"); results["fail"] += 1

    step(5, "DATASETS")
    ds = BASE / "datasets"
    if ds.exists():
        files = list(ds.glob("*.jsonl"))
        check("datasets/ existe", True, f"{len(files)} JSONL"); results["ok"] += 1
    else:
        check("datasets/ existe", False); results["fail"] += 1

    step(6, "SERVIDOR WEB (localhost:8080)")
    try:
        req = urllib.request.Request("http://127.0.0.1:8080/ping")
        with urllib.request.urlopen(req, timeout=3) as resp:
            d = json.loads(resp.read())
            if d.get("status") == "ok":
                check("Servidor web activo", True, "http://localhost:8080"); results["ok"] += 1
            else:
                check("Servidor web", False); results["fail"] += 1
    except:
        check("Servidor web", False, "Ejecuta el batch Iniciar Analizador IA.bat")
        results["fail"] += 1

    step(7, "SCRIPTS DISPONIBLES")
    for bf in list(BASE.glob("*.bat")):
        check(f"Batch: {bf.name}", True); results["ok"] += 1
    for sf in list((BASE / "scripts").glob("*.*")) if (BASE / "scripts").exists() else []:
        check(f"Script: {sf.name}", True); results["ok"] += 1

    step(8, "TRAINING")
    for tf in ["study_engine.py","dataset_builder.py","pdf_scraper.py","train_qlora.py"]:
        ok = check(f"Training: {tf}", (BASE / "training" / tf).exists())
        results["ok" if ok else "fail"] += 1

    step(9, "RED")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        r = sock.connect_ex(('127.0.0.1', 8080))
        sock.close()
        info("Puerto 8080: " + ("ocupado" if r == 0 else "libre"))
        results["ok"] += 1
    except:
        warning("No se pudo verificar puerto 8080"); results["warn"] += 1
    try:
        urllib.request.urlopen("http://8.8.8.8", timeout=3)
        check("Internet", True); results["ok"] += 1
    except:
        warning("Sin internet"); results["warn"] += 1

    step(10, "TUNELES")
    for name, exe in [("cloudflared","cloudflared.exe"),("ngrok","ngrok.exe")]:
        encontrado = (BASE / exe).exists() or _which(exe)
        check(f"{name} disponible", encontrado)
        results["ok" if encontrado else "warn"] += 1

    print()
    print("=" * 50)
    print("  RESUMEN DEL ESCANER")
    print("=" * 50)
    print(f"  Correctos:     {results['ok']}")
    print(f"  Fallos:        {results['fail']}")
    print(f"  Advertencias:  {results['warn']}")
    total = results['ok'] + results['fail'] + results['warn']
    score = (results['ok'] / total * 100) if total > 0 else 0
    print(f"  Puntuacion:    {score:.0f}%")
    print()
    if results['fail'] == 0 and results['warn'] <= 3:
        print("  SISTEMA LISTO PARA USAR")
    elif results['fail'] == 0:
        print("  Sistema OK pero con advertencias")
    else:
        print(f"  Hay {results['fail']} fallos que requieren atencion")
    print()
    print("  Comandos utiles:")
    print("    python -m app.main                 -> Servidor web")
    print("    python training/pdf_scraper.py      -> Descargar PDFs")
    print("    python training/study_engine.py     -> Estudiar PDFs")
    print("    python training/dataset_builder.py  -> Construir dataset")
    print("    python scripts/scan_check.py        -> Este escaner")
    print()

def _which(program):
    try:
        r = subprocess.run(["where", program], capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except: return False

if __name__ == "__main__":
    scan_all()