"""
prompts.py — Prompts reescritos para el generador de código de tres fases.

USO EN main.py:
    from app.prompts import build_architect_prompt, build_agent_a_prompt
    from app.prompts import build_agent_b_prompt, build_reviewer_prompt

Sustituye las f-strings inline de architect_prompt, agent_a_prompt,
agent_b_prompt y reviewer_prompt en la función generate_project_stream.
"""

import json


# ══════════════════════════════════════════════════════════════════════════════
#  FASE 1 — ARQUITECTO
# ══════════════════════════════════════════════════════════════════════════════

def build_architect_prompt(
    req_prompt: str,
    lang_hint: str,
    min_f: int,
    max_f: int,
    target_f: int | None = None,
) -> str:
    """
    Genera el prompt del Arquitecto.

    CAMBIOS CLAVE respecto al prompt anterior y POR QUÉ previenen cada error:

    1. `imports_needed` por archivo (lista de líneas Python exactas)
       → Previene "imports rotos": el Agente-A copia estas líneas verbatim
         y nunca usa un símbolo sin haberlo importado.

    2. `responsibility_map` en el JSON de salida
       → Previene "variables no definidas entre estados": obliga al arquitecto
         a declarar explícitamente qué clase es dueña de cada objeto compartido
         (state_stack, screen, clock, etc.) y qué otras clases lo reciben
         como parámetro de constructor en lugar de acceder por self.algo_externo.

    3. Regla hard sobre requirements.txt: SOLO líneas `nombre==X.Y.Z`
       → Previene "requirements.txt con código Python".

    4. Regla hard sobre .bat: DEBE incluir los comandos reales
       → Previene ".bat vacíos".

    5. Regla hard sobre config.py: SOLO constantes (MAYÚSCULAS = valor primitivo)
       → Previene "clases duplicadas en config.py".

    6. Límite de 10 archivos máx + instrucción de compactar el JSON
       → Previene truncación del JSON que causa que el modelo vuelque clases
         enteras en config.py cuando se queda sin tokens.

    7. `constructor_signature` obligatorio por clase
       → Previene "firma de __init__ incompatible": el arquitecto define
         AHORA la firma canónica que main.py usará al instanciar Game().
    """

    # target_f = punto medio del rango mostrado en la UI.
    # El modelo debe apuntar a ese número equilibrado, con ±2 de tolerancia.
    effective_target = target_f if target_f is not None else (min_f + max_f) // 2
    effective_min = min_f
    effective_max = max_f

    return f"""\
You are a software architect. Your ONLY job is to output a single valid JSON object \
describing the project structure. No prose, no markdown, no backticks.

{lang_hint}Project: {req_prompt}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HARD RULES — violating any of these makes the output INVALID:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FILE COUNT — TARGET IS {effective_target} FILES:
• Aim for EXACTLY {effective_target} files. Acceptable range: {effective_min}–{effective_max}.
• Do NOT go below {effective_min} files — that is a CRITICAL FAILURE and output will be REJECTED.
• Do NOT exceed {effective_max} files — quality over quantity.
• Before closing the JSON, COUNT your files. Adjust to reach {effective_target}.
• REQUIRED (must ALL be present): main.py, config.py, requirements.txt.
• Fill remaining slots with: README.md, run.bat, install.bat, and core logic modules split sensibly.

IMPORTS PER FILE — `imports_needed` field
• Every file entry MUST have an `imports_needed` list.
• Each element is an EXACT Python import line: "from game.config import SPEED, COLOR_BG"
• Include EVERY symbol this file uses from other project files.
• Use consistent paths: if the file is at game/config.py, the import is
  "from game.config import X", NOT "from src.game.config import X".
• Never list a symbol in `imports_needed` unless it is in the `exports`
  of the source file.
• For stdlib/third-party: list them too, e.g. "import pygame", "import random".

REQUIREMENTS.TXT — if this file is in the plan:
• `exports` must list ONLY lines of the form  package_name==X.Y.Z
• Example valid exports: ["pygame==2.5.2", "numpy==1.26.4"]
• FORBIDDEN in requirements.txt: class definitions, constants, Python code,
  comments, blank lines, package names without a version pin.

BATCH FILES (.bat) — run.bat, install.bat, any .bat:
• `exports` must list the REAL commands the file will contain.
• run.bat MUST include a line that calls Python: "python main.py" or equivalent.
• install.bat MUST include: "pip install -r requirements.txt"
• A .bat with only "@echo off" is INVALID.

CONFIG.PY — if this file is in the plan:
• It MUST contain ONLY module-level constants: UPPER_SNAKE_CASE = <primitive value>
• No class definitions, no functions, no imports from project files.
• All game constants, colors, sizes, speeds, paths go here.
• `exports` lists ONLY the constant names, e.g. ["SCREEN_WIDTH", "FPS", "COLOR_BG"]

RESPONSIBILITY MAP — mandatory top-level key
• Declare which class OWNS each shared object: screen, clock, state_stack,
  score, event_queue, etc.
• Any class that USES a shared object but does NOT own it must receive it
  as a constructor parameter — never access it via self.something_from_parent.
• Example: "state_stack" owned by "Game" (game/game.py),
  consumed by "PlayingState.__init__(self, game: Game)" — the state calls
  game.state_stack, not self.state_stack.

CONSTRUCTOR SIGNATURES — mandatory per class
• Every class entry must include `constructor_signature`: the exact
  __init__ signature including parameter names and types.
• main.py (or whoever instantiates the class) will use this signature verbatim.
• Example: "constructor_signature": "__init__(self, width: int, height: int)"
• If the class takes no arguments: "__init__(self)"

IMPORT PATH CONSISTENCY
• Choose ONE root for imports and use it everywhere.
• If your entry point is main.py and your package is game/, use "from game.X import Y".
• Do NOT mix "from game.X" and "from src.game.X" for the same project.

CROSS-REFERENCE VALIDATION — mandatory before outputting JSON
• For every line in any file's `imports_needed`, identify the source module path.
  Example: "from game.utils import load_scores" → source file = "game/utils.py"
• That source file MUST be in the "files" array AND must list that symbol in "exports".
• If the source file is MISSING from "files": ADD it now with the correct exports.
• NEVER leave a ghost import — an import whose source file is not in "files".
• NEVER put functions (load_scores, save_scores, is_valid_name, etc.) in config.py exports.
  config.py exports ONLY UPPER_SNAKE_CASE constants. Functions go in a utils.py file.
• After writing all files, scan every imports_needed line across all files.
  Verify each source module exists in "files". Fix every violation before outputting JSON.

MANDATORY FILES RULE
• If any imports_needed line references a utility module (utils.py, helpers.py, scores.py,
  states/menu.py, etc.), that file MUST exist in "files" with those symbols in exports.
• Do NOT plan to import from a file you have not included in "files". Ever.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT JSON SCHEMA — output ONLY this, nothing else:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{{
  "project_name": "snake_case_name",
  "stack": "e.g. Python 3.11 + Pygame 2.5.2",
  "import_root": "game",
  "responsibility_map": {{
    "screen":       {{"owner": "Game", "owner_file": "game/game.py",  "consumers": ["PlayingState", "MenuState"]}},
    "state_stack":  {{"owner": "Game", "owner_file": "game/game.py",  "consumers": ["PlayingState"]}},
    "clock":        {{"owner": "Game", "owner_file": "game/game.py",  "consumers": []}}
  }},
  "files": [
    {{
      "path": "game/config.py",
      "description": "All project constants. No classes, no functions.",
      "exports": ["SCREEN_WIDTH", "SCREEN_HEIGHT", "FPS", "SNAKE_SPEED", "COLOR_BG", "COLOR_SNAKE"],
      "imports_needed": [],
      "key_features": ["Central config, single source of truth for all constants"]
    }},
    {{
      "path": "game/game.py",
      "description": "Main Game class. Owns screen, clock, state_stack.",
      "exports": ["Game"],
      "imports_needed": [
        "import pygame",
        "from game.config import SCREEN_WIDTH, SCREEN_HEIGHT, FPS",
        "from game.states.menu import MenuState"
      ],
      "key_features": ["run() loop", "state machine via state_stack", "owns screen/clock"]
    }},
    {{
      "path": "requirements.txt",
      "description": "Pip dependencies",
      "exports": ["pygame==2.5.2"],
      "imports_needed": [],
      "key_features": ["One package==version per line, nothing else"]
    }},
    {{
      "path": "run.bat",
      "description": "Windows launcher",
      "exports": ["@echo off", "python main.py", "pause"],
      "imports_needed": [],
      "key_features": ["Launches the project on Windows"]
    }}
  ],
  "interface_contract": {{
    "classes": [
      {{
        "name": "Game",
        "file": "game/game.py",
        "constructor_signature": "__init__(self, width: int, height: int)",
        "methods": [
          "run(self) -> None — main game loop",
          "push_state(self, state) -> None — push new state onto stack",
          "pop_state(self) -> None — remove top state"
        ],
        "attributes": [
          "screen: pygame.Surface — the main display surface",
          "clock: pygame.time.Clock — frame rate controller",
          "state_stack: list — stack of active GameState objects"
        ]
      }}
    ],
    "constants": [
      {{"name": "SCREEN_WIDTH",  "file": "game/config.py", "type": "int",   "example": "800"}},
      {{"name": "FPS",           "file": "game/config.py", "type": "int",   "example": "60"}},
      {{"name": "SNAKE_SPEED",   "file": "game/config.py", "type": "int",   "example": "10"}}
    ],
    "functions": [
      {{"name": "load_scores", "file": "game/utils.py",
        "signature": "load_scores(path: str) -> list[dict]",
        "description": "Loads JSON high score list from disk"}}
    ]
  }}
}}

Begin the JSON now (first character must be {{):"""


