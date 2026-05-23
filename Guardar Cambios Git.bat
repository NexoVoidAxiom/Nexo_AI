@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

echo ============================================================
echo   VOID AXIOM / NEXO - Guardado automatico Git
echo ============================================================
echo.

where git >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Git no esta instalado o no esta en PATH.
    echo Instala Git con:
    echo   winget install Git.Git
    echo.
    pause
    exit /b 1
)

if not exist ".git" (
    echo [ERROR] Esta carpeta aun no tiene Git inicializado.
    echo Ejecuta primero:
    echo   Inicializar Git.bat
    echo.
    pause
    exit /b 1
)

set "GIT_NAME="
for /f "delims=" %%A in ('git config --get user.name 2^>nul') do set "GIT_NAME=%%A"
if not defined GIT_NAME git config --local user.name "Aerys"

set "GIT_EMAIL="
for /f "delims=" %%A in ('git config --get user.email 2^>nul') do set "GIT_EMAIL=%%A"
if not defined GIT_EMAIL git config --local user.email "aerys@example.com"

echo [INFO] Cambios detectados antes de guardar:
git status --short
echo.

echo [INFO] Preparando todos los cambios...
git add -A
if errorlevel 1 (
    echo [ERROR] git add fallo.
    pause
    exit /b 1
)

git diff --cached --quiet
if not errorlevel 1 (
    echo [OK] No hay cambios nuevos para guardar.
    echo.
    git status
    pause
    exit /b 0
)

for /f "tokens=1-3 delims=/" %%A in ("%date%") do (
    set "DD=%%A"
    set "MM=%%B"
    set "YYYY=%%C"
)

set "HH=%time:~0,2%"
set "MN=%time:~3,2%"
set "SS=%time:~6,2%"
set "HH=%HH: =0%"

set "COMMIT_MSG=Auto guardado %YYYY%-%MM%-%DD% %HH%-%MN%-%SS%"

echo [INFO] Creando commit:
echo        %COMMIT_MSG%
echo.

git commit -m "%COMMIT_MSG%"
if errorlevel 1 (
    echo [ERROR] git commit fallo.
    echo Revisa el mensaje anterior de Git.
    pause
    exit /b 1
)

echo.
echo [OK] Guardado completado.
echo.
echo Ultimo commit:
git log --oneline -1
echo.
git status
pause
