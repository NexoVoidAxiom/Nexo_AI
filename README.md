# VOID AXIOM — Esqueleto Maestro v2.0

Sistema multi-agente local con **handover narrativo**, **cola GPU robusta** y **dispatch VRAM-aware**.

Hardware target: GTX 1080 Ti (11 GB VRAM) · i7-9700K · 32 GB RAM

---

## Estructura de carpetas

```
void_axiom/
├── agents/
│   ├── __init__.py
│   ├── base.py           ← AgentConfig: contrato inmutable de cada agente
│   └── registry.py       ← AGENTS dict + get_agent() + INTRUDER_ID
│
├── core/
│   ├── __init__.py
│   ├── classifier.py     ← IntentClassifier: < 1ms, sin GPU
│   ├── dispatcher.py     ← ModelDispatcher: orquestador central
│   ├── gpu_queue.py      ← GPUQueue: cola prioritaria, nunca crashea por OOM
│   ├── handover.py       ← NarrativeHandover: pase de batuta narrativo
│   ├── ollama.py         ← OllamaClient: streaming defensivo con retry
│   └── vram.py           ← VRAMManager: carga/descarga de modelos
│
├── api/
│   ├── __init__.py
│   ├── routes.py         ← Routers adicionales (agentes, debug, queue)
│   ├── auth.py           ← Autenticación JWT (migrar de app/auth.py)
│   ├── pioneers.py       ← Programa Alpha_Pionero (migrar de app/pioneers.py)
│   └── schemas.py        ← Modelos Pydantic compartidos
│
├── training/
│   ├── dataset_builder.py
│   ├── train_qlora.py    ← QLoRA para 32B en GTX 1080 Ti
│   ├── merge.py          ← Merge de adaptadores LoRA
│   └── train_night_cron.sh
│
├── static/
│   └── void_axiom.html   ← Frontend principal (SSE consumer)
│
├── data/
│   └── analizador.db     ← SQLite (gitignored)
│
├── ollama/
│   ├── Modelfile.arch7a
│   ├── Modelfile.arch7b
│   ├── Modelfile.coda
│   ├── Modelfile.rebx3
│   └── Modelfile.intruder
│
├── scripts/
│   ├── init_void_axiom_ollama.sh
│   └── init_void_axiom_ollama.bat
│
├── main.py               ← FastAPI app + lifespan
├── requirements.txt
└── README.md
```

---

## Cómo conectar cada pieza

### 1. Iniciar el sistema

```bash
# 1. Asegúrate de que Ollama corra con las variables correctas
OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=2 ollama serve

# 2. Crear los modelos desde los Modelfiles
ollama create arch7a_void -f ollama/Modelfile.arch7a
ollama create arch7b_void -f ollama/Modelfile.arch7b
ollama create coda_void   -f ollama/Modelfile.coda
ollama create rebx3_void  -f ollama/Modelfile.rebx3
ollama create intruder_void -f ollama/Modelfile.intruder

# 3. Arrancar Void Axiom
pip install -r requirements.txt
python -m uvicorn void_axiom.main:app --host 0.0.0.0 --port 8080 --reload
```

### 2. Flujo de una petición de chat

```
Usuario envía POST /api/chat/stream
    │
    ▼
main.py::chat_stream()
    │  construye ChatRequest, llama a dispatcher.dispatch()
    │
    ▼
core/dispatcher.py::ModelDispatcher.dispatch()
    │  1. classifier.classify(input) → IntentType [< 1ms]
    │  2. Si CODE/LOGIC y modo CHAT → _handoff_to_code()
    │     ├─ narrative_handover.emit_and_inject()  → SSE HandoverEvent
    │     ├─ gpu_queue.submit(vram.switch_to_code_set)
    │     └─ _stream_agent("CODA", ...)
    │  3. Si CHAT → _stream_agent("ARCH-7", ...)
    │     ├─ _rebel_reaction() → REBx3 reacciona con contexto narrativo
    │     └─ _intruder_strike() (12% probabilístico)
    │
    ▼
core/gpu_queue.py::GPUQueue.submit()
    │  · Si GPU libre → ejecutar inmediatamente
    │  · Si GPU ocupada → encolar (no crash)
    │  · Si OOM → reencolar con backoff exponencial
    │  · Si cola llena → QueueFullError → HTTP 503
    │
    ▼
core/ollama.py::OllamaClient.stream()
    │  · POST /api/chat con stream=True
    │  · Yield tokens individuales
    │  · Retry con temperatura creciente si respuesta corrupta
    │
    ▼
SSE stream → Frontend (void_axiom.html)
    · Renderiza tokens en tiempo real
    · Muestra animación de handover al recibir {"type":"handover"}
    · Diferencia colores por agente con el campo "agent"
```