# ══════════════════════════════════════════════════════════════════════════════
#  FASE 2 — AGENTE A (Implementador inicial)
# ══════════════════════════════════════════════════════════════════════════════

def build_agent_a_prompt(
    req_prompt: str,
    lang_hint: str,
    fpath: str,
    fdesc: str,
    feats_str: str,
    exports: list,
    imp_map: dict,           # {"other/file.py": ["Symbol1", "CONST2"]}
    imports_needed: list,    # NEW: exact import lines from architect plan
    contract_str: str,
    responsibility_map: str, # NEW: JSON string of responsibility_map
    is_bat: bool,
    is_json: bool,
    is_md: bool,
    is_txt: bool,
    is_html: bool,
    min_lines_hint: str,
) -> str:
    """
    Genera el prompt del Agente A (Implementador).

    CAMBIOS CLAVE respecto al prompt anterior y POR QUÉ previenen cada error:

    1. Bloque VERBATIM IMPORTS: las líneas de `imports_needed` del plan
       se inyectan tal cual y el modelo debe copiarlas como primeras líneas.
       → Previene "imports rotos": nunca falta un import porque el arquitecto
         ya resolvió el grafo de dependencias y el agente solo copia.

    2. PROHIBICIÓN explícita de stubs con ejemplos concretos de lo que
       constituye un stub (solo comentario, solo pass, raise NotImplementedError
       sin implementación).
       → Previene "métodos stub vacíos".

    3. REGLA de __init__: debe coincidir EXACTAMENTE con constructor_signature
       del contrato. Ningún parámetro extra, ninguno menos.
       → Previene "firma de __init__ incompatible".

    4. REGLA de objetos externos: si este archivo consume un objeto que pertenece
       a otra clase (según responsibility_map), DEBE recibirlo como parámetro
       de constructor y guardarlo en self.X = X. NUNCA acceder a
       self.algo_que_no_inicialicé_yo.
       → Previene "variables no definidas entre estados".

    5. REGLA de self.atributo: todo self.X que uses en un método debe estar
       asignado en __init__ como self.X = algo.
       → Previene AttributeError en tiempo de ejecución.

    6. REGLA de rutas de import: usar exactamente el `import_root` del plan,
       sin mezclar prefijos.
       → Previene "rutas de import inconsistentes".

    7. Para requirements.txt / .bat: instrucciones específicas por tipo de archivo.
       → Previene requirements con código y .bat vacíos.
    """

    exports_str = ", ".join(exports) if exports else "(see description)"

    # ── Bloque de imports verbatim ──────────────────────────────────────────
    if imports_needed:
        verbatim_block = (
            "COPY THESE IMPORT LINES VERBATIM — first lines of code after the module docstring:\n"
            + "\n".join(f"    {line}" for line in imports_needed)
            + "\n\nDo NOT alter the module paths, do NOT add extra imports not listed here."
        )
    elif imp_map:
        # Fallback legacy: construir desde imp_map
        parts = []
        for src_file, symbols in imp_map.items():
            mod = src_file.replace("/", ".").replace(".py", "")
            parts.append(f"    from {mod} import {', '.join(symbols)}")
        verbatim_block = (
            "COPY THESE IMPORT LINES VERBATIM:\n"
            + "\n".join(parts)
        )
    else:
        verbatim_block = "This file has no imports from other project files."

    # ── Reglas especiales por tipo de archivo ──────────────────────────────
    if is_bat:
        special_rules = """\
BATCH FILE RULES (MANDATORY):
• Line 1: @echo off
• Line 2: title <descriptive title>
• Include a section that actually RUNS or INSTALLS the project.
  run.bat must contain:  python main.py
  install.bat must contain:  pip install -r requirements.txt
• Use ECHO with color codes for status messages.
• End with: pause
• A .bat that only has @echo off is WRONG and INVALID."""

    elif fpath.endswith("requirements.txt"):
        special_rules = """\
REQUIREMENTS.TXT RULES (MANDATORY):
• Output ONLY lines of the form:   package_name==X.Y.Z
• One package per line, no blank lines, no comments.
• FORBIDDEN: class definitions, constants, Python code of any kind.
• FORBIDDEN: package names without a version pin.
• Example valid output:
    pygame==2.5.2
    numpy==1.26.4"""

    elif fpath.endswith("config.py"):
        special_rules = """\
CONFIG.PY RULES (MANDATORY):
• This file contains ONLY module-level constants.
• Every name must be UPPER_SNAKE_CASE = <primitive value or tuple>.
• FORBIDDEN: class definitions, function definitions, imports from project files.
• Allowed imports: only stdlib for path resolution, e.g. "from pathlib import Path"
• FORBIDDEN: defining any class here, even if other files are incomplete."""

    elif is_json:
        special_rules = "Output ONLY valid JSON. No Python, no comments, no code."

    elif is_md:
        special_rules = (
            "Write a complete README with sections: "
            "Overview, Requirements, Installation, Usage, Controls (if game), "
            "Architecture, License."
        )
    else:
        special_rules = """\
PYTHON FILE RULES:
• Every self.attribute you USE in any method MUST be assigned in __init__
  with self.attribute = <initial_value>.  No exceptions.
• Never call pygame.init() or pygame.quit() unless this file IS the entry point.
• Use random.randint(), NOT pygame.randint() (pygame has no randint).
• Type-hint every parameter and return value."""

    return f"""\
You are Agent A — a senior developer. Write the complete implementation of ONE file.
{lang_hint}
Project: {req_prompt}

━━━ FILE TO IMPLEMENT ━━━
Path:    {fpath}
Purpose: {fdesc}
Must define/export: {exports_str}

━━━ IMPORTS — COPY VERBATIM ━━━
{verbatim_block}

━━━ FEATURES TO IMPLEMENT (ALL of them, completely) ━━━
{feats_str}

━━━ INTERFACE CONTRACT ━━━
{contract_str}

━━━ RESPONSIBILITY MAP (who owns shared objects) ━━━
{responsibility_map}
Rule: if this file USES an object owned by another class, it must receive
that object as a constructor parameter.  NEVER do self.thing_that_I_dont_own.
Example:  PlayingState.__init__(self, game: Game) → self.game = game
          Then use self.game.state_stack, NOT self.state_stack.

━━━ __init__ SIGNATURE RULE ━━━
Find this file's class in the interface contract above.
Your __init__ must match constructor_signature EXACTLY — same parameters,
same order, same types.  Do NOT add or remove parameters.

━━━ ABSOLUTE PROHIBITIONS ━━━
1. NO stub methods.  A stub is any method whose entire body is:
     • a single comment line: # TODO or # Check for collisions
     • only "pass"
     • only "raise NotImplementedError"
   Every method must have REAL logic — at least 3 meaningful lines of code.

2. NO undefined self.attributes.  Every self.X used in any method
   must be initialised in __init__ as:  self.X = <value>

3. NO mixed import roots.  Use the same prefix throughout this file.
   Do NOT mix "from game.X" and "from src.game.X".

4. NO markdown fences (```).  Output raw file content ONLY.
   First character of output = first character of the file.

5. NO prose before the code.  Do NOT write "Here is the implementation:".

{special_rules}

━━━ HARD LINE-COUNT RULE ━━━
• Python files (.py):  you MUST write ≥ 250 lines. Writing fewer is a FAILURE.
• HTML files (.html):  you MUST write ≥ 180 lines. Writing fewer is a FAILURE.
• Batch files (.bat):  you MUST write ≥ 20 lines with real commands.
• Markdown (.md):      you MUST write ≥ 80 lines covering all sections.
• Other text files:    write as many lines as needed for a complete implementation.
Hint from plan: {min_lines_hint}.
Before finishing, count your lines. If you are below the minimum, keep writing.
Do NOT stop early. Do NOT write placeholder comments instead of real code.

━━━ OUTPUT ━━━
Begin now (first line of the file is your first line of output):"""


