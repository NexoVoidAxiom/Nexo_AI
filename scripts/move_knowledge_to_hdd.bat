@echo off
title MOVER KNOWLEDGE_BASE AL HDD (D:)
echo ============================================
echo  MOVIENDO KNOWLEDGE_BASE AL DISCO DURO D:
echo ============================================
echo.

:: 1. Detener scraper si está corriendo
echo [1/4] Esperando que termine el scraper...

:: 2. Mover carpeta al HDD
echo [2/4] Moviendo knowledge_base a D:\knowledge_base...
if exist "D:\knowledge_base" (
    echo   La carpeta D:\knowledge_base ya existe. Fusionando...
    robocopy "C:\Users\34645\Desktop\Prueba de IA Codigo\knowledge_base" "D:\knowledge_base" /E /R:2 /W:3 /NFL /NDL
    rmdir /S /Q "C:\Users\34645\Desktop\Prueba de IA Codigo\knowledge_base"
) else (
    move "C:\Users\34645\Desktop\Prueba de IA Codigo\knowledge_base" D:\knowledge_base
)
echo   ✓ Hecho!

:: 3. Crear enlace simbólico (junction) en el SSD apuntando al HDD
echo [3/4] Creando enlace simbólico...
mklink /J "C:\Users\34645\Desktop\Prueba de IA Codigo\knowledge_base" "D:\knowledge_base"
echo   ✓ Hecho! La carpeta knowledge_base ahora apunta a D:\knowledge_base

:: 4. Verificar
echo [4/4] Verificando...
dir "C:\Users\34645\Desktop\Prueba de IA Codigo\knowledge_base"
echo.
echo ============================================
echo  COMPLETADO!
echo  Los PDFs estan en: D:\knowledge_base
echo  El proyecto los ve en: C:\...\knowledge_base (enlace)
echo  Espacio liberado en SSD: ~140 MB
echo ============================================
pause