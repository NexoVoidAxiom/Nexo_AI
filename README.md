# Nexo AI — Analizador de Datos con IA Local v2.0

Plataforma de análisis de datos con IA local, chat multi-agente con streaming SSE, autenticación de usuarios, y sistema de generación de proyectos multi-fase.

**Hardware objetivo:** RTX 3090 (24GB VRAM, Ampere) · i7-9700K · 32GB RAM
*Compatible con GTX 1080 Ti (11GB VRAM, Pascal)*

---

## Estructura del proyecto

```
Prueba de IA Codigo/
├── main.py                 ← FastAPI app principal + endpoints
├── requirements.txt        ← Dependencias Python
├── README.md               ← Este archivo
├── migrate_db.py           ← Migraciones de base de datos
├── apply_fixes.py          ← Parches automáticos
├── AUDIT_REPORT.md         ← Auditoría de seguridad y código
│
├── app/
│   ├── __init__.py
│   ├── main.py             ← (bak) Backup del main original
│   ├── config.py           ← Config de hardware, Ollama, uploads, perfiles
│   ├── database.py         ← SQLite: usuarios, sesiones, chats, archivos, API keys
│   ├── auth.py             ← AuthMiddleware JWT + cookies
│   ├── security.py         ← Utilidades de seguridad
│   ├── llm_handler.py      ← OllamaHandler: streaming, perfiles, retry
│   ├── data_processor.py   ← Procesamiento de archivos (PDF, DOCX, XLSX, código…)
│   ├── data_base.py        ← Helper de base de datos
│   ├── prompts.py          ← Builders de prompts: arquitecto, agentes, revisor
│   ├── pioneers.py         ← Alpha_Pionero: planes, donaciones, escalado
│   ├── agent_chat.py       ← Chat multi-agente (void agents A2A)
│   ├── api_keys_router.py  ← Gestión de API keys programáticas
│   ├── void_activity.py    ← Seguimiento de actividad en tiempo real
│   ├── void_ollama.py      ← (bak) Versión anterior de Ollama handler
│   ├── void_memory.py      ← (bak) Sistema de memoria anterior
│   ├── void_agents.py      ← (bak) Sistema de agentes anterior
│   ├── dispatcher.py       ← (bak) Despachador anterior
│   ├── intent_classifier.py← (bak) Clasificador anterior
│   └── *.bak               ← Archivos de backup de versiones anteriores
│
├── static/
│   ├── index.html          ← Frontend principal del chat
│   ├── auth.html           ← Página de login/registro
│   ├── generador.html      ← Frontend del generador de código multi-fase
│   ├── void_axiom.html     ← Frontend Void Axiom (SSE consumer)
│   ├── index_corregido.html← Versión corregida del frontend
│   └── index_fixed.html    ← Versión fixed del frontend
│
├── agents/
│   ├── __init__.py
│   ├── base.py             ← AgentConfig: contrato de cada agente
│   └── registry.py         ← Registro de agentes + INTRUDER_ID
│
├── core/
│   ├── __init__.py
│   ├── classifier.py       ← IntentClassifier (< 1ms, sin GPU)
│   ├── dispatcher.py       ← ModelDispatcher: orquestador central
│   ├── gpu_queue.py        ← GPUQueue: cola prioritaria OOM-safe
│   ├── handover.py         ← NarrativeHandover: pase de batuta narrativo
│   ├── ollama.py           ← OllamaClient: streaming defensivo con retry
│   └── vram.py             ← VRAMManager: carga/descarga de modelos
│
├── api/
│   ├── __init__.py
│   └── routes.py           ← Routers adicionales (agentes, debug, queue)
│
├── training/
│   ├── dataset_builder.py  ← Construcción de datasets de entrenamiento
│   ├── train_qlora.py      ← QLoRA para fine-tuning (RTX 3090)
│   ├── merge.py            ← Merge de adaptadores LoRA
│   ├── pdf_scraper.py      ← Scraper de PDFs para training
│   ├── study_engine.py     ← Motor de estudio continuo
│   ├── continuous_scraper.py ← Scraping continuo de conocimiento
│   └── train_night_cron.sh ← Script cron nocturno de entrenamiento
│
├── ollama/
│   ├── Modelfile.arch7     ← Arch7 Modelfile
│   ├── Modelfile.arch7a    ← Arch7a Modelfile
│   ├── Modelfile.arch7b    ← Arch7b Modelfile
│   ├── Modelfile.coda      ← CODA Modelfile (32B)
│   ├── Modelfile.rebx3     ← REBx3 Modelfile
│   └── Modelfile.intruder  ← Intruder Modelfile
│
├── scripts/
│   ├── init_void_axiom_ollama.bat  ← Inicializador Windows
│   ├── init_void_axiom_ollama.sh   ← Inicializador Linux/Mac
│   ├── move_knowledge_to_hdd.bat   ← Mover knowledge base a HDD
│   ├── scan_check.py               ← Verificación de escaneo
│   └── update_batch.py             ← Actualización batch
│
├── knowledge_base/         ← Base de conocimiento (gitignore)
│   ├── pdf_scraper.db      ← DB de conocimiento extraído
│   ├── arxiv/              ← Papers académicos
│   ├── books/              ← Libros técnicos
│   ├── github/             ← Repositorios clonados
│   ├── internet_archive/   ← Archivos de Internet Archive
│   ├── languages/          ← Recursos de lenguajes
│   ├── openstax/           ← Libros OpenStax
│   ├── tutorials/          ← Tutoriales guardados
│   └── university/         ← Material universitario
│
├── datasets/
│   ├── study_qa_20260526_*.jsonl  ← Datasets de preguntas/respuestas
│
├── data/
│   └── analizador.db       ← SQLite de producción (gitignored)
│
├── fixed/                  ← Archivos corregidos de versiones anteriores
│   ├── config.py
│   ├── database.py
│   ├── main.py
│   ├── void_agents.py
│   ├── void_memory.py
│   └── void_ollama.py
│
├── Iniciar Analizador IA.bat      ← Script de inicio rápido
├── Iniciar Busqueda Continua.bat  ← Búsqueda continua de conocimiento
├── Iniciar Estudio IA.bat         ← Motor de estudio
├── Iniciar Ollama Modo RAM.bat    ← Ollama con parámetros RAM optimizados
├── Guardar Cambios Git.bat        ← Commit rápido Git
├── Inicializar Git.bat            ← Init Git
├── Cerrar Tunel.bat               ← Cerrar túnel/exposición
└── .gitignore
```

