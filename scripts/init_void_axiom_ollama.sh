#!/usr/bin/env bash
# init_void_axiom_ollama.sh — Void Axiom multi-agent init (Linux/macOS)
set -euo pipefail

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║       VOID AXIOM — INICIALIZACIÓN MULTI-AGENTE          ║"
echo "║  GTX 1080 Ti (11GB) + i7-9700K (8c) + 32GB DDR4         ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# ── Aislamiento GPU ────────────────────────────────────────────────────────────
export CUDA_VISIBLE_DEVICES=0

# ── Coexistencia de 4 modelos en VRAM ─────────────────────────────────────────
# OLLAMA_NUM_PARALLEL=1: cola estricta, sin cruce de hilos en GPU
export OLLAMA_MAX_LOADED_MODELS=4
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_KEEP_ALIVE=-1
export OLLAMA_MAX_QUEUE=512

# ── Variables del backend ──────────────────────────────────────────────────────
export VOID_OLLAMA_BASE_URL="http://127.0.0.1:11434"
export VOID_OLLAMA_HTTP_RETRIES=3
export VOID_OLLAMA_CONCURRENCY=1

export VOID_AGENT_NUM_CTX=16384
export VOID_INTRUDER_NUM_CTX=8192
export VOID_AGENT_NUM_PREDICT=200
export VOID_AGENT_TEMPERATURE=0.25
export VOID_HISTORY_MAX_MESSAGES=96
export VOID_HISTORY_MAX_TOKENS=12000
export VOID_EXTENDED_HISTORY_MAX_MESSAGES=512
export VOID_EXTENDED_HISTORY_MAX_TOKENS=55000
export VOID_ACTIVE_TURNS_TO_KEEP=4
export VOID_MAIN_IDLE_SECONDS=45
export VOID_CHAT_IDLE_SECONDS=30

# ── Verificar Ollama ───────────────────────────────────────────────────────────
echo "[..] Verificando Ollama en http://127.0.0.1:11434 ..."
if ! curl -sf http://127.0.0.1:11434 > /dev/null 2>&1; then
    echo "[WARN] Ollama no detectado. Iniciando en segundo plano..."
    ollama serve &
    OLLAMA_PID=$!
    sleep 4
    if ! curl -sf http://127.0.0.1:11434 > /dev/null 2>&1; then
        echo "[ERROR] Ollama no arrancó. Abortando."
        exit 1
    fi
fi
echo "[OK] Ollama activo."

# ── Instalar Modelfiles ────────────────────────────────────────────────────────
echo ""
echo "[..] Instalando modelos Void Axiom..."

for agent in arch7 coda rebx3 intruder; do
    mf="$PROJECT_ROOT/ollama/Modelfile.$agent"
    if [[ ! -f "$mf" ]]; then
        echo "[ERROR] Falta: $mf"
        exit 1
    fi
done

ollama create arch7_void    -f "$PROJECT_ROOT/ollama/Modelfile.arch7"
ollama create coda_void     -f "$PROJECT_ROOT/ollama/Modelfile.coda"
ollama create rebx3_void    -f "$PROJECT_ROOT/ollama/Modelfile.rebx3"
ollama create intruder_void -f "$PROJECT_ROOT/ollama/Modelfile.intruder"

echo "[OK] 4 modelos instalados."

# ── Warm-up: pre-carga en VRAM ────────────────────────────────────────────────
echo ""
echo "[..] Pre-cargando modelos en VRAM (warm-up)..."
for model in arch7_void coda_void rebx3_void intruder_void; do
    echo "    Cargando $model..."
    curl -sf -X POST http://127.0.0.1:11434/api/chat \
         -H "Content-Type: application/json" \
         -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\".\"}],\"stream\":false,\"options\":{\"num_predict\":1}}" \
         > /dev/null 2>&1 || echo "    [WARN] $model: warm-up falló (puede que el modelo esté cargando)"
done

echo "[OK] Warm-up completado."
echo ""
echo "══════════════════════════════════════════════════════════"
echo "  VOID AXIOM listo. Inicia el servidor con:"
echo "  python -m app.main"
echo "══════════════════════════════════════════════════════════"
echo ""