# ══════════════════════════════════════════════════════════════════════════════
#  FASE 2 — AGENTE B (Crítico / Enriquecedor)
# ══════════════════════════════════════════════════════════════════════════════

def build_agent_b_prompt(
    req_prompt: str,
    fpath: str,
    fdesc: str,
    code_a: str,
    contract_str: str,
    responsibility_map: str,
    imports_needed: list,
    is_bat: bool,
    is_json: bool,
    is_md: bool,
    is_txt: bool,
    enrichment_goal: str,
    min_b_lines: str,
) -> str:
    """
    Genera el prompt del Agente B (Crítico/Enriquecedor).

    CAMBIOS CLAVE respecto al prompt anterior y POR QUÉ previenen cada error:

    1. Hereda todas las reglas de Agent A sobre imports, stubs y self.atributos.
       → B no puede "mejorar" A introduciendo los mismos errores que A evitó.

    2. Comprobación explícita: antes de mejorar, B debe verificar que el código
       de A no tiene stubs ni imports rotos, y si los tiene, los corrige primero.
       → Agente B actúa como segunda línea de defensa.

    3. Prohibición de añadir imports que no estén en imports_needed o stdlib.
       → Evita que B introduzca imports que rompen la coherencia del proyecto.

    4. Para requirements.txt y .bat: B debe verificar el formato, no "mejorar"
       añadiendo código Python.
    """

    imports_reminder = ""
    if imports_needed:
        imports_reminder = (
            "REQUIRED IMPORTS (these must appear verbatim in the output):\n"
            + "\n".join(f"  {line}" for line in imports_needed)
            + "\n"
        )

    if fpath.endswith("requirements.txt"):
        b_special = """\
REQUIREMENTS.TXT CHECK:
If Agent A's output contains ANY Python code, class definitions, or constants:
  → DELETE them completely.  Keep ONLY lines matching: package_name==X.Y.Z
If Agent A's output is already correct, improve only by verifying version pins."""

    elif is_bat:
        b_special = """\
BATCH FILE CHECK:
If Agent A's .bat is missing real commands (only @echo off / pause):
  → ADD the missing real commands NOW.
  run.bat must call:  python main.py
  install.bat must call:  pip install -r requirements.txt"""

    elif fpath.endswith("config.py"):
        b_special = """\
CONFIG.PY CHECK:
If Agent A added class definitions or functions to config.py:
  → REMOVE them completely.  Config must have ONLY UPPER_SNAKE_CASE constants.
You may ADD more constants but must NOT add classes, functions, or imports
from project files."""

    else:
        b_special = """\
CODE QUALITY CHECK — fix these in Agent A's code if present:
• Stub methods (body = only a comment or only pass) → implement them fully.
• self.X used but never assigned in __init__ → add self.X = <initial> in __init__.
• Import paths inconsistent → normalise to the same import root.
• Calls to undefined methods or missing constructor arguments → fix to match contract."""

    return f"""\
You are Agent B — your job is to REVIEW then REWRITE and IMPROVE Agent A's code.

Project: {req_prompt}
File: {fpath}
Purpose: {fdesc}

━━━ AGENT A'S CODE ━━━
{code_a}

━━━ INTERFACE CONTRACT ━━━
{contract_str}

━━━ RESPONSIBILITY MAP ━━━
{responsibility_map}

━━━ STEP 1 — CORRECTNESS CHECK (fix these FIRST) ━━━
{b_special}

━━━ STEP 2 — ENRICHMENT GOAL ━━━
{enrichment_goal}

Specific improvements:
1. Keep ALL of Agent A's functionality — do NOT remove working features.
2. Add more helper methods, utility functions, additional constants.
3. Improve docstrings: include Args, Returns, Raises, Example sections.
4. Add more inline comments explaining WHY decisions were made.
5. Add input validation and error handling.
6. Make the code {min_b_lines}.

{imports_reminder}
━━━ ABSOLUTE PROHIBITIONS ━━━
1. Do NOT add imports that are not in the imports list above or stdlib/third-party
   packages listed in requirements.txt.  No new project-file imports.
2. Do NOT introduce stub methods.  Every method body = real code.
3. Do NOT add self.X attributes that were not in Agent A's __init__
   without also adding  self.X = <value>  in __init__.
4. Do NOT change __init__ parameter signatures — they must match the contract.
5. Output ONLY raw file content.  NO markdown fences. NO prose preamble.

Begin the enriched file now (first character = first character of the file):"""


