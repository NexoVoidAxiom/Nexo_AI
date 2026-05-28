@echo off
title VOID AXIOM - BUSCADOR CONTINUO DE PDFS
cd /d "C:\Users\34645\Desktop\Prueba de IA Codigo"
color 0A
echo ============================================
echo   VOID AXIOM - BUSCADOR CONTINUO DE PDFS
echo   Buscando PDFs sin parar en:
echo   - arXiv (nuevos papers CS cada dia)
echo   - Bing (busqueda web de tutoriales)
echo   - GitHub (repositorios con PDFs)
echo ============================================
echo.
echo  Presiona Ctrl+C para detener
echo.
python training/continuous_scraper.py --interval 3600
echo.
pause