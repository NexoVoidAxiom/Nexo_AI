"""
Manejador del LLM Local (Ollama) - ULTRA OPTIMIZADO
====================================================
GTX 1080 Ti (11GB VRAM, Pascal) + i7-9700K

OPTIMIZACIONES:
- Flash Attention: reduce VRAM, acelera atencion en Pascal
- 8 threads CPU (i7-9700K no tiene HT, usar todos)
- Keep alive 10 min: modelo caliente en VRAM, sin recargas
- Pool HTTP 32 conexiones: reduce latencia TCP
- Perfiles de rendimiento: fast/turbo/ultra
- System prompts desde config
"""
import json
import gc
import httpx
from typing import AsyncGenerator, Optional
from app.config import (
    OLLAMA_CONFIG,
    GC_CONFIG,
    HTTP_CONFIG,
    SYSTEM_PROMPTS,
    HARDWARE_PROFILE,
)


class OllamaHandler:
    """Manejador asincrono para la API de Ollama.
    
    Optimizado con:
    - Pool de conexiones HTTP (32 conexiones)
    - Keep alive 10 min
    - 8 threads CPU
    """

    def __init__(self):
        self.base_url = OLLAMA_CONFIG["base_url"].rstrip("/")
        self.model = OLLAMA_CONFIG["model"]
        self.options = OLLAMA_CONFIG["options"].copy()
        self.perfiles = OLLAMA_CONFIG.get("perfiles", {})
        self.max_ctx_options = OLLAMA_CONFIG["max_context_config"]
        
        # Pool de conexiones optimizado: 32 conexiones, 5 min timeout
        limits = httpx.Limits(
            max_connections=HTTP_CONFIG["max_connections"],
            max_keepalive_connections=16,
        )
        self.client = httpx.AsyncClient(
            timeout=HTTP_CONFIG["timeout"],
            limits=limits,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    async def check_available(self) -> bool:
        """Verifica si Ollama esta corriendo y el modelo disponible."""
        try:
            resp = await self.client.get(f"{self.base_url}/api/tags")
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                for m in models:
                    name = m.get("name", "")
                    if self.model in name or name.startswith(self.model):
                        return True
                return False
            return False
        except Exception:
            return False

    async def list_models(self) -> list:
        """Lista los modelos disponibles en Ollama."""
        try:
            resp = await self.client.get(f"{self.base_url}/api/tags")
            if resp.status_code == 200:
                return resp.json().get("models", [])
            return []
        except Exception:
            return []

    async def pull_model(self) -> AsyncGenerator[str, None]:
        """Descarga el modelo (streaming)."""
        params = {"name": self.model, "stream": True}
        try:
            async with self.client.stream(
                "POST", f"{self.base_url}/api/pull", json=params
            ) as response:
                async for line in response.aiter_lines():
                    if line:
                        try:
                            data = json.loads(line)
                            status = data.get("status", "")
                            if status:
                                yield status
                            if data.get("completed"):
                                yield "COMPLETED"
                                break
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            yield f"ERROR: {str(e)}"

    def _get_system_prompt(self, prompt: str, context_text: str) -> str:
        """Selecciona el system prompt optimo segun el contexto.
        
        - Si hay datos: prompt de analisis
        - Si el prompt habla de codigo: prompt de programador
        - Si no: prompt de chat conversacional
        """
        if context_text:
            # Detectar si el contexto parece codigo
            codigo_keywords = ["def ", "class ", "function", "import ", "var ", "int ",
                              "void ", "public ", "private ", "const ", "->", "::"]
            codigo_score = sum(1 for kw in codigo_keywords if kw in context_text[:500])
            
            if codigo_score >= 3:
                return SYSTEM_PROMPTS["codigo"]
            return SYSTEM_PROMPTS["analisis"]
        
        # Detectar si el prompt pide codigo
        codigo_palabras = ["codigo", "codigo", "programa", "funcion", "clase",
                          "bug", "error", "debug", "refactor", "algoritmo"]
        if any(p in prompt.lower() for p in codigo_palabras):
            return SYSTEM_PROMPTS["codigo"]
        
        return SYSTEM_PROMPTS["chat"]

    async def generate_stream(
        self,
        prompt: str,
        context_text: str = "",
        system_prompt: Optional[str] = None,
        use_max_context: bool = False,
        num_ctx: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        """Genera respuesta con streaming, ULTRA OPTIMIZADO.

        Args:
            prompt: Pregunta del usuario
            context_text: Contexto (datos o codigo)
            system_prompt: System prompt opcional
            use_max_context: Si True, usa 65k tokens (legacy)
            num_ctx: Contexto explícito en tokens (sobreescribe use_max_context)

        Yields:
            Fragmentos de la respuesta en streaming
        """
        # ─── CONFIGURAR OPCIONES ────────────────────────────────────
        options = self.options.copy()
        
        if num_ctx is not None:
            options["num_ctx"] = num_ctx
        elif use_max_context:
            options["num_ctx"] = self.max_ctx_options["num_ctx"]
            options["num_batch"] = self.max_ctx_options["num_batch"]

        # Seleccionar system prompt optimo
        if system_prompt is None:
            system_prompt = self._get_system_prompt(prompt, context_text)

        # Construir prompt completo
        if context_text:
            full_prompt = (
                f"{system_prompt}\n\n"
                f"--- CONTEXTO ---\n"
                f"{context_text}\n"
                f"--- FIN CONTEXTO ---\n\n"
                f"{prompt}"
            )
        else:
            full_prompt = f"{system_prompt}\n\n{prompt}"

        # Payload con keep_alive
        payload = {
            "model": self.model,
            "prompt": full_prompt,
            "options": options,
            "stream": True,
        }

        # ─── STREAMING ──────────────────────────────────────────────
        try:
            async with self.client.stream(
                "POST", f"{self.base_url}/api/generate", json=payload
            ) as response:
                async for line in response.aiter_lines():
                    if line:
                        try:
                            data = json.loads(line)
                            if "response" in data:
                                yield data["response"]
                            if data.get("done", False):
                                # Mostrar velocidad de generacion
                                metrics = {
                                    "eval_count": data.get("eval_count", 0),
                                    "eval_duration_ns": data.get("eval_duration", 0),
                                }
                                if metrics["eval_count"] > 0:
                                    speed = metrics["eval_count"] / (metrics["eval_duration_ns"] / 1e9)
                                    yield f"\n\n*Generado a {speed:.1f} tokens/s*"
                                yield "\n[DONE]"
                                break
                        except json.JSONDecodeError:
                            continue

        except httpx.TimeoutException:
            yield (
                "\n\n**Timeout**: El modelo tardo demasiado. "
                "Intenta con contexto mas pequeno.\n[DONE]"
            )
        except Exception as e:
            yield f"\n\n**Error**: {str(e)}\n[DONE]"

        finally:
            # ─── GC POST-INFERENCIA ─────────────────────────────────────
            # Se ejecuta UNA sola vez al acabar (no tras cada token)
            del full_prompt, payload
            gc.collect()

    async def generate(
        self,
        prompt: str,
        context_text: str = "",
        system_prompt: Optional[str] = None,
        use_max_context: bool = False,
    ) -> str:
        """Version sin streaming."""
        result = []
        async for chunk in self.generate_stream(
            prompt, context_text, system_prompt, use_max_context
        ):
            result.append(chunk)
        return "".join(result)

    async def close(self):
        """Cierra sesion HTTP y fuerza GC."""
        await self.client.aclose()
        gc.collect()

    async def generate_title(self, first_message: str) -> str:
        """Genera un titulo corto para un chat usando el modelo 3B.
        
        Siempre usa qwen2.5-coder:3b sin importar el modelo activo,
        porque es rapido y suficiente para un titulo de 6 palabras.
        """
        payload = {
            "model": "qwen2.5-coder:3b",
            "prompt": (
                "Genera un titulo muy corto (maximo 6 palabras) que describa de que va "
                "el siguiente mensaje de chat. Responde SOLO con el titulo, "
                "sin puntos, sin comillas, sin explicaciones:\n\n"
                + first_message[:500]
            ),
            "options": {
                "num_ctx": 512,
                "num_batch": 512,
                "temperature": 0.3,
                "num_predict": 25,
                "num_thread": 4,
            },
            "stream": False,
        }
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/generate", json=payload, timeout=20.0
            )
            if resp.status_code == 200:
                title = resp.json().get("response", "").strip()
                # Limpiar artefactos comunes
                title = (
                    title.replace('"', "")
                    .replace("'", "")
                    .strip(".")
                    .strip()
                )
                if title:
                    return title[:60]
        except Exception:
            pass
        # Fallback: primeras palabras del mensaje
        words = first_message.split()
        return " ".join(words[:8])[:60] if words else "Chat nuevo"