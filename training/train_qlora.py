"""
train_qlora.py — Entrenamiento QLoRA para modelos 32B en RTX 3090 (24 GB VRAM)
===============================================================================
Configurado para:
  - RTX 3090 (24 GB VRAM, Ampere, compute 8.6)
  - i7-9700K + 32 GB RAM
  - 4-bit quantization (bitsandbytes NF4)
  - gradient_checkpointing activado
  - paged_adamw_8bit (reduce uso de VRAM del optimizador)
  - Flash Attention 2 (Ampere nativo)
  - Modelo base recomendado: Qwen2.5-32B-Instruct o DeepSeek-R1-Distill-Qwen-32B

INSTALACIÓN PREVIA:
    pip install torch==2.3.0 torchvision --index-url https://download.pytorch.org/whl/cu121
    pip install transformers==4.44.0 peft==0.12.0 trl==0.9.6
    pip install bitsandbytes==0.43.3 accelerate==0.33.0
    pip install flash-attn --no-build-isolation
    pip install datasets sentencepiece protobuf

USO:
    # Entrenamiento completo
    python train_qlora.py --dataset ./datasets/dataset_sharegpt_*.jsonl --output ./lora_adapters/

    # Solo validar configuración sin entrenar
    python train_qlora.py --dataset ./datasets/dataset.jsonl --dry-run

    # Con modelo base personalizado
    python train_qlora.py --model Qwen/Qwen2.5-32B-Instruct --dataset ./datasets/dataset.jsonl

    # Continuar entrenamiento desde checkpoint
    python train_qlora.py --dataset ./datasets/dataset.jsonl --resume ./lora_adapters/checkpoint-500/
"""

