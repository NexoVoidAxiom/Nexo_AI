@echo off
chcp 65001 >nul
title VOID AXIOM - MOTOR DE ESTUDIO DE PDFS
cd /d "C:\Users\34645\Desktop\Prueba de IA Codigo"
color 0B
echo ============================================
echo   VOID AXIOM - MOTOR DE ESTUDIO
echo   Leyendo CADA PDF y generando QandA
echo   para que la IA sea mas inteligente
echo ============================================
echo.
echo  Modelo: qwen2.5-coder:14b
echo  Workers: 2 (cabe bien en 11GB VRAM)
echo  Source: D:\VOID\knowledge_base
echo.
echo  Presiona Ctrl+C para detener
echo.

REM Abrir ventana CMD extra para logs de error en vivo
start "VOID AXIOM - LOGS" cmd /k "chcp 65001 >nul && cd /d "C:\Users\34645\Desktop\Prueba de IA Codigo" && echo Esperando logs... && powershell -Command "Get-Content -Path 'study_engine.log' -Tail 10 -Wait""

REM Ejecutar estudio con modelo 14B (estable, cabe en VRAM)
python training/study_engine.py --model qwen2.5-coder:14b --source D:\VOID\knowledge_base --out ./datasets/ --workers 2

echo.
echo  Procesamiento completado!
echo  Dataset generado en: ./datasets/
pause