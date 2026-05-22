@echo off
chcp 65001 >nul
title CERRAR TUNEL

echo ============================================================
echo    CERRAR TUNEL (Cloudflare + Pinggy)
echo ============================================================
echo.
taskkill /f /im cloudflared.exe >nul 2>&1
if %errorlevel% equ 0 (
    echo  [OK] Tunel Cloudflare cerrado.
) else (
    echo  [--] No habia tunel Cloudflare activo.
)
taskkill /f /im ssh.exe >nul 2>&1
if %errorlevel% equ 0 (
    echo  [OK] Tunel Pinggy (SSH) cerrado.
) else (
    echo  [--] No habia tunel Pinggy activo.
)
echo.
pause
