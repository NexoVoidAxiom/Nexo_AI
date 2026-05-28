"""
update_batch.py — Actualiza el batch "Iniciar Analizador IA.bat"
"""
import os
import sys

def update_batch():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bak_path = os.path.join(base, "Iniciar Analizador IA.bak")
    bat_path = os.path.join(base, "Iniciar Analizador IA.bat")

    if not os.path.exists(bak_path):
        print("ERROR: Backup no encontrado")
        return False

    with open(bak_path, "r", encoding="utf-8") as f:
        text = f.read()

    print("OK")

    # 1. Add menu items after echo [N]
    target1 = "echo [N]  TUNEL NGROK FIJO   ^<-- URL PERMANENTE GRATIS^>"
    insert1 = "\necho -------------------------------------------\necho [E]  ESCANEAR SISTEMA  ^<-- NUEVO: comprueba TODO^>\necho [K]  DESCARGAR PDFs    ^<-- NUEVO: knowledge base^>\necho [S]  ESTUDIAR PDFs     ^<-- NUEVO: generar Q&A^>"
    if target1 in text:
        text = text.replace(target1, target1 + insert1)
        print("Menu: OK")
    else:
        print("Menu: FAIL - target not found")

    # 2. Add option handlers
    target2 = 'if /i "%op%"=="0" exit /b 0'
    insert2 = '\nif /i "%op%"=="E" goto ESCANEAR\nif /i "%op%"=="e" goto ESCANEAR\nif /i "%op%"=="K" goto DESCARGAR_PDFS\nif /i "%op%"=="k" goto DESCARGAR_PDFS\nif /i "%op%"=="S" goto ESTUDIAR\nif /i "%op%"=="s" goto ESTUDIAR'
    if target2 in text:
        text = text.replace(target2, target2 + insert2)
        print("Handlers: OK")
    else:
        print("Handlers: FAIL - target not found")

    # 3. Add new sections before :CERRAR_TODO
    target3 = ":CERRAR_TODO"
    insert3 = """:ESCANEAR
cls
echo ============================================
echo   ESCANEANDO SISTEMA VOID AXIOM
echo ============================================
echo.
echo  Comprobando modulos, conexiones, archivos...
echo  Esto tomara unos segundos...
echo.
python scripts\scan_check.py
if %errorlevel% neq 0 (
    echo.
    echo  [WARN] No se pudo ejecutar el escaner.
)
echo.
pause
goto MENU

:DESCARGAR_PDFS
cls
echo ============================================
echo   DESCARGANDO PDFS A KNOWLEDGE BASE
echo ============================================
echo.
echo  [A] TODO (recomendado)
echo  [B] Solo libros curados
echo  [C] Solo tutoriales web
echo  [D] Solo papers arXiv
echo  [L] Solo un lenguaje
echo  [V] Solo reporte
echo  [R] Volver
echo.
set /p pdf_op=Opcion: 
if /i "%pdf_op%"=="A" python training\pdf_scraper.py --limit 30
if /i "%pdf_op%"=="B" python training\pdf_scraper.py --books --limit 10
if /i "%pdf_op%"=="C" python training\pdf_scraper.py --tutorials --limit 20
if /i "%pdf_op%"=="D" python training\pdf_scraper.py --arxiv --topics --limit 15
if /i "%pdf_op%"=="L" set /p LANG=Lenguaje: & python training\pdf_scraper.py --language %LANG% --limit 10
if /i "%pdf_op%"=="V" python training\pdf_scraper.py --report
if /i "%pdf_op%"=="R" goto MENU
if not "%pdf_op%"=="" pause
goto MENU

:ESTUDIAR
cls
echo ============================================
echo   MODO ESTUDIO - GENERANDO QandA
echo ============================================
echo.
echo  [1] Estudio completo
echo  [2] Vista previa (dry-run)
echo  [3] Con limite (max 5)
echo  [R] Volver
echo.
set /p est_op=Opcion: 
if /i "%est_op%"=="1" python training\study_engine.py --model qwen2.5-coder:14b --workers 1 --source D:\VOID\knowledge_base
if /i "%est_op%"=="2" python training\study_engine.py --model qwen2.5-coder:14b --workers 1 --source D:\VOID\knowledge_base --dry-run
if /i "%est_op%"=="3" python training\study_engine.py --model qwen2.5-coder:14b --workers 1 --source D:\VOID\knowledge_base --limit 5
if /i "%est_op%"=="R" goto MENU
if not "%est_op%"=="" pause
goto MENU

"""
    if target3 in text:
        text = text.replace(target3, insert3 + target3)
        print("Sections: OK")
    else:
        print("Sections: FAIL - target not found")

    # Write back
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(text)

    # Verify
    with open(bat_path, "r", encoding="utf-8") as f:
        final = f.read()
    
    ok = all([
        "ESCANEAR" in final,
        "DESCARGAR_PDFS" in final,
        "ESTUDIAR" in final,
        "scan_check" in final,
        "pdf_scraper" in final,
        "study_engine" in final,
    ])
    
    print(f"Lines: {len(final.splitlines())}")
    print(f"All checks: {'OK' if ok else 'FAIL'}")
    return ok

if __name__ == "__main__":
    update_batch()