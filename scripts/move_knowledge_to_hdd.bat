@echo off
title MOVER KNOWLEDGE_BASE AL HDD (D:)
:: EJECUTAR COMO ADMINISTRADOR
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: Ejecuta este script como Administrador.
    pause
    exit /b 1
)

echo ============================================
echo  MOVIENDO KNOWLEDGE_BASE AL DISCO DURO D:
echo ============================================
echo.

echo [1/4] Esperando que termine el scraper...
echo.

echo [2/4] Moviendo knowledge_base a D:\knowledge_base...
if exist "D:\knowledge_base" (
    echo   Fusionando con D:\knowledge_base existente...
    robocopy "C:\Users\34645\Desktop\Prueba de IA Codigo\knowledge_base" "D:\knowledge_base" /E /R:2 /W:3 /NFL /NDL
) else (
    robocopy "C:\Users\34645\Desktop\Prueba de IA Codigo\knowledge_base" "D:\knowledge_base" /E /R:2 /W:3 /NFL /NDL
)
echo   ✓ Copia completada!

echo [2b/4] Eliminando carpeta original del SSD...
rmdir /S /Q "C:\Users\34645\Desktop\Prueba de IA Codigo\knowledge_base"
if %errorLevel% neq 0 (
    echo   ERROR: No se pudo eliminar la carpeta. Intenta cerrar el explorador de archivos y reintentar.
    pause
    exit /b 1
)
echo   ✓ Carpeta original eliminada!

echo [3/4] Creando enlace simbolico...
if exist "C:\Users\34645\Desktop\Prueba de IA Codigo\knowledge_base" (
    echo   Eliminando enlace anterior...
    rmdir "C:\Users\34645\Desktop\Prueba de IA Codigo\knowledge_base"
)
mklink /J "C:\Users\34645\Desktop\Prueba de IA Codigo\knowledge_base" "D:\knowledge_base"
echo   ✓ Enlace creado!

echo [4/4] Verificando...
dir "C:\Users\34645\Desktop\Prueba de IA Codigo\knowledge_base"

echo.
echo ============================================
echo  COMPLETADO!
echo  Los PDFs estan en: D:\knowledge_base
echo  El proyecto los ve en: C:\...\knowledge_base (enlace)
echo ============================================
pause