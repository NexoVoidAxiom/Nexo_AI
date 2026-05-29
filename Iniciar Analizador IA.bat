@echo off
chcp 65001 >nul
title ANALIZADOR IA
set "PORT=8080"
set "AUTH_TOKEN=mi-analisis-ia-2024"
set "CF=cloudflared"
set "TOKEN_FILE=cloudflare_token.txt"
set "NGROK_DOMAIN=rockstar-prune-theater.ngrok-free.dev"

where cloudflared >nul 2>&1
if errorlevel 1 if exist "cloudflared.exe" set "CF=cloudflared.exe"
if errorlevel 1 if not exist "cloudflared.exe" set "CF="

where ngrok >nul 2>&1
if errorlevel 1 (set "NGROK=") else (set "NGROK=ngrok")
if not defined NGROK if exist "ngrok.exe" set "NGROK=ngrok.exe"

:MENU
cls
echo ============================================
echo   ANALIZADOR DE DATOS CON IA LOCAL
echo   GTX 1080 Ti + i7-9700K + 32GB RAM
echo ============================================
echo.
echo [1] Instalar dependencias
echo [2] INICIAR solo servidor web (localhost:%PORT%)
echo -------------------------------------------
echo [3] TUNEL CLOUDFLARE   (URL aleatoria, sin cuenta)
echo [4] TUNEL PINGGY       (URL aleatoria, sin cuenta)
echo -------------------------------------------
echo [5] GUARDAR TOKEN CLOUDFLARE FIJO  ^<-- solo 1a vez^>
echo [6] INICIAR TUNEL FIJO CLOUDFLARE  ^<-- URL PERMANENTE^>
echo -------------------------------------------
echo [N] TUNEL NGROK FIJO   ^<-- URL PERMANENTE GRATIS^>
echo     %NGROK_DOMAIN%
echo -------------------------------------------
echo [7] CERRAR SERVIDOR WEB
echo [8] CERRAR TUNEL
echo [9] CERRAR TODO
echo [0] Salir
echo.
echo Token app: %AUTH_TOKEN%
if defined CF (echo cloudflared: OK) else (echo cloudflared: NO INSTALADO - usa opcion 1)
if defined NGROK (echo ngrok: OK) else (echo ngrok: NO INSTALADO - descarga en https://ngrok.com/download)
if exist "%TOKEN_FILE%" (echo Token CF fijo: GUARDADO) else (echo Token CF fijo: NO GUARDADO)
echo.
set /p op="Opcion: "

if /i "%op%"=="1" goto INSTALAR
if /i "%op%"=="2" goto WEB
if /i "%op%"=="3" goto TUNEL_CF_RAPIDO
if /i "%op%"=="4" goto TUNEL_PINGGY
if /i "%op%"=="5" goto GUARDAR_TOKEN
if /i "%op%"=="6" goto TUNEL_FIJO
if /i "%op%"=="N" goto TUNEL_NGROK
if /i "%op%"=="7" goto CERRAR_WEB
if /i "%op%"=="8" goto CERRAR_TUNEL
if /i "%op%"=="9" goto CERRAR_TODO
if /i "%op%"=="0" exit /b 0
if /i "%op%"=="E" goto ESCANEAR
if /i "%op%"=="e" goto ESCANEAR
if /i "%op%"=="K" goto DESCARGAR_PDFS
if /i "%op%"=="k" goto DESCARGAR_PDFS
if /i "%op%"=="S" goto ESTUDIAR
if /i "%op%"=="s" goto ESTUDIAR
goto MENU

:INSTALAR
cls
echo Instalando dependencias Python...
pip install -r requirements.txt -q
echo [OK] Dependencias Python
echo.
echo Descargando cloudflared...
powershell -Command "Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile 'cloudflared.exe' -UseBasicParsing" >nul 2>&1
if exist cloudflared.exe (set "CF=cloudflared.exe" & echo [OK] cloudflared descargado) else (echo [WARN] No se pudo descargar cloudflared)
echo.
echo Descargando ngrok...
powershell -Command "Invoke-WebRequest -Uri 'https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip' -OutFile 'ngrok.zip' -UseBasicParsing" >nul 2>&1
if exist ngrok.zip (
    powershell -Command "Expand-Archive -Path 'ngrok.zip' -DestinationPath '.' -Force" >nul 2>&1
    del ngrok.zip >nul 2>&1
    if exist ngrok.exe (set "NGROK=ngrok.exe" & echo [OK] ngrok descargado) else (echo [WARN] No se pudo extraer ngrok)
) else (echo [WARN] No se pudo descargar ngrok)
echo.
pause
goto MENU

:: -------------------------------------------------------
:: Inicia el servidor en segundo plano si no esta activo
:: -------------------------------------------------------
:ARRANCAR_SERVIDOR_BG
powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing 'http://localhost:%PORT%/ping' -TimeoutSec 8; if ($r.StatusCode -eq 200) { exit 0 } } catch { }; exit 1" >nul 2>&1
if not errorlevel 1 (
    echo  [OK] Servidor ya estaba activo en http://localhost:%PORT%
    exit /b 0
)
echo  [--] Servidor no detectado. Arrancando en segundo plano...
start "SERVIDOR WEB - NO CIERRES" /min cmd /c "python -m app.main & pause"
echo  [..] Esperando que el servidor arranque (max 20 seg)...
set /a _wait=0
:WAIT_LOOP
timeout /t 2 /nobreak >nul
set /a _wait+=2
powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing 'http://localhost:%PORT%/ping' -TimeoutSec 8; if ($r.StatusCode -eq 200) { exit 0 } } catch { }; exit 1" >nul 2>&1
if not errorlevel 1 (
    echo  [OK] Servidor listo en http://localhost:%PORT%
    exit /b 0
)
if %_wait% lss 20 goto WAIT_LOOP
echo  [ERROR] El servidor no arranco en 20 segundos.
echo  Revisa la ventana minimizada "SERVIDOR WEB" para ver el error.
pause
exit /b 1

