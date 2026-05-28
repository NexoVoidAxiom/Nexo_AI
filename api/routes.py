"""
api/routes.py — Routers adicionales de la API de Void Axiom.
============================================================
Se incluye en main.py como:
    from void_axiom.api.routes import router as api_router
    app.include_router(api_router, prefix="/api")
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from void_axiom.core.dispatcher import dispatcher
from void_axiom.core.gpu_queue import gpu_queue
from void_axiom.agents.registry import AGENTS, PUBLIC_AGENT_ORDER

router = APIRouter()


# ── Agentes ────────────────────────────────────────────────────────────────────

@router.get("/agents")
async def list_agents():
    """Lista los agentes disponibles con sus metadatos públicos."""
    return [
        {
            "agent_id": agent_id,
            "display":  agent.display,
            "sigil":    agent.sigil,
            "color":    agent.color,
        }
        for agent_id, agent in AGENTS.items()
    ]


# ── Dispatcher ─────────────────────────────────────────────────────────────────

@router.get("/dispatcher/status")
async def dispatcher_status():
    return await dispatcher.status()


@router.get("/dispatcher/queue")
async def queue_status():
    return {
        "queue_length": gpu_queue.queue_length,
        "gpu_busy":     gpu_queue.is_busy,
    }


# ── Debug / intent ─────────────────────────────────────────────────────────────

class IntentDebugRequest(BaseModel):
    text: str

@router.post("/debug/intent")
async def debug_intent(req: IntentDebugRequest):
    """Devuelve el intent clasificado para un texto dado (sin inferencia)."""
    from void_axiom.core.classifier import IntentClassifier
    clf = IntentClassifier()
    intent = clf.classify(req.text)
    return {"intent": intent.name, "text": req.text}
