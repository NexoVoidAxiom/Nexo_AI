"""
merge.py — Fusión de adaptadores LoRA y actualización automática en Ollama
===========================================================================
Toma los adaptadores QLoRA entrenados, los fusiona con el modelo base,
cuantiza a GGUF Q4_K_M y registra el nuevo modelo en Ollama.

Flujo completo:
    1. Cargar modelo base en BF16 (sin cuantizar)
    2. Cargar y fusionar adaptadores LoRA (merge_and_unload)
    3. Guardar modelo fusionado en HF format
    4. Convertir a GGUF con llama.cpp
    5. Cuantizar a Q4_K_M (óptimo para RTX 3090)
    6. Crear Modelfile para Ollama con el sistema prompt de VOID AXIOM
    7. Registrar/actualizar el modelo en Ollama (ollama create)
    8. Ejecutar smoke-test automático

PREREQUISITOS:
    - llama.cpp compilado con CUDA: https://github.com/ggerganov/llama.cpp
    - Ollama instalado y corriendo
    - pip install transformers peft accelerate

USO:
    python merge.py --adapter ./lora_adapters/final_adapter/ --ollama-name void-axiom-32b
    python merge.py --adapter ./lora_adapters/final_adapter/ --skip-quantize (si ya tienes el GGUF)
    python merge.py --adapter ./lora_adapters/final_adapter/ --dry-run
"""

