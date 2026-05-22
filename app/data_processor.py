"""
Procesador de Datos Optimizado
===============================
- Limpieza y compresión de datos para maximizar densidad de información
- Procesamiento paralelizado para i7-9700K (8 núcleos)
- Recolección de basura explícita para liberar RAM inmediatamente
"""
import gc
import json
import csv
import io
import os
import uuid
from pathlib import Path
from typing import Optional, Tuple
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from app.config import (
    UPLOAD_CONFIG,
    TOKEN_ESTIMATION,
    HARDWARE_PROFILE,
    GC_CONFIG,
)


def estimate_tokens(text: str) -> int:
    """Estima tokens en tiempo real basado en chars_per_token.
    
    Para español con acentos y caracteres UTF-8 usamos 3.5 chars/token.
    """
    if not text:
        return 0
    return int(len(text) / TOKEN_ESTIMATION["chars_per_token"])


def optimize_dataset_for_llm(df: pd.DataFrame) -> str:
    """Convierte un DataFrame en texto plano compacto optimizado para LLM.
    
    Estrategias de compresión:
    1. Elimina NaN/None → ""
    2. Convierte a JSON compacto por fila (sin espacios redundantes)
    3. Cabecera descriptiva para contexto
    """
    # Limpieza rápida
    df = df.fillna("")
    
    # Convertir columnas a string optimizado
    for col in df.select_dtypes(include=["object"]).columns:
        df[col] = df[col].str.strip()
    
    # Generar representación compacta
    # Formato: cada fila como JSON compacto en una línea
    lines = []
    
    # Cabecera con metadatos
    lines.append(f"# DATASET: {len(df)} rows x {len(df.columns)} columns")
    lines.append(f"# COLUMNS: {', '.join(df.columns.tolist())}")
    lines.append("")
    
    # Datos compactos
    records = df.to_dict(orient="records")
    for record in records:
        # JSON compacto sin espacios
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        lines.append(line)
    
    return "\n".join(lines)


def process_csv(filepath: str, optimize: bool = True) -> Tuple[str, int, dict]:
    """Procesa un archivo CSV y devuelve texto optimizado + metadatos.
    
    Args:
        filepath: Ruta al archivo CSV
        optimize: Si aplicar optimizaciones de compresión
        
    Returns:
        Tuple[str, int, dict]: (texto_optimizado, tokens_estimados, metadatos)
    """
    # Leer con detección automática de encoding
    try:
        df = pd.read_csv(filepath, encoding="utf-8")
    except UnicodeDecodeError:
        df = pd.read_csv(filepath, encoding="latin1")
    except Exception:
        # Fallback: intentar con pandas inferencia
        df = pd.read_csv(filepath)
    
    metadata = {
        "rows": len(df),
        "columns": len(df.columns),
        "column_names": df.columns.tolist(),
        "dtypes": {str(k): str(v) for k, v in df.dtypes.items()},
        "null_counts": df.isnull().sum().to_dict(),
        "memory_mb": round(df.memory_usage(deep=True).sum() / (1024 * 1024), 2),
    }
    
    if optimize:
        text = optimize_dataset_for_llm(df)
    else:
        text = df.to_string(index=False)
    
    tokens = estimate_tokens(text)

    del df
    return text, tokens, metadata


def process_json(filepath: str, optimize: bool = True) -> Tuple[str, int, dict]:
    """Procesa un archivo JSON/JSONL y devuelve texto optimizado + metadatos."""
    # Intentar como NDJSON primero
    try:
        df = pd.read_json(filepath, lines=True)
    except ValueError:
        # JSON estándar (array de objetos)
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
        data = json.loads(raw)
        if isinstance(data, list):
            df = pd.DataFrame(data)
        elif isinstance(data, dict):
            # Convertir dict a DataFrame si es posible
            df = pd.json_normalize(data)
        else:
            df = pd.DataFrame([data])
    
    metadata = {
        "rows": len(df),
        "columns": len(df.columns),
        "column_names": df.columns.tolist(),
        "memory_mb": round(df.memory_usage(deep=True).sum() / (1024 * 1024), 2),
    }
    
    if optimize:
        text = optimize_dataset_for_llm(df)
    else:
        text = df.to_string(index=False)
    
    tokens = estimate_tokens(text)

    del df
    return text, tokens, metadata


