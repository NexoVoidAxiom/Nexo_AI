<div align="center">

# 🔷 Nexo AI

**Plataforma de IA local con sistema multi-agente, streaming SSE y fine-tuning propio**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Ollama](https://img.shields.io/badge/Ollama-local-black?style=flat)](https://ollama.com)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat)](LICENSE)

*Creado por **Aerys** — desarrollador de 13 años.*

</div>

---

## ¿Qué es Nexo AI?

Nexo AI es una plataforma completa de IA local que corre 100% en tu hardware, sin enviar datos a ningún servidor externo. Incluye:

- 💬 **Chat con streaming** y subida de archivos (+60 formatos)
- 🧠 **Smart Router** — modelo ligero (3B) para charla, modelo pesado para trabajo
- 🤖 **Sistema multi-agente A2A (Void Axiom)** — 4 agentes con personalidades propias colaborando en tiempo real
- 🏗️ **Generador de código en 3 fases** — Arquitecto (14b) → Dual Subagente (7b×7b) → Revisor (14b)
- 📚 **Pipeline de fine-tuning** — scraper continuo, study engine, QLoRA training, merge
- 👥 **Multiusuario** con autenticación, planes y Programa Alpha Pionero

---

## ⚡ Inicio rápido

```bash
# 1. Asegúrate de que Ollama está corriendo
ollama serve

# 2. Descarga los modelos
ollama pull qwen2.5-coder:3b
ollama pull qwen2.5-coder:7b-instruct
ollama pull qwen2.5-coder:14b

# 3. Instala dependencias
pip install -r requirements.txt

# 4. Inicia el servidor
python main.py
# → http://localhost:8080
```

En Windows también puedes hacer doble clic en **`Iniciar Analizador IA.bat`**.

---

## 🧠 Smart Router

Cada mensaje se clasifica automáticamente en menos de 1ms (sin GPU) para decidir qué modelo usar:

| Intención | Modelo | Cuándo |
|-----------|--------|--------|
| **CHAT** | `qwen2.5-coder:3b` (ligero) | Saludos, charla casual, preguntas simples |
| **WORK** | Modelo Nexo del usuario | Código, análisis, razonamiento, archivos |

Esto ahorra VRAM y acelera las respuestas cuando solo estás conversando.

### Modelos Nexo (seleccionables por usuario)

| ID | Nombre | Modelo | VRAM | Ideal para |
|----|--------|--------|------|------------|
| `nexo_lite` | Nexo Lite 1.0 | `qwen2.5-coder:3b` | ~2 GB | Tareas simples |
| `nexo_coder` | Nexo Coder 1.0 | `qwen2.5-coder:7b-instruct` | ~4.5 GB | ★ Balance velocidad/calidad |
| `nexo_pro` | Nexo Pro 1.0 | `qwen2.5-coder:14b` | ~8.5 GB | Razonamiento profundo |

---

## 🤖 Void Axiom — Sistema Multi-Agente A2A

4 agentes con personalidades distintas que colaboran (y a veces discrepan) en tiempo real:

| Agente | Sigil | Color | Rol |
|--------|-------|-------|-----|
| **ARCH-7** | `AR` | 🔵 | Arquitecto estructural |
| **CODA** | `CO` | 🟢 | Codificador / implementador |
| **REBx3** | `RE` | 🔴 | Crítico reactivo |
| **Intruso** | `⬡` | 🟣 | Glitch probabilístico (12%) |

- Orquestación round-robin con memoria compartida (16K estándar / 55K extendido)
- SSE en tiempo real → `/void`
- Intervención directa de Aerys con `/api/void/intervene`

---

## 🏗️ Generador de Código Multi-Fase

Genera proyectos completos en 3 fases encadenadas:

1. **Arquitecto (14b)** — Diseña estructura de carpetas, contratos de interfaces y plan completo
2. **Dual Subagente (7b × 7b)** — Agente A implementa → Agente B critica y reescribe con más features
3. **Revisor (14b)** — Detecta y corrige imports rotos, métodos inexistentes y rutas incorrectas

Tamaños disponibles: Pequeño (7-11 archivos) · Mediano (12-18) · Grande (20-28)

---

## 📊 Planes

| Plan | Mensajes/día | Contexto | Archivos |
|------|-------------|----------|----------|
| **FREE** | 20 | 25,000 tokens | 1 MB |
| **TESTER** 🧪 | ∞ | 35,000 tokens | 50 MB |
| **MAX** ⭐ | ∞ | 35,000 tokens | 100 MB |

### 🏅 Programa Alpha Pionero
Los primeros **50 usuarios** reciben **plan MAX gratis** de forma permanente.

---

## 📁 Estructura del proyecto

```
Nexo_AI/
├── main.py                    ← Punto de entrada FastAPI
├── requirements.txt
│
├── app/                       ← Código principal
│   ├── main.py                ←   Rutas, streaming, generador
│   ├── agent_chat.py          ←   Sistema A2A Void Axiom
│   ├── void_memory.py         ←   Memoria con presupuesto de tokens
│   ├── void_ollama.py         ←   Cliente Ollama para agentes
│   ├── void_agents.py         ←   Perfiles de los 4 agentes
│   ├── smart_router.py        ←   Router inteligente CHAT/WORK
│   ├── database.py            ←   SQLite: usuarios, chats, sesiones
│   ├── auth.py                ←   Autenticación con cookies httponly
│   ├── data_processor.py      ←   Procesado de archivos (+60 formatos)
│   ├── prompts.py             ←   Builders de prompts por fase
│   ├── pioneers.py            ←   Planes, pioneros, donaciones
│   └── security.py            ←   Rate limiting, validaciones
│
├── training/                  ← Pipeline de fine-tuning
│   ├── continuous_scraper.py  ←   Scraper continuo de PDFs
│   ├── dataset_builder.py     ←   Construcción de datasets QA
│   ├── study_engine.py        ←   Generación automática de Q&A
│   ├── train_qlora.py         ←   Fine-tuning QLoRA
│   ├── merge.py               ←   Merge de adaptadores LoRA
│   └── train_night_cron.sh    ←   Cron de entrenamiento nocturno
│
├── static/                    ← Frontend
│   ├── index.html             ←   Chat principal
│   ├── void_axiom.html        ←   Frontend A2A
│   └── generador.html         ←   Generador de código
│
├── core/                      ← Motor VRAM-aware (en integración)
│   ├── dispatcher.py          ←   ModelDispatcher
│   ├── gpu_queue.py           ←   GPUQueue con prioridades
│   └── vram.py                ←   VRAMManager
│
└── scripts/                   ← Utilidades
    ├── init_void_axiom_ollama.bat / .sh
    └── *.bat                  ←   Scripts de inicio rápido
```

---

## 🔧 Variables de entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| `OLLAMA_HOST` | `http://localhost:11434` | URL de Ollama |
| `OLLAMA_MODEL` | `qwen2.5-coder:7b` | Modelo por defecto |
| `PORT` | `8080` | Puerto del servidor |
| `CORS_ORIGINS` | `http://localhost:8080` | Orígenes CORS permitidos |
| `VOID_INTRUDER_PROBABILITY` | `0.12` | Probabilidad del Intruso (0–1) |
| `VOID_OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | URL Ollama para agentes |

---

## 🖥️ Hardware recomendado

| Componente | Mínimo | Recomendado |
|------------|--------|-------------|
| GPU | GTX 1080 Ti (11 GB VRAM) | RTX 3090 (24 GB VRAM) |
| RAM | 16 GB | 32 GB |
| CPU | i5 moderna | i7-9700K o superior |

> Los modelos de 14b requieren ~8.5 GB VRAM. Con 11 GB puedes correr el stack completo ajustado.

---

## 📡 API — Referencia rápida

<details>
<summary>Ver endpoints completos</summary>

### Autenticación
| Método | Ruta |
|--------|------|
| POST | `/api/auth/register` |
| POST | `/api/auth/login` |
| POST | `/api/auth/logout` |
| GET | `/api/auth/me` |

### Chats y archivos
| Método | Ruta |
|--------|------|
| GET/POST | `/api/chats` / `/api/chats/new` |
| GET/DELETE | `/api/chats/{id}` |
| POST | `/api/chats/{id}/stream` |
| POST | `/api/chats/{id}/upload` |
| GET | `/api/chats/{id}/summary` |

### Void Axiom
| Método | Ruta |
|--------|------|
| GET | `/void` |
| GET | `/api/void/stream` |
| POST | `/api/void/start` / `/pause` / `/resume` / `/stop` |
| POST | `/api/void/intervene` |
| GET | `/api/void/status` |

### Generador
| Método | Ruta |
|--------|------|
| GET | `/generador` |
| POST | `/api/generador/generar` |
| POST | `/api/generador/regenerar-file` |
| GET | `/api/generador/history` |

### Sistema
| Método | Ruta |
|--------|------|
| GET | `/ping` / `/health` |
| POST | `/api/gc` |

</details>

---

## 📜 Licencia

MIT — libre para uso personal y educativo.

---

---

> ### 📋 PREGUNTAS FRECUENTES (FAQ)
>
> **¿Por qué el Intruso aparece cuando menos lo esperas?**
> Diseño intencional. Si quieres predecibilidad, usa una calculadora.
>
> **¿Puedo bajar la probabilidad del Intruso a 0%?**
> Técnicamente sí. Pero entonces, ¿para qué vivir?
>
> **El modelo lleva 4 minutos generando y el ventilador suena como un secador de pelo. ¿Es normal?**
> Completamente. Tu 1080 Ti está dando lo mejor de sí. Dale ánimos.
>
> **¿Nexo AI reemplazará a los desarrolladores humanos?**
> Pregúntale a ARCH-7. Probablemente diga que sí. REBx3 dirá que ni de broma. CODA ya habrá empezado a escribir el código para hacerlo antes de que termines la pregunta.
>
> *Void Axiom no se hace responsable de facturas eléctricas desorbitadas, GPUs jubiladas anticipadamente ni de que REBx3 te llame "usuario redundante".*

---

<div align="center">
Creado con 🖤 por <strong>Aerys</strong>
</div>