import os
import sys
import json
import logging
import argparse
from pathlib import Path
from datetime import datetime

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("train_qlora.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("train_qlora")

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN OPTIMIZADA PARA RTX 3090 + 32B
# ══════════════════════════════════════════════════════════════════════════════

# Modelo base por defecto (descargado automáticamente desde HuggingFace)
DEFAULT_MODEL = "Qwen/Qwen2.5-32B-Instruct"

# Alternativas soportadas:
# "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B"  ← excelente para razonamiento
# "mistralai/Mistral-Small-3.1-24B-Instruct-2503"  ← si quieres 24B más cómodo
# "Qwen/Qwen2.5-Coder-32B-Instruct"  ← si el foco es código

# ── QLoRA: cuantización 4-bit NF4 ────────────────────────────────────────────
BNBCONFIG = {
    "load_in_4bit":                 True,
    "bnb_4bit_quant_type":          "nf4",         # NF4 > FP4 en calidad
    "bnb_4bit_compute_dtype":       "bfloat16",    # BF16: nativo en Ampere, estable
    "bnb_4bit_use_double_quant":    True,           # QLoRA doble-cuantización
}

# ── LoRA: adaptadores de rango bajo ──────────────────────────────────────────
LORACONFIG = {
    # r=16 es el punto dulce para RTX 3090 con 32B:
    # r=8  → muy poco impacto en modelos grandes
    # r=32 → demasiada VRAM, puede no caber
    "r":            16,
    "lora_alpha":   32,            # alpha = 2×r es la regla empírica
    "lora_dropout": 0.05,
    "bias":         "none",
    # Capas target para Qwen2.5 / LLaMA-like architectures
    "target_modules": [
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ],
    "task_type":    "CAUSAL_LM",
}

# ── Training: ajustado para caber en 24 GB VRAM ──────────────────────────────
TRAINING_ARGS = {
    # ── Batch y gradientes
    "per_device_train_batch_size":      1,     # con 32B en 4-bit, 1 es seguro
    "gradient_accumulation_steps":      8,     # effective batch = 8 → similar a bs=8
    "gradient_checkpointing":           True,  # ahorra ~40% VRAM, algo más lento
    "gradient_checkpointing_kwargs":    {"use_reentrant": False},

    # ── Optimizador
    "optim":                            "paged_adamw_8bit",  # paginado: VRAM del optim en RAM
    "learning_rate":                    2e-4,
    "weight_decay":                     0.001,
    "max_grad_norm":                    0.3,   # recorte de gradientes

    # ── Scheduler
    "lr_scheduler_type":                "cosine",
    "warmup_ratio":                     0.03,

    # ── Épocas y pasos
    "num_train_epochs":                 3,
    "max_steps":                        -1,    # -1 = usar épocas
    "save_steps":                       250,
    "logging_steps":                    10,
    "eval_steps":                       250,

    # ── Precisión
    "bf16":                             True,  # BF16 nativo en Ampere
    "fp16":                             False, # no mezclar con BF16
    "tf32":                             True,  # TF32 en Ampere para speedup adicional

    # ── Context y padding
    "max_seq_length":                   2048,  # 2K seguro con 32B en 24 GB VRAM
    "packing":                          True,  # empaca múltiples ejemplos cortos

    # ── Dataset
    "dataset_text_field":               "text",
    "report_to":                        "none",  # cambiar a "wandb" si quieres tracking

    # ── Otros
    "group_by_length":                  True,  # agrupa por longitud → menos padding
    "dataloader_num_workers":           4,     # i7-9700K tiene 8 cores, usar 4 para datos
    "remove_unused_columns":            False,
    "save_total_limit":                 3,     # guarda solo los 3 últimos checkpoints
    "load_best_model_at_end":           False, # con packing y causal LM, mejor false
    "prediction_loss_only":             True,
}


# ══════════════════════════════════════════════════════════════════════════════
# CARGA DE DATASET
# ══════════════════════════════════════════════════════════════════════════════

def load_jsonl_dataset(paths: list[Path], tokenizer, max_seq_length: int):
    """
    Carga uno o varios archivos JSONL.
    Soporta formato ShareGPT y Alpaca.
    Convierte al formato 'text' que espera SFTTrainer.
    """
    from datasets import Dataset

    raw_entries = []
    for p in paths:
        log.info(f"Cargando dataset: {p}")
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    raw_entries.append(entry)
                except json.JSONDecodeError as e:
                    log.warning(f"Línea JSON inválida en {p}: {e}")

    log.info(f"Entradas cargadas: {len(raw_entries)}")

    # Detectar formato (ShareGPT tiene 'conversations', Alpaca tiene 'instruction')
    sample = raw_entries[0] if raw_entries else {}
    fmt = "sharegpt" if "conversations" in sample else "alpaca"
    log.info(f"Formato detectado: {fmt}")

    def format_sharegpt(entry: dict) -> str:
        """Aplica chat template de Qwen/LLaMA."""
        messages = []
        for conv in entry.get("conversations", []):
            role = conv["from"]
            if role == "system":
                messages.append({"role": "system", "content": conv["value"]})
            elif role == "human":
                messages.append({"role": "user", "content": conv["value"]})
            elif role == "gpt":
                messages.append({"role": "assistant", "content": conv["value"]})
        try:
            return tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=False
            )
        except Exception:
            # Fallback manual si el tokenizer no tiene chat template
            parts = []
            for m in messages:
                parts.append(f"<|{m['role']}|>\n{m['content']}<|end|>")
            return "\n".join(parts) + tokenizer.eos_token

    def format_alpaca(entry: dict) -> str:
        inst = entry.get("instruction", "")
        inp  = entry.get("input", "")
        out  = entry.get("output", "")
        prompt = f"### Instrucción:\n{inst}\n\n### Entrada:\n{inp}\n\n### Respuesta:\n{out}"
        return prompt + tokenizer.eos_token

    formatter = format_sharegpt if fmt == "sharegpt" else format_alpaca

    texts = []
    skipped = 0
    for entry in raw_entries:
        text = formatter(entry)
        # Filtrar por longitud
        tok_len = len(tokenizer.encode(text))
        if tok_len > max_seq_length:
            skipped += 1
            continue
        texts.append({"text": text})

    if skipped:
        log.info(f"Ejemplos saltados por longitud > {max_seq_length}: {skipped}")

    log.info(f"Dataset final: {len(texts)} ejemplos")
    return Dataset.from_list(texts)


# ══════════════════════════════════════════════════════════════════════════════
# MONITOREO DE VRAM
# ══════════════════════════════════════════════════════════════════════════════

def log_vram_usage(stage: str = ""):
    """Registra el uso de VRAM en el log."""
    try:
        import torch
        if torch.cuda.is_available():
            allocated = torch.cuda.memory_allocated() / (1024**3)
            reserved  = torch.cuda.memory_reserved()  / (1024**3)
            log.info(f"[VRAM {stage}] Asignada: {allocated:.2f} GB | Reservada: {reserved:.2f} GB")
    except Exception:
        pass