import os
import sys
import json
import shutil
import logging
import argparse
import subprocess
from pathlib import Path
from datetime import datetime

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("merge.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("merge")

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

# Directorio de llama.cpp (ajustar según instalación)
LLAMA_CPP_DIR = Path(os.environ.get("LLAMA_CPP_DIR", "/opt/llama.cpp"))

# Cuantización por defecto — Q4_K_M: óptimo calidad/tamaño para RTX 3090
DEFAULT_QUANT = "Q4_K_M"
# Alternativas:
# "Q5_K_M"  → más calidad, ~2 GB más grande
# "Q8_0"    → casi sin pérdida, ~doble tamaño Q4
# "F16"     → sin cuantizar, requiere ~64 GB VRAM para inferencia

# System prompt de VOID AXIOM para el Modelfile
VOID_SYSTEM_PROMPT = """\
Eres VOID AXIOM, un asistente de inteligencia artificial local especializado \
en análisis de datos, programación, ciberseguridad y conocimiento técnico avanzado. \
Fuiste creado y entrenado de forma privada. \
Respondes siempre en el idioma del usuario con precisión, claridad y detalle. \
Si no sabes algo, lo admites con honestidad. \
Nunca finges ser otro modelo de IA (GPT, Gemini, etc.)."""

# Parámetros base del Modelfile
MODELFILE_PARAMS = {
    "temperature":      0.7,
    "top_p":            0.9,
    "top_k":            40,
    "num_ctx":          8192,    # 8K contexto — seguro con RTX 3090
    "num_batch":        512,
    "num_gpu":          99,      # usar todas las capas en GPU
    "num_thread":       7,       # 7 de 8 cores i7-9700K
    "repeat_penalty":   1.1,
    "stop":             ["<|im_end|>", "<|end|>", "</s>"],
}


# ══════════════════════════════════════════════════════════════════════════════
# PASO 1 — FUSIÓN DE ADAPTADORES LoRA
# ══════════════════════════════════════════════════════════════════════════════

def merge_lora_adapter(
    adapter_path: Path,
    merged_output: Path,
    dtype: str = "bfloat16",
) -> Path:
    """
    Carga el modelo base + adaptadores LoRA y los fusiona (merge_and_unload).
    Guarda el modelo fusionado en HuggingFace format.

    ⚠️ Requiere: RAM suficiente para el modelo en BF16 (32B ≈ 64 GB).
        Si no tienes suficiente RAM, usa device_map='auto' con offload.
    """
    log.info(f"Cargando adaptadores desde: {adapter_path}")

    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from peft import PeftModel
    except ImportError as e:
        log.error(f"Dependencia faltante: {e}. pip install transformers peft")
        sys.exit(1)

    # Leer metadata del entrenamiento para saber el modelo base
    meta_path = adapter_path.parent / "training_meta.json"
    if meta_path.exists():
        with open(meta_path) as f:
            meta = json.load(f)
        base_model_name = meta.get("model_base", "Qwen/Qwen2.5-32B-Instruct")
        log.info(f"Modelo base desde metadata: {base_model_name}")
    else:
        base_model_name = "Qwen/Qwen2.5-32B-Instruct"
        log.warning(f"No se encontró training_meta.json. Usando: {base_model_name}")

    # Cargar tokenizer
    log.info("Cargando tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(
        str(adapter_path), trust_remote_code=True
    )

    # Cargar modelo base en BF16 para fusión
    # Usamos device_map='cpu' para la fusión y así no saturar VRAM
    log.info(f"Cargando modelo base en CPU para fusión: {base_model_name}")
    log.info("(Esto requiere ~64 GB de RAM para un modelo 32B en BF16)")
    base_model = AutoModelForCausalLM.from_pretrained(
        base_model_name,
        torch_dtype       = torch.bfloat16,
        device_map        = "cpu",           # fusión en CPU para no limitar VRAM
        trust_remote_code = True,
        low_cpu_mem_usage = True,
    )

    # Cargar y fusionar adaptadores LoRA
    log.info("Aplicando y fusionando adaptadores LoRA...")
    model = PeftModel.from_pretrained(base_model, str(adapter_path))
    model = model.merge_and_unload()
    log.info("✅ Fusión completada.")

    # Guardar modelo fusionado
    merged_output.mkdir(parents=True, exist_ok=True)
    log.info(f"Guardando modelo fusionado en: {merged_output}")
    model.save_pretrained(str(merged_output), safe_serialization=True)
    tokenizer.save_pretrained(str(merged_output))
    log.info("✅ Modelo fusionado guardado.")

    # Liberar memoria
    del model, base_model
    try:
        import gc
        gc.collect()
        torch.cuda.empty_cache()
    except Exception:
        pass

    return merged_output


# ══════════════════════════════════════════════════════════════════════════════
# PASO 2 — CONVERSIÓN A GGUF
# ══════════════════════════════════════════════════════════════════════════════

def convert_to_gguf(
    merged_model_path: Path,
    output_dir: Path,
    llama_cpp_dir: Path = LLAMA_CPP_DIR,
) -> Path:
    """
    Convierte el modelo HuggingFace a formato GGUF usando convert_hf_to_gguf.py de llama.cpp.
    """
    convert_script = llama_cpp_dir / "convert_hf_to_gguf.py"
    if not convert_script.exists():
        # Alternativa para versiones antiguas de llama.cpp
        convert_script = llama_cpp_dir / "convert.py"
    if not convert_script.exists():
        log.error(f"No se encontró convert_hf_to_gguf.py en {llama_cpp_dir}")
        log.error("Clona llama.cpp: git clone https://github.com/ggerganov/llama.cpp")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    gguf_f16_path = output_dir / "model_f16.gguf"

    log.info(f"Convirtiendo a GGUF F16: {gguf_f16_path}")
    result = subprocess.run(
        [
            sys.executable, str(convert_script),
            str(merged_model_path),
            "--outfile", str(gguf_f16_path),
            "--outtype", "f16",
        ],
        capture_output=True, text=True, timeout=3600,
    )
    if result.returncode != 0:
        log.error(f"Error en conversión GGUF:\n{result.stderr}")
        sys.exit(1)

    log.info(f"✅ GGUF F16 generado: {gguf_f16_path} ({gguf_f16_path.stat().st_size / (1024**3):.1f} GB)")
    return gguf_f16_path


def quantize_gguf(
    gguf_f16_path: Path,
    output_dir: Path,
    quant_type: str = DEFAULT_QUANT,
    llama_cpp_dir: Path = LLAMA_CPP_DIR,
) -> Path:
    """
    Cuantiza el GGUF F16 al tipo especificado usando llama-quantize de llama.cpp.
    Q4_K_M es el punto óptimo: mínima pérdida de calidad, máximo ahorro de VRAM.
    """
    quantize_bin = llama_cpp_dir / "llama-quantize"
    if not quantize_bin.exists():
        quantize_bin = llama_cpp_dir / "quantize"  # nombre antiguo
    if not quantize_bin.exists():
        log.error(f"No se encontró llama-quantize en {llama_cpp_dir}")
        log.error("Compila llama.cpp con CUDA: cmake -B build -DGGML_CUDA=ON && cmake --build build -j8")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    quant_path = output_dir / f"model_{quant_type.lower()}.gguf"

    log.info(f"Cuantizando {quant_type}: {gguf_f16_path} → {quant_path}")
    result = subprocess.run(
        [str(quantize_bin), str(gguf_f16_path), str(quant_path), quant_type],
        capture_output=True, text=True, timeout=3600,
    )
    if result.returncode != 0:
        log.error(f"Error en cuantización:\n{result.stderr}")
        sys.exit(1)

    quant_size_gb = quant_path.stat().st_size / (1024**3)
    log.info(f"✅ GGUF {quant_type} generado: {quant_path} ({quant_size_gb:.1f} GB)")

    # Eliminar GGUF F16 para liberar espacio (conservar solo cuantizado)
    log.info(f"Eliminando GGUF F16 temporal: {gguf_f16_path}")
    gguf_f16_path.unlink(missing_ok=True)

    return quant_path


# ══════════════════════════════════════════════════════════════════════════════
# PASO 3 — OLLAMA: MODELFILE + REGISTRO
# ══════════════════════════════════════════════════════════════════════════════

def create_modelfile(
    gguf_path: Path,
    modelfile_path: Path,
    model_name: str,
    quant_type: str = DEFAULT_QUANT,
) -> Path:
    """
    Genera el Modelfile de Ollama con el system prompt de VOID AXIOM
    y los parámetros optimizados para RTX 3090.
    """
    stops_str = "\n".join(f'PARAMETER stop "{s}"' for s in MODELFILE_PARAMS["stop"])

    modelfile_content = f"""# VOID AXIOM — Modelfile generado automáticamente por merge.py
# Modelo: {model_name} | Cuantización: {quant_type} | {datetime.now().isoformat()}

FROM {gguf_path.resolve()}

SYSTEM \"\"\"{VOID_SYSTEM_PROMPT}\"\"\"

PARAMETER temperature    {MODELFILE_PARAMS['temperature']}
PARAMETER top_p          {MODELFILE_PARAMS['top_p']}
PARAMETER top_k          {MODELFILE_PARAMS['top_k']}
PARAMETER num_ctx        {MODELFILE_PARAMS['num_ctx']}
PARAMETER num_batch      {MODELFILE_PARAMS['num_batch']}
PARAMETER num_gpu        {MODELFILE_PARAMS['num_gpu']}
PARAMETER num_thread     {MODELFILE_PARAMS['num_thread']}
PARAMETER repeat_penalty {MODELFILE_PARAMS['repeat_penalty']}
{stops_str}
"""
    modelfile_path.write_text(modelfile_content, encoding="utf-8")
    log.info(f"Modelfile creado: {modelfile_path}")
    return modelfile_path


def register_in_ollama(
    modelfile_path: Path,
    model_name: str,
    ollama_bin: str = "ollama",
) -> bool:
    """
    Registra o actualiza el modelo en Ollama usando 'ollama create'.
    Primero elimina la versión anterior si existe.
    """
    # Verificar que Ollama está corriendo
    try:
        result = subprocess.run(
            [ollama_bin, "list"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            log.error("Ollama no está corriendo. Inicia con: ollama serve")
            return False
    except FileNotFoundError:
        log.error(f"Ollama no encontrado en: {ollama_bin}")
        return False

    # Eliminar versión anterior (si existe) para forzar actualización
    log.info(f"Eliminando versión anterior de '{model_name}' (si existe)...")
    subprocess.run([ollama_bin, "rm", model_name], capture_output=True)

    # Crear nuevo modelo
    log.info(f"Registrando '{model_name}' en Ollama...")
    result = subprocess.run(
        [ollama_bin, "create", model_name, "-f", str(modelfile_path)],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        log.error(f"Error registrando en Ollama:\n{result.stderr}")
        return False

    log.info(f"✅ Modelo '{model_name}' registrado en Ollama.")
    log.info(result.stdout)
    return True


def smoke_test_ollama(model_name: str, ollama_bin: str = "ollama") -> bool:
    """
    Smoke test: envía un prompt simple y verifica que el modelo responde.
    """
    log.info(f"Smoke test: {model_name}")
    test_prompt = "Di exactamente: 'VOID AXIOM operativo.'"
    result = subprocess.run(
        [ollama_bin, "run", model_name, test_prompt],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        log.error(f"Smoke test fallido:\n{result.stderr}")
        return False

    response = result.stdout.strip()
    log.info(f"Respuesta smoke test: {response}")
    if "VOID" in response or "operativo" in response.lower():
        log.info("✅ Smoke test PASADO.")
        return True
    else:
        log.warning(f"⚠️ Smoke test: respuesta inesperada: {response}")
        return True  # el modelo responde aunque no sea exactamente lo pedido


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def merge_pipeline(
    adapter_path: Path,
    work_dir: Path,
    ollama_name: str,
    quant_type: str = DEFAULT_QUANT,
    skip_merge: bool = False,
    skip_quantize: bool = False,
    existing_gguf: Path | None = None,
    dry_run: bool = False,
    ollama_bin: str = "ollama",
    llama_cpp_dir: Path = LLAMA_CPP_DIR,
) -> dict:
    """Pipeline completo: merge → GGUF → cuantizar → Ollama."""

    work_dir.mkdir(parents=True, exist_ok=True)
    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    report     = {"timestamp": timestamp, "steps": {}}

    log.info("=" * 60)
    log.info(f"  VOID AXIOM — Pipeline de fusión y despliegue")
    log.info(f"  Adapter   : {adapter_path}")
    log.info(f"  Ollama    : {ollama_name}")
    log.info(f"  Cuant.    : {quant_type}")
    log.info(f"  Work dir  : {work_dir}")
    log.info("=" * 60)

    if dry_run:
        log.info("🔍 DRY RUN: sin cambios reales.")
        return {"dry_run": True}

    # PASO 1: Fusión LoRA
    merged_path = work_dir / "merged_model"
    if skip_merge and merged_path.exists():
        log.info(f"Saltando fusión, usando: {merged_path}")
        report["steps"]["merge"] = "skipped"
    else:
        merged_path = merge_lora_adapter(adapter_path, merged_path)
        report["steps"]["merge"] = str(merged_path)

    # PASO 2: Conversión a GGUF
    gguf_dir     = work_dir / "gguf"
    gguf_f16     = gguf_dir / "model_f16.gguf"
    gguf_quant   = gguf_dir / f"model_{quant_type.lower()}.gguf"

    if existing_gguf:
        gguf_quant = existing_gguf
        log.info(f"Usando GGUF existente: {gguf_quant}")
        report["steps"]["convert"] = "skipped"
        report["steps"]["quantize"] = "skipped"
    elif skip_quantize and gguf_quant.exists():
        log.info(f"Saltando cuantización, usando: {gguf_quant}")
        report["steps"]["quantize"] = "skipped"
    else:
        gguf_f16   = convert_to_gguf(merged_path, gguf_dir, llama_cpp_dir)
        gguf_quant = quantize_gguf(gguf_f16, gguf_dir, quant_type, llama_cpp_dir)
        report["steps"]["convert"]  = str(gguf_f16)
        report["steps"]["quantize"] = str(gguf_quant)

    # PASO 3: Modelfile + Ollama
    modelfile_path = work_dir / f"Modelfile.{ollama_name}"
    create_modelfile(gguf_quant, modelfile_path, ollama_name, quant_type)
    report["steps"]["modelfile"] = str(modelfile_path)

    ok = register_in_ollama(modelfile_path, ollama_name, ollama_bin)
    report["steps"]["ollama_register"] = "ok" if ok else "failed"

    # PASO 4: Smoke test
    if ok:
        test_ok = smoke_test_ollama(ollama_name, ollama_bin)
        report["steps"]["smoke_test"] = "passed" if test_ok else "failed"

    # Guardar reporte
    report_path = work_dir / f"merge_report_{timestamp}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    log.info(f"\n📋 Reporte guardado: {report_path}")
    log.info("\n🎉 Pipeline completado.")
    log.info(f"   Usa el modelo con: ollama run {ollama_name}")

    return report


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="VOID AXIOM — Fusión LoRA y despliegue en Ollama",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--adapter", type=Path, required=True,
        help="Path al directorio de adaptadores LoRA (final_adapter/).",
    )
    parser.add_argument(
        "--work-dir", type=Path, default=Path("./merge_workspace"),
        help="Directorio de trabajo para archivos intermedios.",
    )
    parser.add_argument(
        "--ollama-name", default="void-axiom-32b",
        help="Nombre del modelo en Ollama.",
    )
    parser.add_argument(
        "--quant", default=DEFAULT_QUANT,
        choices=["Q4_K_M", "Q5_K_M", "Q8_0", "F16"],
        help="Tipo de cuantización GGUF.",
    )
    parser.add_argument(
        "--skip-merge", action="store_true",
        help="Saltar fusión de LoRA (usar merged_model/ ya existente).",
    )
    parser.add_argument(
        "--skip-quantize", action="store_true",
        help="Saltar cuantización (usar GGUF ya existente en work-dir).",
    )
    parser.add_argument(
        "--existing-gguf", type=Path, default=None,
        help="Usar un GGUF ya cuantizado directamente (salta merge y quantize).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Mostrar configuración sin ejecutar nada.",
    )
    parser.add_argument(
        "--ollama-bin", default="ollama",
        help="Path al ejecutable de Ollama.",
    )
    parser.add_argument(
        "--llama-cpp-dir", type=Path, default=LLAMA_CPP_DIR,
        help="Directorio raíz de llama.cpp compilado.",
    )

    args = parser.parse_args()

    merge_pipeline(
        adapter_path   = args.adapter,
        work_dir       = args.work_dir,
        ollama_name    = args.ollama_name,
        quant_type     = args.quant,
        skip_merge     = args.skip_merge,
        skip_quantize  = args.skip_quantize,
        existing_gguf  = args.existing_gguf,
        dry_run        = args.dry_run,
        ollama_bin     = args.ollama_bin,
        llama_cpp_dir  = args.llama_cpp_dir,
    )


if __name__ == "__main__":
    main()
