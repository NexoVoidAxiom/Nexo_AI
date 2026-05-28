"""
pioneers.py — Lógica Alpha_Pionero + API de Donaciones y Escalado
==================================================================
Se integra con el database.py y main.py existentes del proyecto.

REGLA ALPHA_PIONERO:
  - Los primeros 50 usuarios registrados (excl. admin) → plan_max GRATIS
  - Del usuario 51 en adelante → plan_free_limitado
  - El número de pionero es permanente e intransferible

PLANES:
  plan_max          → Sin límites de mensajes, acceso a todos los modelos,
                      máxima prioridad de GPU, badge Pioneer #N
  plan_free_limitado → 20 mensajes/día, solo modelo rápido, sin archivos >1MB

ENDPOINTS NUEVOS (para añadir a main.py):
  GET  /api/pioneers/status        → estado del programa pionero
  GET  /api/pioneers/leaderboard   → tabla de pioneros
  GET  /api/user/plan              → plan del usuario actual
  POST /api/donations/intent       → crear intención de donación
  GET  /api/donations/tiers        → tiers de donación disponibles
  GET  /api/admin/scale-readiness  → estado para migración a servidor 96-core

INSTALACIÓN:
  # Añadir a requirements.txt:
  stripe>=8.0.0    # para donaciones con tarjeta (opcional)
  
  # En main.py añadir:
  from app.pioneers import router as pioneers_router
  app.include_router(pioneers_router)
"""

import logging
import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import Optional, Annotated

from fastapi import APIRouter, Depends, HTTPException, Header, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field

# Importar desde el database.py existente del proyecto
try:
    from app.database import (
        get_db,
        get_user_by_token,
        get_pioneer_count,
        assign_pioneer_plan,
        get_user_plan,
        get_pioneer_leaderboard,
        is_plan_max,
        PLAN_MAX,
        PLAN_FREE,
        PIONEER_LIMIT,
    )
    DB_AVAILABLE = True
except ImportError:
    # Fallback si se usa de forma standalone
    DB_AVAILABLE = False
    PLAN_MAX     = "plan_max"
    PLAN_FREE    = "plan_free_limitado"
    PIONEER_LIMIT = 50

log = logging.getLogger("pioneers")
router = APIRouter(prefix="/api", tags=["Pioneros & Planes"])


# ══════════════════════════════════════════════════════════════════════════════
# MODELOS PYDANTIC
# ══════════════════════════════════════════════════════════════════════════════

class PioneerStatus(BaseModel):
    total_pioneers:     int
    slots_remaining:    int
    pioneer_limit:      int     = PIONEER_LIMIT
    program_active:     bool
    message:            str


class UserPlanInfo(BaseModel):
    user_id:            int
    username:           str
    plan:               str
    pioneer_number:     Optional[int]
    is_pioneer:         bool
    plan_assigned_at:   Optional[str]
    limits:             dict


class DonationTier(BaseModel):
    id:         str
    name:       str
    amount_eur: float
    perks:      list[str]
    badge:      str


class DonationIntent(BaseModel):
    tier_id:    str
    message:    Optional[str] = Field(None, max_length=500)


class ScaleReadinessReport(BaseModel):
    current_hardware:   dict
    target_hardware:    dict
    users_total:        int
    pioneers_count:     int
    estimated_load:     dict
    migration_checklist: list[dict]
    ready_for_migration: bool


# ══════════════════════════════════════════════════════════════════════════════
# LÍMITES POR PLAN
# ══════════════════════════════════════════════════════════════════════════════

PLAN_LIMITS = {
    PLAN_MAX: {
        "messages_per_day":     -1,          # sin límite
        "max_file_size_mb":     100,
        "models_allowed":       ["all"],
        "gpu_priority":         "high",
        "context_tokens":       30000,
        "can_upload_files":     True,
        "can_use_agents":       True,
        "api_access":           True,
        "description":          "Plan Pionero MAX — Sin límites, acceso completo.",
    },
    PLAN_FREE: {
        "messages_per_day":     20,
        "max_file_size_mb":     1,
        "models_allowed":       ["fast"],    # solo perfil 'fast' del config.py
        "gpu_priority":         "normal",
        "context_tokens":       20000,
        "can_upload_files":     False,
        "can_use_agents":       False,
        "api_access":           False,
        "description":          "Plan Free Limitado — 20 mensajes/día, funcionalidad básica.",
    },
}


