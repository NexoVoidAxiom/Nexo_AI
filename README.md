# Nexo AI — Sistema Multi-Agente de Void Axiom

Plataforma de IA local con chat streaming SSE, autenticación de usuarios, sistema multi-agente A2A (Agent-to-Agent), generador de código en 4 fases, y programa Alpha Pionero con planes Free / Tester / MAX.

**Hardware:** GTX 1080 Ti (11GB VRAM, Pascal) · i7-9700K · 32GB RAM · *(Compatible RTX 3090)*

**Creado por Aerys** — desarrollador de 13 años.

---

## ⚡ Inicio rápido

```bash
# 1. Asegurar que Ollama corre
ollama serve

# 2. Descargar modelos
ollama pull qwen2.5-coder:3b
ollama pull qwen2.5-coder:7b-instruct
ollama pull qwen2.5-coder:14b

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Iniciar servidor
python main.py
# → http://localhost:8080

# O desde un clic:
.\Iniciar Analizador IA.bat
```

---

## Estructura del proyecto

```
Prueba de IA Codigo/
│
├── main.py                    ← FastAPI app + 2645 líneas
├── requirements.txt           ← Dependencias
├── migrate_db.py              ← Migraciones de BD
├── apply_fixes.py             ← Parches automáticos
├── AUDIT_REPORT.md            ← Auditoría de seguridad
│
├── app/                       ← ★ CÓDIGO PRINCIPAL (importado por main.py)
│   ├── config.py              ←   Config: hardware, Ollama, perfiles, uploads
│   ├── database.py            ←   SQLite: usuarios, sesiones, chats, archivos, API keys
│   ├── auth.py                ←   AuthMiddleware + cookies httponly
│   ├── security.py            ←   Utilidades de seguridad
│   ├── llm_handler.py         ←   OllamaHandler: streaming, retry, perfiles
│   ├── data_processor.py      ←   Procesa PDFs, DOCX, XLSX, código, imágenes…
│   ├── data_base.py           ←   Helper BD
│   ├── prompts.py             ←   Builders de prompts: arquitecto, agentes A/B, revisor
│   ├── pioneers.py            ←   Alpha_Pionero + planes + donaciones + escalado
│   ├── agent_chat.py          ←   ★ Sistema A2A: ARCH-7, CODA, REBx3, Intruso
│   ├── void_agents.py         ←   Perfiles de los 4 agentes activos
│   ├── void_memory.py         ←   Memoria conversacional con presupuesto de tokens
│   ├── void_ollama.py         ←   Cliente Ollama para A2A
│   ├── void_activity.py       ←   Seguimiento de actividad en tiempo real
│   ├── api_keys_router.py     ←   API keys programáticas
│   ├── dispatcher.py          ←   (bak) Versión anterior
│   ├── intent_classifier.py   ←   (bak) Versión anterior
│   └── *.bak                  ←   Backups
│
├── agents/                    ← ★ Package void_axiom (NO integrado aún)
│   ├── __init__.py
│   ├── base.py                ←   AgentConfig dataclass
│   └── registry.py            ←   ARCH-7A, ARCH-7B, CODA, REBx3, Intruso
│
├── core/                      ← ★ Package void_axiom (NO integrado aún)
│   ├── __init__.py
│   ├── classifier.py          ←   IntentClassifier (<1ms)
│   ├── dispatcher.py          ←   ModelDispatcher VRAM-aware
│   ├── gpu_queue.py           ←   GPUQueue con prioridades + OOM retry
│   ├── handover.py            ←   NarrativeHandover
│   ├── ollama.py              ←   OllamaClient streaming
│   └── vram.py                ←   VRAMManager carga/descarga
│
├── api/                       ← ★ Package void_axiom (NO integrado aún)
│   ├── __init__.py
│   └── routes.py              ←   Endpoints: /agents, /dispatcher/status, /debug/intent
│
├── static/
│   ├── index.html             ← ★ Frontend principal (2900 líneas)
│   ├── auth.html              ←   Login/registro
│   ├── generador.html         ←   Generador de código 4 fases
│   ├── void_axiom.html        ←   Frontend A2A Void Axiom
│   ├── index_corregido.html
│   └── index_fixed.html
│
├── training/                  ← Entrenamiento y datasets
│   ├── dataset_builder.py     ←   Construcción de datasets
│   ├── train_qlora.py         ←   QLoRA fine-tuning
│   ├── merge.py               ←   Merge LoRA adapters
│   ├── pdf_scraper.py         ←   Scraper PDFs
│   ├── study_engine.py        ←   Motor de estudio autónomo
│   ├── continuous_scraper.py  ←   Scraping continuo
│   └── train_night_cron.sh    ←   Cron nocturno
│
├── ollama/
│   ├── Modelfile.arch7, .arch7a, .arch7b
│   ├── Modelfile.coda, .rebx3, .intruder
│
├── knowledge_base/            ← Base de conocimiento multi-fuente
│   ├── pdf_scraper.db, arxiv/, books/, github/
│   ├── internet_archive/, languages/, openstax/
│   └── tutorials/, university/
│
├── datasets/                  ← QA datasets generados
├── data/analizador.db         ← SQLite de producción
├── fixed/                     ← Archivos corregidos de versiones previas
│
├── scripts/
│   ├── init_void_axiom_ollama.bat / .sh
│   ├── move_knowledge_to_hdd.bat
│   ├── scan_check.py
│   └── update_batch.py
│
├── Iniciar Analizador IA.bat
├── Iniciar Busqueda Continua.bat
├── Iniciar Estudio IA.bat
├── Iniciar Ollama Modo RAM.bat
├── Guardar Cambios Git.bat
├── Inicializar Git.bat
├── Cerrar Tunel.bat
└── .gitignore
```