---

## Componentes principales

### 1. Chat con Streaming (SSE)

- **POST /api/chats/{chat_id}/stream** → Streaming con Server-Sent Events
- **Autenticación** por cookies de sesión (30 días)
- **Detección de desconexión** del cliente (WinError 10054 solucionado)
- **Subida de archivos** multi-formato vinculados a cada chat
- **Búsqueda web automática** vía RSS + DuckDuckGo con caché de 2 min
- **Título automático** con modelo 3B en background
- **Exportación de chats** en JSON completo
- **Resumen e insights** por chat (términos frecuentes, previews)

### 2. Sistema Multi-Agente (Void Axiom)

Arquitectura de agentes con handover narrativo y cola GPU:

| Agente | Modelo | Rol |
|--------|--------|-----|
| **ARCH-7a / ARCH-7b** | Qwen 2.5 7B | Agentes principales de chat y código |
| **CODA** | Qwen 2.5 32B | Agente de análisis profundo |
| **REBx3** | Qwen 2.5 7B | Crítico / reacción rebelde |
| **Intruder** | Qwen 2.5 7B | Intervención aleatoria (12%) |

- **NarrativeHandover**: pase de batuta narrativo entre agentes con contexto
- **GPUQueue**: cola prioritaria con semáforo, OOM retry (3 intentos con backoff)
- **VRAMManager**: carga/descarga inteligente de modelos según VRAM disponible
- **Prioridades**: 0=Pioneer/admin, 1=plan_max, 2=plan_free