def get_plan_limits(plan: str) -> dict:
    return PLAN_LIMITS.get(plan, PLAN_LIMITS[PLAN_FREE])


# ══════════════════════════════════════════════════════════════════════════════
# RATE LIMITING POR PLAN (integración con el sistema existente)
# ══════════════════════════════════════════════════════════════════════════════

def check_daily_message_limit(user_id: int, plan: str) -> tuple[bool, int, int]:
    """
    Verifica si el usuario puede enviar otro mensaje hoy.
    Retorna (allowed: bool, used: int, limit: int)
    """
    limits = get_plan_limits(plan)
    max_messages = limits["messages_per_day"]

    if max_messages == -1:
        return True, 0, -1  # plan_max: sin límite

    if not DB_AVAILABLE:
        return True, 0, max_messages

    today_str = date.today().isoformat()
    with get_db() as conn:
        # Contar mensajes del usuario HOY
        row = conn.execute(
            """
            SELECT COUNT(*) as cnt
            FROM chat_messages cm
            JOIN chats c ON c.id = cm.chat_id
            WHERE c.user_id = ?
              AND cm.role = 'user'
              AND DATE(cm.created_at) = ?
            """,
            (user_id, today_str),
        ).fetchone()
        used = row["cnt"] if row else 0

    allowed = used < max_messages
    return allowed, used, max_messages


# ══════════════════════════════════════════════════════════════════════════════
# AUTENTICACIÓN (reutiliza el sistema existente)
# ══════════════════════════════════════════════════════════════════════════════

async def get_current_user_optional(
    request: Request,
    authorization: Annotated[Optional[str], Header()] = None,
) -> Optional[dict]:
    """Extrae usuario del token (Authorization: Bearer O cookie session_token).

    El sistema principal usa cookie session_token; este fallback garantiza
    compatibilidad sin necesidad de cambiar el frontend.
    """
    if not DB_AVAILABLE:
        return None

    # 1) Header Authorization: Bearer <token>  (clientes API / Postman)
    if authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "").strip()
        if token:
            user = get_user_by_token(token)
            if user:
                return user

    # 2) Cookie session_token  (frontend web — sistema de auth principal)
    token = request.cookies.get("session_token")
    if token:
        try:
            from app import database as db
            return db.get_session_user(token)
        except Exception:
            pass

    return None


async def require_current_user(
    user: Annotated[Optional[dict], Depends(get_current_user_optional)],
) -> dict:
    """Requiere usuario autenticado. Lanza 401 si no hay sesión."""
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión requerida. Inicia sesión primero.",
        )
    return user


async def require_admin(
    user: Annotated[dict, Depends(require_current_user)],
) -> dict:
    """Requiere rol de administrador."""
    if user.get("username") != "admin" and not user.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado. Se requieren permisos de administrador.",
        )
    return user


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS — PROGRAMA PIONERO
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/pioneers/status", response_model=PioneerStatus)
async def get_pioneer_program_status():
    """
    Estado actual del programa Alpha_Pionero.
    Público — sin autenticación requerida.
    """
    if DB_AVAILABLE:
        count = get_pioneer_count()
    else:
        count = 0

    remaining    = max(0, PIONEER_LIMIT - count)
    active       = remaining > 0

    if active:
        msg = (
            f"🚀 El programa Alpha_Pionero está ACTIVO. "
            f"Quedan {remaining} de {PIONEER_LIMIT} plazas gratuitas de Plan MAX. "
            f"¡Regístrate ahora para asegurar tu plaza!"
        )
    else:
        msg = (
            f"El programa Alpha_Pionero está COMPLETO. "
            f"Los {PIONEER_LIMIT} pioneros ya han sido asignados. "
            f"Nuevos usuarios acceden con Plan Free Limitado."
        )

    return PioneerStatus(
        total_pioneers  = count,
        slots_remaining = remaining,
        program_active  = active,
        message         = msg,
    )