### 3. Handover Narrativo — el pase de batuta

El `NarrativeHandover` (core/handover.py) opera en 3 pasos:

1. **Extrae un fragmento** de la última respuesta del agente saliente
   (máx. 160 chars, descarta si detecta bleeding).
2. **Rellena una plantilla** según el tipo de handover:
   - `CHAT_TO_CODE`: "El análisis indica tarea de implementación. Fragmento: «…»"
   - `REBEL_REACT`: "Respuesta anterior para revisión crítica: «…»"
   - `INTRUDER_IN`: vacío (el Intruso no recibe contexto)
3. **Emite un `HandoverEvent`** como línea SSE que el frontend consume
   para mostrar la animación de transferencia.

El fragmento NO contiene instrucciones de sistema — la identidad del receptor
siempre viene de `agents/registry.py`, nunca del handover.

### 4. Cola GPU — sin crashes por OOM

`core/gpu_queue.py::GPUQueue`:

- **Un Semaphore(1)** garantiza una sola inferencia GPU a la vez.
- **Prioridades**: 0=Pioneer/admin, 1=plan_max, 2=plan_free.
  Dentro de la misma prioridad, FIFO por timestamp.
- **OOM retry**: si Ollama devuelve error de VRAM, el job se reencola
  hasta `MAX_OOM_RETRIES=3` veces con backoff `2^n` segundos.
- **Cola llena**: si hay > 32 jobs en espera, `QueueFullError` → HTTP 503.
  El servidor nunca cuelga esperando indefinidamente.

### 5. Variables de entorno clave

| Variable | Default | Descripción |
|----------|---------|-------------|
| `VOID_OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | URL de Ollama |
| `VOID_AGENT_NUM_CTX` | `16384` | Contexto por defecto |
| `VOID_AGENT_TEMPERATURE` | `0.25` | Temperatura base |
| `VOID_INTRUDER_PROBABILITY` | `0.12` | Frecuencia del Intruso |
| `VOID_MAX_RETRIES` | `3` | Reintentos ante respuesta corrupta |
| `VOID_RETRY_TEMP_JITTER` | `0.4` | Incremento de temp por reintento |
| `OLLAMA_MAX_LOADED_MODELS` | `2` | Máx. modelos en VRAM simultáneos |

---

## Migración desde el código existente

| Archivo antiguo | Destino |
|----------------|---------|
| `app/void_agents.py` | `agents/registry.py` + `agents/base.py` |
| `app/dispatcher.py` | `core/dispatcher.py` (refactorizado) |
| `app/intent_classifier.py` | `core/classifier.py` |
| `app/void_ollama.py` | `core/ollama.py` |
| `app/void_memory.py` | Pendiente → `core/memory.py` |
| `app/config.py` | Variables de entorno + `core/vram.py` |
| `app/auth.py` | `api/auth.py` (sin cambios) |
| `app/pioneers.py` | `api/pioneers.py` (sin cambios) |
| `app/main.py` | `main.py` (simplificado) |
| `training/*` | `training/*` (sin cambios) |

Los archivos `.bak` y el directorio `fixed/` pueden eliminarse —
el esqueleto maestro reemplaza esas versiones.