:WEB
cls
echo Iniciando servidor web...
powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing 'http://localhost:%PORT%/ping' -TimeoutSec 8; if ($r.StatusCode -eq 200) { exit 0 } } catch { }; exit 1" >nul 2>&1
if not errorlevel 1 (
    echo  [INFO] El servidor ya esta activo en http://localhost:%PORT%
    pause & goto MENU
)
powershell -NoProfile -Command "$o=@(Get-NetTCPConnection -LocalPort %PORT% -State Listen -EA SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); if ($o.Count -gt 0) { Write-Host '[ERROR] Puerto %PORT% ocupado por otro proceso'; exit 1 }"
if errorlevel 1 (pause & goto MENU)
echo  http://localhost:%PORT%   Token: %AUTH_TOKEN%
echo  NO CIERRES ESTA VENTANA.
echo.
python -m app.main
pause
goto MENU

:TUNEL_CF_RAPIDO
cls
echo ============================================
echo   TUNEL CLOUDFLARE RAPIDO (URL aleatoria)
echo ============================================
if not defined CF (echo [ERROR] cloudflared no instalado. Usa opcion 1. & pause & goto MENU)
:: Cerrar tunel anterior automaticamente
echo  [..] Cerrando tunel Cloudflare previo si existia...
taskkill /f /im cloudflared.exe >nul 2>&1
timeout /t 1 /nobreak >nul
:: Arrancar servidor si no esta activo
call :ARRANCAR_SERVIDOR_BG
if errorlevel 1 goto MENU
echo  Presiona Ctrl+C para detener el tunel.
echo.
"%CF%" tunnel --protocol http2 --edge-ip-version 4 --no-autoupdate --url http://localhost:%PORT%
pause & goto MENU

:TUNEL_PINGGY
cls
echo ============================================
echo   TUNEL PINGGY (URL aleatoria, max 60 min)
echo ============================================
:: Arrancar servidor si no esta activo
call :ARRANCAR_SERVIDOR_BG
if errorlevel 1 goto MENU
echo  Presiona Ctrl+C para detener el tunel.
echo.
ssh -p 443 -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -R0:localhost:%PORT% a.pinggy.io
pause & goto MENU

:TUNEL_NGROK
cls
echo ============================================
echo   TUNEL NGROK FIJO - URL PERMANENTE GRATIS
echo ============================================
echo.
if not defined NGROK (
    echo [ERROR] ngrok no instalado.
    echo Usa opcion [1] para descargarlo automaticamente.
    echo O descargalo manualmente en: https://ngrok.com/download
    pause & goto MENU
)
:: Cerrar sesion de ngrok anterior automaticamente (evita ERR_NGROK_334)
echo  [..] Cerrando sesion ngrok previa si existia...
taskkill /f /im ngrok.exe >nul 2>&1
timeout /t 2 /nobreak >nul
:: Arrancar servidor si no esta activo
call :ARRANCAR_SERVIDOR_BG
if errorlevel 1 goto MENU
echo  Tu URL fija permanente:
echo  https://%NGROK_DOMAIN%
echo.
echo  Presiona Ctrl+C para detener el tunel.
echo.
"%NGROK%" http --domain=%NGROK_DOMAIN% %PORT%
pause & goto MENU

:GUARDAR_TOKEN
cls
echo ============================================
echo   CONFIGURAR URL FIJA - CLOUDFLARE TUNNEL
echo ============================================
echo.
echo  Sigue estos pasos en el navegador:
echo.
echo  1. Ve a: https://one.dash.cloudflare.com
echo     (crea cuenta gratis si no tienes)
echo.
echo  2. En el menu izquierdo:
echo     Networks ^> Tunnels
echo.
echo  3. Clic en "Add a tunnel"
echo.
echo  4. Selecciona "Cloudflared" y clic Next
echo.
echo  5. Ponle un nombre (ej: mi-analizador) y clic Save
echo.
echo  6. Copia el TOKEN largo y pegalo abajo.
echo.
set /p CF_TOKEN="Token de Cloudflare: "
if "%CF_TOKEN%"=="" (echo [ERROR] No se introdujo token. & pause & goto MENU)
echo %CF_TOKEN%> "%TOKEN_FILE%"
echo.
echo [OK] Token guardado en %TOKEN_FILE%
echo.
pause
goto MENU

