"""
api_keys_router.py — Gestión de API Keys para acceso programático
=================================================================
Permite a los usuarios generar claves para llamar a la app desde
scripts, curl, n8n, etc., sin usar la sesión del navegador.

INTEGRACIÓN EN main.py (añadir junto al resto de routers):
──────────────────────────────────────────────────────────
    from app.api_keys_router import router as api_keys_router
    app.include_router(api_keys_router)
──────────────────────────────────────────────────────────

EJEMPLOS DE USO EXTERNO:
──────────────────────────────────────────────────────────
    # Listar chats
    curl https://tu-dominio/api/chats \\
         -H "X-API-Key: ak_a1b2c3..."

    # Enviar mensaje
    curl -X POST https://tu-dominio/api/chats/1/stream \\
         -H "X-API-Key: ak_a1b2c3..." \\
         -H "Content-Type: application/json" \\
         -d '{"prompt": "Hola!", "free_chat": true}'

    # Python
    import requests
    s = requests.Session()
    s.headers["X-API-Key"] = "ak_a1b2c3..."
    r = s.get("https://tu-dominio/api/chats")
──────────────────────────────────────────────────────────
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app import database as db
from app.security import rate_limiter, get_client_ip, validate_api_key_name, LIMITS

router = APIRouter(prefix="/api/keys", tags=["API Keys"])


# ─── Modelos ──────────────────────────────────────────────────────────────────

class CreateKeyRequest(BaseModel):
    name: str = Field(
        default="Mi API Key",
        min_length=1,
        max_length=64,
        description="Nombre descriptivo para identificar la key.",
    )


class ApiKeyPublic(BaseModel):
    """Representación pública de una API key (sin el valor real ni el hash)."""
    id:           int
    key_prefix:   str
    name:         str
    created_at:   str
    last_used_at: str | None = None


class ApiKeyCreated(ApiKeyPublic):
    """Respuesta al crear una key: incluye el valor real UNA sola vez."""
    key: str


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("", response_model=list[ApiKeyPublic], summary="Listar mis API keys")
async def list_keys(user: dict = Depends(get_current_user)):
    """
    Devuelve todas las API keys activas del usuario autenticado.
    El valor real de la key nunca se devuelve aquí; solo el prefijo y metadatos.
    """
    return db.list_user_api_keys(user["id"])


@router.post("", response_model=ApiKeyCreated, status_code=201, summary="Crear API key")
async def create_key(
    data: CreateKeyRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """
    Genera una nueva API key para el usuario.

    ⚠️ El valor completo de la key (`key`) se devuelve **solo en esta respuesta**.
       Guárdala en un lugar seguro; no podrás volver a verla.

    Límites:
      - Máximo 10 keys activas por usuario.
      - Rate limit: 5 creaciones/minuto.
    """
    ip = get_client_ip(request)
    await rate_limiter.check(
        key=f"api_key_create:{ip}:{user['id']}",
        limit=LIMITS["api_key"]["limit"],
        window=LIMITS["api_key"]["window"],
        detail="Demasiadas keys creadas recientemente. Espera un minuto.",
    )

    name = validate_api_key_name(data.name)
    key_data = db.create_api_key(user["id"], name)
    return key_data


@router.delete("/{key_id}", status_code=200, summary="Revocar API key")
async def revoke_key(
    key_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """
    Revoca (desactiva permanentemente) una API key por su ID.
    Solo puedes revocar tus propias keys.
    """
    ip = get_client_ip(request)
    await rate_limiter.check(
        key=f"api_key_revoke:{ip}:{user['id']}",
        limit=LIMITS["api_key"]["limit"],
        window=LIMITS["api_key"]["window"],
        detail="Demasiadas operaciones recientes. Espera un minuto.",
    )

    revoked = db.revoke_api_key(key_id, user["id"])
    if not revoked:
        raise HTTPException(
            status_code=404,
            detail="API key no encontrada o no pertenece a tu cuenta.",
        )
    return {"status": "ok", "message": f"API key #{key_id} revocada correctamente."}


@router.delete("", status_code=200, summary="Revocar todas mis API keys")
async def revoke_all_keys(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Revoca todas las API keys activas del usuario de una sola vez."""
    ip = get_client_ip(request)
    await rate_limiter.check(
        key=f"api_key_revoke_all:{ip}:{user['id']}",
        limit=3,
        window=60,
        detail="Operación demasiado frecuente. Espera un minuto.",
    )

    count = db.revoke_all_user_api_keys(user["id"])
    return {"status": "ok", "revoked": count}
