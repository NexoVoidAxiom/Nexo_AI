"""
security.py — Seguridad integral para Analizador IA
=====================================================
Módulos incluidos:
  1. SecurityHeadersMiddleware  — Cabeceras HTTP (CSP, HSTS, X-Frame-Options…)
  2. RateLimiter                — Sliding window por IP y por usuario
  3. CsrfProtection             — Double-submit cookie (X-CSRF-Token)
  4. sanitize_input()           — Saneamiento de strings de entrada
  5. validate_username/email/password() — Validadores con errores claros

INTEGRACIÓN EN main.py (añadir justo después de crear `app`):
──────────────────────────────────────────────────────────────
    from app.security import SecurityHeadersMiddleware, RateLimiter
    app.add_middleware(SecurityHeadersMiddleware)
    rate_limiter = RateLimiter()
──────────────────────────────────────────────────────────────
"""

import asyncio
import hashlib
import re
import secrets
import time
from collections import defaultdict
from typing import Optional

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


# ══════════════════════════════════════════════════════════════════════════════
# 1. CABECERAS DE SEGURIDAD HTTP
# ══════════════════════════════════════════════════════════════════════════════

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Añade cabeceras de seguridad estándar a todas las respuestas HTTP.

    • Content-Security-Policy  — Restringe fuentes de scripts, estilos e iframes.
    • X-Frame-Options           — Previene clickjacking.
    • X-Content-Type-Options    — Desactiva MIME sniffing.
    • Referrer-Policy           — Limita información en el header Referer.
    • Permissions-Policy        — Deniega APIs del navegador no usadas.
    • Strict-Transport-Security — Fuerza HTTPS (activo si hay header X-Forwarded-Proto).
    • X-XSS-Protection          — Activar el filtro XSS del navegador (legacy).
    """

    # CSP adaptada a la app: todo cargado inline/mismo origen.
    # Si añades CDN externos (Stripe, etc.) agrega sus dominios aquí.
    _CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "   # unsafe-inline necesario para el <script> inline de index.html
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self';"
    )

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        response.headers["X-Frame-Options"]           = "DENY"
        response.headers["X-Content-Type-Options"]    = "nosniff"
        response.headers["X-XSS-Protection"]          = "1; mode=block"
        response.headers["Referrer-Policy"]           = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]        = (
            "camera=(), microphone=(), geolocation=(), "
            "payment=(), usb=(), bluetooth=()"
        )
        response.headers["Content-Security-Policy"]   = self._CSP

        # HSTS solo cuando la conexión llega por HTTPS (detrás de proxy/Cloudflare)
        proto = request.headers.get("X-Forwarded-Proto", "")
        if proto == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )

        return response


# ══════════════════════════════════════════════════════════════════════════════
# 2. RATE LIMITER — Sliding Window en memoria
# ══════════════════════════════════════════════════════════════════════════════

class RateLimiter:
    """
    Limitador de velocidad sliding-window en memoria.

    Diseñado para un único proceso (no distribuido). Si escalas a varios
    workers/procesos, migra el state a Redis.

    Uso:
        limiter = RateLimiter()

        # En un endpoint:
        await limiter.check(
            key=f"login:{client_ip}",
            limit=10,
            window=60,
            detail="Demasiados intentos de login. Espera 1 minuto."
        )
    """

    def __init__(self) -> None:
        self._windows: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def check(
        self,
        key: str,
        limit: int,
        window: int,
        detail: str = "Demasiadas peticiones. Inténtalo más tarde.",
    ) -> None:
        """
        Verifica si `key` ha superado `limit` peticiones en `window` segundos.
        Lanza HTTPException 429 si se supera el límite.
        """
        allowed = await self._is_allowed(key, limit, window)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=detail,
                headers={"Retry-After": str(window)},
            )

    async def _is_allowed(self, key: str, limit: int, window: int) -> bool:
        async with self._lock:
            now    = time.monotonic()
            cutoff = now - window
            # Limpiar entradas antiguas fuera de la ventana
            self._windows[key] = [t for t in self._windows[key] if t > cutoff]
            if len(self._windows[key]) >= limit:
                return False
            self._windows[key].append(now)
            return True

    async def cleanup(self) -> None:
        """Limpia entradas caducadas de todos los buckets. Llamar periódicamente."""
        async with self._lock:
            now = time.monotonic()
            self._windows = defaultdict(
                list,
                {k: [t for t in v if t > now - 3600] for k, v in self._windows.items() if v},
            )


# Instancia global compartida (importar en main.py y endpoints)
rate_limiter = RateLimiter()

# Límites predefinidos por categoría
LIMITS = {
    "auth":      {"limit": 10,  "window": 60},   # login/register: 10 intentos/min
    "api":       {"limit": 120, "window": 60},   # API general: 120 req/min por usuario
    "upload":    {"limit": 20,  "window": 60},   # Subida de archivos: 20/min
    "stream":    {"limit": 30,  "window": 60},   # Chat stream: 30/min
    "api_key":   {"limit": 5,   "window": 60},   # Crear/borrar API keys: 5/min
}


def get_client_ip(request: Request) -> str:
    """Obtiene la IP real del cliente, considerando proxies."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


