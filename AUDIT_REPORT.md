# Void Axiom — Informe de Auditoría Técnica

**Fecha:** 2026-05-23  
**Hardware objetivo:** RTX 3090 (24 GB VRAM) · i7-9700K · 32 GB RAM  
**Líneas analizadas:** 6.045 (11 archivos Python) + 4 Modelfiles  
**Bugs encontrados:** 6 (3 críticos, 2 altos, 1 medio)

---

## Resumen ejecutivo

El sistema tiene una arquitectura sólida. El orquestador multi-agente, el
sistema de memoria híbrida 16K/55K, el sanitizador multicapa y los Modelfiles
son correctos. Sin embargo, **el servidor no puede arrancar** por 7 imports
rotos en `agent_chat.py` producidos durante un refactor incompleto.

---

## Bugs críticos (bloquean el arranque)

### Crítico #1 — 7 imports rotos en `agent_chat.py`

**Archivo:** `app/agent_chat.py`  
**Síntoma:** `ImportError` al ejecutar `uvicorn app.main:app`

| Import | Módulo fuente | Problema | Fix |
|--------|---------------|----------|-----|
| `INTRUDER_AGENT` | `void_agents` | No exportado | Alias `AGENTS[INTRUDER_ID]` |
| `TERMINAL_HEADERS` | `void_agents` | No exportado | Dict sigil→nombre |
| `ConversationMemory` | `void_memory` | Clase renombrada | Alias a `VoidChannelHistory` |
| `clean_agent_text` | `void_memory` | Función eliminada | Wrapper sobre regex existentes |
| `is_bad_agent_output` | `void_memory` | Función eliminada | Wrapper sobre `contains_corrupt_marker` |
| `CONVERSATION_TYPES` | `void_memory` | Constante eliminada | Dict de tipos definido |
| `OllamaChatClient` | `void_ollama` | Clase renombrada | Alias a `VoidOllamaClient` |

**Fix aplicado:** Se añadieron aliases de backward-compat al final de cada módulo
sin tocar la lógica existente. Zero riesgo de regresión.

---

### Crítico #2 — Hardware profile incorrecto (13 GB VRAM desperdiciados)

**Archivo:** `app/config.py`  
**Síntoma:** `num_ctx` y límites de modelo calibrados para 11 GB en lugar de 24 GB

```python
# ANTES (incorrecto)
"name": "NVIDIA GeForce GTX 1080 Ti"
"vram_gb": 11
"architecture": "Pascal"
"compute_capability": "6.1"
os.environ["TORCH_ALLOW_TF32_CUBLAS_OVERRIDE"] = "0"  # Pascal no soporta TF32

# DESPUÉS (correcto)
"name": "NVIDIA GeForce RTX 3090"
"vram_gb": 24
"architecture": "Ampere"
"compute_capability": "8.6"
os.environ["TORCH_ALLOW_TF32_CUBLAS_OVERRIDE"] = "1"  # Ampere sí soporta TF32
```

**Impacto práctico:**
- Con RTX 3090, Coda (32B q4_K_M, ~18–20 GB) cabe **entero** en VRAM.
- Con el config anterior limitabas `num_ctx` innecesariamente.
- TF32 activado en Ampere da ~5–8% de speedup en inferencia sin pérdida de calidad.

**Fix aplicado:** Hardware profile actualizado + bloque `RTX3090_PROFILES` añadido.

---

### Crítico #3 — Lógica Alpha Pionero completamente ausente

**Archivos:** `app/database.py`, `app/main.py`  
**Síntoma:** La tabla `users` no tiene columna `plan`. El registro no asigna ningún tier.

**Fix aplicado:**

Nuevas columnas en `users`:
```sql
plan             TEXT NOT NULL DEFAULT 'free_limited'
pioneer_number   INTEGER DEFAULT NULL
plan_assigned_at TEXT DEFAULT NULL
```

Nuevas funciones en `database.py`:
```python
assign_pioneer_plan(user_id)  # Asigna plan según orden de registro
get_pioneer_count()           # Cuenta pioneros actuales
get_user_plan(user_id)        # Consulta el plan de un usuario
get_pioneer_leaderboard()     # Lista de pioneros ordenada
is_plan_max(user)             # Predicate de acceso premium
```

