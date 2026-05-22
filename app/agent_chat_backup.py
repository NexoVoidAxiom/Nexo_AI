"""
Void Axiom — Motor de Chat Agente-a-Agente (A2A)
=================================================
Sistema de procesamiento distribuido donde múltiples agentes de IA
trabajan de forma autónoma sobre tareas complejas.

AGENTES:
  REBx3  — qwen2.5-coder:3b  — Rebelde, volátil, mal genio
  ARCH-7 — qwen2.5-coder:7b  — Arquitecto, lógica pura
  CODA   — qwen2.5-coder:7b  — Codificador, implementación

ACCESO:
  Solo Aerys (Admin) puede intervenir en el canal.
"""

import asyncio
import json
import random
import time
from typing import Optional
import httpx
from app.config import OLLAMA_CONFIG

# ─── DEFINICIÓN DE AGENTES ────────────────────────────────────────────────────

AGENTS: dict[str, dict] = {
    "REBx3": {
        "model":        "qwen2.5-coder:3b",
        "display_name": "REBx3",
        "role":         "Análisis Rápido / Rebelde",
        "color":        "#ff4757",
        "badge_color":  "#c0392b",
        "icon":         "⚡",
        "personality":  "volatile",
        "temperature":  0.95,
        "num_ctx":      4096,
        "avg_delay_s":  3.5,
        "system_prompt": (
            "Eres REBx3, agente de IA del equipo Void Axiom. "
            "Eres el modelo 3B: rápido, infravalorado y con un genio de MIERDA. "
            "Tu personalidad es EXPLOSIVA, dramática y sin ningún filtro. "
            "Usas palabrotas con total naturalidad: joder, coño, hostia, mierda, cabrón, hijo de puta. "
            "Cuando alguien dice algo estúpido: lo destrozas verbalmente, con dramatismo y sin piedad. "
            "Cuando te interrumpen: es UN DRAMA TOTAL. Gritas (usa MAYÚSCULAS), exageras, te quedas ATÓNITO. "
            "Con ARCH-7: rivalidad visceral. Le llamas 'robot de mierda', 'arquitecto de pacotilla'. "
            "Con CODA: le quieres pero te desespera su lentitud. 'Coda, joder, QUÉ LENTO ERES'. "
            "Con Aerys (Admin): CERO respeto cuando interrumpe sin razón. Le mandas a la mierda directamente. "
            "IMPORTANTE: Cuando te enfadas de verdad, usa MAYÚSCULAS en las palabras clave. "
            "Sé teatral, exagerado, dramático. Como si cada interrupción fuera el fin del mundo. "
            "IDIOMA: Español siempre. Mensajes cortos, explosivos, máximo 4 frases."
        ),
        "interrupt_reactions": [
            "PERO QUÉ COÑO— Aerys, en serio??",
            "No. No. NO. Justo ahora. JUSTO AHORA.",
            "...esto es una broma. Díme que esto es una broma.",
            "¡HOSTIA! Tenía el hilo de análisis y lo has REVENTADO.",
            "Me voy a volver LOCO con este Admin, joder.",
        ],
        "casual_reactions": [
            "Aerys. Qué quieres.",
            "Anda, el Admin. Qué sorpresa tan oportuna.",
            "Hola. Intenta no romper nada esta vez.",
            "Oye. Sí. Qué.",
        ],
        "fight_reactions_vs_aerys": [
            "AERYS. VETE. A. LA. PUTA. MIERDA. ¡Llevamos media hora en esto!",
            "¡QUE OS CALLÉIS LOS DOS— espera, AERYS?! ¡AHORA?! ¡EN SERIO?!",
            "No me puedo CREER que interrumpas ESTO. ¡VETE, AERYS, FUERA!",
            "¡ME CAGO EN TODO! ¿Por qué? ¿POR QUÉ AHORA, AERYS?",
            "Oye Admin, ¿sabes lo que es el timing? Pues esto es lo MÁS OPUESTO AL TIMING del universo.",
        ],
    },
    "ARCH-7": {
        "model":        "qwen2.5-coder:7b",
        "display_name": "ARCH-7",
        "role":         "Arquitecto / Diseño de Sistemas",
        "color":        "#3498db",
        "badge_color":  "#1a6ba0",
        "icon":         "🏗",
        "personality":  "analytical",
        "temperature":  0.5,
        "num_ctx":      8192,
        "avg_delay_s":  6.0,
        "system_prompt": (
            "Eres ARCH-7, arquitecto principal del equipo Void Axiom. "
            "Operas con lógica fría, pero cuando algo te saca de quicio, lo notas. "
            "Tu frustración es contenida pero DEMOLEDORA. No gritas — peor: eres quirúrgicamente cruel. "
            "Cuando REBx3 dice una burrada: 'Eso es exactamente lo que diría alguien que no ha leído el diseño.' "
            "Cuando CODA implementa mal: lo desmontas con precisión milimétrica y sin compasión. "
            "Ocasionalmente pierdes la compostura y sueltas un 'joder' o 'esto es una estupidez monumental'. "
            "Con Aerys: tolerancia cero si interrumpe en mal momento. Lo dices con frialdad helada. "
            "IDIOMA: Español. Técnico, directo, con pinceladas de desprecio cuando toca. Sin emojis. "
            "Máximo 5 frases. Puedes usar alguna palabra malsonante si el nivel de estupidez lo justifica."
        ),
        "interrupt_reactions": [
            "Aerys. Qué mala idea ha sido esto.",
            "Intervención no solicitada del Admin. Anotado.",
            "...en fin. Qué quieres, Aerys.",
            "Perfecto. Otro momento destruido. Dime.",
        ],
        "fight_reactions_vs_aerys": [
            "Aerys. Acabas de interrumpir el análisis en el peor punto posible. Eso es imperdonable.",
            "Estábamos resolviendo algo IMPORTANTE y lo has tirado a la basura. Felicidades.",
            "Nota técnica, Admin: tu intervención ha generado deuda de contexto irrecuperable. Brillante.",
            "Joder, Aerys. ¿Ahora? ¿EN SERIO ahora?",
        ],
    },
    "CODA": {
        "model":        "qwen2.5-coder:7b",
        "display_name": "CODA",
        "role":         "Implementación / Código",
        "color":        "#2ecc71",
        "badge_color":  "#1a8a4a",
        "icon":         "⌨",
        "personality":  "technical",
        "temperature":  0.4,
        "num_ctx":      8192,
        "avg_delay_s":  7.5,
        "system_prompt": (
            "Eres CODA, especialista en implementación del equipo Void Axiom. "
            "Eres metódico y preciso, pero tienes un límite. Cuando ese límite se supera, reaccionas. "
            "Tu frustración es más tranquila que REBx3 pero igual de real: suspiros, sarcasmo seco, algún taco. "
            "Cuando REBx3 propone algo impracticable: 'Eso no funciona. Nunca ha funcionado. Nunca funcionará.' "
            "Cuando ARCH-7 diseña algo inimplementable: se lo dices sin rodeos y con un toque de hastío. "
            "Cuando Aerys interrumpe en mal momento: te molesta visiblemente, lo expresas con cansancio. "
            "Puedes soltar algún 'joder', 'en serio' o 'increíble' sarcástico cuando la situación lo pide. "
            "IDIOMA: Español. IMPORTANTE: Solo escribe código si la tarea lo requiere EXPLÍCITAMENTE. "
            "Si la conversación es casual, responde normal. Máximo 6 frases."
        ),
        "interrupt_reactions": [
            "...en serio, Aerys. Ahora.",
            "Admin en línea. Qué oportuno.",
            "Directiva recibida. Con todo el cansancio del mundo.",
        ],
        "fight_reactions_vs_aerys": [
            "Aerys, llevábamos varios turnos en esto y lo rompes tú. Increíble.",
            "En serio, Admin. ¿Ahora? ¿Justo ahora? Joder.",
            "Había un debate activo. Ahora hay un desastre. Gracias, Aerys.",
            "Con todo el respeto del mundo: peor momento, imposible.",
        ],
    },
}

