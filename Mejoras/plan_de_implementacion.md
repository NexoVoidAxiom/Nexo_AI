# Plan de Implementación - Optimizaciones Nexo AI

Basado en el análisis de `nexo_ai_optimizaciones.svg` y el código actual:

## Prioridad Alta (Implementar ahora)
1. **main.py → routers separados** (2.7k líneas → modular)
2. **Eliminar gc.collect()** tras cada stream (innecesario)
3. **Mejorar smart router** con embeddings básicos
4. **Migrar /api/generate → /api/chat** en llm_handler.py

## Prioridad Media (Implementar después)  
5. **Caché persistente de búsqueda web** (SQLite en vez de RAM)
6. **Eliminar dispatcher duplicado** (core/dispatcher.py vs app/dispatcher.py)

## Mejoras Futuras (Documentar)
7. Training con evaluación automática
8. Frontend con bundler
9. RAG con vector DB