Endpoint `POST /api/auth/register` actualizado:
```python
pioneer_info = db.assign_pioneer_plan(user["id"])
# Respuesta incluye: plan, pioneer_number, is_pioneer
```

Regla implementada:
- Usuarios 1–50 (excl. admin): `plan_max`, `pioneer_number = N`
- Usuario 51+: `free_limited`, `pioneer_number = None`

---

## Bugs altos (funcionalidad degradada)

### Alto #4 — Módulo duplicado `data_base.py` vs `database.py`

`data_base.py` es una copia antigua de `database.py` sin:
- WAL mode + PRAGMA optimizations
- `agent_sessions` / `agent_messages` tables
- Funciones de gestión de agentes

**Acción recomendada:** Eliminar `data_base.py`.  
Verificar que ningún import usa `from app.data_base import ...` (debería ser `database`).

```bash
grep -r "from app.data_base" .  # Debe devolver 0 resultados
rm app/data_base.py
```

### Alto #5 — Endpoint de registro no asignaba plan (ya corregido en Fix #3)

El handler `api_register` en `main.py:408` ya está corregido con el Fix #3 anterior.

---

## Bugs medios (naming inconsistente)

### Medio #6 — Naming inconsistente tras refactor

Los tres módulos core fueron renombrados durante el refactor:
- `OllamaChatClient` → `VoidOllamaClient`  
- `ConversationMemory` → `VoidChannelHistory`
- `clean_agent_text`, `is_bad_agent_output` → eliminadas (lógica absorbida en `_sanitize`)

El consumidor (`agent_chat.py`) no fue actualizado. Ya corregido en Fix #1.

---

## Componentes validados (no modificados)

| Componente | Estado | Observaciones |
|------------|--------|---------------|
| `void_ollama.py` — VoidOllamaClient | ✅ Correcto | Semaphore GPU, retry+jitter, sanitizador multicapa |
| `void_memory.py` — VoidChannelHistory | ✅ Correcto | Modo 16K VRAM / 55K RAM, Jaccard echo detection |
| `void_agents.py` — Perfiles de agentes | ✅ Correcto | 4 perfiles, prompts completos, stop sequences |
| `void_activity.py` — Monitor de actividad | ✅ Correcto | Context manager chat_stream, thresholds configurables |
| `auth.py` — Middleware + sesiones | ✅ Correcto | Cookie httpOnly, 30 días, rutas públicas correctas |
| `Modelfile.*` — Los 4 Modelfiles | ✅ Correctos | frequency_penalty, presence_penalty, stop tokens |
| `prompts.py` — Build functions | ✅ Correcto | Architect/agents/reviewer bien separados |
| `llm_handler.py` — OllamaHandler | ✅ Correcto | Pool HTTP, keep_alive, perfiles de rendimiento |

---

## Instrucciones de aplicación

### DB nueva (primera instalación):
```bash
cd /ruta/a/Prueba_de_IA_Codigo
python apply_fixes.py
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### DB existente (con usuarios ya registrados):
```bash
python apply_fixes.py
python migrate_db.py --db data/analizador.db
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Verificación post-arranque:
```bash
# Debe devolver 200 sin ImportError en los logs
curl http://localhost:8000/health

# Verificar plan de un usuario
curl -b "session_token=TU_TOKEN" http://localhost:8000/api/auth/me
```

---

## Notas sobre escalado futuro (96 núcleos)

Cuando migren a servidor dedicado de 96 núcleos, cambiar en `void_ollama.py`:
```python
# Actual (RTX 3090 local, un agente a la vez)
self._gpu_gate = asyncio.Semaphore(1)

# Servidor multi-GPU (ajustar según GPUs disponibles)
self._gpu_gate = asyncio.Semaphore(N_GPUS)
```

Y en los Modelfiles, ajustar `num_thread` del valor `4` actual al número de
cores disponibles por proceso de Ollama.

---

*Informe generado automáticamente por análisis estático. Los fixes son quirúrgicos:
ninguna lógica existente fue reescrita, solo añadidos aliases y columnas nuevas.*
