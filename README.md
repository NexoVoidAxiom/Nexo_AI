# Nexo AI — Sistema Multi-Agente de Void Axiom

Plataforma de IA local con **Smart Router inteligente** (modelo ligero 3B para charla casual, modelo pesado para trabajo), chat streaming SSE, autenticación de usuarios, sistema multi-agente A2A (Agent-to-Agent), generador de código en 4 fases, y programa Alpha Pionero con planes Free / Tester / MAX.

**Hardware:** RTX 3090 (24GB VRAM, Ampere) · i7-9700K · 32GB RAM *(Compatibilidad con GTX 1080 Ti 11GB)*

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

## 🧠 Smart Router — Router Inteligente

Nexo AI incluye un **Smart Router** que analiza cada mensaje del usuario y decide automáticamente qué modelo usar:

| Intención detectada | Modelo usado | Descripción |
|---|---|---|
| **CHAT** (saludos, charla casual, preguntas simples) | `qwen2.5-coder:3b` (ligero) | Respuesta rápida, casi sin VRAM |
| **WORK** (código, análisis, razonamiento, archivos) | Modelo Nexo del usuario | Potencia completa |

El router clasifica usando palabras clave + patrones estructurales:
- Si NO hay señales de trabajo → **CHAT** (modelo 3B)
- Si HAY señales de trabajo o hay duda → **WORK** (modelo pesado del usuario)

Esto ahorra VRAM y acelera las respuestas cuando solo estás conversando.

---

## 🎯 Modelos Nexo (selección por usuario)

Cada usuario puede elegir su modelo pesado preferido, que se guarda en su perfil:

| ID | Display | Modelo Ollama | VRAM aprox | Ideal para |
|---|---|---|---|---|
| `nexo_lite` | **Nexo Lite 1.0** | `qwen2.5-coder:3b` | ~2 GB | Tareas simples, chat rápido |
| `nexo_coder` | **Nexo Coder 1.0** | `qwen2.5-coder:7b-instruct` | ~4.5 GB | ★ Balance velocidad/calidad |
| `nexo_pro` | **Nexo Pro 1.0** | `qwen2.5-coder:14b` | ~8.5 GB | Razonamiento profundo, código complejo |

