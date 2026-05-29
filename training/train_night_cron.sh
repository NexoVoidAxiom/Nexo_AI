#!/bin/bash
# ============================================================
# train_night_cron.sh — Pipeline nocturno automático v2.0
# ============================================================
# Ciclo completo: scrape PDFs → study Q&A → build dataset → train → merge → deploy
#
# CRON (2:00 AM diariamente):
#   0 2 * * * /bin/bash /ruta/proyecto/training/train_night_cron.sh >> /var/log/void_train.log 2>&1
#
# VARIABLES DE ENTORNO:
#   KNOWLEDGE_BASE=/knowledge_base     directorio con PDFs
#   PROJECT_DIR=/ruta/al/proyecto      raíz del proyecto
#   OLLAMA_MODEL_NAME=void-axiom-32b   nombre en Ollama
#   STUDY_MODEL=coda_void              modelo para generar Q&A
#   SKIP_SCRAPE=1                      saltar descarga de PDFs
#   SKIP_STUDY=1                       saltar generación de Q&A
#   SKIP_TRAIN=1                       saltar entrenamiento
#   PDF_LIMIT=30                       máx PDFs a descargar por fuente
#   PDF_SOURCES=books,arxiv            fuentes activas (comma-separated)

set -euo pipefail

# ── Configuración ──────────────────────────────────────────────────────────────
PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
KNOWLEDGE_BASE="${KNOWLEDGE_BASE:-$PROJECT_DIR/knowledge_base}"
DATASETS_DIR="$PROJECT_DIR/datasets"
ADAPTERS_DIR="$PROJECT_DIR/lora_adapters"
MERGE_DIR="$PROJECT_DIR/merge_workspace"
LOG_DIR="$PROJECT_DIR/logs"
OLLAMA_MODEL="${OLLAMA_MODEL_NAME:-void-axiom-32b}"
STUDY_MODEL="${STUDY_MODEL:-coda_void}"
PDF_LIMIT="${PDF_LIMIT:-30}"
PDF_SOURCES="${PDF_SOURCES:-books,university}"
MIN_DATASET_ENTRIES=10
PYTHON="${PYTHON:-python3}"

# Controles de fases
SKIP_SCRAPE="${SKIP_SCRAPE:-0}"
SKIP_STUDY="${SKIP_STUDY:-0}"
SKIP_TRAIN="${SKIP_TRAIN:-0}"

# ── Inicialización ─────────────────────────────────────────────────────────────
mkdir -p "$LOG_DIR" "$DATASETS_DIR" "$ADAPTERS_DIR" "$KNOWLEDGE_BASE"
LOG_FILE="$LOG_DIR/train_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

print_header() {
    echo ""
    echo "╔══════════════════════════════════════════════════════╗"
    echo "║   $1"
    echo "╚══════════════════════════════════════════════════════╝"
}

print_step() {
    echo ""
    echo "── $1"
    echo "   $(date '+%H:%M:%S')"
    echo ""
}

print_header "VOID AXIOM — Pipeline Nocturno v2.0 — $(date '+%Y-%m-%d %H:%M:%S')"
echo "  Proyecto     : $PROJECT_DIR"
echo "  KnowledgeBase: $KNOWLEDGE_BASE"
echo "  Modelo       : $OLLAMA_MODEL"
echo "  Study Model  : $STUDY_MODEL"
echo "  Fases activas: scrape=$([ $SKIP_SCRAPE -eq 0 ] && echo SI || echo NO) study=$([ $SKIP_STUDY -eq 0 ] && echo SI || echo NO) train=$([ $SKIP_TRAIN -eq 0 ] && echo SI || echo NO)"

# ── Verificar GPU ──────────────────────────────────────────────────────────────
print_step "Verificando hardware"
if ! nvidia-smi &>/dev/null; then
    echo "[ERROR] GPU no disponible. Abortando."
    exit 1
fi
GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)
GPU_MEM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader | head -1)
GPU_FREE=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader | head -1)
echo "[OK] GPU: $GPU_NAME | Total: $GPU_MEM | Libre: $GPU_FREE"

# Verificar que haya suficiente VRAM libre para entrenar (≥ 10 GB)
FREE_MB=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1)
if [ "$FREE_MB" -lt 10000 ]; then
    echo "[WARN] VRAM libre insuficiente (${FREE_MB} MB < 10000 MB)"
    echo "       Esperando 5 minutos para que se libere..."
    sleep 300
    FREE_MB=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1)
    if [ "$FREE_MB" -lt 10000 ]; then
        echo "[ERROR] VRAM aún insuficiente. Abortando entrenamiento pero continuando otras fases."
        SKIP_TRAIN=1
    fi