# ══════════════════════════════════════════════════════════════════════════════
# 3. PROTECCIÓN CSRF — Double-Submit Cookie
# ══════════════════════════════════════════════════════════════════════════════

_CSRF_COOKIE  = "csrf_token"
_CSRF_HEADER  = "X-CSRF-Token"
_CSRF_EXEMPT_CONTENT_TYPES = {"application/json"}   # fetch() JSON ya está protegido por CORS
_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


class CsrfProtection:
    """
    Protección CSRF mediante double-submit cookie.

    Flujo:
      1. En cualquier GET, se genera y establece la cookie `csrf_token` (no httpOnly).
      2. El JS del frontend lee la cookie y la incluye en el header X-CSRF-Token
         en peticiones mutantes (POST/PUT/DELETE).
      3. El servidor verifica que header == cookie.

    Endpoints exentos:
      • Peticiones de solo lectura (GET, HEAD…).
      • Peticiones autenticadas con API Key (X-API-Key) — son scripts, no navegadores.
      • Content-Type: application/json (protegido por CORS preflight).

    INTEGRACIÓN EN main.py (helper para usar en endpoints sensibles):
    ──────────────────────────────────────────────────────────────────
        from app.security import csrf
        @app.post("/api/sensitive")
        async def sensitive(request: Request):
            await csrf.verify(request)
            ...
    ──────────────────────────────────────────────────────────────────

    INTEGRACIÓN EN index.html (añadir helper JS):
    ──────────────────────────────────────────────
        function getCsrfToken() {
            return document.cookie.split('; ')
                .find(r => r.startsWith('csrf_token='))
                ?.split('=')[1] ?? '';
        }
        // Incluir en cada fetch mutante:
        headers: { 'X-CSRF-Token': getCsrfToken(), ... }
    ──────────────────────────────────────────────
    """

    def generate_token(self) -> str:
        return secrets.token_hex(32)

    def _get_cookie_token(self, request: Request) -> Optional[str]:
        return request.cookies.get(_CSRF_COOKIE)

    def _get_header_token(self, request: Request) -> Optional[str]:
        return request.headers.get(_CSRF_HEADER)

    def _is_exempt(self, request: Request) -> bool:
        """Devuelve True si la petición está exenta de verificación CSRF."""
        # Métodos seguros
        if request.method in _CSRF_SAFE_METHODS:
            return True
        # Autenticado con API Key → script externo, no navegador
        if request.headers.get("X-API-Key"):
            return True
        # Content-Type JSON → protegido por CORS preflight
        ct = request.headers.get("content-type", "")
        if any(ct.startswith(exempt) for exempt in _CSRF_EXEMPT_CONTENT_TYPES):
            return True
        return False

    async def verify(self, request: Request) -> None:
        """
        Verifica el token CSRF. Lanza 403 si no coincide.
        Llamar al inicio de endpoints mutantes sensibles (formularios HTML).
        Para peticiones JSON desde el frontend actual, está exento automáticamente.
        """
        if self._is_exempt(request):
            return

        cookie_token  = self._get_cookie_token(request)
        header_token  = self._get_header_token(request)

        if not cookie_token or not header_token:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Token CSRF ausente.",
            )

        # Comparación segura contra timing attacks
        if not secrets.compare_digest(cookie_token, header_token):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Token CSRF inválido.",
            )

    def set_cookie(self, response: Response, request: Request) -> None:
        """
        Establece la cookie CSRF si no existe.
        Llamar desde endpoints GET que sirven HTML (/, /auth, /void, /generador).
        """
        if not request.cookies.get(_CSRF_COOKIE):
            response.set_cookie(
                key=_CSRF_COOKIE,
                value=self.generate_token(),
                max_age=86400 * 7,   # 7 días
                httponly=False,      # Debe ser legible por JS
                samesite="strict",
                secure=request.headers.get("X-Forwarded-Proto") == "https",
            )