@router.get("/pioneers/leaderboard")
async def get_pioneers_leaderboard(limit: int = 50):
    """
    Lista pública de pioneros (sin emails ni datos sensibles).
    """
    if not DB_AVAILABLE:
        return {"pioneers": [], "total": 0}

    pioneers = get_pioneer_leaderboard(limit=limit)
    # Sanitizar: devolver solo datos públicos
    public_list = [
        {
            "pioneer_number": p["pioneer_number"],
            "username":       p["username"],
            "joined_at":      p["created_at"][:10] if p.get("created_at") else None,
            "badge":          f"🏆 Pioneer #{p['pioneer_number']}",
        }
        for p in pioneers if p.get("pioneer_number")
    ]
    return {"pioneers": public_list, "total": len(public_list)}


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS — PLAN DE USUARIO
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/user/plan", response_model=UserPlanInfo)
async def get_my_plan(
    user: Annotated[dict, Depends(require_current_user)],
):
    """Devuelve el plan y los límites del usuario autenticado."""
    if DB_AVAILABLE:
        plan_data = get_user_plan(user["id"])
    else:
        plan_data = {"plan": PLAN_FREE, "pioneer_number": None, "is_pioneer": False}

    plan    = plan_data.get("plan", PLAN_FREE)
    limits  = get_plan_limits(plan)

    # Añadir uso actual del día si está en plan limitado
    if plan == PLAN_FREE:
        allowed, used, max_msgs = check_daily_message_limit(user["id"], plan)
        limits = {**limits, "messages_used_today": used, "messages_remaining_today": max(0, max_msgs - used)}

    return UserPlanInfo(
        user_id         = user["id"],
        username        = user["username"],
        plan            = plan,
        pioneer_number  = plan_data.get("pioneer_number"),
        is_pioneer      = plan_data.get("is_pioneer", False),
        plan_assigned_at= plan_data.get("plan_assigned_at"),
        limits          = limits,
    )


@router.get("/user/can-message")
async def can_send_message(
    user: Annotated[dict, Depends(require_current_user)],
):
    """
    Verificación rápida: ¿puede el usuario enviar otro mensaje ahora?
    Usar desde el frontend antes de cada envío en plan free.
    """
    if DB_AVAILABLE:
        plan_data = get_user_plan(user["id"])
        plan = plan_data.get("plan", PLAN_FREE)
    else:
        plan = PLAN_FREE

    allowed, used, limit = check_daily_message_limit(user["id"], plan)
    return {
        "allowed":    allowed,
        "used_today": used,
        "limit":      limit,
        "plan":       plan,
        "message":    "" if allowed else f"Has alcanzado el límite de {limit} mensajes diarios. ¡Vuelve mañana!",
    }


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS — DONACIONES
# ══════════════════════════════════════════════════════════════════════════════

DONATION_TIERS = [
    DonationTier(
        id="cafe",
        name="☕ Café",
        amount_eur=3.0,
        perks=["Gracias en el Discord", "Badge 'Supporter' en tu perfil"],
        badge="☕",
    ),
    DonationTier(
        id="colaborador",
        name="🤝 Colaborador",
        amount_eur=10.0,
        perks=[
            "Todo lo de Café",
            "Acceso anticipado a nuevas funciones",
            "Tu nombre en el README del proyecto",
        ],
        badge="🤝",
    ),
    DonationTier(
        id="patron",
        name="⭐ Patrón",
        amount_eur=25.0,
        perks=[
            "Todo lo de Colaborador",
            "Prioridad GPU durante 1 mes",
            "Canal privado de Discord para feedback directo",
            "Voto en el roadmap de desarrollo",
        ],
        badge="⭐",
    ),
    DonationTier(
        id="fundador",
        name="🏛️ Fundador",
        amount_eur=100.0,
        perks=[
            "Todo lo de Patrón",
            "Acceso permanente a Plan MAX aunque seas usuario >50",
            "Tu nombre en los créditos del modelo",
            "Reunión mensual de 30min con el equipo",
            "Acceso a servidor 96-core cuando se active",
        ],
        badge="🏛️",
    ),
]


