@echo off
title UNIFICAR KNOWLEDGE_BASE EN HDD
:: EJECUTAR COMO ADMINISTRADOR
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ============================================
    echo  ERROR: Ejecuta este script como Administrador
    echo ============================================
    pause
    exit /b 1
)

echo ============================================
echo  UNIFICANDO KNOWLEDGE_BASE EN EL HDD
echo ============================================
echo.
echo  Esto va a UNIR los PDFs de estas carpetas:
echo    D:\knowledge_base (284 PDFs existentes)
echo    D:\VOID\knowledge_base (nuevos PDFs)
echo  En una SOLA carpeta: D:\knowledge_base_unificada
echo.
echo  Luego creara el enlace para que el proyecto los vea.
echo.

set DESTINO=D:\knowledge_base_unificada

echo [1/4] Unificando todas las carpetas...
if exist "%DESTINO%" (
    echo   La carpeta destino ya existe, fusionando...
) else (
    mkdir "%DESTINO%"
)

if exist "D:\knowledge_base" (
    echo   Copiando desde D:\knowledge_base...
    robocopy "D:\knowledge_base" "%DESTINO%" /E /R:2 /W:3 /NFL /NDL /NP
)
if exist "D:\VOID\knowledge_base" (
    echo   Copiando desde D:\VOID\knowledge_base...
    robocopy "D:\VOID\knowledge_base" "%DESTINO%" /E /R:2 /W:3 /NFL /NDL /NP /XO
)
if exist "C:\Users\34645\Desktop\Prueba de IA Codigo\knowledge_base" (
    echo   Copiando desde knowledge_base local...
    robocopy "C:\Users\34645\Desktop\Prueba de IA Codigo\knowledge_base" "%DESTINO%" /E /R:2 /W:3 /NFL /NDL /NP /XO
)
echo   ✓ Union completada!

echo [2/4] Dando permisos a la carpeta unificada...
icacls "%DESTINO%" /grant "%USERNAME%:(OI)(CI)F" /T /Q
echo   ✓ Permisos concedidos!

echo [3/4] Reemplazando enlace simbolico del proyecto...
rmdir "C:\Users\34645\Desktop\Prueba de IA Codigo\knowledge_base" 2>nul
mklink /J "C:\Users\34645\Desktop\Prueba de IA Codigo\knowledge_base" "%DESTINO%"
echo   ✓ Enlace creado!

echo [4/4] Verificando...
echo.
echo   Archivos en la carpeta unificada:
dir "%DESTINO%" /S /A-D 2>nul | find /C "."
echo.
echo   Los 284 PDFs + los nuevos estan en: %DESTINO%
echo   El proyecto los ve en: knowledge_base (enlace)
echo.

echo ============================================
echo  COMPLETADO!
echo  Ahora puedes ejecutar el scraper SIN usar
echo  --out, y los PDFs se guardaran en el HDD.
echo ============================================
pause