### 3. Generador de Código Multi-Fase (4 fases)

Generación de proyectos completos con streaming NDJSON:

1. **🏗️ FASE 1 — Arquitecto (14b)**:
   - Diseña estructura completa (carpetas, archivos, interfaces)
   - Mínimo 9 archivos (pequeño), 15 (mediano), 24 (grande)
2. **🤝 FASE 2 — Dual Subagente (7b × 7b)**:
   - **Agente A** (Implementador): escribe código inicial (mín. 150 líneas)
   - **Agente B** (Crítico/Enriquecedor): critica y reescribe mejorando
   - Diálogo iterativo por archivo
3. **🔍 FASE 3 — Revisor (14b)**:
   - Verifica imports, métodos, variables, rutas
   - Corrección automática de errores
4. **📦 Exportación**: ZIP del proyecto a disco

**Características**:
- Opciones avanzadas: tests, Dockerfile, CI/CD, nivel de comentarios
- Verificación automática (análisis AST de Python, validación de imports)
- Historial persistente de generaciones (últimas 50)
- Regeneración de archivos individuales

### 4. Sistema de Usuarios y Planes

**Autenticación**:
- Registro/login con hash SHA-256 + salt
- Sesiones por cookie httponly (30 días)
- API keys para acceso programático

**Programa Alpha_Pionero**:
- Los primeros **50 usuarios** registrados obtienen **plan_max GRATIS**
- Badge "Pioneer #N" permanente e intransferible
- Del usuario 51 en adelante → plan_free_limitado

| Plan | Mensajes/día | Modelos | Archivos | Prioridad GPU |
|------|-------------|---------|----------|---------------|
| **plan_max** | Ilimitado | Todos (3b, 7b, 14b) | Hasta 110K tokens | Alta (0) |
| **plan_free_limitado** | 20/día | Solo 3b | <1MB | Baja (2) |

**Códigos de redención** (admin): canjeables para activar plan_max

### 5. Administración

Endpoints protegidos para administradores:

- `GET /api/admin/stats` — Estadísticas globales
- `GET /api/admin/users` — Lista de usuarios
- `GET /api/admin/chats` — Todos los chats del sistema
- `DELETE /api/admin/users/{id}` — Eliminar usuario
- `POST /api/admin/users/{id}/logout` — Forzar cierre de sesión
- `GET /api/admin/users/{id}/messages` — Ver mensajes de cualquier usuario
- `POST /api/admin/codes/create` — Generar código de redención
- `DELETE /api/admin/codes/{code}` — Eliminar código
- `GET /api/admin/scale-readiness` — Estado para migración a servidor superior

### 6. Base de Conocimiento y Training

- **Knowledge base** multi-fuente: arXiv, GitHub, OpenStax, Internet Archive, tutorials
- **Scraping continuo** de PDFs y documentación
- **Fine-tuning QLoRA** para modelos 32B en RTX 3090 (24GB VRAM)
- **Study Engine**: motor de estudio autónomo con generación de QA datasets
- **Cron nocturno** de entrenamiento programado

---

## Instalación y puesta en marcha

### 1. Instalar Ollama

```bash
# Descargar e instalar desde https://ollama.com/download
```

### 2. Descargar modelos base

```bash
ollama pull qwen2.5-coder:3b
ollama pull qwen2.5-coder:7b
ollama pull qwen2.5-coder:14b

# Opcional: modelos Void Axiom (desde Modelfiles)
ollama create arch7_void -f ollama/Modelfile.arch7
ollama create coda_void -f ollama/Modelfile.coda
ollama create rebx3_void -f ollama/Modelfile.rebx3
ollama create intruder_void -f ollama/Modelfile.intruder
```

### 3. Variables de entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| `OLLAMA_HOST` | `http://localhost:11434` | URL de Ollama |
| `OLLAMA_MODEL` | `qwen2.5-coder:7b` | Modelo por defecto |
| `PORT` | `8080` | Puerto del servidor web |
| `AUTH_TOKEN` | `mi-analisis-ia-2024` | Token de autenticación legacy |

