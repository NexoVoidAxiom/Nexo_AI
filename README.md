# 🧠 Analizador de Datos con IA Local

**Plataforma web de Análisis de Datos potenciada por IA Local**
*Exprimida al máximo para **GTX 1080 Ti (11GB VRAM)** + **Intel i7-9700K** + **32GB RAM***

---

## 📋 Requisitos de Hardware

| Componente | Especificación | Optimización |
|-----------|---------------|--------------|
| **GPU** | NVIDIA GeForce GTX 1080 Ti (11GB VRAM, Pascal) | KV Cache en VRAM, sin Tensor Cores, FlashAttention |
| **CPU** | Intel Core i7-9700K (8C/8T, hasta 4.9 GHz) | 7 hilos para LLM, 1 libre para OS |
| **RAM** | 32 GB DDR4 | Pesos del modelo en RAM, GC explícito post-procesamiento |
| **Disco** | SSD recomendado (para carga rápida de datasets) | - |

---

## 🚀 Instalación Rápida

### 1. Instalar Ollama

```bash
# Descargar e instalar Ollama desde: https://ollama.com/download/windows
# O con winget (PowerShell como Admin):
winget install Ollama.Ollama
```

### 2. Preparar modelos Void Axiom

```bash
# Recomendado para el canal multi-agente:
scripts\init_void_axiom_ollama.bat

# Bases usadas por los Modelfiles:
ollama pull qwen2.5:3b
ollama pull qwen2.5-coder:3b
ollama pull qwen2.5:1.5b
```

### 3. Configurar Ollama para GTX 1080 Ti

El lanzador de Void Axiom detecta la GTX 1080 Ti y arranca Ollama aislado con estas variables:

```bash
CUDA_VISIBLE_DEVICES=<ID de la GTX 1080 Ti>
OLLAMA_NUM_PARALLEL=1
OLLAMA_MAX_LOADED_MODELS=4
OLLAMA_KEEP_ALIVE=-1
OLLAMA_CONTEXT_LENGTH=2048
```

**Variables clave explicadas:**
- `CUDA_VISIBLE_DEVICES` → expone solo la GTX 1080 Ti al proceso de Ollama
- `OLLAMA_NUM_PARALLEL=1` → una inferencia activa por modelo; evita multiplicar KV cache
- `OLLAMA_MAX_LOADED_MODELS=4` → mantiene ARCH-7, CODA, REBx3 y "..." residentes
- `OLLAMA_KEEP_ALIVE=-1` → no descarga los modelos entre turnos

### 4. Instalar dependencias del proyecto

```bash
# Navegar al directorio del proyecto
cd Prueba\ de\ IA\ Codigo

# Instalar dependencias Python
pip install -r requirements.txt
```

### 5. Iniciar la aplicación

```bash
# Primero, asegúrate que Ollama esté corriendo (debería estar como servicio de Windows)
# Luego, inicia el servidor web:
python -m app.main
```

La web estará disponible en: **http://localhost:8080**

---

## ⚙️ Arquitectura de Hardware y Optimizaciones

### ARQUITECTURA INVERTIDA (Pesos en RAM, KV Cache en VRAM)

