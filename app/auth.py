"""
Autenticacion basada en usuarios con sesiones SQLite
=====================================================
Reemplaza el sistema de token unico por:
- Registro de usuarios (username + email + contrasena)
- Login con sesion persistente (cookie session_token, 30 dias)
- Middleware que protege todas las rutas HTML
- Dependencia FastAPI para proteger endpoints de API
"""
from fastapi import Request, HTTPException, Depends
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app import database as db


# ─── RUTAS QUE NO REQUIEREN AUTENTICACION ────────────────────────────────────
PUBLIC_PATHS = [
    "/auth",
    "/static/",
    "/health",
    "/api/auth/login",
    "/api/auth/register",
]


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware que redirige al login si el usuario no tiene sesion activa.

    Solo actua sobre rutas HTML (no API ni estáticos).
    Los endpoints de API usan la dependencia get_current_user().
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Rutas publicas: pasar sin verificar
        if any(path.startswith(p) for p in PUBLIC_PATHS):
            return await call_next(request)

        # Verificar sesion
        token = request.cookies.get("session_token")
        user = db.get_session_user(token)

        if user:
            # Inyectar usuario en el state de la request
            request.state.user = user
            return await call_next(request)

        # Sin sesion: redirigir al login (solo para peticiones HTML)
        accept = request.headers.get("accept", "")
        if "application/json" in accept or path.startswith("/api/"):
            raise HTTPException(status_code=401, detail="No autenticado. Inicia sesion.")

        return RedirectResponse(url="/auth")


# ─── DEPENDENCIA FASTAPI ──────────────────────────────────────────────────────

async def get_current_user(request: Request) -> dict:
    """Dependencia para endpoints de API que requieren autenticacion.

    Uso: user = Depends(get_current_user)
    """
    # Intentar desde request.state (inyectado por middleware)
    user = getattr(request.state, "user", None)
    if user:
        return user

    # Fallback: leer cookie directamente
    token = request.cookies.get("session_token")
    user = db.get_session_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado. Inicia sesion.")
    return user


# ─── PAGINA DE AUTH (LOGIN + REGISTRO) ───────────────────────────────────────