def check_gpu_requirements():
    """Verifica que el hardware sea compatible."""
    try:
        import torch
        if not torch.cuda.is_available():
            log.error("❌ CUDA no disponible. Este script requiere GPU NVIDIA.")
            sys.exit(1)
        gpu_name  = torch.cuda.get_device_name(0)
        vram_gb   = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        compute   = torch.cuda.get_device_capability(0)
        log.info(f"GPU detectada: {gpu_name}")
        log.info(f"VRAM total  : {vram_gb:.1f} GB")
        log.info(f"Compute cap : {compute[0]}.{compute[1]}")
        if vram_gb < 20:
            log.warning(f"⚠️  Solo {vram_gb:.1f} GB VRAM. Se recomienda al menos 24 GB para 32B en 4-bit.")
        if compute[0] < 8:
            log.warning("⚠️  Ampere (compute 8.0+) recomendado para BF16 y Flash Attention 2.")
        return vram_gb
    except ImportError:
        log.error("PyTorch no instalado. pip install torch")
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# ENTRENAMIENTO PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def train(
    model_name: str,
    dataset_paths: list[Path],
    output_dir: Path,
    resume_from: Path | None = None,
    dry_run: bool = False,
):
    """Pipeline de entrenamiento QLoRA completo."""

    # 1. Verificar GPU
    vram_gb = check_gpu_requirements()
    log.info("=" * 60)
    log.info("  VOID AXIOM — QLoRA 32B Training")
    log.info(f"  Modelo  : {model_name}")
    log.info(f"  VRAM    : {vram_gb:.1f} GB")
    log.info(f"  Dataset : {dataset_paths}")
    log.info(f"  Output  : {output_dir}")
    log.info("=" * 60)

    if dry_run:
        log.info("🔍 DRY RUN: validando configuración sin cargar el modelo.")
        log.info("✅ Configuración QLoRA válida. Usa --no-dry-run para entrenar.")
        return

    # 2. Imports de entrenamiento
    try:
        import torch
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
            TrainingArguments,
        )
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
        from trl import SFTTrainer, DataCollatorForCompletionOnlyLM
    except ImportError as e:
        log.error(f"Dependencia no encontrada: {e}")
        log.error("Instala con: pip install transformers peft trl bitsandbytes accelerate")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    # 3. Cuantización 4-bit NF4
    log.info("Configurando BitsAndBytes 4-bit NF4...")
    bnb_config = BitsAndBytesConfig(
        load_in_4bit                = BNBCONFIG["load_in_4bit"],
        bnb_4bit_quant_type         = BNBCONFIG["bnb_4bit_quant_type"],
        bnb_4bit_compute_dtype      = torch.bfloat16,
        bnb_4bit_use_double_quant   = BNBCONFIG["bnb_4bit_use_double_quant"],
    )

    # 4. Tokenizer
    log.info(f"Cargando tokenizer: {model_name}")
    tokenizer = AutoTokenizer.from_pretrained(
        model_name,
        trust_remote_code = True,
        padding_side      = "right",  # para SFTTrainer con packing
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
        log.info("pad_token asignado al eos_token")

    # 5. Modelo base en 4-bit
    log.info(f"Cargando modelo en 4-bit: {model_name}")
    log.info("(Esto puede tardar varios minutos la primera vez...)")
    log_vram_usage("antes de cargar modelo")

    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        quantization_config   = bnb_config,
        device_map            = "auto",          # auto-distribuye en GPU/RAM
        trust_remote_code     = True,
        attn_implementation   = "flash_attention_2",  # FA2 nativo en Ampere
        torch_dtype           = torch.bfloat16,
    )
    log_vram_usage("después de cargar modelo")

    # 6. Preparar para QLoRA
    model = prepare_model_for_kbit_training(
        model,
        use_gradient_checkpointing         = True,
        gradient_checkpointing_kwargs      = {"use_reentrant": False},
    )

    # 7. Aplicar LoRA
    log.info(f"Aplicando LoRA: r={LORACONFIG['r']}, alpha={LORACONFIG['lora_alpha']}")
    peft_config = LoraConfig(
        r               = LORACONFIG["r"],
        lora_alpha      = LORACONFIG["lora_alpha"],
        lora_dropout    = LORACONFIG["lora_dropout"],
        bias            = LORACONFIG["bias"],
        target_modules  = LORACONFIG["target_modules"],
        task_type       = LORACONFIG["task_type"],
    )
    model = get_peft_model(model, peft_config)
    model.print_trainable_parameters()
    log_vram_usage("después de aplicar LoRA")

    # 8. Dataset
    dataset = load_jsonl_dataset(
        dataset_paths,
        tokenizer,
        max_seq_length = TRAINING_ARGS["max_seq_length"],
    )

    # Split train/eval 95/5
    split = dataset.train_test_split(test_size=0.05, seed=42)
    train_dataset = split["train"]
    eval_dataset  = split["test"]
    log.info(f"Train: {len(train_dataset)} | Eval: {len(eval_dataset)}")

    # 9. Training Arguments
    run_name = f"void_axiom_qlora_{datetime.now().strftime('%Y%m%d_%H%M')}"
    training_args = TrainingArguments(
        output_dir                      = str(output_dir),
        run_name                        = run_name,
        per_device_train_batch_size     = TRAINING_ARGS["per_device_train_batch_size"],
        per_device_eval_batch_size      = 1,
        gradient_accumulation_steps     = TRAINING_ARGS["gradient_accumulation_steps"],
        gradient_checkpointing          = TRAINING_ARGS["gradient_checkpointing"],
        gradient_checkpointing_kwargs   = TRAINING_ARGS["gradient_checkpointing_kwargs"],
        optim                           = TRAINING_ARGS["optim"],
        learning_rate                   = TRAINING_ARGS["learning_rate"],
        weight_decay                    = TRAINING_ARGS["weight_decay"],
        max_grad_norm                   = TRAINING_ARGS["max_grad_norm"],
        lr_scheduler_type               = TRAINING_ARGS["lr_scheduler_type"],
        warmup_ratio                    = TRAINING_ARGS["warmup_ratio"],
        num_train_epochs                = TRAINING_ARGS["num_train_epochs"],
        save_steps                      = TRAINING_ARGS["save_steps"],
        eval_steps                      = TRAINING_ARGS["eval_steps"],
        evaluation_strategy             = "steps",
        logging_steps                   = TRAINING_ARGS["logging_steps"],
        bf16                            = TRAINING_ARGS["bf16"],
        fp16                            = TRAINING_ARGS["fp16"],
        tf32                            = TRAINING_ARGS["tf32"],
        group_by_length                 = TRAINING_ARGS["group_by_length"],
        dataloader_num_workers          = TRAINING_ARGS["dataloader_num_workers"],
        remove_unused_columns           = TRAINING_ARGS["remove_unused_columns"],
        save_total_limit                = TRAINING_ARGS["save_total_limit"],
        report_to                       = TRAINING_ARGS["report_to"],
        resume_from_checkpoint          = str(resume_from) if resume_from else None,
    )

    # 10. SFTTrainer
    log.info("Inicializando SFTTrainer...")
    trainer = SFTTrainer(
        model           = model,
        tokenizer       = tokenizer,
        train_dataset   = train_dataset,
        eval_dataset    = eval_dataset,
        args            = training_args,
        dataset_text_field  = "text",
        max_seq_length  = TRAINING_ARGS["max_seq_length"],
        packing         = TRAINING_ARGS["packing"],
    )

    # 11. Entrenar
    log.info("🚀 Iniciando entrenamiento QLoRA...")
    log_vram_usage("inicio entrenamiento")

    trainer.train(resume_from_checkpoint=str(resume_from) if resume_from else None)

    log.info("✅ Entrenamiento completado.")
    log_vram_usage("fin entrenamiento")

    # 12. Guardar adaptadores LoRA finales
    final_adapter_path = output_dir / "final_adapter"
    trainer.model.save_pretrained(str(final_adapter_path))
    tokenizer.save_pretrained(str(final_adapter_path))
    log.info(f"✅ Adaptadores guardados en: {final_adapter_path}")

    # 13. Guardar metadata
    meta = {
        "model_base":    model_name,
        "adapter_path":  str(final_adapter_path),
        "lora_config":   LORACONFIG,
        "training_args": {k: str(v) for k, v in TRAINING_ARGS.items()},
        "dataset_files": [str(p) for p in dataset_paths],
        "train_samples": len(train_dataset),
        "eval_samples":  len(eval_dataset),
        "finished_at":   datetime.now().isoformat(),
    }
    with open(output_dir / "training_meta.json", "w") as f:
        json.dump(meta, f, indent=2)

    return final_adapter_path


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="VOID AXIOM — Entrenamiento QLoRA 32B en RTX 3090",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help="Modelo base HuggingFace o path local.",
    )
    parser.add_argument(
        "--dataset", action="append", type=Path, required=True,
        help="Archivos .jsonl de entrenamiento (se puede repetir).",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("./lora_adapters"),
        help="Directorio de salida para adaptadores LoRA.",
    )
    parser.add_argument(
        "--resume", type=Path, default=None,
        help="Checkpoint desde el que continuar entrenamiento.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validar configuración sin cargar el modelo ni entrenar.",
    )
    args = parser.parse_args()

    train(
        model_name    = args.model,
        dataset_paths = args.dataset,
        output_dir    = args.output,
        resume_from   = args.resume,
        dry_run       = args.dry_run,
    )


if __name__ == "__main__":
    main()
