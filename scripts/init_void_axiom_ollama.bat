@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0.."

echo ============================================================
echo   VOID AXIOM / NEXO - Ollama aislado en GTX 1080 Ti
echo ============================================================

where nvidia-smi >nul 2>nul
if errorlevel 1 (
    echo [ERROR] nvidia-smi no esta disponible en PATH.
    exit /b 1
)

set "GTX_ID="
for /f "tokens=1,* delims=," %%A in ('nvidia-smi --query-gpu^=index^,name --format^=csv^,noheader ^| findstr /I "1080 Ti"') do (
    if not defined GTX_ID set "GTX_ID=%%A"
)
set "GTX_ID=%GTX_ID: =%"

if not defined GTX_ID (
    echo [ERROR] No se encontro una NVIDIA GeForce GTX 1080 Ti.
    echo         Revisa el ID con:
    echo         nvidia-smi --query-gpu=index,name --format=csv
    exit /b 1
)

tasklist /FI "IMAGENAME eq ollama.exe" | find /I "ollama.exe" >nul
if not errorlevel 1 (
    echo [ERROR] Ollama ya esta ejecutandose.
    echo         Cierralo antes para garantizar aislamiento por CUDA_VISIBLE_DEVICES.
    exit /b 1
)

rem Un solo runner por modelo evita multiplicar KV cache en 11GB.
rem OLLAMA_MAX_LOADED_MODELS=4 mantiene los cuatro perfiles calientes.
set "CUDA_VISIBLE_DEVICES=%GTX_ID%"
set "OLLAMA_NUM_PARALLEL=1"
set "OLLAMA_MAX_LOADED_MODELS=4"
set "OLLAMA_KEEP_ALIVE=-1"
set "OLLAMA_CONTEXT_LENGTH=2048"
set "OLLAMA_FLASH_ATTENTION=1"
set "OLLAMA_KV_CACHE_TYPE=f16"
set "OLLAMA_SCHED_SPREAD=0"

echo [OK] GTX 1080 Ti detectada como GPU fisica ID %GTX_ID%.
echo [OK] CUDA_VISIBLE_DEVICES=%CUDA_VISIBLE_DEVICES%
echo [OK] OLLAMA_NUM_PARALLEL=%OLLAMA_NUM_PARALLEL%
echo [OK] OLLAMA_MAX_LOADED_MODELS=%OLLAMA_MAX_LOADED_MODELS%
echo [OK] OLLAMA_KEEP_ALIVE=%OLLAMA_KEEP_ALIVE%

where ollama >nul 2>nul
if errorlevel 1 (
    echo [ERROR] ollama.exe no esta disponible en PATH.
    exit /b 1
)

echo [INFO] Lanzando ollama serve con entorno aislado...
start "Ollama Void Axiom - GTX 1080 Ti only" /min cmd /c "set CUDA_VISIBLE_DEVICES=%CUDA_VISIBLE_DEVICES%&& set OLLAMA_NUM_PARALLEL=%OLLAMA_NUM_PARALLEL%&& set OLLAMA_MAX_LOADED_MODELS=%OLLAMA_MAX_LOADED_MODELS%&& set OLLAMA_KEEP_ALIVE=%OLLAMA_KEEP_ALIVE%&& set OLLAMA_CONTEXT_LENGTH=%OLLAMA_CONTEXT_LENGTH%&& set OLLAMA_FLASH_ATTENTION=%OLLAMA_FLASH_ATTENTION%&& set OLLAMA_KV_CACHE_TYPE=%OLLAMA_KV_CACHE_TYPE%&& set OLLAMA_SCHED_SPREAD=%OLLAMA_SCHED_SPREAD%&& ollama serve"

echo [INFO] Esperando API local de Ollama...
for /L %%I in (1,1,45) do (
    curl.exe -fsS http://127.0.0.1:11434/api/tags >nul 2>nul
    if not errorlevel 1 goto OLLAMA_READY
    timeout /t 1 /nobreak >nul
)

echo [ERROR] Ollama no respondio en http://127.0.0.1:11434.
exit /b 1

:OLLAMA_READY
echo [OK] Ollama activo.

echo [INFO] Descargando bases ligeras cuantizadas desde Ollama si faltan...
ollama pull qwen2.5:3b
if errorlevel 1 exit /b 1
ollama pull qwen2.5-coder:3b
if errorlevel 1 exit /b 1
ollama pull qwen2.5:1.5b
if errorlevel 1 exit /b 1

echo [INFO] Creando perfiles Void Axiom...
ollama create void-arch7 -f "ollama\Modelfile.arch7"
if errorlevel 1 exit /b 1
ollama create void-coda -f "ollama\Modelfile.coda"
if errorlevel 1 exit /b 1
ollama create void-rebx3 -f "ollama\Modelfile.rebx3"
if errorlevel 1 exit /b 1
ollama create void-intruder -f "ollama\Modelfile.intruder"
if errorlevel 1 exit /b 1

echo [INFO] Precargando los 4 modelos con keep_alive=-1...
for %%M in (void-arch7 void-coda void-rebx3 void-intruder) do (
    echo   - %%M
    curl.exe -s http://127.0.0.1:11434/api/generate -H "Content-Type: application/json" -d "{\"model\":\"%%M\",\"prompt\":\"ping\",\"stream\":false,\"keep_alive\":\"-1\",\"options\":{\"num_predict\":1,\"num_ctx\":256}}" >nul
)

echo [INFO] Modelos residentes segun Ollama:
ollama ps

echo ============================================================
echo   Entorno listo.
echo   Backend:
echo   python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
echo ============================================================

endlocal
