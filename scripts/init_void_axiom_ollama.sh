#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "============================================================"
echo "  VOID AXIOM / NEXO - Ollama aislado en GTX 1080 Ti"
echo "============================================================"

if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "[ERROR] nvidia-smi no esta disponible en PATH." >&2
  exit 1
fi

GTX_ID="$(
  nvidia-smi --query-gpu=index,name --format=csv,noheader |
    awk -F, 'tolower($2) ~ /1080 ti/ { gsub(/ /, "", $1); print $1; exit }'
)"

if [[ -z "${GTX_ID}" ]]; then
  echo "[ERROR] No se encontro una NVIDIA GeForce GTX 1080 Ti." >&2
  echo "        Revisa el ID con: nvidia-smi --query-gpu=index,name --format=csv" >&2
  exit 1
fi

if pgrep -x ollama >/dev/null 2>&1; then
  echo "[ERROR] Ollama ya esta ejecutandose." >&2
  echo "        Detenlo antes para garantizar aislamiento por CUDA_VISIBLE_DEVICES." >&2
  exit 1
fi

export CUDA_VISIBLE_DEVICES="${GTX_ID}"
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_MAX_LOADED_MODELS=4
export OLLAMA_KEEP_ALIVE=-1
export OLLAMA_CONTEXT_LENGTH=2048
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_KV_CACHE_TYPE=f16
export OLLAMA_SCHED_SPREAD=0

echo "[OK] GTX 1080 Ti detectada como GPU fisica ID ${GTX_ID}."
echo "[OK] CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES}"
echo "[OK] OLLAMA_NUM_PARALLEL=${OLLAMA_NUM_PARALLEL}"
echo "[OK] OLLAMA_MAX_LOADED_MODELS=${OLLAMA_MAX_LOADED_MODELS}"
echo "[OK] OLLAMA_KEEP_ALIVE=${OLLAMA_KEEP_ALIVE}"

if ! command -v ollama >/dev/null 2>&1; then
  echo "[ERROR] ollama no esta disponible en PATH." >&2
  exit 1
fi

echo "[INFO] Lanzando ollama serve con entorno aislado..."
nohup ollama serve > ollama/void_ollama.log 2>&1 &

echo "[INFO] Esperando API local de Ollama..."
for _ in $(seq 1 45); do
  if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

curl -fsS http://127.0.0.1:11434/api/tags >/dev/null
echo "[OK] Ollama activo."

echo "[INFO] Descargando bases ligeras cuantizadas desde Ollama si faltan..."
ollama pull qwen2.5:3b
ollama pull qwen2.5-coder:3b
ollama pull qwen2.5:1.5b

echo "[INFO] Creando perfiles Void Axiom..."
ollama create void-arch7 -f "ollama/Modelfile.arch7"
ollama create void-coda -f "ollama/Modelfile.coda"
ollama create void-rebx3 -f "ollama/Modelfile.rebx3"
ollama create void-intruder -f "ollama/Modelfile.intruder"

echo "[INFO] Precargando los 4 modelos con keep_alive=-1..."
for model in void-arch7 void-coda void-rebx3 void-intruder; do
  echo "  - ${model}"
  curl -s http://127.0.0.1:11434/api/generate \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${model}\",\"prompt\":\"ping\",\"stream\":false,\"keep_alive\":\"-1\",\"options\":{\"num_predict\":1,\"num_ctx\":256}}" \
    >/dev/null
done

echo "[INFO] Modelos residentes segun Ollama:"
ollama ps

echo "============================================================"
echo "  Entorno listo."
echo "  Backend:"
echo "  python -m uvicorn app.main:app --host 0.0.0.0 --port 8080"
echo "============================================================"