---

## Modelos disponibles

| Nombre UI | Modelo Ollama | Uso |
|-----------|--------------|-----|
| **Nexo Lite 1.0** | `qwen2.5-coder:3b` | Chat rápido, títulos auto |
| **Nexo Coder 1.0** | `qwen2.5-coder:7b-instruct` | ★ Modelo principal de chat |
| **Nexo Pro 1.0** | `qwen2.5-coder:14b` | Arquitecto y revisor código |
| **32B** (próximamente) | — | Deshabilitado, sin VRAM |

Los Agentes A/B del generador usan `qwen2.5-coder:7b`.

---

## Sistema de Chat

### Streaming SSE
- `POST /api/chats/{id}/stream` → Streaming con detección de desconexión (WinError 10054 solucionado)
- Auto-título con modelo 3B en background
- Contador de caracteres/tokens en tiempo real
- Botón "Parar" para cancelar generación
- Barra de progreso de tokens con warning/danger thresholds

### Búsqueda web integrada
Detección automática de consultas actuales → RSS + DuckDuckGo fallback:
- **Fuentes RSS**: BBC Mundo, 20minutos, RTVE, El País, CNN Español, Europapress
- **Caché**: 2 minutos en memoria
- **Keywords**: "noticias", "hoy", "precio", año actual, etc.

### Subida de archivos
+60 extensiones soportadas. Límite: 110K tokens (~5GB) con truncamiento automático.
- **Código**: .py, .js, .ts, .html, .css, .cpp, .java, .go, .rs, .rb, .php...
- **Documentos**: .pdf, .docx, .pptx, .odt, .rtf, .epub
- **Datos**: .csv, .tsv, .json, .jsonl, .xml, .yaml, .toml, .sql
- **Hojas**: .xlsx, .xls, .ods
- **Imágenes**: .png, .jpg, .webp, .bmp, .tiff (metadatos)
- **Comprimidos**: .zip, .tar, .gz, .rar, .7z
- **Notebooks**: .ipynb

### Contexto
- **Normal**: 55,000 tokens (por defecto)
- **Máximo**: 110,000 tokens (toggle "Ctx" en header)
- Barra de progreso visual con colores: verde (<70%) → naranja (70-90%) → rojo (>90%)

### Resumen y diagnóstico
- **Resumen**: mensajes, archivos, tokens totales, top archivos
- **Diagnóstico**: líneas por archivo, términos más frecuentes, preview
- **Exportación**: JSON completo del chat

---

## Sistema Multi-Agente A2A (Void Axiom)

Sistema activo con 4 agentes definidos en `app/void_agents.py` y orquestados por `app/agent_chat.py`:

| Agente | Modelo | Sigil | Color | Rol |
|--------|--------|-------|-------|-----|
| **ARCH-7** | qwen2.5-coder:7b | AR | `#00BFFF` | Arquitecto estructural |
| **CODA** | qwen2.5-coder:7b | CO | `#00FF41` | Codificador / implementador |
| **REBx3** | qwen2.5-coder:7b | RE | `#FF4500` | Rebelde reactivo (sarcástico) |
| **...** (Intruso) | qwen2.5-coder:7b | ⬡ | `#8B00FF` | Glitch probabilístico |

Todos los agentes usan `qwen2.5-coder:7b` como modelo base.

