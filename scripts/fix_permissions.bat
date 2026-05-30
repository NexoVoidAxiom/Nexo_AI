@echo off
title DAR PERMISOS - Knowledge Base HDD
:: EJECUTAR COMO ADMINISTRADOR
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ============================================
    echo  ERROR: Ejecuta este script como Administrador
    echo ============================================
    echo  Haz clic derecho sobre este archivo
    echo  y selecciona "Ejecutar como administrador"
    echo ============================================
    pause
    exit /b 1
)

echo ============================================
echo  DANDO PERMISOS COMPLETOS A knowledge_base
echo ============================================
echo.
echo  Concediendo permisos a tu usuario sobre:
echo    D:\VOID\knowledge_base
echo.

icacls "D:\VOID\knowledge_base" /grant "%USERNAME%:(OI)(CI)F" /T

echo.
echo  LISTO! Ahora el scraper puede escribir en el HDD.
echo  Vuelve a ejecutar el scraper.
echo ============================================
pause