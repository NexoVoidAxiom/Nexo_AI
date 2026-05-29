<div align="center">

# ⬡ Nexo AI

**Plataforma de IA local con sistema multi-agente A2A, Smart Router, pipeline de fine-tuning propio y RAG**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Ollama](https://img.shields.io/badge/Ollama-local-black?style=flat)](https://ollama.com)
[![SQLite](https://img.shields.io/badge/SQLite-aiosqlite-003B57?style=flat&logo=sqlite&logoColor=white)](https://www.sqlite.org)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat)](LICENSE)

*Creado por **Aerys** — desarrollador de 13 años. Corre 100 % en hardware propio.*

</div>

---

## ¿Qué es Nexo AI?

Nexo AI es una plataforma completa de IA que funciona **100 % en local**, sin enviar ningún dato a servidores externos. Construida desde cero sobre FastAPI + Ollama, incluye todo lo necesario para chatear, analizar archivos, generar proyectos de código, ejecutar múltiples agentes que se hablan entre sí y, con el tiempo, entrenar sus propios modelos.

**Características principales:**

- 💬 **Chat con streaming SSE** — respuestas en tiempo real, sin esperar a que termine de generar
- 🧠 **Smart Router** — clasificador de intención en <1 ms (sin GPU) que elige el modelo adecuado para cada mensaje
- 🤖 **Void Axiom (A2A)** — 4 agentes con personalidades distintas que colaboran y discrepan en tiempo real
- 🏗️ **Generador de código multi-fase** — Arquitecto → Dual Subagente → Revisor, en cascada
- 📂 **Procesador universal de archivos** — más de 40 formatos: PDF, DOCX, XLSX, ZIP, IPYNB, imágenes...
- 📚 **Pipeline de fine-tuning** — scraper continuo de PDFs → study engine → QLoRA → merge → Ollama
- 🔍 **RAG** — indexación vectorial con ChromaDB + embeddings locales para consultar la knowledge base
- 🌐 **Búsqueda web integrada** — DuckDuckGo como fallback cuando el prompt pide información actualizada
- 👥 **Multiusuario** — autenticación con cookies httponly, planes, rate limiting y Programa Alpha Pionero

---

## ⚡ Inicio rápido

```bash
# 1. Asegúrate de que Ollama está corriendo
ollama serve

# 2. Descarga los modelos base
ollama pull qwen2.5-coder:3b
ollama pull qwen2.5-coder:7b-instruct
ollama pull qwen2.5-coder:14b

# 3. Instala dependencias Python
pip install -r requirements.txt

# 4. Inicia el servidor
python main.py
# → http://localhost:8080
```

En Windows también puedes hacer doble clic en **`Iniciar Analizador IA.bat`**.

Para inicializar los agentes de Void Axiom con sus Modelfiles personalizados:

```bash
# Linux / macOS
bash scripts/init_void_axiom_ollama.sh

# Windows
scripts\init_void_axiom_ollama.bat
```

---

## 🧠 Smart Router

Cada mensaje pasa por un clasificador de intención antes de llegar al modelo. El clasificador corre en CPU en menos de 1 ms y decide entre dos rutas:

| Intención | Modelo asignado | Ejemplos |
|-----------|-----------------|----------|
| **CHAT** | `qwen2.5-coder:3b` (ligero) | Saludos, preguntas simples, charla casual |
| **WORK** | Modelo Nexo del usuario | Código, análisis, razonamiento, archivos adjuntos |

Esto evita cargar modelos pesados para mensajes que no lo necesitan, ahorrando VRAM y acelerando la respuesta.

### Modelos Nexo (seleccionables por usuario)

| ID interno | Nombre en UI | Modelo Ollama | VRAM aprox. | Ideal para |
|------------|--------------|---------------|-------------|------------|
| `nexo_lite` | Nexo Lite 1.0 | `qwen2.5-coder:3b` | ~2 GB | Tareas simples y rápidas |
| `nexo_coder` | Nexo Coder 1.0 | `qwen2.5-coder:7b-instruct` | ~4.5 GB | ★ Balance velocidad/calidad |
| `nexo_pro` | Nexo Pro 1.0 | `qwen2.5-coder:14b` | ~8.5 GB | Razonamiento profundo |
| *(próximo)* | Nexo Ultra | `qwen2.5-coder:32b` | ~19+ GB | Máxima calidad *(requiere más VRAM)* |

---

## 🤖 Void Axiom — Sistema Multi-Agente A2A

Void Axiom es el motor de agentes colaborativos de Nexo. Cuatro agentes con identidades fijas e inyectadas en cada llamada a Ollama se turnan en rondas, comparten memoria y pueden discrepar entre sí:

| Agente | Sigil | Personalidad | Modelo recomendado |
|--------|-------|-------------|-------------------|
| **ARCH-7** | `AR` | Arquitecto estructural. Frío, analítico, quirúrgico. | `qwen2.5:7b-instruct-q8_0` |
| **CODA** | `CO` | Codificador/implementador. Pragmático, orientado a resultados. | `qwen2.5-coder:32b-instruct-q4KM` |
| **REBx3** | `RE` | Crítico reactivo. Confrontacional, revisa y contradice. | `qwen2.5:3b-instruct-q8_0` |
| **Intruso** | `⬡` | Glitch probabilístico (12 % de aparición). Impredecible. | `qwen2.5:1.5b-instruct-q8_0` |

**Características del runtime:**

- Orquestación round-robin con presupuesto de tokens (16 K estándar / 55 K extendido)
- Guard anti-bleeding inyectado antes de cada system prompt para evitar contaminación entre agentes
- Sanitizador multicapa: filtra volcados de formato, cabeceras de rol y respuestas corruptas
- Retry con jitter de temperatura (+0.4 por intento) para recuperarse de salidas inválidas
- SSE en tiempo real accesible en `/void`
- Intervención directa del operador vía `POST /api/void/intervene`

### Motor VRAM-Aware (core/)

El directorio `core/` contiene el dispatcher de segunda generación diseñado para hardware con más VRAM (RTX 3090, 24 GB), actualmente en proceso de integración:

- **`dispatcher.py`** — Orquestador VRAM-aware con handoff dinámico entre `CHAT_SET` y `CODE_SET`
- **`gpu_queue.py`** — Cola de inferencia con prioridades (Pioneer > MAX > Free) y manejo de OOM sin crash
- **`vram.py`** — Gestor de VRAM: carga/descarga de modelos bajo demanda según presupuesto disponible
- **`classifier.py`** — Clasificador de intención para el dispatcher (IntentType: CHAT / CODE / LOGIC)
- **`handover.py`** — Eventos de handoff narrativos emitidos por SSE al cambiar de set de modelos

---

## 🏗️ Generador de Código Multi-Fase

El generador produce proyectos completos en tres fases encadenadas, cada una ejecutada por un modelo distinto:

```
Prompt del usuario
       │
       ▼
┌─────────────────────────────────┐
│  FASE 1 — Arquitecto (14b)      │  Diseña estructura de carpetas,
│                                 │  contratos de interfaces y plan
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────┐
│  FASE 2A — Agente A (7b)        │  Implementa todos los archivos
│  FASE 2B — Agente B (7b)        │  Critica y reescribe con mejoras
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────┐
│  FASE 3 — Revisor (14b)         │  Detecta imports rotos, métodos
│                                 │  inexistentes y rutas incorrectas
└─────────────────────────────────┘
```

Tamaños de proyecto disponibles: **Pequeño** (7–11 archivos) · **Mediano** (12–18) · **Grande** (20–28)

Accesible en `/generador`.

---

## 📂 Procesador de archivos

`app/data_processor.py` actúa como dispatcher universal. Cuando subes un archivo al chat, se procesa automáticamente según su extensión y el contenido se inyecta en el contexto de la conversación.

| Categoría | Formatos soportados |
|-----------|---------------------|
| **Datos estructurados** | `.csv`, `.tsv`, `.json`, `.jsonl` |
| **Documentos Office** | `.pdf`, `.docx`, `.doc`, `.xlsx`, `.xls`, `.xlsm`, `.ods`, `.pptx`, `.ppt` |
| **Imágenes** | `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.bmp`, `.tiff` (metadatos + OCR opcional) |
| **Archivos comprimidos** | `.zip`, `.tar`, `.gz`, `.tgz`, `.tar.gz`, `.tar.bz2`, `.tar.xz` |
| **Notebooks** | `.ipynb` (celdas de código y markdown) |
| **Texto y código** | `.txt`, `.log`, `.md`, `.rst`, `.rtf`, `.odt`, `.epub`, y cualquier archivo de código fuente |

Los archivos ZIP y TAR se inspeccionan recursivamente: se extraen y procesan sus contenidos internos.

---

## 📚 Pipeline de Fine-Tuning

El directorio `training/` contiene un pipeline completo de entrenamiento que corre en local:

```
knowledge_base/           ←  PDFs, libros, tutoriales, papers arXiv
       │
       ▼
continuous_scraper.py     ←  Descarga PDFs de arXiv, Semantic Scholar y fuentes curadas
       │                       Soporta búsqueda dinámica por tema (--search "topic")
       ▼
rag_indexer.py            ←  Indexa cada PDF en ChromaDB con embeddings locales
       │                       Modelo: nomic-embed-text (vía Ollama)
       ▼
study_engine.py           ←  Lee chunks de la KB y genera pares Q&A con Ollama
       │                       Concurrencia real, checkpoint/resume, reintentos
       ▼
dataset_builder.py        ←  Convierte los Q&A al formato ShareGPT para QLoRA
       │
       ▼
train_qlora.py            ←  Fine-tuning QLoRA (4-bit NF4, Flash Attention 2)
       │                       Configurado para RTX 3090 (Ampere, compute 8.6)
       ▼
merge.py                  ←  Fusiona adaptadores LoRA + cuantiza a GGUF Q4_K_M
       │                       + registra el modelo resultante en Ollama
       ▼
ollama/Modelfile.*        ←  Modelfiles de los agentes (ARCH-7, CODA, REBx3, Intruder)
```

El cron `train_night_cron.sh` automatiza el ciclo completo de madrugada cuando la GPU está libre.

### RAG interactivo

```bash
# Indexar toda la knowledge base
python training/rag_indexer.py

# Consulta directa
python training/rag_query.py "¿Cómo funciona el GIL de Python?"

# Modo interactivo
python training/rag_query.py --interactive

# Filtrar por categoría (arxiv, books, tutorials, university)
python training/rag_query.py --category arxiv "attention mechanism"
```

---

## 👥 Usuarios y planes

### Planes

| Plan | Mensajes/día | Contexto | Archivos | Notas |
|------|-------------|----------|----------|-------|
| **FREE** | 20 | 25 000 tokens | 1 MB | Plan por defecto |
| **TESTER** 🧪 | ∞ | 35 000 tokens | 50 MB | Acceso beta extendido |
| **MAX** ⭐ | ∞ | 35 000 tokens | 100 MB | Sin restricciones |

### 🏅 Programa Alpha Pionero

Los primeros **50 usuarios** registrados obtienen **plan MAX de forma permanente y gratuita**. Del usuario 51 en adelante se aplica el plan FREE. El número de pionero es intransferible y aparece en el leaderboard público.

---

## 📁 Estructura del proyecto

```
Nexo_AI/
├── main.py                      ← Punto de entrada — arranca uvicorn
├── requirements.txt
│
├── app/                         ← Núcleo de la aplicación
│   ├── main.py                  ←   Rutas FastAPI, streaming SSE, generador
│   ├── config.py                ←   Hardware profile y variables de entorno
│   ├── auth.py                  ←   Autenticación con cookies httponly + middleware
│   ├── database.py              ←   SQLite: usuarios, chats, mensajes, archivos, sesiones
│   ├── smart_router.py          ←   Router CHAT/WORK + mapping de modelos Nexo
│   ├── intent_classifier.py     ←   Clasificador de intención (sin GPU, <1 ms)
│   ├── agent_chat.py            ←   Orquestador A2A de Void Axiom (runtime de agentes)
│   ├── void_agents.py           ←   Perfiles, system prompts e identidades de los 4 agentes
│   ├── void_memory.py           ←   Memoria con presupuesto de tokens (16 K / 55 K)
│   ├── void_ollama.py           ←   Cliente Ollama asíncrono para los agentes
│   ├── void_activity.py         ←   Monitor de actividad del sistema Void
│   ├── dispatcher.py            ←   Dispatcher VRAM-aware (en integración con core/)
│   ├── data_processor.py        ←   Procesador universal de archivos (40+ formatos)
│   ├── llm_handler.py           ←   Handler HTTP para Ollama (streaming + no-streaming)
│   ├── prompts.py               ←   Builders de prompts para cada fase del generador
│   ├── pioneers.py              ←   Lógica Alpha Pionero, planes y donaciones
│   ├── security.py              ←   Cabeceras HTTP, rate limiting, CSRF, sanitización
│   └── api_keys_router.py       ←   Gestión de API keys
│
├── core/                        ← Motor VRAM-aware v2 (RTX 3090 target)
│   ├── dispatcher.py            ←   Orquestador con handoff CHAT_SET ↔ CODE_SET
│   ├── gpu_queue.py             ←   Cola de inferencia con prioridades y OOM handling
│   ├── vram.py                  ←   Gestor de VRAM con carga/descarga dinámica
│   ├── classifier.py            ←   Clasificador de intención del dispatcher v2
│   ├── handover.py              ←   Eventos narrativos de handoff (SSE)
│   └── ollama.py                ←   Cliente Ollama del core
│
├── training/                    ← Pipeline de fine-tuning completo
│   ├── continuous_scraper.py    ←   Scraper continuo + búsqueda dinámica arXiv/S2
│   ├── rag_indexer.py           ←   Indexador RAG en ChromaDB (nomic-embed-text)
│   ├── rag_query.py             ←   Motor de consultas RAG interactivo
│   ├── study_engine.py          ←   Generador de Q&A desde la knowledge base
│   ├── dataset_builder.py       ←   Constructor de datasets en formato ShareGPT
│   ├── train_qlora.py           ←   Entrenamiento QLoRA (4-bit, Flash Attention 2)
│   ├── merge.py                 ←   Merge LoRA + cuantización GGUF + registro Ollama
│   └── train_night_cron.sh      ←   Cron de entrenamiento nocturno automatizado
│
├── static/                      ← Frontend
│   ├── index.html               ←   Chat principal (responsive, dropdown de modelos)
│   ├── void_axiom.html          ←   Panel de Void Axiom en tiempo real
│   ├── generador.html           ←   Generador de código multi-fase
│   └── auth.html                ←   Pantalla de login/registro
│
├── knowledge_base/              ← Base de conocimiento para fine-tuning y RAG
│   ├── arxiv/                   ←   Papers de CS/ML/AI descargados de arXiv
│   ├── books/                   ←   Libros técnicos en abierto
│   ├── tutorials/               ←   Tutoriales por lenguaje (Python, JS, Rust, Go...)
│   ├── university/              ←   Material universitario
│   └── languages/               ←   Referencias de lenguajes de programación
│
├── datasets/                    ← Q&A generados (formato JSONL)
│
├── ollama/                      ← Modelfiles de los agentes Void Axiom
│   ├── Modelfile.arch7          ←   ARCH-7 (variante base)
│   ├── Modelfile.arch7a/b       ←   ARCH-7A y ARCH-7B (swap dual)
│   ├── Modelfile.coda           ←   CODA
│   ├── Modelfile.rebx3          ←   REBx3
│   └── Modelfile.intruder       ←   Intruso
│
├── agents/                      ← Módulo de agentes (en desarrollo)
│   ├── base.py                  ←   Clase base de agente
│   └── registry.py              ←   Registro de agentes disponibles
│
├── scripts/                     ← Utilidades
│   ├── init_void_axiom_ollama.sh / .bat  ←  Crea los modelos Void en Ollama
│   ├── scan_check.py            ←   Auditoría de integridad del sistema
│   └── update_batch.py          ←   Actualizaciones por lotes
│
└── api/                         ← Módulo API (en desarrollo)
    └── routes.py
```

---

## 🔧 Variables de entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| `OLLAMA_HOST` | `http://localhost:11434` | URL base de Ollama |
| `OLLAMA_MODEL` | `qwen2.5-coder:7b-instruct` | Modelo por defecto al arrancar |
| `PORT` | `8080` | Puerto del servidor FastAPI |
| `CORS_ORIGINS` | `http://localhost:8080` | Orígenes CORS permitidos |
| `OLLAMA_KEEP_ALIVE` | `600` | Segundos que el modelo permanece en VRAM entre peticiones |
| `OLLAMA_FLASH_ATTENTION` | `1` | Activa Flash Attention (Ampere+) |
| `VOID_INTRUDER_PROBABILITY` | `0.12` | Probabilidad de aparición del Intruso (0–1) |
| `VOID_OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | URL Ollama específica para los agentes |
| `VOID_AGENT_NUM_CTX` | `16384` | Contexto en tokens por agente (modo estándar) |
| `VOID_AGENT_TEMPERATURE` | `0.25` | Temperatura base de los agentes |
| `VOID_RETRY_TEMP_JITTER` | `0.4` | Jitter de temperatura por reintento |
| `VOID_MAX_RETRIES` | `3` | Reintentos máximos ante respuesta corrupta |
| `KNOWLEDGE_BASE` | `./knowledge_base` | Ruta de la base de conocimiento |

---

## 🖥️ Hardware

El proyecto está optimizado para funcionar con hardware de gama media-alta de consumo, sin necesitar infraestructura cloud.

| Componente | Hardware de desarrollo | Mínimo recomendado |
|------------|-----------------------|--------------------|
| GPU | GTX 1080 Ti (11 GB VRAM) | GTX 1070 (8 GB VRAM) |
| GPU objetivo (core/) | RTX 3090 (24 GB VRAM) | RTX 3080 (10 GB VRAM) |
| RAM | 32 GB | 16 GB |
| CPU | i7-9700K (8 núcleos) | i5 de 8ª gen+ |
| Almacenamiento KB | ~400 MB (creciente) | SSD recomendado |

> Con 11 GB de VRAM (GTX 1080 Ti) puedes correr el stack completo con los tres modelos Nexo (Lite/Coder/Pro). El Nexo Ultra (32B) requiere una GPU de la generación RTX 30/40.

---

## 📡 API — Referencia

<details>
<summary>Ver todos los endpoints</summary>

### Autenticación
| Método | Ruta | Descripción |
|--------|------|-------------|
| `POST` | `/api/auth/register` | Registro de usuario |
| `POST` | `/api/auth/login` | Login + cookie de sesión |
| `POST` | `/api/auth/logout` | Cierre de sesión |
| `GET` | `/api/auth/me` | Usuario autenticado actual |

### Chats y mensajes
| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/chats` | Lista de chats del usuario |
| `POST` | `/api/chats/new` | Crear nuevo chat |
| `GET` | `/api/chats/{id}` | Detalle + mensajes de un chat |
| `DELETE` | `/api/chats/{id}` | Eliminar chat |
| `POST` | `/api/chats/{id}/stream` | Enviar mensaje (respuesta SSE streaming) |
| `GET` | `/api/chats/{id}/summary` | Resumen del chat |
| `GET` | `/api/chats/{id}/status` | Tokens usados y archivos adjuntos |

### Archivos
| Método | Ruta | Descripción |
|--------|------|-------------|
| `POST` | `/api/chats/{id}/upload` | Subir un archivo |
| `POST` | `/api/chats/{id}/upload/multiple` | Subir varios archivos |
| `POST` | `/api/chats/{id}/upload/text` | Cargar texto pegado |
| `POST` | `/api/chats/{id}/files/remove` | Eliminar un archivo del chat |
| `POST` | `/api/chats/{id}/files/clear` | Eliminar todos los archivos |

### Modelos
| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/models` | Lista de modelos disponibles y modelo activo |
| `POST` | `/api/models/switch` | Cambiar el modelo Nexo activo |

### Void Axiom (A2A)
| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/void` | Panel Void Axiom (HTML) |
| `GET` | `/api/void/stream` | Stream SSE de la conversación A2A |
| `POST` | `/api/void/start` | Iniciar sesión de agentes |
| `POST` | `/api/void/pause` | Pausar |
| `POST` | `/api/void/resume` | Reanudar |
| `POST` | `/api/void/stop` | Detener |
| `POST` | `/api/void/intervene` | Inyectar mensaje del operador |
| `GET` | `/api/void/status` | Estado actual de la sesión |

### Generador
| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/generador` | Interfaz del generador (HTML) |
| `POST` | `/api/generador/generar` | Generar proyecto en 3 fases |
| `POST` | `/api/generador/regenerar-file` | Regenerar un archivo concreto |
| `GET` | `/api/generador/history` | Historial de proyectos generados |

### Planes y pioneros
| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/user/plan` | Plan del usuario actual |
| `GET` | `/api/pioneers/status` | Estado del programa Pioneer |
| `GET` | `/api/pioneers/leaderboard` | Tabla de pioneros |
| `GET` | `/api/donations/tiers` | Tiers de donación disponibles |
| `POST` | `/api/donations/intent` | Registrar intención de donación |

### Sistema
| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/ping` | Health check básico |
| `GET` | `/health` | Estado detallado del sistema |
| `POST` | `/api/gc` | Forzar recolección de basura |

</details>

---

## 📜 Licencia

MIT — libre para uso personal, educativo y proyectos propios.

---

---

> ### 📋 PREGUNTAS FRECUENTES
>
> **¿Por qué el Intruso aparece cuando menos lo esperas?**
> Diseño intencional. Si quieres predecibilidad, usa una calculadora.
>
> **¿Puedo bajar la probabilidad del Intruso a 0 %?**
> Técnicamente sí (`VOID_INTRUDER_PROBABILITY=0`). Pero entonces, ¿para qué vivir?
>
> **El modelo lleva 4 minutos generando y el ventilador suena como un secador de pelo. ¿Es normal?**
> Completamente. Tu 1080 Ti está dando lo mejor de sí. Dale ánimos.
>
> **¿Por qué hay un `AUDIT_REPORT.md` en el repo?**
> Porque a veces conviene dejar constancia de los bugs que uno mismo introduce. El informe documenta 6 bugs encontrados (3 críticos) en el refactor del sistema multi-agente, incluyendo 7 imports rotos que impedían el arranque. Todos corregidos. La transparencia es la única política de PR aceptable cuando eres tu propio cliente de soporte.
>
> **¿Nexo AI puede reemplazar a los desarrolladores humanos?**
> Pregúntale a ARCH-7. Dirá que sí con una presentación de diez secciones. REBx3 dirá que ni de broma y adjuntará un informe de fallos. CODA ya habrá empezado a implementar el reemplazo antes de que termines la frase. El Intruso simplemente dirá algo que no tiene nada que ver y te dejará pensando.
>
> **¿Cuándo sale el modelo 32B?**
> Cuando la factura de la luz lo permita.
>
> *Void Axiom no se hace responsable de facturas eléctricas desorbitadas, GPUs jubiladas anticipadamente, pérdida de sueño por logs de entrenamiento a las 3 AM, ni de que REBx3 te llame "usuario redundante" en tu propia máquina.*

---

```
              ⬡ · · · · · · · · · · · · · · · · · · · · · · · · ⬡
             ·                                                   ·
            ·   AR ──────────────────────────────────── AR       ·
           ·   /           VOID AXIOM A2A              \         ·
          ·   /                                         \        ·
         ·  CO ─────────────────────────────────────── RE       ·
        ·    \              ⬡ Intruso 12%              /         ·
       ·      \            (cuando no toca)            /          ·
      ·        ────────────────────────────────────────           ·
     ·                                                            ·
      ⬡ · · · · · · · · · · · · · · · · · · · · · · · · · · · · ⬡

                  NEXO AI  ·  local  ·  open  ·  tuyo
```

<div align="center">
Creado con 🖤 por <strong>Aerys</strong>
</div>