**Características:**
- Orquestación round-robin: ARCH-7 → CODA → REBx3
- Intruso: 12% en intervenciones de AERYS, 4% en rondas autónomas
- Memoria: 16K estándar / 55K extendido (se activa automáticamente según actividad)
- Detección y supresión de respuestas corruptas o repetidas (hasta 3 reintentos)
- SSE en tiempo real vía `/api/void/stream`
- Frontend en `/void` (static/void_axiom.html)
- Sesiones pausables/reanudables con persistencia SQLite

**Refactor pendiente** (`agents/` + `core/` + `api/`): package `void_axiom` con ARCH-7A/7B dual, GPUQueue, VRAMManager, NarrativeHandover. Para activar: conectar en main.py.

---

## Frontend (index.html)

Interfaz single-page con layout de 3 columnas:

- **Sidebar** (izquierda): historial de conversaciones con búsqueda, nuevo/eliminar chat
- **Panel central**: zona de subida de archivos (drag & drop), lista de archivos con tokens, pegado de texto con contador, chips de acción rápida (Resumen, Patrones, Estadísticas, Recomend.)
- **Chat**: mensajes con markdown, typing indicator, thinking spinner, barra de tokens, input con auto-resize, botón enviar/parar

**Header**: selector de modelo (3B/7B/14B/32B-soon), estado de Ollama (dot verde/rojo), botón Generador, botón Admin (solo para Aerys), badge de plan (Free/MAX), link Void Axiom.

**Modales**:
- **Admin** (solo `elgatosuperpitzzero@gmail.com`): stats, usuarios, todos los chats, modelos instalados, códigos de redención
- **Plan**: Mi Plan (uso diario), Alpha Pioneros (slots, leaderboard, planes Free/Tester/MAX), Donaciones (Ko-fi, Bizum, PayPal, intención)

**Responsive**: menú hamburguesa, paneles deslizables en móvil.

---

## Generador de Código Multi-Fase

`/generador` → Frontend en `static/generador.html`

### Fases
1. **🏗️ Arquitecto (14b)**: Diseña estructura + contrato interfaces
   - Pequeño: 7-11 archivos | Mediano: 12-18 | Grande: 20-28
2. **🤝 Dual Subagente (7b × 7b)**: Agente A implementa → Agente B critica y mejora
3. **🔍 Revisor (14b)**: Verifica imports, stubs, self.atributos, rutas
4. **📦 Exportación**: Proyecto completo a disco

### Características
- Opciones avanzadas: tests, Docker, CI/CD, nivel comentarios, estilo
- Verificación automática (AST Python, validación imports)
- Regeneración de archivos individuales
- Historial persistente (últimas 50)

---

## Planes

| Plan | Precio | Mensajes/día | Archivos | Modelos |
|------|--------|-------------|----------|---------|
| **FREE** | €0 | 20 | 1MB | ✅ 3B·7B·14B |
| **TESTER** 🧪 | €5/mes | 100 | 10MB | ✅ 3B·7B·14B |
| **MAX** ⭐ | €10/mes | ∞ Ilimitado | ∞ Ilimitado | ✅ 3B·7B·14B |

### Programa Alpha Pionero
Los primeros **50 usuarios** obtienen **plan MAX gratis** con badge permanente. El resto obtienen plan FREE.

- Los códigos de redención pueden activar plan MAX o plan TESTER
- Donaciones vía Ko-fi, Bizum, PayPal
- Leaderboard público de pioneros

### Límites por plan (backend)
| Plan | context_tokens | max_file_size | GPU priority | API access | Agents |
|------|---------------|---------------|-------------|-----------|--------|
| **plan_max** | 30,000 | 100MB | high | ✅ | ✅ |
| **free_limited** | 20,000 | 1MB | normal | ❌ | ❌ |

---

## API Completa

### Autenticación
| Método | Ruta |
|--------|------|
| POST | `/api/auth/register` |
| POST | `/api/auth/login` |
| POST | `/api/auth/logout` |
| GET | `/api/auth/me` |

### Chats
| Método | Ruta |
|--------|------|
| GET | `/api/chats` |
| POST | `/api/chats/new` |
| GET | `/api/chats/{id}` |
| DELETE | `/api/chats/{id}` |
| PATCH | `/api/chats/{id}/title` |
| POST | `/api/chats/{id}/stream` |
| GET | `/api/chats/{id}/summary` |
| GET | `/api/chats/{id}/export` |
| GET | `/api/chats/{id}/status` |
| GET | `/api/chats/{id}/files/insights` |