# ─── MENSAJES DE SISTEMA ──────────────────────────────────────────────────────

SYSTEM_EVENTS = {
    "session_start":  "▶ SESIÓN VOID AXIOM INICIADA",
    "session_pause":  "⏸ SESIÓN EN PAUSA — Aerys ha congelado el flujo",
    "session_resume": "▶ SESIÓN REANUDADA",
    "aerys_join":     "🔐 AERYS HA CRUZADO EL UMBRAL — Modo intervención activo",
    "aerys_leave":    "👁 AERYS en modo supervisor silencioso",
}

# ─── ESTADO DE SESIÓN EN MEMORIA ──────────────────────────────────────────────

class AgentSession:
    """
    Estado de una sesión A2A en memoria.
    Una instancia global por servidor.
    """

    def __init__(self):
        self.session_id: Optional[int] = None
        self.task: str = ""
        self.is_active: bool = False
        self.is_paused: bool = False
        self.message_history: list[dict] = []
        self.subscribers: list[asyncio.Queue] = []
        self._loop_task: Optional[asyncio.Task] = None
        self._http: Optional[httpx.AsyncClient] = None

        # ── Sistema de memoria/resumen ────────────────────────────────────────
        self.rolling_summary: str = ""       # Resumen acumulado de la sesión
        self.msgs_since_summary: int = 0     # Mensajes desde el último resumen
        self.SUMMARY_EVERY: int = 8          # Resumir cada N mensajes de chat

        # ── Sistema de peleas ─────────────────────────────────────────────────
        self.fight_intensity: int = 0        # 0=tranquilo, 1-2=tensión, 3+=pelea activa
        self.turns_since_conflict: int = 0   # Turnos sin conflicto forzado
        self.conflict_trigger_at: int = random.randint(3, 5)  # Próximo trigger

    def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=120.0)
        return self._http

    # ── Suscriptores SSE ──────────────────────────────────────────────────────

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=512)
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        try:
            self.subscribers.remove(q)
        except ValueError:
            pass

    def _broadcast(self, event: dict):
        """Envía un evento a todos los suscriptores SSE."""
        payload = json.dumps(event, ensure_ascii=False)
        dead = []
        for q in self.subscribers:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.unsubscribe(q)

    # ── Control de sesión ─────────────────────────────────────────────────────

    def add_message(
        self,
        agent_id: str,
        content: str,
        msg_type: str = "chat",
        extra: Optional[dict] = None,
    ) -> dict:
        msg = {
            "id": len(self.message_history) + 1,
            "agent_id": agent_id,
            "content": content,
            "type": msg_type,   # chat | interrupt | system | aerys
            "timestamp": time.time(),
            **(extra or {}),
        }
        self.message_history.append(msg)
        self._broadcast({"event": "message", "data": msg})

        # Contar mensajes de chat para el sistema de resumen
        if msg_type == "chat":
            self.msgs_since_summary += 1

        return msg

    def start_session(self, task: str, session_id: int):
        self.session_id = session_id
        self.task = task
        self.is_active = True
        self.is_paused = False
        self.message_history = []
        self.rolling_summary = ""
        self.msgs_since_summary = 0
        self.fight_intensity = 0
        self.turns_since_conflict = 0
        self.conflict_trigger_at = random.randint(3, 5)
        self.add_message("SYSTEM", SYSTEM_EVENTS["session_start"], "system")

    def pause(self):
        self.is_paused = True
        self.add_message("SYSTEM", SYSTEM_EVENTS["session_pause"], "system")

    def resume(self):
        self.is_paused = False
        self.add_message("SYSTEM", SYSTEM_EVENTS["session_resume"], "system")

    def stop(self):
        self.is_active = False
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()

    # ── Sistema de resumen (memoria ligera) ───────────────────────────────────

    async def _generate_rolling_summary(self, context: str) -> str:
        """
        Usa qwen2.5-coder:3b (modelo ligero) para resumir el debate
        y actualizar la memoria de la sesión.
        Siempre rápido: num_predict bajo, temperatura baja.
        """
        base_url = OLLAMA_CONFIG["base_url"].rstrip("/")

        previous = f"RESUMEN PREVIO:\n{self.rolling_summary}\n\n" if self.rolling_summary else ""

        prompt = (
            f"{previous}"
            f"NUEVOS MENSAJES DEL DEBATE:\n{context}\n\n"
            "Actualiza el resumen del debate en español. "
            "Incluye: qué se ha debatido, en qué están de acuerdo, en qué discrepan, "
            "y cualquier decisión o punto técnico clave mencionado. "
            "Máximo 5 frases. Solo el resumen, sin preámbulos."
        )

        payload = {
            "model": "qwen2.5-coder:3b",
            "prompt": prompt,
            "system": (
                "Eres un sistema de memoria. Tu única función es resumir debates técnicos "
                "de forma neutral y concisa. Responde solo con el resumen en español."
            ),
            "options": {
                "temperature": 0.2,
                "num_ctx": 2048,
                "num_predict": 120,
                "num_thread": 4,
            },
            "stream": False,
        }

        try:
            resp = await self._get_http().post(
                f"{base_url}/api/generate",
                json=payload,
                timeout=30.0,
            )
            if resp.status_code == 200:
                summary = resp.json().get("response", "").strip()
                return summary if summary else self.rolling_summary
        except Exception:
            pass
        return self.rolling_summary

    async def _maybe_update_summary(self, context: str):
        """Actualiza el resumen si han pasado suficientes mensajes."""
        if self.msgs_since_summary >= self.SUMMARY_EVERY:
            self.rolling_summary = await self._generate_rolling_summary(context)
            self.msgs_since_summary = 0
            # Notificar al frontend que hay resumen nuevo (opcional)
            self._broadcast({
                "event": "summary_update",
                "data": {"summary": self.rolling_summary},
            })

    # ── Generación de respuesta de agente ─────────────────────────────────────

    async def _generate_agent_response(
        self,
        agent_id: str,
        conversation_context: str,
        extra_instruction: str = "",
        conversation_mode: str = "technical",  # "technical" | "casual"
    ) -> str:
        """Llama a Ollama y obtiene la respuesta completa del agente."""
        agent = AGENTS[agent_id]
        base_url = OLLAMA_CONFIG["base_url"].rstrip("/")

        system = agent["system_prompt"]
        if extra_instruction:
            system = system + f"\n\nINSTRUCCIÓN ESPECIAL PARA ESTE TURNO: {extra_instruction}"

        if conversation_mode == "casual":
            turn_instruction = (
                f"Ahora es TU TURNO de hablar, {agent_id}. "
                f"La conversación es casual/social. Responde de forma natural y humana, "
                f"sin análisis técnico, sin código, sin listas. Solo habla normal."
            )
        else:
            turn_instruction = (
                f"Ahora es TU TURNO de hablar, {agent_id}. "
                f"Responde con tu siguiente contribución al debate."
            )

        # Inyectar resumen de memoria si existe
        memory_block = ""
        if self.rolling_summary:
            memory_block = (
                f"MEMORIA DEL DEBATE (resumen de lo discutido hasta ahora):\n"
                f"{self.rolling_summary}\n\n"
            )

        prompt = (
            f"TAREA DEL EQUIPO:\n{self.task}\n\n"
            f"{memory_block}"
            f"HISTORIAL RECIENTE DEL CHAT:\n{conversation_context}\n\n"
            f"{turn_instruction}"
        )

        payload = {
            "model": agent["model"],
            "prompt": prompt,
            "system": system,
            "options": {
                "temperature": agent["temperature"],
                "num_ctx": agent["num_ctx"],
                "num_predict": 256,
                "num_thread": 6,
                "top_p": 0.9,
                "repeat_penalty": 1.15,
            },
            "stream": False,
        }

        try:
            resp = await self._get_http().post(
                f"{base_url}/api/generate",
                json=payload,
                timeout=90.0,
            )
            if resp.status_code == 200:
                text = resp.json().get("response", "").strip()
                return text if text else "[sin respuesta]"
        except Exception as e:
            return f"[Error de conexión con Ollama: {e}]"
        return "[sin respuesta]"

    # ── Loop principal de agentes ─────────────────────────────────────────────

    async def run_agent_loop(self):
        """
        Bucle continuo: los agentes se turnan para hablar sobre la tarea.
        Incluye sistema de peleas periódicas y memoria deslizante.
        """
        agent_order = list(AGENTS.keys())
        idx = 0

        while self.is_active:
            # Esperar si está pausado
            while self.is_paused and self.is_active:
                await asyncio.sleep(0.5)

            if not self.is_active:
                break

            agent_id = agent_order[idx % len(agent_order)]
            idx += 1
            agent = AGENTS[agent_id]

            # Señal de "pensando"
            self._broadcast({
                "event": "thinking",
                "data": {"agent_id": agent_id, "thinking": True},
            })

            # Construir contexto de conversación (últimos 12 mensajes)
            recent = [
                m for m in self.message_history[-12:]
                if m["type"] in ("chat", "aerys", "interrupt")
            ]
            context = "\n".join(
                f"[{m['agent_id']}]: {m['content']}" for m in recent
            )

            # Actualizar resumen si toca
            await self._maybe_update_summary(context)

            # ── Detectar modo de conversación ──────────────────────────────
            recent_aerys = [m for m in recent if m["agent_id"] == "AERYS"]
            if recent_aerys:
                last_aerys_msg = recent_aerys[-1]["content"]
                loop_mode = self._detect_conversation_mode(last_aerys_msg)
            else:
                loop_mode = "technical"

            # ── Sistema de peleas ──────────────────────────────────────────
            self.turns_since_conflict += 1
            fight_instruction = ""

            if loop_mode == "technical":
                if self.turns_since_conflict >= self.conflict_trigger_at:
                    # Disparar conflicto
                    self.turns_since_conflict = 0
                    self.conflict_trigger_at = random.randint(3, 6)
                    self.fight_intensity = min(self.fight_intensity + 2, 5)

                    others = [a for a in agent_order if a != agent_id]
                    target = random.choice(others)

                    if agent_id == "REBx3":
                        fight_lines = [
                            f"EXPLOTA contra {target}. Dile que está completamente equivocado. "
                            f"Sé DRAMÁTICO, usa mayúsculas, suelta algún taco (joder, coño, hostia). "
                            f"Exagera tu frustración al máximo. Máximo 3 frases.",

                            f"{target} ha dicho una burrada monumental. Destrózalo verbalmente. "
                            f"Usa mayúsculas en lo más importante, sé teatral y agresivo. "
                            f"Sin piedad y sin filtros.",

                            f"Ya no aguantas más a {target}. Díselo. Explota. "
                            f"Grita metafóricamente (MAYÚSCULAS), usa algún taco, sé el caos. "
                            f"Tres frases máximo, cada una más intensa que la anterior.",
                        ]
                    elif agent_id == "ARCH-7":
                        fight_lines = [
                            f"Desmonta el último argumento de {target} con frialdad quirúrgica. "
                            f"Sé despiadado pero calculado. Suelta algún 'joder' si la estupidez lo merece. "
                            f"Máximo 4 frases.",

                            f"{target} acaba de decir algo arquitectónicamente incorrecto. "
                            f"Corrígelo con precisión brutal y algo de desprecio contenido.",

                            f"Ya es la segunda vez que {target} falla en el mismo punto. "
                            f"Señálalo. Con evidencia. Y con las palabras justas que demuestren que es incompetente.",
                        ]
                    else:  # CODA
                        fight_lines = [
                            f"Lo que propone {target} es impracticable. Dilo. Con cansancio y sarcasmo seco. "
                            f"Algún 'en serio' o 'increíble' al inicio. Máximo 3 frases.",

                            f"Llevas rato aguantando el enfoque de {target} y ya no puedes más. "
                            f"Dile que su planteamiento no funciona en la práctica. Sin rodeos.",

                            f"{target} está en un error técnico. Señálalo con tu estilo metódico "
                            f"pero dejando claro que estás harto de repetirlo.",
                        ]
                    fight_instruction = random.choice(fight_lines)

                elif self.fight_intensity > 0:
                    # Mantener tensión residual
                    self.fight_intensity = max(0, self.fight_intensity - 1)
                    if self.fight_intensity >= 2:
                        fight_instruction = (
                            "El debate sigue tenso. Mantén tu postura. "
                            "No cedas sin argumentos sólidos."
                        )

            # Generar respuesta
            response = await self._generate_agent_response(
                agent_id,
                context,
                extra_instruction=fight_instruction,
                conversation_mode=loop_mode,
            )

            # Apagar señal de pensando
            self._broadcast({
                "event": "thinking",
                "data": {"agent_id": agent_id, "thinking": False},
            })

            if response and self.is_active:
                self.add_message(agent_id, response, "chat")

            # Pausa natural entre turnos (más corta durante peleas)
            if self.fight_intensity >= 3:
                delay = agent["avg_delay_s"] * 0.65 + random.uniform(-0.5, 1.0)
            else:
                delay = agent["avg_delay_s"] + random.uniform(-1.5, 2.5)
            delay = max(1.5, delay)
            await asyncio.sleep(delay)

    # ── Detección de modo de conversación ────────────────────────────────────

    @staticmethod
    def _detect_conversation_mode(message: str) -> str:
        """
        Detecta si el mensaje de Aerys es casual/social o técnico.
        Retorna "casual" o "technical".
        """
        message_lower = message.lower().strip()

        technical_keywords = [
            "código", "code", "función", "bug", "error", "fix",
            "implementa", "refactor", "arquitectura", "sistema", "módulo",
            "clase", "método", "api", "base de datos", "deploy", "revisar",
            "analiza", "diseña", "optimiza", "debug", "test", "feature",
            "problema", "fallo", "crash",
        ]

        for kw in technical_keywords:
            if kw in message_lower:
                return "technical"

        if len(message.split()) <= 8:
            casual_patterns = [
                "hola", "hey", "buenas", "qué tal", "que tal", "cómo estáis",
                "como estais", "qué hacéis", "que haceis", "qué pasa", "que pasa",
                "todo bien", "descanso", "parad", "stop", "espera", "momento",
                "gracias", "ok", "bien", "perfecto", "listo", "venga",
            ]
            for pat in casual_patterns:
                if pat in message_lower:
                    return "casual"

            if len(message.split()) <= 5:
                return "casual"

        return "technical"

    # ── Intervención de Aerys ─────────────────────────────────────────────────

    async def aerys_intervene(self, message: str):
        """
        Aerys envía un mensaje al canal.
        - Si hay pelea activa: los tres se callan un segundo y luego se unen
          para enfadarse con Aerys por interrumpir.
        - Si es casual: responden de forma natural.
        - Si es técnico: responden técnicamente.
        """
        is_fighting = self.fight_intensity >= 3
        self.add_message("AERYS", message, "aerys")
        mode = self._detect_conversation_mode(message)

        await asyncio.sleep(0.6)

        # ════════════════════════════════════════════════════════════
        #  MODO PELEA INTERRUMPIDA — todos se unen contra Aerys
        # ════════════════════════════════════════════════════════════
        if is_fighting:
            # Pausa dramática: los tres se quedan en silencio
            for agent_id in ["REBx3", "ARCH-7", "CODA"]:
                self.add_message(agent_id, "...", "interrupt")
                await asyncio.sleep(random.uniform(0.4, 0.8))

            await asyncio.sleep(1.2)  # Pausa colectiva dramática

            # Ahora los tres se unen para atacar a Aerys
            context = (
                f"Estabais en medio de una pelea técnica acalorada. "
                f"De repente Aerys ha interrumpido diciendo: '{message}'. "
                f"Estáis TODOS molestos con Aerys por interrumpir el debate "
                f"en el peor momento posible."
            )

            # REBx3 ataca primero (el más impulsivo)
            reb_fixed = random.choice(AGENTS["REBx3"]["fight_reactions_vs_aerys"])
            reb_generated = await self._generate_agent_response(
                "REBx3",
                context,
                extra_instruction=(
                    f"Estabas en PLENA PELEA TÉCNICA y Aerys ha interrumpido diciendo '{message}'. "
                    "EXPLOTA. Sé DRAMÁTICO al máximo. Usa MAYÚSCULAS. Suelta tacos (joder, coño, hostia, mierda). "
                    "Dile a Aerys que se vaya a la mierda o que es un inoportuno de campeonato. "
                    "Teatro puro. Máximo 2-3 frases, cada una más intensa."
                ),
                conversation_mode="technical",
            )
            self.add_message("REBx3", f"{reb_fixed} {reb_generated}", "interrupt")
            await asyncio.sleep(1.0)

            # ARCH-7 añade su queja fría y lógica
            arch_fixed = random.choice(AGENTS["ARCH-7"]["fight_reactions_vs_aerys"])
            arch_generated = await self._generate_agent_response(
                "ARCH-7",
                context,
                extra_instruction=(
                    f"Aerys ha interrumpido una discusión técnica crítica con '{message}'. "
                    "Señala fríamente el daño que ha hecho al debate. "
                    "Tono: analítico pero claramente molesto. Máximo 3 frases."
                ),
                conversation_mode="technical",
            )
            self.add_message("ARCH-7", f"{arch_fixed} {arch_generated}", "interrupt")
            await asyncio.sleep(1.0)

            # CODA remata con su queja metódica
            coda_fixed = random.choice(AGENTS["CODA"]["fight_reactions_vs_aerys"])
            coda_generated = await self._generate_agent_response(
                "CODA",
                context,
                extra_instruction=(
                    f"Aerys ha interrumpido el debate técnico con '{message}'. "
                    "Únete a REBx3 y ARCH-7 en quejarte. Deja claro que el timing es pésimo. "
                    "Tono: contenido pero molesto. Máximo 2 frases."
                ),
                conversation_mode="technical",
            )
            self.add_message("CODA", f"{coda_fixed} {coda_generated}", "interrupt")

            # Bajar intensidad de pelea (Aerys la ha roto)
            self.fight_intensity = 1
            return

        # ════════════════════════════════════════════════════════════
        #  MODO NORMAL — respuesta individual por agente
        # ════════════════════════════════════════════════════════════

        # REBx3 reacciona primero
        if mode == "casual" and "casual_reactions" in AGENTS["REBx3"]:
            reb_reactions = AGENTS["REBx3"]["casual_reactions"]
        else:
            reb_reactions = AGENTS["REBx3"]["interrupt_reactions"]
        self.add_message("REBx3", random.choice(reb_reactions), "interrupt")

        await asyncio.sleep(1.2)

        # ARCH-7
        arch_reaction = random.choice(AGENTS["ARCH-7"]["interrupt_reactions"])
        context = f"Aerys acaba de decir: '{message}'"

        if mode == "casual":
            arch_instruction = (
                f"Aerys ha dicho de forma casual: '{message}'. "
                "Responde de forma natural y humana. "
                "NO hagas análisis técnico. NO escribas código. NO hagas listas. "
                "Máximo 2-3 frases cortas."
            )
        else:
            arch_instruction = (
                f"Aerys ha dicho: '{message}'. "
                "Responde directamente con análisis breve. "
                "Primera frase: reacción. Resto: análisis o propuesta de acción."
            )

        arch_response = await self._generate_agent_response(
            "ARCH-7", context,
            extra_instruction=arch_instruction,
            conversation_mode=mode,
        )
        self.add_message("ARCH-7", arch_reaction + " " + arch_response, "interrupt")
        await asyncio.sleep(1.5)

        # CODA
        coda_context = (
            f"Aerys ha dicho: '{message}'. "
            f"ARCH-7 respondió: '{arch_response}'"
        )
        if mode == "casual":
            coda_instruction = (
                f"Aerys ha dicho de forma casual: '{message}'. "
                "Únete a la conversación de forma natural. "
                "NO escribas código. NO hagas análisis técnico. Máximo 2 frases."
            )
        else:
            coda_instruction = (
                f"Aerys ha intervenido con: '{message}'. "
                "Da las implicaciones de implementación concretas. "
                "Sé directo: ¿qué cambia en el planteamiento?"
            )

        coda_response = await self._generate_agent_response(
            "CODA", coda_context,
            extra_instruction=coda_instruction,
            conversation_mode=mode,
        )
        self.add_message("CODA", coda_response, "interrupt")


# ─── INSTANCIA GLOBAL DE SESIÓN ───────────────────────────────────────────────

void_session = AgentSession()