### 4. Iniciar Ollama

```bash
# Windows (o usar Iniciar Ollama Modo RAM.bat)
OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=2 ollama serve

# Linux/Mac
OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=2 ollama serve
```

### 5. Iniciar el servidor

```bash
pip install -r requirements.txt
python main.py
# Servidor en http://localhost:8080

# O usar el script de inicio:
.\Iniciar Analizador IA.bat
```

---

## API Endpoints

### Autenticación
| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/auth/register` | Registro de nuevo usuario |
| POST | `/api/auth/login` | Inicio de sesión |
| POST | `/api/auth/logout` | Cierre de sesión |
| GET | `/api/auth/me` | Información del usuario actual |

### Chats
| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/chats` | Listar chats del usuario |
| POST | `/api/chats/new` | Crear nuevo chat |
| GET | `/api/chats/{id}` | Obtener chat con mensajes y archivos |
| DELETE | `/api/chats/{id}` | Eliminar chat |
| PATCH | `/api/chats/{id}/title` | Renombrar chat |
| POST | `/api/chats/{id}/stream` | Chat streaming SSE |
| GET | `/api/chats/{id}/summary` | Resumen del chat |
| GET | `/api/chats/{id}/export` | Exportar chat completo |
| GET | `/api/chats/{id}/status` | Estado de archivos |
| GET | `/api/chats/{id}/files/insights` | Análisis de términos frecuentes |

### Archivos
| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/upload` | Subir archivo a un chat |
| POST | `/api/chats/{id}/upload` | Subir archivo a chat específico |
| POST | `/api/chats/{id}/upload/multiple` | Subir múltiples archivos |
| POST | `/api/chats/{id}/upload/text` | Pegar texto como archivo |
| POST | `/api/chats/{id}/files/remove` | Eliminar archivo |
| POST | `/api/chats/{id}/files/clear` | Limpiar todos los archivos |

### Modelos
| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/models` | Listar modelos disponibles |
| POST | `/api/models/switch` | Cambiar modelo activo |

### Planes y Pioneros
| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/pioneers/status` | Estado del programa pionero |
| GET | `/api/pioneers/leaderboard` | Tabla de pioneros |
| GET | `/api/user/plan` | Plan del usuario actual |
| POST | `/api/redeem-code` | Canjear código de plan |
| GET | `/api/donations/tiers` | Tiers de donación |

### Generador de Código
| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/generador` | Página del generador |
| POST | `/api/generador/generar` | Generar proyecto (streaming 4 fases) |
| POST | `/api/generador/regenerar-file` | Regenerar archivo individual |
| POST | `/api/generador/verificar` | Verificar proyecto generado |
| POST | `/api/generador/exportar` | Exportar proyecto a disco |
| GET | `/api/generador/history` | Historial de generaciones |
| GET | `/api/generador/history/{id}` | Detalle de generación |
| DELETE | `/api/generador/history/{id}` | Eliminar generación |

### Administración
| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/admin/stats` | Estadísticas del sistema |
| GET | `/api/admin/users` | Lista de usuarios |
| DELETE | `/api/admin/users/{id}` | Eliminar usuario |
| POST | `/api/admin/users/{id}/logout` | Forzar logout |
| GET | `/api/admin/chats` | Todos los chats |
| DELETE | `/api/admin/chats/{id}` | Eliminar cualquier chat |
| GET | `/api/admin/users/{id}/messages` | Ver mensajes de usuario |
| POST | `/api/admin/codes/create` | Crear código de plan |
| GET | `/api/admin/codes` | Listar códigos |
| DELETE | `/api/admin/codes/{code}` | Eliminar código |
| GET | `/api/admin/scale-readiness` | Estado de escalado |

### Sistema
| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/ping` | Health check rápido |
| GET | `/health` | Estado completo del sistema |
| POST | `/api/gc` | Forzar garbage collector + vaciar caché CUDA |
| GET | `/` | Página principal (index.html) |
| GET | `/auth` | Página de login/registro |