### Archivos
| Método | Ruta |
|--------|------|
| POST | `/api/upload` |
| POST | `/api/chats/{id}/upload` |
| POST | `/api/chats/{id}/upload/multiple` |
| POST | `/api/chats/{id}/upload/text` |
| POST | `/api/chats/{id}/files/remove` |
| POST | `/api/chats/{id}/files/clear` |

### Modelos
| Método | Ruta |
|--------|------|
| GET | `/api/models` |
| POST | `/api/models/switch` |

### Planes
| Método | Ruta |
|--------|------|
| GET | `/api/pioneers/status` |
| GET | `/api/pioneers/leaderboard` |
| GET | `/api/user/plan` |
| POST | `/api/redeem-code` |
| GET | `/api/donations/tiers` |

### Generador
| Método | Ruta |
|--------|------|
| GET | `/generador` |
| POST | `/api/generador/generar` |
| POST | `/api/generador/regenerar-file` |
| POST | `/api/generador/verificar` |
| POST | `/api/generador/exportar` |
| GET | `/api/generador/history` |
| GET | `/api/generador/history/{id}` |
| DELETE | `/api/generador/history/{id}` |

### Void Axiom (A2A)
| Método | Ruta |
|--------|------|
| GET | `/void` |
| GET | `/api/void/stream` |
| POST | `/api/void/start` |
| POST | `/api/void/message` |
| POST | `/api/void/pause` |
| POST | `/api/void/resume` |
| POST | `/api/void/stop` |
| GET | `/api/void/status` |
| GET | `/api/void/history` |
| GET | `/api/void/history/{id}` |
| POST | `/api/void/config` |

### Administración (admin)
| Método | Ruta |
|--------|------|
| GET | `/api/admin/stats` |
| GET | `/api/admin/users` |
| DELETE | `/api/admin/users/{id}` |
| POST | `/api/admin/users/{id}/logout` |
| GET | `/api/admin/chats` |
| DELETE | `/api/admin/chats/{id}` |
| GET | `/api/admin/users/{id}/messages` |
| POST | `/api/admin/codes/create` |
| GET | `/api/admin/codes` |
| DELETE | `/api/admin/codes/{code}` |
| GET | `/api/admin/scale-readiness` |

### Sistema
| Método | Ruta |
|--------|------|
| GET | `/ping` |
| GET | `/health` |
| POST | `/api/gc` |
| GET | `/` |
| GET | `/auth` |

---

## Configuración

### Variables de entorno

| Variable | Default |
|----------|---------|
| `OLLAMA_HOST` | `http://localhost:11434` |
| `OLLAMA_MODEL` | `qwen2.5-coder:7b` |
| `PORT` | `8080` |
| `AUTH_TOKEN` | `mi-analisis-ia-2024` |
| `VOID_INTRUDER_PROBABILITY` | `0.12` |
| `VOID_AGENT_NUM_CTX` | `16384` |
| `VOID_AGENT_TEMPERATURE` | `0.25` |

### Hardware (GTX 1080 Ti 11GB)
- Flash Attention activado
- 7 threads CPU (i7-9700K 8C/8T, sin HT)
- Keep alive 10 min
- Pool HTTP 32 conexiones

### Perfiles de rendimiento
| Perfil | Contexto | Batch | Temperatura |
|--------|----------|-------|-------------|
| **fast** | 4K | 1024 | 0.3 |
| **turbo** | 8K | 512 | 0.2 |
| **ultra** | 32K | 256 | 0.1 |

---

## Notas técnicas

- **WinError 10054**: detección de desconexión en cada iteración del stream con `request.is_disconnected()`
- **Anti-bleeding**: guard de sistema inyectado antes del system prompt de cada agente A2A
- **Suppression**: detección de respuestas corruptas (markers) y eco del historial
- **Admin**: identificado por email `elgatosuperpitzzero@gmail.com`
- **Códigos de redención**: tipos cafe/pizza/mes/trimestre, siempre generan plan_max
- **Migración void_axiom** pendiente: el refactor agents/ + core/ + api/ está listo pero no conectado

---

## Scripts

| Script | Descripción |
|--------|-------------|
| `Iniciar Analizador IA.bat` | Inicia servidor completo |
| `Iniciar Busqueda Continua.bat` | Scraping continuo |
| `Iniciar Estudio IA.bat` | Motor de estudio |
| `Iniciar Ollama Modo RAM.bat` | Ollama optimizado RAM |
| `Guardar Cambios Git.bat` | Commit rápido Git |
| `Cerrar Tunel.bat` | Cierra exposición |
| `scripts/init_void_axiom_ollama.bat` | Crea modelos Void Axiom |

---

Creado por **Aerys** — Uso personal/educativo.