fi

# ── Lock file ─────────────────────────────────────────────────────────────────
LOCK_FILE="/tmp/void_axiom_training.lock"
if [ -f "$LOCK_FILE" ]; then
    LOCK_AGE=$(( $(date +%s) - $(stat -c %Y "$LOCK_FILE" 2>/dev/null || echo 0) ))
    if [ "$LOCK_AGE" -lt 86400 ]; then
        echo "[WARN] Ya hay un pipeline en curso (lock $LOCK_AGE s). Abortando."
        exit 0
    else
        echo "[INFO] Lock antiguo (${LOCK_AGE}s), eliminando..."
        rm -f "$LOCK_FILE"
    fi
fi
touch "$LOCK_FILE"
trap "rm -f $LOCK_FILE; echo '[INFO] Lock liberado.'" EXIT

# ══════════════════════════════════════════════════════════════════════════════
# FASE 1 — Scrape PDFs
# ══════════════════════════════════════════════════════════════════════════════
if [ "$SKIP_SCRAPE" -eq 0 ]; then
    print_step "FASE 1: Scraping de PDFs"

    # Construir flags de fuentes
    SCRAPE_FLAGS=""
    IFS=',' read -ra SOURCES <<< "$PDF_SOURCES"
    for src in "${SOURCES[@]}"; do
        case "$src" in
            books)      SCRAPE_FLAGS="$SCRAPE_FLAGS --books" ;;
            arxiv)      SCRAPE_FLAGS="$SCRAPE_FLAGS --arxiv" ;;
            topics)     SCRAPE_FLAGS="$SCRAPE_FLAGS --topics" ;;
            university) SCRAPE_FLAGS="$SCRAPE_FLAGS --university" ;;
            pwc)        SCRAPE_FLAGS="$SCRAPE_FLAGS --pwc" ;;
            all)        SCRAPE_FLAGS="--all" ; break ;;
        esac
    done

    $PYTHON "$PROJECT_DIR/training/pdf_scraper.py" \
        $SCRAPE_FLAGS \
        --limit "$PDF_LIMIT" \
        --out "$KNOWLEDGE_BASE" || {
        echo "[WARN] Scraper terminó con error, continuando..."
    }

    # Contar PDFs disponibles
    PDF_COUNT=$(find "$KNOWLEDGE_BASE" -name "*.pdf" 2>/dev/null | wc -l)
    echo "[OK] PDFs en knowledge base: $PDF_COUNT"
else
    echo "[SKIP] Fase de scraping omitida (SKIP_SCRAPE=1)"
    PDF_COUNT=$(find "$KNOWLEDGE_BASE" -name "*.pdf" 2>/dev/null | wc -l)
    echo "       PDFs existentes: $PDF_COUNT"
fi

# ══════════════════════════════════════════════════════════════════════════════
# FASE 2 — Study Engine (Q&A automático)
# ══════════════════════════════════════════════════════════════════════════════
if [ "$SKIP_STUDY" -eq 0 ]; then
    print_step "FASE 2: Generando Q&A con Study Engine"

    # Definir OLLAMA_BASE_URL antes de usarla en el curl
    OLLAMA_BASE_URL="${VOID_OLLAMA_BASE_URL:-http://127.0.0.1:11434}"

    # Solo ejecutar si Ollama está corriendo
    if curl -sf "$OLLAMA_BASE_URL/api/tags" > /dev/null 2>&1 || \
       curl -sf "http://127.0.0.1:11434/api/tags" > /dev/null 2>&1; then
        $PYTHON "$PROJECT_DIR/training/study_engine.py" \
            --source "$KNOWLEDGE_BASE" \
            --model "$STUDY_MODEL" \
            --out "$DATASETS_DIR" \
            --limit 20 || {
            echo "[WARN] Study engine terminó con error, continuando..."
        }
        echo "[OK] Q&A generados en $DATASETS_DIR"
    else
        echo "[WARN] Ollama no accesible — saltando generación de Q&A"
        echo "       Inicia Ollama con: ollama serve"
    fi
else
    echo "[SKIP] Study Engine omitido (SKIP_STUDY=1)"
fi

# ══════════════════════════════════════════════════════════════════════════════
# FASE 3 — Construir Dataset
# ══════════════════════════════════════════════════════════════════════════════
print_step "FASE 3: Construyendo dataset JSONL"