---

## Formatos de archivo soportados

- **Código**: .py, .js, .ts, .jsx, .tsx, .html, .css, .cpp, .java, .go, .rs, .rb, .php y +30 más
- **Documentos**: .pdf, .docx, .doc, .pptx, .ppt, .odt, .rtf, .epub
- **Datos**: .csv, .tsv, .json, .jsonl, .xml, .yaml, .toml, .sql, .db
- **Hojas de cálculo**: .xlsx, .xls, .xlsm, .ods
- **Imágenes**: .png, .jpg, .jpeg, .gif, .webp, .bmp, .tiff (metadatos + OCR opcional)
- **Comprimidos**: .zip, .tar, .gz, .tar.gz, .rar, .7z
- **Notebooks**: .ipynb

Tamaño máximo por archivo: **110,000 tokens** (~5GB raw con truncamiento automático)

---

## Perfiles de rendimiento (Ollama)

| Perfil | Contexto | Batch | Temperatura | Uso |
|--------|----------|-------|-------------|-----|
| **fast** | 4K | 1024 | 0.3 | Respuesta rápida |
| **turbo** | 8K | 512 | 0.2 | Balance velocidad/calidad |
| **ultra** | 32K | 256 | 0.1 | Máxima calidad, contexto grande |
| **max_context** | 110K | 128 | 0.2 | Contexto máximo para archivos grandes |

---

## Configuración de hardware

El sistema está optimizado para **RTX 3090 (24GB VRAM, Ampere)** con:
- Flash Attention activado (OLLAMA_FLASH_ATTENTION=1)
- TF32 activado (RTX 3090 Ampere)
- Keep alive 10 min (modelo caliente en VRAM)
- 7 threads CPU para LLM (1 libre para el sistema)
- Pool HTTP de 32 conexiones

Para **GTX 1080 Ti (11GB VRAM)**: ajustar `num_ctx` a 4096-8192 y usar modelos 7B Q4_K_M (~4.5GB VRAM).

---

## Búsqueda web integrada

El sistema detecta automáticamente consultas que requieren información actual:
- Keywords como "noticias", "hoy", "precio", "2026", etc.
- Fuentes RSS: BBC Mundo, 20minutos, RTVE, El País, CNN Español, Europapress
- Fallback: DuckDuckGo News API
- Caché en memoria de 2 minutos

---

## Licencia

Uso personal/educativo. Creado por **Aerys** (desarrollador de 13 años).

---

## Scripts útiles

| Script | Descripción |
|--------|-------------|
| `Iniciar Analizador IA.bat` | Inicia el servidor completo |
| `Iniciar Busqueda Continua.bat` | Inicia scraping continuo de conocimiento |
| `Iniciar Estudio IA.bat` | Motor de estudio autónomo |
| `Iniciar Ollama Modo RAM.bat` | Ollama optimizado para RAM |
| `Guardar Cambios Git.bat` | Commit rápido a Git |
| `Cerrar Tunel.bat` | Cierra exposición externa |
| `scripts/init_void_axiom_ollama.bat` | Inicializa modelos Void Axiom |

> ### ⚠️ AVISO DE PROTOCOLO DE SEGURIDAD
> 
> **ADVERTENCIA:** El Agente **3B (Rebelde)** tiene una tolerancia limitada a las consultas redundantes. En casos extremos, puede volverse muy enojón y acabar explotando (metafóricamente... o no). 
> 
> Si detectas que el sistema entra en un bucle de insultos o el ventilador de tu PC empieza a sonar como una turbina, recomendamos aplicar el **Protocolo de Mantenimiento Analógico**: dale un golpe seco al chasis para reiniciarle la memoria. 
> 
> *Void Axiom no se hace responsable de daños colaterales, abolladuras en el hardware o crisis existenciales del usuario.*