```
┌─────────────────────────────────────────────────────┐
│                    32 GB RAM                         │
│  ┌──────────────────────────────────────────────┐   │
│  │  Perfiles Void Axiom Qwen 3B/1.5B en 4-bit   │   │
│  │  ≈ 8-9 GB                                    │   │
│  └──────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────┐   │
│  │  Python / FastAPI / Pandas / DataFrames      │   │
│  │  ≈ 2-4 GB                                    │   │
│  └──────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────┐   │
│  │  Windows / Sistema / Otros                    │   │
│  │  ≈ 4-6 GB                                    │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│                  11 GB VRAM (GTX 1080 Ti)            │
│  ┌──────────────────────────────────────────────┐   │
│  │  KV Cache (Contexto masivo ~65k tokens)      │   │
│  │  BLOQUEADO en VRAM para inferencia rápida     │   │
│  │  ≈ 8-9 GB                                    │   │
│  └──────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────┐   │
│  │  Fragmentos del modelo (capas activas)       │   │
│  │  ≈ 2-3 GB                                    │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

### Optimizaciones específicas para GTX 1080 Ti (Pascal)

| Optimización | Descripción |
|-------------|-------------|
| **num_thread=7** | Usa 7 de 8 hilos CPU. Deja 1 hilo libre para evitar stuttering del sistema |
| **FlashAttention** | Activado por defecto en Ollama ≥ 0.3.x. Reduce uso de VRAM en atención |
| **num_batch=512** | Batch reducido para contexto masivo (evita OOM) |
| **num_ctx=65536** | Contexto de ~65k tokens (seguro para 11GB VRAM) |
| **GC explícito** | `gc.collect()` después de cada carga de datos y cada inferencia |
| **CUDA empty_cache** | Libera caché CUDA después de cada operación pesada |
| **Sin TensorFloat-32** | Desactivado porque Pascal no lo soporta nativamente |

### Modelos recomendados para 11GB VRAM

| Modelo | Params | VRAM usada | Contexto seguro |
|--------|--------|-----------|----------------|
| **void-arch7 / void-rebx3** | 3B | ~2-3 GB c/u | 2048 tokens |
| **void-coda** | 3B coder | ~2-3 GB | 2048 tokens |
| **void-intruder** | 1.5B | ~1-2 GB | 1536 tokens |
| **qwen2.5:7b** | 7B | ~5-6 GB | ~80k+ tokens |
| **llama3.1:8b** | 8B | ~5-6 GB | ~80k+ tokens |
| **mistral:7b** | 7B | ~4-5 GB | ~100k tokens |
| **deepseek-r1:8b** | 8B | ~6-7 GB | ~65k tokens |
| **phi3:14b** | 14B | ~9-10 GB | ~32k tokens (justo) |

---

## 🌐 Despliegue con Cloudflare Tunnel (cloudflared)

### ¿Por qué Cloudflare Tunnel?

- ✅ **Sin abrir puertos en el router** — La conexión es saliente, no entrante
- ✅ **SSL/HTTPS automático** — Cifrado de extremo a extremo
- ✅ **Subdominio gratuito** — `*.trycloudflare.com`
- ✅ **Protección DDoS** — Filtro de Cloudflare incluido
- ✅ **Acceso desde cualquier lugar** — Móvil, otra PC, tablet

### Paso 1: Instalar cloudflared

#### En Windows (opción 1 - manual):
1. Descarga el ejecutable: https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
2. Renómbralo a `cloudflared.exe`
3. Muévelo a `C:\Windows\System32\` (para acceso global)
4. Verifica: abre CMD y escribe `cloudflared --version`

#### En Windows (opción 2 - winget):
```powershell
winget install cloudflare.cloudflared
```

### Paso 2: Iniciar el túnel

Asegúrate de que tu app web esté corriendo en `http://localhost:8080`.

Luego, abre un **nuevo terminal** (el de la app debe seguir corriendo) y ejecuta:

```bash
cloudflared tunnel --url http://localhost:8080
```

**Output esperado:**
```
Your quick Tunnel has been created! Visit it at:
https://tunel-aleatorio-1234.trycloudflare.com
```

¡Esa URL es tu dominio público! Puedes acceder desde cualquier dispositivo con internet.

### Paso 3: Acceder desde el móvil u otra PC

1. Abre el navegador en tu móvil/tablet/otra PC
2. Ingresa la URL que te dio cloudflared (ej: `https://tunel-aleatorio-1234.trycloudflare.com`)
3. Verás la página de login de tu app
4. Ingresa el token de autenticación (por defecto: `mi-analisis-ia-2024`)
5. ¡Listo! Ya puedes usar tu analizador de datos desde cualquier lugar

### Paso 4 (Opcional): Túnel persistente con dominio propio

Si tienes un dominio propio en Cloudflare:

```bash
# 1. Autenticarte (solo una vez)
cloudflared tunnel login

# 2. Crear un túnel con nombre
cloudflared tunnel create mi-analisis-ia

# 3. Crear archivo de configuración
```

Crea `config.yml`:
```yaml
tunnel: mi-analisis-ia
credentials-file: C:\Users\34645\.cloudflared\mi-analisis-ia.json

ingress:
  - hostname: analisis.tudominio.com
    service: http://localhost:8080
  - service: http_status:404
```

```bash
# 4. Configurar DNS
cloudflared tunnel route dns mi-analisis-ia analisis.tudominio.com

# 5. Iniciar el túnel como servicio
cloudflared tunnel run mi-analisis-ia
```

### Paso 5: Iniciar como servicio de Windows (para que arranque solo)

#### Servicio para cloudflared:
```powershell
# Como servicio de Windows (arranque automático)
cloudflared service install
```

#### Script para la app web (crea `iniciar-servidor.bat`):
```batch
@echo off
cd /d "C:\Users\34645\Desktop\Prueba de IA Codigo"
python -m app.main
pause
```

Y programa esa tarea en el **Programador de Tareas de Windows** para que inicie con el sistema.

---

## 🔒 Seguridad

### Autenticación
La app incluye un sistema de login ultra-básico con token. **CAMBIA EL TOKEN POR DEFECTO**:

```bash
# En Windows (PowerShell):
$env:AUTH_TOKEN="tu-contraseña-muy-segura-123"

# O configúralo como variable de sistema permanente:
[System.Environment]::SetEnvironmentVariable("AUTH_TOKEN","tu-contraseña-muy-segura-123","User")
```

### Recomendaciones de seguridad
1. ✅ Cambia el token por defecto (`mi-analisis-ia-2024`)
2. ✅ Usa HTTPS (Cloudflare Tunnel lo provee automáticamente)
3. ✅ El túnel es conexión saliente: no abre puertos en tu router
4. ⚠️ Cloudflare Tunnel gratuito expone tu IP a Cloudflare, no al público
5. ⚠️ `trycloudflare.com` es temporal (cada vez que reinicias el túnel cambia la URL)

