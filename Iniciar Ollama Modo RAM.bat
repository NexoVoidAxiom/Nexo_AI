@echo off
title Ollama - Pesos en RAM / Tokens en VRAM
color 0D
echo ============================================
echo   Ollama - Modo OPTIMIZADO para GTX 1080 Ti
echo ============================================
echo.
echo  Los pesos del modelo se cargaran en RAM
echo  La VRAM quedara libre para los tokens
echo.
echo  Modelo recomendado: qwen2.5-coder:32b
echo.
echo  Cerrando Ollama si estaba abierto...
taskkill /F /IM ollama.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo  Iniciando Ollama con pesos en RAM...
set OLLAMA_NUM_GPU_LAYERS=0
set OLLAMA_GPU_LAYERS=0
start /B ollama serve

timeout /t 3 /nobreak >nul
echo.
echo  ✅ Ollama activo. Pesos en RAM, VRAM libre.
echo.
echo  Con 32B: ~19GB en RAM, ~2-4GB VRAM para tokens
echo  Velocidad esperada: ~5-8 tokens/s
echo.
echo  Presiona cualquier tecla para cerrar esta ventana
echo  (Ollama seguira corriendo en segundo plano)
pause >nul