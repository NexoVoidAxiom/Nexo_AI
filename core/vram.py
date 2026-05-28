"""
core/vram.py — Gestor de VRAM para Void Axiom.
===============================================
Hardware target: GTX 1080 Ti (11 GB VRAM)

MAPA DE VRAM (11 GB total)
───────────────────────────────────────────────────────────────
  ARCH-7A  │ qwen2.5-coder:7b   │ ≈  4.5 GB  │ primario
  ARCH-7B  │ qwen2.5-coder:7b   │ ≈  4.5 GB  │ swap con ARCH-7A
  Coda     │ qwen3-coder:14b    │ ≈  8.5 GB  │
  REBx3    │ qwen2.5-coder:3b   │ ≈  2.0 GB  │
  Intruder │ qwen2.5-coder:3b   │ ≈  2.0 GB  │

SETS ACTIVOS
───────────────────────────────────────────────────────────────
  CHAT_SET  │ ARCH-7A + REBx3 + Intruder  │ ≈  8.5 GB ✓
  CODE_SET  │ Coda + Intruder              │ ≈ 10.5 GB ✓
  ARCH-7B se carga bajo demanda, desalojando ARCH-7A (mismo tamaño).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Iterable

import httpx

from void_axiom.agents.registry import AGENTS, INTRUDER_ID

log = logging.getLogger("void.vram")

VRAM_BUDGET_GB: float = 11.0

MODEL_VRAM_GB: dict[str, float] = {
    "arch7a":   4.5,
    "arch7b":   4.5,   # mismo tamaño que arch7a — swap 1:1
    "coda":     8.5,
    "rebx3":    2.0,
    "intruder": 2.0,
}

_MODEL_KEY_MAP: dict[str, str] = {
    "ARCH-7A":   "arch7a",
    "ARCH-7B":   "arch7b",
    "CODA":      "coda",
    "REBx3":     "rebx3",
    INTRUDER_ID: "intruder",
}

# ARCH-7B no está en CHAT_SET: se carga bajo demanda con swap arch7a↔arch7b
CHAT_SET = frozenset({"arch7a", "rebx3", "intruder"})
CODE_SET  = frozenset({"coda", "intruder"})

MODEL_LOAD_TIMEOUT   = 45.0
MODEL_UNLOAD_TIMEOUT = 10.0


class VRAMManager:
    def __init__(self, base_url: str = "http://127.0.0.1:11434") -> None:
        self._base  = base_url.rstrip("/")
        self._lock  = asyncio.Lock()
        self.loaded: set[str] = set()

    @property
    def vram_used_gb(self) -> float:
        return sum(MODEL_VRAM_GB.get(k, 0) for k in self.loaded)

    @property
    def vram_free_gb(self) -> float:
        return VRAM_BUDGET_GB - self.vram_used_gb

    # ── VRAM_BUDGET_GB accesible como propiedad de instancia ──────────────
    @property
    def VRAM_BUDGET_GB(self) -> float:
        return VRAM_BUDGET_GB

    async def warm_model(self, model_key: str, model_name: str) -> bool:
        async with httpx.AsyncClient(timeout=MODEL_LOAD_TIMEOUT) as client:
            try:
                resp = await client.post(
                    f"{self._base}/api/generate",
                    json={"model": model_name, "prompt": "", "keep_alive": -1,
                          "stream": False, "options": {"num_predict": 1, "num_gpu": 99}},
                )
                if resp.status_code == 200:
                    self.loaded.add(model_key)
                    log.info("Modelo cargado: %s (%.1f GB | libre: %.1f GB)",
                             model_name, MODEL_VRAM_GB.get(model_key, 0), self.vram_free_gb)
                    return True
                log.warning("Ollama %d al cargar %s", resp.status_code, model_name)
                return False
            except Exception as exc:
                log.error("Error cargando %s: %s", model_name, exc)
                return False

    async def evict_model(self, model_key: str, model_name: str) -> bool:
        async with httpx.AsyncClient(timeout=MODEL_UNLOAD_TIMEOUT) as client:
            try:
                await client.post(
                    f"{self._base}/api/generate",
                    json={"model": model_name, "prompt": "", "keep_alive": 0,
                          "stream": False, "options": {"num_predict": 1}},
                )
                self.loaded.discard(model_key)
                log.info("Modelo descargado: %s", model_name)
                return True
            except Exception as exc:
                log.warning("Error descargando %s: %s", model_name, exc)
                self.loaded.discard(model_key)
                return False

    async def warm_set(self, model_keys: Iterable[str]) -> None:
        tasks = []
        for key in model_keys:
            agent_id = {v: k for k, v in _MODEL_KEY_MAP.items()}.get(key, key.upper())
            model_name = AGENTS.get(agent_id, {}).get("model", key) if isinstance(
                AGENTS.get(agent_id), dict) else getattr(AGENTS.get(agent_id), "model", key)
            tasks.append(self.warm_model(key, model_name))
        await asyncio.gather(*tasks, return_exceptions=True)

    async def swap_arch7(self, to: str) -> None:
        """
        Alterna entre ARCH-7A y ARCH-7B.
        'to' debe ser 'arch7a' o 'arch7b'.
        Como tienen el mismo tamaño (4.5 GB), el swap es 1:1 en VRAM.
        """
        evict_key  = "arch7b" if to == "arch7a" else "arch7a"
        evict_id   = "ARCH-7B" if to == "arch7a" else "ARCH-7A"
        load_id    = "ARCH-7A" if to == "arch7a" else "ARCH-7B"

        if evict_key in self.loaded:
            await self.evict_model(evict_key, AGENTS[evict_id].model)
        await self.warm_model(to, AGENTS[load_id].model)

    async def switch_to_code_set(self) -> float:
        """Descarga CHAT_SET activo y carga CODA. Retorna VRAM liberada."""
        freed = 0.0
        evict_tasks = []
        for key, agent_id in [("arch7a", "ARCH-7A"), ("arch7b", "ARCH-7B"), ("rebx3", "REBx3")]:
            if key in self.loaded:
                freed += MODEL_VRAM_GB.get(key, 0)
                evict_tasks.append(self.evict_model(key, AGENTS[agent_id].model))
        if evict_tasks:
            await asyncio.gather(*evict_tasks)
        await self.warm_model("coda", AGENTS["CODA"].model)
        return freed

    async def restore_chat_set(self) -> None:
        """Descarga CODA y restaura CHAT_SET con ARCH-7A como primario."""
        await self.evict_model("coda", AGENTS["CODA"].model)
        await asyncio.gather(
            self.warm_model("arch7a", AGENTS["ARCH-7A"].model),
            self.warm_model("rebx3",  AGENTS["REBx3"].model),
        )
        log.info("CHAT_SET restaurado. VRAM usada: %.1f GB", self.vram_used_gb)