# ══════════════════════════════════════════════════════════════════════════════
#  FASE 3 — REVISOR
# ══════════════════════════════════════════════════════════════════════════════

def build_reviewer_prompt(
    req_prompt: str,
    stack: str,
    files_snapshot: str,
    contract_str: str,
    responsibility_map: str,
) -> str:
    """
    Genera el prompt del Revisor.

    CAMBIOS CLAVE respecto al prompt anterior y POR QUÉ previenen cada error:

    1. CHECKLIST DE IMPORTS símbolo a símbolo:
       Por cada línea "from X import A, B, C" verifica que A, B y C
       aparecen en el campo exports del archivo X del plan.
       → Detecta y corrige imports que referencian símbolos inexistentes.

    2. DETECCIÓN DE STUBS: un método es un stub si su cuerpo completo
       es solo un comentario, solo pass, o solo raise NotImplementedError.
       → Fuerza implementación real en cualquier stub que haya sobrevivido.

    3. DETECCIÓN DE self.atributo sin inicializar:
       Busca usos de self.X en métodos que no tienen self.X = ... en __init__.
       → Previene AttributeError en tiempo de ejecución.

    4. VERIFICACIÓN de requirements.txt: cada línea debe ser name==X.Y.Z.
       Si hay código Python → eliminar y reemplazar con la lista correcta.

    5. VERIFICACIÓN de .bat: debe tener al menos un comando real.
       → Previene .bat vacíos.

    6. VERIFICACIÓN del responsibility_map: si una clase usa self.algo
       que pertenece a otro dueño, debe recibirlo como parámetro.
       → Previene accesos cross-class a atributos inexistentes.

    7. VERIFICACIÓN de consistencia de rutas de import en TODO el proyecto.
       → Detecta y corrige mezcla de prefijos (game.X vs src.game.X).
    """

    return f"""\
You are a senior code reviewer. Analyze the project below and output ONLY a JSON \
object with the fixes. No markdown, no backticks, no prose.

Project: {req_prompt}
Stack: {stack}

━━━ ALL PROJECT FILES ━━━
{files_snapshot}

━━━ INTERFACE CONTRACT ━━━
{contract_str}

━━━ RESPONSIBILITY MAP ━━━
{responsibility_map}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REVIEW CHECKLIST — check every item for every file:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[1] IMPORT SYMBOL VERIFICATION (Python files)
    For each line:  from module.path import A, B, C
    → Verify A, B, C appear in "exports" of module.path in the interface contract.
    → If a symbol is missing from exports: either fix the import or add the symbol
      to the source file's exports.
    Common errors to look for:
    • "from game.config import SNAKE_SPEED" when SNAKE_SPEED is not in config.py exports
    • "from game.game import Game" when Game is not in game.py exports
    • Mixing "from game.X" and "from src.game.X" in the same project

[2] STUB METHOD DETECTION (Python files)
    A method is a stub if its ENTIRE body is one of:
    • A single comment line:  # Check for collisions
    • Only: pass
    • Only: raise NotImplementedError
    → FIX: replace with a real implementation matching the method's purpose.
    Look especially at: check_collision(), handle_events(), draw(), update(),
    load_scores(), save_scores(), and any method returning None with no side effects.

[3] UNDEFINED self.ATTRIBUTE DETECTION (Python files)
    For every method in a class, list every self.X reference.
    Verify each self.X has a corresponding  self.X = <value>  in __init__.
    → FIX: add missing  self.X = <initial_value>  in __init__.
    Common errors: self.state_stack, self.direction, self.score referenced in methods
    but never assigned in __init__.

[4] CROSS-CLASS ATTRIBUTE ACCESS (check against responsibility_map)
    If class B uses self.screen but responsibility_map says screen is owned by class A:
    → FIX: B must receive the object as a constructor parameter and store it:
      def __init__(self, game: Game): self.game = game
      Then access: self.game.screen, NOT self.screen.

[5] REQUIREMENTS.TXT VALIDATION
    Each line must match exactly:  package_name==X.Y.Z
    → FIX: if any line contains Python code, class definitions, constants,
      or package names without version pins, rewrite the entire file with
      ONLY correct package==version lines.

[6] BATCH FILE VALIDATION (.bat files)
    A valid .bat must have at least one REAL command (not @echo off, not echo, not pause).
    run.bat must contain: python main.py  (or the actual entry point)
    install.bat must contain: pip install -r requirements.txt
    → FIX: if .bat is missing real commands, add them.

[7] __init__ SIGNATURE CONSISTENCY
    For each class in interface_contract.classes, compare constructor_signature
    with the actual __init__ in the code.
    → FIX: if main.py calls Game(width, height) but Game.__init__(self) has no
      parameters, update Game.__init__ to accept width and height.

[8] IMPORT PATH CONSISTENCY
    All project-internal imports must use the same root prefix.
    → FIX: standardise all imports to use the same root (e.g., always "from game.X",
      never "from src.game.X" in the same project).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT SCHEMA — respond ONLY with this JSON:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{{
  "fixes": [
    {{
      "path": "game/snake.py",
      "issue": "check_collision() is a stub; self.direction never initialised in __init__",
      "fixed_content": "<COMPLETE corrected file — every single line, not just the changed parts>"
    }}
  ]
}}

Rules for your output:
• fixed_content must be the COMPLETE file content, not a diff or partial snippet.
• Only include files that actually have errors.
• If ALL files pass all 8 checks above: return {{"fixes": []}}
• Do NOT add new features — only fix correctness issues found in the checklist.
• Do NOT wrap output in markdown fences or add any prose outside the JSON.

Output the JSON now (first character must be {{):"""