# Instancia global
csrf = CsrfProtection()


# ══════════════════════════════════════════════════════════════════════════════
# 4. VALIDACIÓN Y SANEAMIENTO DE INPUTS
# ══════════════════════════════════════════════════════════════════════════════

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_\-\.]{3,32}$")
_EMAIL_RE    = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$",
    re.IGNORECASE,
)

# Patrones de inyección comunes (SQLi, XSS, path traversal básico)
_INJECTION_RE = re.compile(
    r"(--|;|'|\"|<script|<\/script|javascript:|data:|vbscript:|"
    r"union\s+select|drop\s+table|insert\s+into|"
    r"\.\./|\.\.\\)",
    re.IGNORECASE,
)


def sanitize_input(value: str, max_length: int = 500) -> str:
    """
    Sanea un string de entrada:
      • Elimina bytes nulos.
      • Elimina caracteres de control excepto \n y \t.
      • Recorta espacios en los extremos.
      • Trunca al máximo de longitud.

    No lanza excepciones; devuelve el valor limpio.
    """
    if not isinstance(value, str):
        return ""
    # Eliminar bytes nulos
    value = value.replace("\x00", "")
    # Eliminar caracteres de control (excepto \n, \r, \t)
    value = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    # Recortar
    value = value.strip()
    # Truncar
    return value[:max_length]


def check_for_injection(value: str, field_name: str = "campo") -> None:
    """
    Lanza HTTPException 400 si detecta patrones de inyección típicos.
    Usar en campos sensibles (username, email, etc.).
    """
    if _INJECTION_RE.search(value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El {field_name} contiene caracteres no permitidos.",
        )


def validate_username(username: str) -> str:
    """
    Valida y devuelve el username limpio.
    Lanza HTTPException 400 si no cumple los requisitos.
    """
    username = sanitize_input(username, max_length=32)
    if not username:
        raise HTTPException(400, "El nombre de usuario no puede estar vacío.")
    if len(username) < 3:
        raise HTTPException(400, "El nombre de usuario debe tener al menos 3 caracteres.")
    if not _USERNAME_RE.match(username):
        raise HTTPException(
            400,
            "El nombre de usuario solo puede contener letras, números, _, - y . "
            "(3–32 caracteres).",
        )
    check_for_injection(username, "nombre de usuario")
    return username


def validate_email(email: str) -> str:
    """
    Valida y devuelve el email en minúsculas.
    Lanza HTTPException 400 si el formato es inválido.
    """
    email = sanitize_input(email, max_length=254).lower()
    if not email:
        raise HTTPException(400, "El email no puede estar vacío.")
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, "Formato de email inválido.")
    check_for_injection(email, "email")
    return email


def validate_password(password: str) -> str:
    """
    Valida la contraseña.
    Lanza HTTPException 400 si no cumple los requisitos.
    """
    if not password:
        raise HTTPException(400, "La contraseña no puede estar vacía.")
    if len(password) < 8:
        raise HTTPException(400, "La contraseña debe tener al menos 8 caracteres.")
    if len(password) > 128:
        raise HTTPException(400, "La contraseña no puede superar los 128 caracteres.")
    if password.isdigit():
        raise HTTPException(400, "La contraseña no puede ser solo números.")
    if password.isalpha():
        raise HTTPException(400, "La contraseña debe incluir al menos un número o símbolo.")
    return password


def validate_api_key_name(name: str) -> str:
    """Valida el nombre descriptivo de una API key."""
    name = sanitize_input(name, max_length=64)
    if not name:
        raise HTTPException(400, "El nombre de la API key no puede estar vacío.")
    check_for_injection(name, "nombre de API key")
    return name