def get_auth_page() -> str:
    """Devuelve la pagina combinada de login y registro con tabs."""
    return """<!DOCTYPE html>
<html lang="es" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Acceso - Analizador IA</title>
    <style>
        :root {
            --bg: #0d1117; --card: #161b22; --border: #30363d;
            --text: #c9d1d9; --dim: #8b949e; --accent: #58a6ff;
            --accent-h: #79c0ff; --green: #3fb950; --red: #f85149;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg); color: var(--text);
            min-height: 100vh; display: flex; align-items: center; justify-content: center;
        }
        .card {
            background: var(--card); border: 1px solid var(--border);
            border-radius: 14px; padding: 40px; width: 100%; max-width: 420px;
        }
        .brand { text-align: center; margin-bottom: 28px; }
        .brand .icon { font-size: 40px; margin-bottom: 8px; }
        .brand h1 { font-size: 22px; color: var(--accent); }
        .brand p { font-size: 13px; color: var(--dim); margin-top: 4px; }
        .tabs { display: flex; border-bottom: 1px solid var(--border); margin-bottom: 24px; }
        .tab {
            flex: 1; padding: 10px; text-align: center; cursor: pointer;
            font-size: 14px; font-weight: 600; color: var(--dim);
            border-bottom: 2px solid transparent; transition: all 0.2s;
        }
        .tab.active { color: var(--accent); border-bottom-color: var(--accent); }
        .form { display: none; flex-direction: column; gap: 14px; }
        .form.active { display: flex; }
        .field { display: flex; flex-direction: column; gap: 5px; }
        .field label { font-size: 12px; color: var(--dim); font-weight: 600; }
        .field input {
            padding: 10px 14px; background: var(--bg); border: 1px solid var(--border);
            border-radius: 8px; color: var(--text); font-size: 14px; outline: none;
            transition: border-color 0.2s;
        }
        .field input:focus { border-color: var(--accent); }
        .btn {
            padding: 11px; background: var(--accent); color: #fff; border: none;
            border-radius: 8px; font-size: 14px; font-weight: 700; cursor: pointer;
            transition: background 0.2s; margin-top: 4px;
        }
        .btn:hover { background: var(--accent-h); }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .msg { font-size: 13px; padding: 10px 14px; border-radius: 8px; text-align: center; display: none; }
        .msg.err { background: rgba(248,81,73,0.15); color: var(--red); border: 1px solid rgba(248,81,73,0.3); }
        .msg.ok  { background: rgba(63,185,80,0.15);  color: var(--green); border: 1px solid rgba(63,185,80,0.3); }
        .hint { font-size: 12px; color: var(--dim); text-align: center; margin-top: 4px; }
    </style>
</head>
<body>
<div class="card">
    <div class="brand">
        <div class="icon">&#129302;</div>
        <h1>Analizador IA</h1>
        <p>GTX 1080 Ti &bull; Ollama Local</p>
    </div>

    <div class="tabs">
        <div class="tab active" id="tabLogin" onclick="switchTab('login')">Iniciar sesion</div>
        <div class="tab"       id="tabReg"   onclick="switchTab('register')">Registrarse</div>
    </div>

    <!-- LOGIN -->
    <form class="form active" id="formLogin" onsubmit="doLogin(event)">
        <div class="field">
            <label>Usuario o Email</label>
            <input type="text" id="lUser" placeholder="nombre o email@ejemplo.com" autofocus required>
        </div>
        <div class="field">
            <label>Contrasena</label>
            <input type="password" id="lPass" placeholder="••••••••" required>
        </div>
        <div class="msg" id="lMsg"></div>
        <button class="btn" type="submit" id="lBtn">Entrar</button>
        <p class="hint">&#128274; Sesion segura de 30 dias</p>
    </form>

    <!-- REGISTRO -->
    <form class="form" id="formReg" onsubmit="doRegister(event)">
        <div class="field">
            <label>Nombre de usuario</label>
            <input type="text" id="rUser" placeholder="min. 3 caracteres" required minlength="3">
        </div>
        <div class="field">
            <label>Email</label>
            <input type="email" id="rEmail" placeholder="tu@email.com" required>
        </div>
        <div class="field">
            <label>Contrasena</label>
            <input type="password" id="rPass" placeholder="min. 6 caracteres" required minlength="6">
        </div>
        <div class="field">
            <label>Repetir contrasena</label>
            <input type="password" id="rPass2" placeholder="••••••••" required>
        </div>
        <div class="msg" id="rMsg"></div>
        <button class="btn" type="submit" id="rBtn">Crear cuenta</button>
        <p class="hint">&#127381; Sin cuotas &bull; IA local privada</p>
    </form>
</div>

<script>
function switchTab(t) {
    document.getElementById('tabLogin').classList.toggle('active', t==='login');
    document.getElementById('tabReg').classList.toggle('active', t==='register');
    document.getElementById('formLogin').classList.toggle('active', t==='login');
    document.getElementById('formReg').classList.toggle('active', t==='register');
}

function showMsg(id, msg, type) {
    const el = document.getElementById(id);
    el.textContent = msg;
    el.className = 'msg ' + type;
    el.style.display = 'block';
}

async function doLogin(e) {
    e.preventDefault();
    const btn = document.getElementById('lBtn');
    btn.disabled = true; btn.textContent = 'Entrando...';
    try {
        const r = await fetch('/api/auth/login', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                username_or_email: document.getElementById('lUser').value,
                password: document.getElementById('lPass').value
            })
        });
        const d = await r.json();
        if (r.ok) {
            showMsg('lMsg', '&#9989; Bienvenido! Redirigiendo...', 'ok');
            setTimeout(() => window.location.href = '/', 800);
        } else {
            showMsg('lMsg', d.detail || 'Credenciales incorrectas', 'err');
            btn.disabled = false; btn.textContent = 'Entrar';
        }
    } catch {
        showMsg('lMsg', 'Error de conexion', 'err');
        btn.disabled = false; btn.textContent = 'Entrar';
    }
}

async function doRegister(e) {
    e.preventDefault();
    const pass = document.getElementById('rPass').value;
    const pass2 = document.getElementById('rPass2').value;
    if (pass !== pass2) { showMsg('rMsg', 'Las contrasenas no coinciden', 'err'); return; }

    const btn = document.getElementById('rBtn');
    btn.disabled = true; btn.textContent = 'Creando cuenta...';
    try {
        const r = await fetch('/api/auth/register', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                username: document.getElementById('rUser').value,
                email:    document.getElementById('rEmail').value,
                password: pass
            })
        });
        const d = await r.json();
        if (r.ok) {
            showMsg('rMsg', '&#9989; Cuenta creada! Iniciando sesion...', 'ok');
            setTimeout(() => window.location.href = '/', 1000);
        } else {
            showMsg('rMsg', d.detail || 'Error al crear cuenta', 'err');
            btn.disabled = false; btn.textContent = 'Crear cuenta';
        }
    } catch {
        showMsg('rMsg', 'Error de conexion', 'err');
        btn.disabled = false; btn.textContent = 'Crear cuenta';
    }
}
</script>
</body>
</html>"""