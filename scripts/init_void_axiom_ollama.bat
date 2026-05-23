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
    echo         Cierralo antes para garantizar que CUDA_VISIBLE_DEVICES sea respetado.
    exit /b 1
)

set "CUDA_VISIBLE_DEVICES=%GTX_ID%"
set "OLLAMA_NUM_PARALLEL=4"
set "OLLAMA_MAX_LOADED_MODELS=4"
set "OLLAMA_KEEP_ALIVE=-1"
set "OLLAMA_CONTEXT_LENGTH=16384"
set "OLLAMA_FLASH_ATTENTION=1"
set "OLLAMA_KV_CACHE_TYPE=f16"
set "OLLAMA_SCHED_SPREAD=0"

echo [OK] GTX 1080 Ti detectada como GPU fisica ID %GTX_ID%.
echo [OK] CUDA_VISIBLE_DEVICES=%CUDA_VISIBLE_DEVICES%
echo [OK] OLLAMA_NUM_PARALLEL=%OLLAMA_NUM_PARALLEL%
echo [OK] OLLAMA_MAX_LOADED_MODELS=%OLLAMA_MAX_LOADED_MODELS%
echo [OK] OLLAMA_CONTEXT_LENGTH=%OLLAMA_CONTEXT_LENGTH%

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

echo [INFO] Preparando bases Qwen 2.5 cuantizadas...
call :ensure_model "qwen2.5:3b-instruct-q4_K_M" "qwen2.5:3b" "qwen2.5-coder:3b"
if errorlevel 1 exit /b 1
call :ensure_model "qwen2.5-coder:3b-instruct-q4_K_M" "qwen2.5-coder:3b" ""
if errorlevel 1 exit /b 1
call :ensure_model "qwen2.5:1.5b-instruct-q4_K_M" "qwen2.5:1.5b" "qwen2.5-coder:1.5b"
if errorlevel 1 exit /b 1

echo [INFO] Creando perfiles Void Axiom...
call :create_profile "void-arch7" "ollama\Modelfile.arch7"
if errorlevel 1 exit /b 1
call :create_profile "void-coda" "ollama\Modelfile.coda"
if errorlevel 1 exit /b 1
call :create_profile "void-rebx3" "ollama\Modelfile.rebx3"
if errorlevel 1 exit /b 1
call :create_profile "void-intruder" "ollama\Modelfile.intruder"
if errorlevel 1 exit /b 1

echo [INFO] Precargando los 4 modelos con keep_alive=-1s y num_ctx=16384...
for %%M in (void-arch7 void-coda void-rebx3 void-intruder) do (
    echo   - %%M
    curl.exe -s http://127.0.0.1:11434/api/generate -H "Content-Type: application/json" -d "{\"model\":\"%%M\",\"prompt\":\"ping\",\"stream\":false,\"keep_alive\":\"-1s\",\"options\":{\"num_predict\":1,\"num_ctx\":16384,\"num_gpu\":99}}" >nul
)

echo [INFO] Modelos residentes segun Ollama:
ollama ps

echo ============================================================
echo   Entorno listo.
echo   Backend:
echo   python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
echo ============================================================

endlocal
exit /b 0

:ensure_model
set "EXACT=%~1"
set "CANON=%~2"
set "ALT=%~3"

ollama show "%EXACT%" >nul 2>nul
if not errorlevel 1 (
    echo [OK] %EXACT% ya existe.
    exit /b 0
)

echo [INFO] Intentando pull exacto: %EXACT%
ollama pull "%EXACT%" >nul 2>nul
if not errorlevel 1 (
    echo [OK] %EXACT% descargado.
    exit /b 0
)

echo [WARN] El tag exacto no existe en esta libreria de Ollama. Usando alias local.
echo [INFO] Descargando base canonica: %CANON%
ollama pull "%CANON%"
if errorlevel 1 (
    if defined ALT (
        echo [WARN] Base canonica no disponible. Probando alternativa: %ALT%
        set "CANON=%ALT%"
        ollama pull "!CANON!"
        if errorlevel 1 exit /b 1
    ) else (
        exit /b 1
    )
)

set "TMP_MODELFILE=%TEMP%\void_alias_%RANDOM%_%RANDOM%.Modelfile"
> "%TMP_MODELFILE%" echo FROM !CANON!
ollama create "%EXACT%" -f "%TMP_MODELFILE%"
set "CREATE_CODE=%ERRORLEVEL%"
del "%TMP_MODELFILE%" >nul 2>nul
exit /b %CREATE_CODE%

:create_profile
set "PROFILE=%~1"
set "MODELFILE=%~2"
ollama create "%PROFILE%" -f "%MODELFILE%"
if not errorlevel 1 exit /b 0

echo [WARN] Creacion estricta fallo. Reintentando sin penalties OpenAI-only para compatibilidad nativa...
set "TMP_PROFILE=%TEMP%\void_profile_%RANDOM%_%RANDOM%.Modelfile"
findstr /V /C:"PARAMETER frequency_penalty" /C:"PARAMETER presence_penalty" "%MODELFILE%" > "%TMP_PROFILE%"
ollama create "%PROFILE%" -f "%TMP_PROFILE%"
set "PROFILE_CODE=%ERRORLEVEL%"
del "%TMP_PROFILE%" >nul 2>nul
exit /b %PROFILE_CODE%