:TUNEL_FIJO
cls
echo ============================================
echo   INICIANDO TUNEL FIJO CLOUDFLARE
echo ============================================
echo.
if not defined CF (echo [ERROR] cloudflared no instalado. Usa opcion 1. & pause & goto MENU)
if not exist "%TOKEN_FILE%" (
    echo [ERROR] No hay token guardado.
    echo Ejecuta primero la opcion [5] para configurarlo.
    pause & goto MENU
)
set /p CF_TOKEN=<"%TOKEN_FILE%"
if "%CF_TOKEN%"=="" (echo [ERROR] El archivo de token esta vacio. & pause & goto MENU)
:: Cerrar tunel anterior automaticamente
echo  [..] Cerrando tunel Cloudflare previo si existia...
taskkill /f /im cloudflared.exe >nul 2>&1
timeout /t 1 /nobreak >nul
:: Arrancar servidor si no esta activo
call :ARRANCAR_SERVIDOR_BG
if errorlevel 1 goto MENU
echo  Tu URL permanente aparece en el dashboard de Cloudflare:
echo  https://one.dash.cloudflare.com ^> Networks ^> Tunnels
echo.
echo  Presiona Ctrl+C para detener.
echo.
"%CF%" tunnel --no-autoupdate run --token %CF_TOKEN%
pause & goto MENU

:CERRAR_WEB
cls
powershell -NoProfile -Command "$o=@(Get-NetTCPConnection -LocalPort %PORT% -State Listen -EA SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); if (-not $o) { Write-Host 'No habia servidor activo.' } else { foreach ($p in $o) { try { $pr=Get-Process -Id $p -EA Stop; Write-Host ('Cerrando PID '+$p+' ('+$pr.ProcessName+')'); Stop-Process -Id $p -Force } catch {} } }"
echo. & pause & goto MENU

:CERRAR_TUNEL
echo  [..] Cerrando tuneles activos...
taskkill /f /im cloudflared.exe >nul 2>&1
if %errorlevel% equ 0 (echo  [OK] Tunel Cloudflare cerrado) else (echo  [--] No habia tunel Cloudflare)
taskkill /f /im ngrok.exe >nul 2>&1
if %errorlevel% equ 0 (echo  [OK] Tunel ngrok cerrado) else (echo  [--] No habia tunel ngrok)
echo  Para Pinggy pulsa Ctrl+C en esa ventana.
echo. & pause & goto MENU

:ESCANEAR
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
echo  [4] Reanudar desde checkpoint
echo  [5] Con limite + resume
echo  [6] Solo un PDF especifico
echo  [R] Volver
echo.
echo  Modelo  : qwen2.5-coder:14b
echo  Workers : 2
echo  Fuente  : D:\VOID\knowledge_base
echo.
set /p est_op=Opcion: 
if /i "%est_op%"=="1" python training\study_engine.py --model qwen2.5-coder:14b --workers 2 --source D:\VOID\knowledge_base --qa-per-chunk 2
if /i "%est_op%"=="2" python training\study_engine.py --model qwen2.5-coder:14b --workers 2 --source D:\VOID\knowledge_base --dry-run
if /i "%est_op%"=="3" python training\study_engine.py --model qwen2.5-coder:14b --workers 2 --source D:\VOID\knowledge_base --limit 5
if /i "%est_op%"=="4" python training\study_engine.py --model qwen2.5-coder:14b --workers 2 --source D:\VOID\knowledge_base --qa-per-chunk 2 --resume
if /i "%est_op%"=="5" python training\study_engine.py --model qwen2.5-coder:14b --workers 2 --source D:\VOID\knowledge_base --limit 5 --resume
if /i "%est_op%"=="6" set /p pdf_path="Ruta del PDF: " && python training\study_engine.py --model qwen2.5-coder:14b --workers 2 --qa-per-chunk 2 --pdf "%pdf_path%"
if /i "%est_op%"=="R" goto MENU
if not "%est_op%"=="" pause
goto MENU

:CERRAR_TODO
echo  [..] Cerrando servidor y tuneles...
powershell -NoProfile -Command "$o=@(Get-NetTCPConnection -LocalPort %PORT% -State Listen -EA SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); if ($o) { foreach ($p in $o) { try { Stop-Process -Id $p -Force; Write-Host ('  [OK] Servidor cerrado PID '+$p) } catch {} } }"
taskkill /f /im cloudflared.exe >nul 2>&1
if %errorlevel% equ 0 (echo  [OK] Cloudflare cerrado) else (echo  [--] No habia Cloudflare)
taskkill /f /im ngrok.exe >nul 2>&1
if %errorlevel% equ 0 (echo  [OK] ngrok cerrado) else (echo  [--] No habia ngrok)
taskkill /f /im ssh.exe >nul 2>&1
if %errorlevel% equ 0 (echo  [OK] Pinggy SSH cerrado) else (echo  [--] No habia Pinggy)
echo. & pause & goto MENU