**API endpoints para selección de modelo:**

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/nexo-models` | Lista modelos disponibles + selección actual |
| GET | `/api/nexo-models/current` | Modelo Nexo actual del usuario |
| POST | `/api/nexo-models/switch` | Cambia el modelo Nexo del usuario |

**Ejemplo:**
```javascript
// Cambiar a Nexo Pro 1.0
fetch('/api/nexo-models/switch', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({model_id: 'nexo_pro'})
});
```

---

## 📊 Planes y Límites

| Plan | Precio | Mensajes/día | Contexto (tokens) | Archivos | Modelos |
|---|---|---|---|---|---|
| **FREE** | €0 | 20 | **25,000** | 1MB | ✅ 3B·7B·14B |
| **TESTER** 🧪 | €5/mes | ∞ Ilimitado | **35,000** | 50MB | ✅ 3B·7B·14B |
| **MAX** ⭐ | €10/mes | ∞ Ilimitado | **35,000** | 100MB | ✅ 3B·7B·14B |

El `context_tokens` controla directamente el `num_ctx` de Ollama (ventana de contexto del modelo). Si los archivos del usuario superan este límite, se truncan automáticamente.

### Programa Alpha Pionero
Los primeros **50 usuarios** obtienen **plan MAX gratis** con badge permanente. Los usuarios 51+ obtienen plan FREE.

- Códigos de redención pueden activar plan MAX o TESTER
- Donaciones vía Ko-fi, Bizum, PayPal
- Leaderboard público de pioneros

---

## Estructura del proyecto

```
Prueba de IA Codigo/
│
├── main.py                    ← FastAPI app
├── requirements.txt           ← Dependencias
├── migrate_db.py              ← Migraciones de BD
├── apply_fixes.py             ← Parches automáticos
├── AUDIT_REPORT.md            ← Auditoría de seguridad
│
├── app/                       ← ★ CÓDIGO PRINCIPAL
│   ├── config.py              ←   Config: hardware, Ollama, perfiles, uploads
│   ├── database.py            ←   SQLite: usuarios, sesiones, chats, archivos, API keys, selected_model
│   ├── auth.py                ←   AuthMiddleware + cookies httponly
│   ├── llm_handler.py         ←   OllamaHandler: streaming, retry, perfiles
│   ├── smart_router.py        ←   ★ Router inteligente (CHAT ligero / WORK pesado)
│   ├── data_processor.py      ←   Procesa PDFs, DOCX, XLSX, código, imágenes...
│   ├── prompts.py             ←   Builders de prompts: arquitecto, agentes A/B, revisor
│   ├── pioneers.py            ←   Alpha_Pionero + planes + donaciones + escalado
│   ├── agent_chat.py          ←   ★ Sistema A2A: ARCH-7, CODA, REBx3, Intruso
│   ├── void_agents.py         ←   Perfiles de los 4 agentes activos
│   ├── void_memory.py         ←   Memoria conversacional con presupuesto de tokens
│   ├── void_ollama.py         ←   Cliente Ollama para A2A
│   ├── void_activity.py       ←   Seguimiento de actividad en tiempo real
│   ├── api_keys_router.py     ←   API keys programáticas
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
├── index.html                 ← Frontend principal (raíz del proyecto)
│
├── static/
│   ├── index.html             ←   Frontend con Smart Router + planes dinámicos
│   ├── auth.html              ←   Login/registro
│   ├── generador.html         ←   Generador de código 4 fases
│   ├── void_axiom.html        ←   Frontend A2A Void Axiom
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

## Sistema de Chat

### Streaming SSE
- `POST /api/chats/{id}/stream` → Streaming con detección de desconexión (WinError 10054 solucionado)
- **Smart Router integrado**: clasifica cada mensaje en CHAT (modelo 3B) o WORK (modelo pesado)
- Auto-título con modelo 3B en background
- Contador de tokens en tiempo real
- Botón "Parar" para cancelar generación
- Barra de progreso de tokens con el límite del plan (25k/35k)

### Búsqueda web integrada
Detección automática de consultas actuales → RSS + DuckDuckGo fallback:
- **Fuentes RSS**: BBC Mundo, 20minutos, RTVE, El País, CNN Español, Europapress
- **Caché**: 2 minutos en memoria
- **Keywords**: "noticias", "hoy", "precio", año actual, etc.

### Subida de archivos
+60 extensiones soportadas. Límite: según el plan del usuario (25k o 35k tokens de contexto).
- Código, documentos, datos, hojas, imágenes, comprimidos, notebooks.

### Contexto
- **Plan FREE**: 25,000 tokens de contexto
- **Plan MAX / TESTER**: 35,000 tokens de contexto
- Barra de progreso visual con colores: verde (<70%) → naranja (70-90%) → rojo (>90%)

---

## Sistema Multi-Agente A2A (Void Axiom)

Sistema activo con 4 agentes definidos en `app/void_agents.py` y orquestados por `app/agent_chat.py`:

| Agente | Modelo | Sigil | Color | Rol |
|--------|--------|-------|-------|-----|
| **ARCH-7** | qwen2.5-coder:7b | AR | `#00BFFF` | Arquitecto estructural |
| **CODA** | qwen2.5-coder:7b | CO | `#00FF41` | Codificador / implementador |
| **REBx3** | qwen2.5-coder:7b | RE | `#FF4500` | Rebelde reactivo (sarcástico) |
| **...** (Intruso) | qwen2.5-coder:7b | ⬡ | `#8B00FF` | Glitch probabilístico |

