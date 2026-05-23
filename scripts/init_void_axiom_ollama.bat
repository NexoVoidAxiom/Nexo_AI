@echo off
chcp 65001 >nul
title VOID AXIOM — Init Ollama Multi-Agent

echo.
echo ╔══════════════════════════════════════════════════════════╗
echo ║       VOID AXIOM — INICIALIZACIÓN MULTI-AGENTE          ║
echo ║  GTX 1080 Ti (11GB) + i7-9700K (8c) + 32GB DDR4         ║
echo ╚══════════════════════════════════════════════════════════╝
echo.

:: ── Aislamiento GPU ───────────────────────────────────────────────────────────
:: Mapea únicamente la GTX 1080 Ti. Si tienes otra GPU secundaria,
:: ajusta el índice (0 = primera GPU por orden PCI).
set CUDA_VISIBLE_DEVICES=0

:: ── Coexistencia de modelos en VRAM ──────────────────────────────────────────
:: 4 modelos cargados simultáneamente = 0 lag de swap entre turnos.
:: OLLAMA_NUM_PARALLEL=1 es CRÍTICO: las llamadas van en cola,
:: no en paralelo, para evitar el cruce de hilos en GPU (bug de coherencia).
set OLLAMA_MAX_LOADED_MODELS=4
set OLLAMA_NUM_PARALLEL=1

:: Keep-alive infinito: los modelos nunca se descargan entre llamadas.
set OLLAMA_KEEP_ALIVE=-1

:: Memoria del host para KV cache overflow (55K modo idle → RAM)
set OLLAMA_MAX_QUEUE=512

:: ── Variables del backend ─────────────────────────────────────────────────────
set VOID_OLLAMA_BASE_URL=http://127.0.0.1:11434
set VOID_OLLAMA_HTTP_RETRIES=3
:: Concurrencia=1: serializa todas las llamadas HTTP (mutex por código + por Ollama)
set VOID_OLLAMA_CONCURRENCY=1

:: Contexto y memoria
set VOID_AGENT_NUM_CTX=16384
set VOID_INTRUDER_NUM_CTX=8192
set VOID_AGENT_NUM_PREDICT=200
set VOID_AGENT_TEMPERATURE=0.25
set VOID_HISTORY_MAX_MESSAGES=96
set VOID_HISTORY_MAX_TOKENS=12000
set VOID_EXTENDED_HISTORY_MAX_MESSAGES=512
set VOID_EXTENDED_HISTORY_MAX_TOKENS=55000
set VOID_ACTIVE_TURNS_TO_KEEP=4

:: Umbrales de reposo para activar modo 55K
set VOID_MAIN_IDLE_SECONDS=45
set VOID_CHAT_IDLE_SECONDS=30

:: ── Verificar Ollama activo ───────────────────────────────────────────────────
echo [..] Verificando Ollama en http://127.0.0.1:11434 ...
curl -s -o nul -w "%%{http_code}" http://127.0.0.1:11434 > tmp_status.txt 2>&1
set /p HTTP_STATUS=<tmp_status.txt
del tmp_status.txt >nul 2>&1

if "%HTTP_STATUS%"=="200" (
    echo [OK] Ollama está activo.
) else (
    echo [WARN] Ollama no detectado. Iniciando...
    start "" ollama serve
    timeout /t 4 /nobreak >nul
)

:: ── Construir e instalar los 4 Modelfiles ────────────────────────────────────
echo.
echo [..] Instalando modelos Void Axiom en Ollama...
echo.

:: Verifica que los Modelfiles existen
for %%M in (arch7 coda rebx3 intruder) do (
    if not exist "ollama\Modelfile.%%M" (
        echo [ERROR] Falta ollama\Modelfile.%%M
        pause & exit /b 1
    )
)

ollama create arch7_void    -f ollama\Modelfile.arch7
ollama create coda_void     -f ollama\Modelfile.coda
ollama create rebx3_void    -f ollama\Modelfile.rebx3
ollama create intruder_void -f ollama\Modelfile.intruder

if errorlevel 1 (
    echo [ERROR] Fallo al crear uno o más modelos.
    pause & exit /b 1
)

echo.
echo [OK] Los 4 modelos instalados correctamente.
echo.

:: ── Pre-carga en caliente (warm-up) ──────────────────────────────────────────
:: Envía un ping mínimo a cada modelo para forzar la carga en VRAM
:: antes de que llegue el primer usuario. Elimina el lag del primer turno.
echo [..] Pre-cargando modelos en VRAM (warm-up)...
for %%M in (arch7_void coda_void rebx3_void intruder_void) do (
    echo     Cargando %%M...
    curl -s -X POST http://127.0.0.1:11434/api/chat ^
         -H "Content-Type: application/json" ^
         -d "{\"model\":\"%%M\",\"messages\":[{\"role\":\"user\",\"content\":\".\"}],\"stream\":false,\"options\":{\"num_predict\":1}}" ^
         >nul 2>&1
)

echo [OK] Warm-up completado. Los 4 modelos están en VRAM.
echo.
echo ══════════════════════════════════════════════════════════
echo   VOID AXIOM listo. Inicia el servidor con:
echo   python -m app.main
echo ══════════════════════════════════════════════════════════
echo.
pause