---

## 🎮 Uso de la Aplicación

### Subir datos
1. **Drag & Drop**: Arrastra un archivo CSV, JSON, JSONL, TXT, LOG o TSV al área indicada
2. **Click**: Haz clic en el área de upload para seleccionar archivo
3. **Pegar texto**: Usa el área de texto para pegar datos directamente

### Análisis
1. Después de cargar los datos, verás las estadísticas (tokens, filas, columnas)
2. Puedes activar "Contexto masivo" para datasets grandes (>10k tokens)
3. Escribe tu pregunta en el chat o usa los análisis rápidos predefinidos
4. La respuesta se muestra en tiempo real (streaming)

### Monitoreo
- **Barra de tokens**: Muestra el uso del contexto disponible
- **Indicador de estado**: Muestra si Ollama está conectado
- **Botón "Limpiar"**: Reinicia el estado y libera memoria

---

## 🛠️ Solución de Problemas

### Error: "Ollama no está disponible"
```bash
# Verifica que Ollama está corriendo
ollama list

# Asegúrate que el modelo existe
ollama pull qwen2.5:3b

# Verifica el puerto
curl http://localhost:11434/api/tags
```

### Error: CUDA Out of Memory (OOM)
```bash
# Reduce el contexto
# En app/config.py, cambia:
# "num_ctx": 65536 → "num_ctx": 32768
# "num_batch": 512 → "num_batch": 256

# O usa un modelo más ligero:
ollama pull qwen2.5:7b
# Y cambia en config.py: "model": "qwen2.5:7b"
```

### Error: La web va lenta o se congela
- Verifica que `num_thread=7` esté configurado (deja 1 hilo para el OS)
- Monitorea el uso de RAM/VRAM con el Monitor de Recursos de Windows
- Reduce el contexto si es necesario
- El GC automático libera RAM cada 5 minutos

### Error: Cloudflare Tunnel no funciona
```bash
# Prueba que la app funciona localmente primero
curl http://localhost:8080/health

# Verifica que no hay firewall bloqueando
# Reinstala cloudflared
cloudflared tunnel --url http://localhost:8080 --no-autoupdate
```

---

## 📊 Endpoints de la API

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/` | Página principal (HTML) |
| `GET` | `/login` | Página de login (HTML) |
| `GET` | `/health` | Healthcheck del servidor |
| `POST` | `/api/auth/login` | Login con token |
| `GET` | `/api/models` | Lista modelos de Ollama |
| `POST` | `/api/upload` | Subir archivo (multipart) |
| `POST` | `/api/upload/text` | Subir texto directo |
| `GET` | `/api/data/status` | Estado de los datos cargados |
| `POST` | `/api/chat/stream` | Chat con streaming (SSE) |
| `POST` | `/api/chat` | Chat sin streaming |
| `POST` | `/api/gc` | Forzar recolección de basura |
| `POST` | `/api/estimate-tokens` | Estimar tokens de un texto |

---

## 🔄 Flujo de Trabajo Típico

```
1. Inicias Ollama (servicio de Windows)
2. Inicias la app web: python -m app.main
3. Abres http://localhost:8080
4. Arrastras un CSV de ventas
5. El sistema estima ~15,000 tokens
6. Activas "Contexto masivo" si el dataset es grande
7. Preguntas: "¿Cuáles fueron los productos más vendidos por región?"
8. La IA analiza los datos en contexto y responde con streaming
9. Preguntas: "¿Hay alguna anomalía en los datos de este trimestre?"
10. Apagas todo cuando terminas
```

---

## 📝 Notas Técnicas

- **El login es ultra-básico**: una contraseña hardcodeada o variable de entorno. No es un sistema de autenticación empresarial.
- **Cloudflare Tunnel gratuito** (`trycloudflare.com`) genera URLs temporales. Cada vez que reinicias el túnel, cambia la URL.
- **Para uso continuo**: configura un túnel persistente con un dominio propio en Cloudflare.
- **La estimación de tokens** es aproximada (~3.5 caracteres/token para español). Los tokens reales pueden variar según el modelo.
- **Si usas contexto masivo (65k tokens)**, la primera inferencia será más lenta mientras se llena el KV Cache en VRAM.

---

## 🏆 Rendimiento Esperado (GTX 1080 Ti + i7-9700K)

| Operación | Tiempo Estimado |
|-----------|-----------------|
| Carga de CSV (10,000 filas, 20 columnas) | < 1 segundo |
| Procesamiento y compresión | 0.5-2 segundos |
| Inferencia (modelo 14B, 512 tokens de salida) | 8-15 segundos |
| Inferencia con contexto masivo (65k tokens) | 20-40 segundos |
| Velocidad de generación | 15-25 tokens/segundo |

---

## 📄 Licencia

Uso personal y educativo. Construido con amor para la comunidad de IA Local.

---

*Creado para una **GTX 1080 Ti (11GB)** + **i7-9700K** + **32GB RAM** — porque el hardware legacy también merece IA de última generación.*