**Características:**
- Orquestación round-robin: ARCH-7 → CODA → REBx3
- Intruso: 12% en intervenciones de AERYS, 4% en rondas autónomas
- Memoria: 16K estándar / 55K extendido (se activa automáticamente según actividad)
- Detección y supresión de respuestas corruptas o repetidas
- SSE en tiempo real vía `/api/void/stream`
- Frontend en `/void` (static/void_axiom.html)

---

## Frontend (index.html)

Interfaz single-page con layout de 3 columnas:

- **Sidebar**: historial de conversaciones
- **Panel central**: subida de archivos (drag & drop), lista con tokens, pegado de texto
- **Chat**: mensajes markdown, typing indicator, barra de tokens con límite del plan

**Header**: selector de modelo Nexo (Lite/Coder/Pro), estado Ollama, badge de plan (Free/MAX/Tester), link Void Axiom.

**Modales**:
- **Admin**: stats, usuarios, chats, modelos, códigos de redención
- **Plan**: Mi Plan, Alpha Pioneros, Donaciones

---

## Generador de Código Multi-Fase

`/generador` → Frontend en `static/generador.html`

**Fases:**
1. **🏗️ Arquitecto (14b)**: Diseña estructura + contrato interfaces
2. **🤝 Dual Subagente (7b × 7b)**: Agente A implementa → Agente B mejora
3. **🔍 Revisor (14b)**: Verifica imports, stubs, rutas
4. **📦 Exportación**: Proyecto completo a disco

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

### Modelos Ollama
| Método | Ruta |
|--------|------|
| GET | `/api/models` |
| POST | `/api/models/switch` |

### Modelos Nexo (Smart Router)
| Método | Ruta |
|--------|------|
| GET | `/api/nexo-models` |
| GET | `/api/nexo-models/current` |
| POST | `/api/nexo-models/switch` |

### Planes
| Método | Ruta |
|--------|------|
| GET | `/api/pioneers/status` |
| GET | `/api/pioneers/leaderboard` |
| GET | `/api/user/plan` |
| GET | `/api/user/can-message` |
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
| POST | `/api/void/pause` |
| POST | `/api/void/resume` |
| POST | `/api/void/stop` |
| POST | `/api/void/intervene` |
| POST | `/api/void/private` |
| GET | `/api/void/status` |
| GET | `/api/void/sessions` |
| GET | `/api/void/history` |
| GET | `/api/void/export` |

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

| Variable | Default | Descripción |
|----------|---------|-------------|
| `OLLAMA_HOST` | `http://localhost:11434` | URL de Ollama |
| `OLLAMA_MODEL` | `qwen2.5-coder:7b` | Modelo por defecto |
| `PORT` | `8080` | Puerto del servidor |
| `AUTH_TOKEN` | (generado automáticamente) | Token de autenticación |
| `CORS_ORIGINS` | `http://localhost:8080` | Orígenes CORS permitidos |
| `VOID_INTRUDER_PROBABILITY` | `0.12` | Probabilidad de Intruso (0-1) |
| `VOID_AGENT_NUM_CTX` | `16384` | Contexto de agentes Void |
| `VOID_AGENT_TEMPERATURE` | `0.25` | Temperatura de agentes Void |

### Hardware (RTX 3090 24GB)
- Flash Attention activado
- 7 threads CPU (i7-9700K 8C/8T, sin HT)
- Keep alive 10 min
- Pool HTTP 32 conexiones
- TF32 activado para Ampere

---

## Notas técnicas

- **Smart Router**: clasificación léxico+estructural en <1ms, sin GPU, decide dinámicamente modelo ligero (3B) vs pesado
- **Modelo por usuario**: cada usuario guarda su preferencia de modelo Nexo en la BD (`selected_model`)
- **Límites por plan**: `context_tokens` controla el `num_ctx` de Ollama, truncando archivos si es necesario
- **WinError 10054**: detección de desconexión en cada iteración del stream con `request.is_disconnected()`
- **Anti-bleeding**: guard de sistema inyectado antes del system prompt de cada agente A2A
- **Admin**: solo el creador del proyecto
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