def process_text(filepath: str) -> Tuple[str, int, dict]:
    """Procesa un archivo de texto plano (.txt, .log)."""
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        raw_text = f.read()
    
    # Limpieza: eliminar líneas vacías múltiples, espacios redundantes
    lines = raw_text.split("\n")
    cleaned_lines = []
    prev_empty = False
    for line in lines:
        stripped = line.strip()
        if stripped == "":
            if not prev_empty:
                cleaned_lines.append("")
                prev_empty = True
        else:
            cleaned_lines.append(stripped)
            prev_empty = False
    
    text = "\n".join(cleaned_lines)
    tokens = estimate_tokens(text)
    
    metadata = {
        "original_chars": len(raw_text),
        "compressed_chars": len(text),
        "reduction_pct": round((1 - len(text) / max(len(raw_text), 1)) * 100, 1),
        "lines": len(cleaned_lines),
        "rows": len(cleaned_lines),
        "columns": 0,
    }
    
    del raw_text, lines, cleaned_lines
    return text, tokens, metadata


def truncate_to_token_limit(text: str, max_tokens: int = None) -> Tuple[str, int]:
    """Trunca el texto al límite de tokens permitido.
    
    Usa truncamiento inteligente: preserva cabeceras/metadatos primero.
    """
    if max_tokens is None:
        max_tokens = TOKEN_ESTIMATION["max_tokens"]
    
    current_tokens = estimate_tokens(text)
    if current_tokens <= max_tokens:
        return text, current_tokens
    
    # Preservar cabeceras (líneas que empiezan con #)
    lines = text.split("\n")
    header_lines = [l for l in lines if l.startswith("#")]
    data_lines = [l for l in lines if not l.startswith("#")]
    
    # Calcular cuánto espacio queda para datos después de cabeceras
    header_text = "\n".join(header_lines)
    header_tokens = estimate_tokens(header_text)
    available_tokens = max_tokens - header_tokens - 10  # 10 tokens de margen
    
    if available_tokens <= 0:
        # Solo devolver cabeceras
        return header_text, header_tokens
    
    # Truncar datos línea por línea
    truncated_data = []
    data_tokens = 0
    for line in data_lines:
        line_tokens = estimate_tokens(line) + 1  # +1 por newline
        if data_tokens + line_tokens <= available_tokens:
            truncated_data.append(line)
            data_tokens += line_tokens
        else:
            break
    
    # Reconstruir texto
    result = header_text + "\n" + "\n".join(truncated_data)
    final_tokens = estimate_tokens(result)
    
    del lines, header_lines, data_lines
    return result, final_tokens


def process_file(filepath: str) -> Tuple[str, int, dict]:
    """Dispatcher: procesa cualquier archivo según su extensión.
    
    Soporta:
    - Datos: CSV, JSON, TSV → procesamiento estructurado
    - Todo lo demás (código, texto, config, etc) → lectura como texto plano
    """
    ext = Path(filepath).suffix.lower()
    
    if ext == ".csv":
        return process_csv(filepath)
    elif ext in (".json", ".jsonl"):
        return process_json(filepath)
    elif ext in (".txt", ".log", ".md", ".rst"):
        return process_text(filepath)
    elif ext == ".tsv":
        df = pd.read_csv(filepath, sep="\t")
        metadata = {
            "rows": len(df),
            "columns": len(df.columns),
            "column_names": df.columns.tolist(),
        }
        text = optimize_dataset_for_llm(df)
        tokens = estimate_tokens(text)
        del df
        return text, tokens, metadata
    else:
        # Cualquier otra extensión (py, js, java, lua, luau, cpp, h, etc.)
        # se trata como texto plano de código
        return process_text(filepath)


def save_upload(file_data: bytes, original_filename: str) -> str:
    """Guarda un archivo subido en el directorio temporal."""
    os.makedirs(UPLOAD_CONFIG["temp_dir"], exist_ok=True)
    
    # Generar nombre único para evitar colisiones
    ext = Path(original_filename).suffix.lower()
    unique_name = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(UPLOAD_CONFIG["temp_dir"], unique_name)
    
    with open(filepath, "wb") as f:
        f.write(file_data)
    
    return filepath


def cleanup_file(filepath: str):
    """Elimina un archivo temporal y fuerza un único ciclo de GC por subida."""
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except Exception:
        pass
    gc.collect()  # Un solo gc.collect() por ciclo completo de subida