@router.get("/donations/tiers")
async def get_donation_tiers():
    """Lista los tiers de donación disponibles."""
    return {
        "tiers": [t.dict() for t in DONATION_TIERS],
        "payment_note": (
            "Las donaciones se procesan a través de PayPal o transferencia bancaria. "
            "Contacta con el administrador para coordinar el pago."
        ),
        "impact": (
            "Las donaciones financian el servidor de 96 núcleos que permitirá "
            "dar Plan MAX a más usuarios gratuitamente."
        ),
    }


@router.post("/donations/intent")
async def create_donation_intent(
    intent: DonationIntent,
    user: Annotated[dict, Depends(require_current_user)],
):
    """
    Registra la intención de donación del usuario.
    En producción, aquí se crearía una sesión de Stripe o PayPal.
    """
    tier = next((t for t in DONATION_TIERS if t.id == intent.tier_id), None)
    if not tier:
        raise HTTPException(status_code=400, detail=f"Tier '{intent.tier_id}' no existe.")

    # Log de la intención (en producción: crear pago en Stripe/PayPal)
    log.info(f"Intención de donación: user={user['username']} tier={tier.id} amount={tier.amount_eur}€")

    # Guardar en base de datos si está disponible
    if DB_AVAILABLE:
        with get_db() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS donation_intents (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL,
                    tier_id     TEXT    NOT NULL,
                    amount_eur  REAL    NOT NULL,
                    message     TEXT,
                    status      TEXT    DEFAULT 'pending',
                    created_at  TEXT    DEFAULT (datetime('now'))
                )
                """,
            )
            conn.execute(
                "INSERT INTO donation_intents (user_id, tier_id, amount_eur, message) VALUES (?,?,?,?)",
                (user["id"], tier.id, tier.amount_eur, intent.message),
            )

    return {
        "status":       "intent_registered",
        "tier":         tier.dict(),
        "user":         user["username"],
        "instructions": (
            "Tu intención de donación ha sido registrada. "
            "El administrador te contactará para coordinar el pago. "
            "Una vez confirmado, tus beneficios serán activados automáticamente."
        ),
        "contact": "Puedes escribir a admin@voidaxiom.local o en el canal Discord #donaciones",
    }


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS — ADMIN: PREPARACIÓN PARA ESCALADO
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/admin/scale-readiness", response_model=ScaleReadinessReport)
async def get_scale_readiness(
    admin: Annotated[dict, Depends(require_admin)],
):
    """
    Informe de preparación para migrar al servidor dedicado de 96 núcleos.
    Solo accesible por administradores.
    """
    # Stats actuales de la DB
    users_total = pioneers_count = active_sessions = 0
    if DB_AVAILABLE:
        with get_db() as conn:
            users_total     = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            pioneers_count  = conn.execute(
                "SELECT COUNT(*) FROM users WHERE pioneer_number IS NOT NULL"
            ).fetchone()[0]
            active_sessions = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE expires_at > datetime('now')"
            ).fetchone()[0]

    checklist = [
        {"item": "Base de datos exportada a PostgreSQL",          "status": "pending", "priority": "high"},
        {"item": "Variables de entorno documentadas en .env.prod","status": "pending", "priority": "high"},
        {"item": "Nginx configurado como reverse proxy",           "status": "pending", "priority": "high"},
        {"item": "SSL/TLS con Let's Encrypt",                      "status": "pending", "priority": "high"},
        {"item": "Modelo GGUF copiado al servidor destino",        "status": "pending", "priority": "high"},
        {"item": "Ollama instalado en servidor 96-core",           "status": "pending", "priority": "medium"},
        {"item": "Load balancer para múltiples instancias Ollama", "status": "pending", "priority": "medium"},
        {"item": "Redis para caché de sesiones distribuidas",      "status": "pending", "priority": "medium"},
        {"item": "Monitoreo con Prometheus + Grafana",             "status": "pending", "priority": "low"},
        {"item": "Backups automáticos diarios",                    "status": "pending", "priority": "medium"},
        {"item": "CI/CD pipeline configurado",                     "status": "pending", "priority": "low"},
        {"item": "Tests de carga superados (>100 usuarios concurrentes)", "status": "pending", "priority": "high"},
    ]

    return ScaleReadinessReport(
        current_hardware={
            "cpu":      "Intel i7-9700K (8C/8T)",
            "ram_gb":   32,
            "gpu":      "RTX 3090 (24 GB VRAM)",
            "storage":  "Local SSD",
            "network":  "Cloudflare Tunnel / ngrok",
        },
        target_hardware={
            "cpu":      "Servidor dedicado 96 núcleos",
            "ram_gb":   256,
            "gpu":      "A100/H100 o multi-GPU",
            "storage":  "NVMe RAID + object storage",
            "network":  "IP dedicada + dominio propio",
            "os":       "Ubuntu 24.04 LTS",
        },
        users_total         = users_total,
        pioneers_count      = pioneers_count,
        estimated_load={
            "current_concurrent_users": active_sessions,
            "target_concurrent_users":  500,
            "estimated_gpu_required":   "2× A100 80GB o 4× RTX 4090",
            "estimated_monthly_cost_eur": 800,
        },
        migration_checklist = checklist,
        ready_for_migration = False,  # se actualizará manualmente
    )


@router.post("/admin/pioneers/manual-assign")
async def manual_pioneer_assign(
    user_id: int,
    admin: Annotated[dict, Depends(require_admin)],
):
    """
    Asigna manualmente plan_max a un usuario (para fundadores/donadores).
    Solo admin.
    """
    if not DB_AVAILABLE:
        return {"error": "DB no disponible"}

    with get_db() as conn:
        user = conn.execute("SELECT id, username FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail=f"Usuario {user_id} no encontrado.")
        conn.execute(
            "UPDATE users SET plan=?, plan_assigned_at=? WHERE id=?",
            (PLAN_MAX, datetime.now().isoformat(), user_id),
        )

    log.info(f"Admin {admin['username']} asignó plan_max manualmente a user_id={user_id}")
    return {
        "success":  True,
        "user_id":  user_id,
        "username": dict(user)["username"],
        "plan":     PLAN_MAX,
        "assigned_by": admin["username"],
    }


# ══════════════════════════════════════════════════════════════════════════════
# MIDDLEWARE: VERIFICAR LÍMITE ANTES DE PROCESAR CHAT
# ══════════════════════════════════════════════════════════════════════════════

async def enforce_plan_limits(user_id: int, plan: str) -> None:
    """
    Lanza HTTPException si el usuario ha superado su límite diario.
    Llamar desde los endpoints de chat antes de procesar el mensaje.
    """
    allowed, used, limit = check_daily_message_limit(user_id, plan)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error":    "daily_limit_reached",
                "message":  f"Has alcanzado tu límite de {limit} mensajes diarios.",
                "used":     used,
                "limit":    limit,
                "resets_at": "00:00 UTC",
                "upgrade":  "Regístrate antes del usuario #50 para obtener Plan MAX gratis.",
            },
        )


# ══════════════════════════════════════════════════════════════════════════════
# INTEGRACIÓN CON main.py — INSTRUCCIONES
# ══════════════════════════════════════════════════════════════════════════════

INTEGRATION_INSTRUCTIONS = """
Para integrar pioneers.py en el main.py existente:

1. Copiar este archivo a: app/pioneers.py

2. En app/main.py, añadir después de los imports existentes:
   from app.pioneers import router as pioneers_router, enforce_plan_limits, get_user_plan

3. Registrar el router (después de crear la app FastAPI):
   app.include_router(pioneers_router)

4. En el endpoint /api/chat o equivalente, añadir verificación:
   # Al inicio del handler de chat:
   plan_data = get_user_plan(current_user["id"])
   await enforce_plan_limits(current_user["id"], plan_data["plan"])

5. En app/database.py asegurarse de que existe PIONEER_LIMIT = 50
   (ya está definido según el código revisado)

6. Añadir a requirements.txt (opcional para pagos reales):
   stripe>=8.0.0
"""

if __name__ == "__main__":
    print(INTEGRATION_INSTRUCTIONS)