# Buscar Q&A de study engine para fusionar
MERGE_FLAGS=""
QA_FILES=$(ls "$DATASETS_DIR"/study_qa_*.jsonl 2>/dev/null | head -5)
for qa_file in $QA_FILES; do
    MERGE_FLAGS="$MERGE_FLAGS --merge $qa_file"
done

$PYTHON "$PROJECT_DIR/training/dataset_builder.py" \
    --source "$KNOWLEDGE_BASE" \
    --out "$DATASETS_DIR" \
    --format sharegpt \
    --validate \
    --min-quality 1 \
    $MERGE_FLAGS

# Verificar dataset generado
LATEST_DATASET=$(ls -t "$DATASETS_DIR"/dataset_sharegpt_*.jsonl 2>/dev/null | head -1)
if [ -z "$LATEST_DATASET" ]; then
    echo "[ERROR] No se generó dataset. Abortando."
    exit 1
fi
ENTRIES=$(wc -l < "$LATEST_DATASET")
echo "[OK] Dataset: $ENTRIES entradas en $(basename "$LATEST_DATASET")"

if [ "$ENTRIES" -lt "$MIN_DATASET_ENTRIES" ]; then
    echo "[WARN] Menos de $MIN_DATASET_ENTRIES entradas. Saltando entrenamiento."
    SKIP_TRAIN=1
fi

# ══════════════════════════════════════════════════════════════════════════════
# FASE 4 — Entrenamiento QLoRA
# ══════════════════════════════════════════════════════════════════════════════
if [ "$SKIP_TRAIN" -eq 0 ]; then
    print_step "FASE 4: Entrenamiento QLoRA 32B"

    ADAPTER_RUN_DIR="$ADAPTERS_DIR/run_$(date +%Y%m%d_%H%M)"
    $PYTHON "$PROJECT_DIR/training/train_qlora.py" \
        --dataset "$LATEST_DATASET" \
        --output "$ADAPTER_RUN_DIR"

    FINAL_ADAPTER="$ADAPTER_RUN_DIR/final_adapter"
    if [ ! -d "$FINAL_ADAPTER" ]; then
        echo "[ERROR] No se encontró final_adapter. Abortando merge."
        exit 1
    fi
    echo "[OK] Adaptadores: $FINAL_ADAPTER"

    # ── Fase 5 — Merge y deploy ───────────────────────────────────────────────
    print_step "FASE 5: Fusión y deploy → Ollama"
    $PYTHON "$PROJECT_DIR/training/merge.py" \
        --adapter "$FINAL_ADAPTER" \
        --work-dir "$MERGE_DIR" \
        --ollama-name "$OLLAMA_MODEL" \
        --quant Q4_K_M

    # ── Fase 6 — Smoke test ───────────────────────────────────────────────────
    print_step "FASE 6: Smoke test"
    RESP=$(ollama run "$OLLAMA_MODEL" "Responde solo: VOID AXIOM OK" 2>/dev/null || echo "ERROR")
    if echo "$RESP" | grep -qi "void\|ok\|axiom"; then
        echo "[OK] Smoke test PASADO: $RESP"
    else
        echo "[WARN] Smoke test respuesta inesperada: $RESP"
    fi

    # ── Limpieza ──────────────────────────────────────────────────────────────
    print_step "Limpieza: conservando últimos 3 adapters"
    ls -dt "$ADAPTERS_DIR"/run_* 2>/dev/null | tail -n +4 | xargs rm -rf 2>/dev/null || true

else
    echo "[SKIP] Entrenamiento omitido (SKIP_TRAIN=1 o dataset insuficiente)"
fi

# ══════════════════════════════════════════════════════════════════════════════
# RESUMEN FINAL
# ══════════════════════════════════════════════════════════════════════════════
print_header "Pipeline completado — $(date '+%Y-%m-%d %H:%M:%S')"

PDF_TOTAL=$(find "$KNOWLEDGE_BASE" -name "*.pdf" 2>/dev/null | wc -l)
DS_SIZE=$(du -sh "$DATASETS_DIR" 2>/dev/null | cut -f1)

echo "  PDFs en base         : $PDF_TOTAL"
echo "  Entradas en dataset  : $ENTRIES"
echo "  Tamaño datasets dir  : $DS_SIZE"
echo "  Log guardado en      : $LOG_FILE"
if [ "$SKIP_TRAIN" -eq 0 ]; then
    echo "  Modelo activo        : $OLLAMA_MODEL"
fi
echo ""
