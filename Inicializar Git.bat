@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

echo ============================================================
echo   VOID AXIOM / NEXO - Inicializador Git
echo ============================================================
echo.

where git >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Git no esta instalado o no esta en PATH.
    echo.
    echo Instala Git con:
    echo   winget install Git.Git
    echo.
    pause
    exit /b 1
)

echo [OK] Git detectado:
git --version
echo.

if not exist ".gitignore" (
    echo [INFO] Creando .gitignore seguro...
    > ".gitignore" (
        echo # Python
        echo __pycache__/
        echo *.py[cod]
        echo *$py.class
        echo .pytest_cache/
        echo .mypy_cache/
        echo .ruff_cache/
        echo.
        echo # Entornos locales
        echo .env
        echo .env.*
        echo venv/
        echo .venv/
        echo env/
        echo.
        echo # Datos, caches y salidas generadas
        echo data/*.db
        echo data/*.sqlite
        echo uploads/
        echo generated_projects/
        echo *.log
        echo ollama/*.log
        echo.
        echo # Secretos y configuracion local
        echo cloudflare_token.txt
        echo config.yml
        echo *.pem
        echo *.key
        echo.
        echo # Binarios locales grandes
        echo *.exe
        echo.
        echo # IDE / sistema
        echo .vscode/
        echo .idea/
        echo Thumbs.db
        echo Desktop.ini
    )
) else (
    findstr /C:"# --- Void Axiom local ignores ---" ".gitignore" >nul 2>nul
    if errorlevel 1 (
        echo [INFO] .gitignore ya existe. Anadiendo reglas locales seguras...
        >> ".gitignore" echo.
        >> ".gitignore" echo # --- Void Axiom local ignores ---
        >> ".gitignore" echo cloudflare_token.txt
        >> ".gitignore" echo config.yml
        >> ".gitignore" echo data/*.db
        >> ".gitignore" echo data/*.sqlite
        >> ".gitignore" echo uploads/
        >> ".gitignore" echo generated_projects/
        >> ".gitignore" echo ollama/*.log
        >> ".gitignore" echo *.exe
        >> ".gitignore" echo __pycache__/
        >> ".gitignore" echo *.py[cod]
        >> ".gitignore" echo .env
        >> ".gitignore" echo .env.*
    ) else (
        echo [OK] .gitignore ya tenia reglas Void Axiom.
    )
)

echo.
if not exist ".git" (
    echo [INFO] Inicializando repositorio Git...
    git init
    if errorlevel 1 (
        echo [ERROR] No se pudo inicializar Git.
        pause
        exit /b 1
    )
) else (
    echo [OK] Git ya estaba inicializado en esta carpeta.
)

echo.
set "GIT_NAME="
for /f "delims=" %%A in ('git config --get user.name 2^>nul') do set "GIT_NAME=%%A"
if not defined GIT_NAME (
    set /p "GIT_NAME=Nombre para Git [Aerys]: "
    if not defined GIT_NAME set "GIT_NAME=Aerys"
    git config --local user.name "!GIT_NAME!"
    echo [OK] user.name local = !GIT_NAME!
) else (
    echo [OK] user.name = !GIT_NAME!
)

set "GIT_EMAIL="
for /f "delims=" %%A in ('git config --get user.email 2^>nul') do set "GIT_EMAIL=%%A"
if not defined GIT_EMAIL (
    set /p "GIT_EMAIL=Email para Git [aerys@example.com]: "
    if not defined GIT_EMAIL set "GIT_EMAIL=aerys@example.com"
    git config --local user.email "!GIT_EMAIL!"
    echo [OK] user.email local = !GIT_EMAIL!
) else (
    echo [OK] user.email = !GIT_EMAIL!
)

echo.
echo [INFO] Estado actual:
git status --short

echo.
echo [INFO] Preparando commit inicial...
git add .
if errorlevel 1 (
    echo [ERROR] git add fallo.
    pause
    exit /b 1
)

git diff --cached --quiet
if not errorlevel 1 (
    echo [OK] No hay cambios nuevos para commitear.
    echo.
    git status
    pause
    exit /b 0
)

git commit -m "Estado inicial"
if errorlevel 1 (
    echo [ERROR] git commit fallo.
    echo Revisa el mensaje anterior de Git.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Git listo. Ya tienes un punto seguro: "Estado inicial"
echo ============================================================
echo.
git status
pause
