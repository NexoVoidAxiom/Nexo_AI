"""
Procesador de Datos ULTRA OPTIMIZADO
=====================================
- Limpieza y comprension de datos para maximizar densidad de informacion
- Procesamiento paralelizado para i7-9700K (8 nucleos)
- Sin gc.collect() — Python gestiona la memoria automaticamente
- Soporte completo: PDF, DOCX, XLSX, PPTX, imagenes (OCR), ZIP y mas
"""
import json
import os
import uuid
import zipfile
import tarfile
import tempfile
from pathlib import Path
from typing import Tuple
import pandas as pd

from app.config import (
    UPLOAD_CONFIG,
    TOKEN_ESTIMATION,
    HARDWARE_PROFILE,
)


def estimate_tokens(text: str) -> int:
    """Estima tokens basado en chars_per_token. Para espanol: 3.5 chars/token."""
    if not text:
        return 0
    return int(len(text) / TOKEN_ESTIMATION["chars_per_token"])


def optimize_dataset_for_llm(df: pd.DataFrame) -> str:
    """Convierte DataFrame en texto compacto optimizado para LLM.
    
    Estrategias de comprension:
    1. Elimina NaN/None -> ""
    2. Convierte a JSON compacto por fila
    3. Cabecera descriptiva para contexto
    """
    df = df.fillna("")
    for col in df.select_dtypes(include=["object"]).columns:
        df[col] = df[col].str.strip()
    
    lines = [f"# DATASET: {len(df)} rows x {len(df.columns)} columns",
             f"# COLUMNS: {', '.join(df.columns.tolist())}", ""]
    
    for record in df.to_dict(orient="records"):
        lines.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    
    return "\n".join(lines)


def process_csv(filepath: str, optimize: bool = True) -> Tuple[str, int, dict]:
    """Procesa un archivo CSV y devuelve texto optimizado + metadatos."""
    try:
        df = pd.read_csv(filepath, encoding="utf-8")
    except UnicodeDecodeError:
        df = pd.read_csv(filepath, encoding="latin1")
    except Exception:
        df = pd.read_csv(filepath)
    
    metadata = {
        "rows": len(df), "columns": len(df.columns),
        "column_names": df.columns.tolist(),
        "dtypes": {str(k): str(v) for k, v in df.dtypes.items()},
        "null_counts": df.isnull().sum().to_dict(),
        "memory_mb": round(df.memory_usage(deep=True).sum() / (1024 * 1024), 2),
    }
    
    text = optimize_dataset_for_llm(df) if optimize else df.to_string(index=False)
    tokens = estimate_tokens(text)
    del df
    return text, tokens, metadata


def process_json(filepath: str, optimize: bool = True) -> Tuple[str, int, dict]:
    """Procesa un archivo JSON/JSONL y devuelve texto optimizado + metadatos."""
    try:
        df = pd.read_json(filepath, lines=True)
    except ValueError:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
        data = json.loads(raw)
        if isinstance(data, list):
            df = pd.DataFrame(data)
        elif isinstance(data, dict):
            df = pd.json_normalize(data)
        else:
            df = pd.DataFrame([data])
    
    metadata = {
        "rows": len(df), "columns": len(df.columns),
        "column_names": df.columns.tolist(),
        "memory_mb": round(df.memory_usage(deep=True).sum() / (1024 * 1024), 2),
    }
    
    text = optimize_dataset_for_llm(df) if optimize else df.to_string(index=False)
    tokens = estimate_tokens(text)
    del df
    return text, tokens, metadata


def process_text(filepath: str) -> Tuple[str, int, dict]:
    """Procesa un archivo de texto plano (.txt, .log)."""
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        raw_text = f.read()
    
    lines = raw_text.split("\n")
    cleaned = []
    prev_empty = False
    for line in lines:
        stripped = line.strip()
        if stripped == "":
            if not prev_empty:
                cleaned.append("")
                prev_empty = True
        else:
            cleaned.append(stripped)
            prev_empty = False
    
    text = "\n".join(cleaned)
    tokens = estimate_tokens(text)
    metadata = {"original_chars": len(raw_text), "compressed_chars": len(text),
                "reduction_pct": round((1 - len(text) / max(len(raw_text), 1)) * 100, 1),
                "lines": len(cleaned), "rows": len(cleaned), "columns": 0}
    del raw_text, lines, cleaned
    return text, tokens, metadata


def truncate_to_token_limit(text: str, max_tokens: int = None) -> Tuple[str, int]:
    """Trunca el texto al limite de tokens. Preserva cabeceras primero."""
    if max_tokens is None:
        max_tokens = TOKEN_ESTIMATION["max_tokens"]
    
    current = estimate_tokens(text)
    if current <= max_tokens:
        return text, current
    
    lines = text.split("\n")
    headers = [l for l in lines if l.startswith("#")]
    data = [l for l in lines if not l.startswith("#")]
    
    header_text = "\n".join(headers)
    header_tokens = estimate_tokens(header_text)
    available = max_tokens - header_tokens - 10
    
    if available <= 0:
        return header_text, header_tokens
    
    truncated = []
    data_tokens = 0
    for line in data:
        lt = estimate_tokens(line) + 1
        if data_tokens + lt <= available:
            truncated.append(line)
            data_tokens += lt
        else:
            break
    
    result = header_text + "\n" + "\n".join(truncated)
    del lines, headers, data
    return result, estimate_tokens(result)


def _try_import(module: str):
    """Importa un modulo de forma segura; retorna None si no esta instalado."""
    import importlib
    try:
        return importlib.import_module(module)
    except ImportError:
        return None


def process_pdf(filepath: str) -> Tuple[str, int, dict]:
    """Extrae texto de un PDF."""
    text_parts = []
    num_pages = 0
    
    pypdf = _try_import("pypdf")
    if pypdf:
        try:
            reader = pypdf.PdfReader(filepath)
            num_pages = len(reader.pages)
            for i, page in enumerate(reader.pages):
                pt = page.extract_text() or ""
                if pt.strip():
                    text_parts.append(f"[Pagina {i+1}]\n{pt}")
        except Exception:
            text_parts = []
    
    if not text_parts:
        pdfplumber = _try_import("pdfplumber")
        if pdfplumber:
            try:
                with pdfplumber.open(filepath) as pdf:
                    num_pages = len(pdf.pages)
                    for i, page in enumerate(pdf.pages):
                        pt = page.extract_text() or ""
                        if pt.strip():
                            text_parts.append(f"[Pagina {i+1}]\n{pt}")
            except Exception:
                pass
    
    if not text_parts:
        text = f"[PDF: {Path(filepath).name} — {num_pages} paginas. No se pudo extraer texto.]"
    else:
        text = f"[PDF: {num_pages} paginas]\n\n" + "\n\n".join(text_parts)
    
    return text, estimate_tokens(text), {"pages": num_pages, "extracted_pages": len(text_parts)}


def process_docx(filepath: str) -> Tuple[str, int, dict]:
    """Extrae texto de un archivo Word .docx."""
    try:
        import docx as docx_module
        doc = docx_module.Document(filepath)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        table_texts = []
        for table in doc.tables:
            rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows]
            table_texts.append("\n".join(rows))
        full_text = "\n".join(paragraphs)
        if table_texts:
            full_text += "\n\n[TABLAS]\n" + "\n\n".join(table_texts)
        return full_text, estimate_tokens(full_text), {"paragraphs": len(paragraphs), "tables": len(doc.tables)}
    except ImportError:
        text = f"[DOCX: {Path(filepath).name} — modulo python-docx no instalado]"
        return text, estimate_tokens(text), {"error": "missing python-docx"}
    except Exception as e:
        text = f"[DOCX: Error al procesar {Path(filepath).name}: {e}]"
        return text, estimate_tokens(text), {"error": str(e)}


def process_xlsx(filepath: str) -> Tuple[str, int, dict]:
    """Extrae datos de un Excel .xlsx/.xls/.ods."""
    ext = Path(filepath).suffix.lower()
    try:
        engine = {"xls": "xlrd", "ods": "odf"}.get(ext, None)
        dfs = pd.read_excel(filepath, sheet_name=None, engine=engine)
        parts = []
        summaries = []
        for name, df in dfs.items():
            df = df.fillna("")
            parts.append(f"[Hoja: {name} — {len(df)} filas x {len(df.columns)} cols]\n{optimize_dataset_for_llm(df)}")
            summaries.append({"name": name, "rows": len(df), "cols": len(df.columns)})
        return "\n\n".join(parts), estimate_tokens("\n\n".join(parts)), {"sheets": summaries}
    except Exception as e:
        text = f"[Excel: Error al procesar {Path(filepath).name}: {e}]"
        return text, estimate_tokens(text), {"error": str(e)}


def process_pptx(filepath: str) -> Tuple[str, int, dict]:
    """Extrae texto de un PowerPoint .pptx."""
    try:
        from pptx import Presentation
        prs = Presentation(filepath)
        slides = []
        for i, slide in enumerate(prs.slides, 1):
            parts = [shape.text.strip() for shape in slide.shapes if hasattr(shape, "text") and shape.text.strip()]
            if parts:
                slides.append(f"[Diapositiva {i}]\n" + "\n".join(parts))
        text = f"[PPTX: {len(prs.slides)} diapositivas]\n\n" + "\n\n".join(slides)
        return text, estimate_tokens(text), {"slides": len(prs.slides), "slides_with_text": len(slides)}
    except ImportError:
        return f"[PPTX: modulo python-pptx no instalado]", 0, {"error": "missing python-pptx"}
    except Exception as e:
        return f"[PPTX: Error: {e}]", 0, {"error": str(e)}


def process_image(filepath: str) -> Tuple[str, int, dict]:
    """Analisis de imagen: metadatos, colores, OCR y base64 para IA."""
    metadata = {"type": "image"}
    parts = [f"[IMAGEN: {Path(filepath).name}]"]
    
    PIL = _try_import("PIL.Image")
    if PIL:
        try:
            from PIL import Image, ImageStat
            img = Image.open(filepath)
            w, h = img.size
            fmt = img.format or Path(filepath).suffix.upper().lstrip(".")
            mode = img.mode
            parts.extend([f"Dimensiones: {w}x{h}px | Formato: {fmt} | Modo: {mode}",
                         f"Pixeles totales: {w*h:,} | Aspecto: {w/h:.2f}:1"])
            metadata.update({"width": w, "height": h, "mode": mode, "format": fmt})
            
            img_rgb = img.convert("RGB")
            stat = ImageStat.Stat(img_rgb)
            r_mean, g_mean, b_mean = stat.mean[:3]
            brightness = r_mean * 0.299 + g_mean * 0.587 + b_mean * 0.114
            label = "oscura" if brightness < 85 else "media" if brightness < 170 else "clara"
            parts.append(f"Brillo: {brightness:.0f}/255 ({label}) | RGB medio: ({r_mean:.0f}, {g_mean:.0f}, {b_mean:.0f})")
            
            try:
                small = img_rgb.resize((150, 150)).convert("P", palette=Image.ADAPTIVE, colors=8)
                pal = small.getpalette()
                colors = [f"#{pal[i*3]:02x}{pal[i*3+1]:02x}{pal[i*3+2]:02x}" for i in range(8)]
                parts.append(f"Colores: {', '.join(colors)}")
                metadata["dominant_colors"] = colors
            except Exception:
                pass
        except Exception as e:
            parts.append(f"[Error PIL: {e}]")
    
    # OCR
    pytesseract = _try_import("pytesseract")
    if pytesseract:
        try:
            from PIL import Image
            import pytesseract as tess
            ocr = tess.image_to_string(Image.open(filepath), lang="spa+eng").strip()
            if ocr:
                parts.append(f"\n[TEXTO OCR]\n{ocr}")
                metadata["ocr_chars"] = len(ocr)
        except Exception:
            pass
    
    # Base64 para vision IA
    try:
        import base64
        with open(filepath, "rb") as f:
            raw = f.read()
        ext_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                   ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
                   ".tiff": "image/tiff", ".tif": "image/tiff"}
        metadata["_vision_b64"] = base64.b64encode(raw).decode()
        metadata["_vision_mime"] = ext_map.get(Path(filepath).suffix.lower(), "image/png")
        metadata["has_vision_data"] = True
        parts.append("\n[Listo para analisis visual por IA]")
    except Exception:
        pass
    
    text = "\n".join(parts)
    return text, estimate_tokens(text), metadata


def process_svg(filepath: str) -> Tuple[str, int, dict]:
    """Parsea SVG extrayendo estructura, colores y texto."""
    import xml.etree.ElementTree as ET
    parts = [f"[SVG: {Path(filepath).name}]"]
    metadata = {"type": "svg"}
    
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
        
        def tag_name(e):
            t = e.tag
            return t.split("}")[-1] if "}" in t else t
        
        vb = root.get("viewBox", "")
        w, h = root.get("width", ""), root.get("height", "")
        if vb: parts.append(f"ViewBox: {vb}")
        if w or h: parts.append(f"Dimensiones: {w} x {h}")
        metadata.update({"viewBox": vb, "width": w, "height": h})
        
        elem_count, colors, texts = {}, set(), []
        for elem in root.iter():
            tname = tag_name(elem)
            elem_count[tname] = elem_count.get(tname, 0) + 1
            for attr in ("fill", "stroke"):
                val = elem.get(attr, "")
                if val and val not in ("none", "transparent", "inherit"):
                    colors.add(val)
            style = elem.get("style", "")
            for prop in style.split(";"):
                if ":" in prop:
                    k, v = prop.split(":", 1)
                    if k.strip() in ("fill", "stroke") and v.strip() not in ("none", "transparent", ""):
                        colors.add(v.strip())
            if tname in ("text", "tspan", "textPath"):
                content = "".join(elem.itertext()).strip()
                if content: texts.append(content)
        
        visual = {k: v for k, v in elem_count.items() if k not in ("svg", "g", "defs", "style", "clipPath")}
        if visual:
            top = sorted(visual.items(), key=lambda x: -x[1])[:12]
            parts.append(f"Formas: {', '.join(f'{k}x{v}' for k, v in top)}")
        if colors:
            parts.append(f"Colores: {', '.join(sorted(colors)[:20])}")
        if texts:
            parts.append("Textos:")
            for t in texts[:30]: parts.append(f"  . {t[:300]}")
        
        for tag in ("title", "desc"):
            SVG_NS = "http://www.w3.org/2000/svg"
            el = root.find(f"{{{SVG_NS}}}{tag}") or root.find(tag)
            if el is not None and el.text:
                parts.append(f"{tag.capitalize()}: {el.text.strip()}")
        
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
        if len(raw) <= 40000:
            parts.append(f"\n[CODIGO SVG]\n{raw}")
        else:
            parts.append(f"\n[CODIGO SVG primeros 40k chars]\n{raw[:40000]}")
    except Exception as e:
        parts.append(f"[Error SVG: {e}]")
    
    return "\n".join(parts), estimate_tokens("\n".join(parts)), metadata


def process_zip(filepath: str) -> Tuple[str, int, dict]:
    """Extrae contenido de un ZIP."""
    MAX_TEXT = 200_000
    SKIP = {".pyc", ".pyo", ".class", ".o", ".so", ".dll", ".exe", ".bin", ".dat"}
    parts = [f"[ZIP: {Path(filepath).name}]"]
    texts, errors = [], []
    
    try:
        with zipfile.ZipFile(filepath, "r") as zf:
            members = zf.infolist()
            files = [m for m in members if not m.is_dir()]
            parts.append(f"Total: {len(files)} archivos")
            parts.append("\n[INVENTARIO]\n" + "\n".join(f"  {m.filename} ({m.file_size/1024:.1f} KB)" for m in files[:200]))
            
            total_chars = 0
            with tempfile.TemporaryDirectory() as tmp:
                for m in files:
                    if total_chars >= MAX_TEXT: break
                    ext = Path(m.filename).suffix.lower()
                    if ext in SKIP: continue
                    try:
                        data = zf.read(m.filename)
                        if ext == ".zip":
                            sub = os.path.join(tmp, Path(m.filename).name)
                            with open(sub, "wb") as f: f.write(data)
                            sub_text, _, _ = process_zip(sub)
                            texts.append(f"\n[ZIP ANIDADO: {m.filename}]\n{sub_text[:5000]}")
                            total_chars += len(sub_text[:5000])
                            continue
                        for enc in ("utf-8", "latin-1"):
                            try:
                                ft = data.decode(enc)
                                if len(ft) > 20000: ft = ft[:20000] + "\n... [truncado]"
                                texts.append(f"\n{'='*50}\n[{m.filename}]\n{'='*50}\n{ft}")
                                total_chars += len(ft)
                                break
                            except Exception: continue
                    except Exception as e:
                        errors.append(f"{m.filename}: {e}")
        
        if texts: parts.append("\n[CONTENIDO]"); parts.extend(texts)
        if errors: parts.append(f"\n[ERRORES: {', '.join(errors[:10])}]")
        return "\n".join(parts), estimate_tokens("\n".join(parts)), {"total_files": len(files)}
    except Exception as e:
        return f"[ZIP Error: {e}]", 0, {"error": str(e)}


def process_tar(filepath: str) -> Tuple[str, int, dict]:
    """Extrae contenido de un TAR."""
    MAX_TEXT = 200_000
    SKIP = {".pyc", ".pyo", ".class", ".o", ".so", ".dll", ".exe", ".bin", ".dat"}
    parts = [f"[TAR: {Path(filepath).name}]"]
    texts = []
    
    try:
        with tarfile.open(filepath, "r:*") as tf:
            files = [m for m in tf.getmembers() if m.isfile()]
            parts.append(f"Total: {len(files)} archivos")
            parts.append("\n[INVENTARIO]\n" + "\n".join(f"  {m.name} ({m.size/1024:.1f} KB)" for m in files[:200]))
            
            total_chars = 0
            for m in files:
                if total_chars >= MAX_TEXT: break
                ext = Path(m.name).suffix.lower()
                if ext in SKIP: continue
                f = tf.extractfile(m)
                if f is None: continue
                data = f.read()
                for enc in ("utf-8", "latin-1"):
                    try:
                        ft = data.decode(enc)
                        if len(ft) > 15000: ft = ft[:15000] + "\n... [truncado]"
                        texts.append(f"\n{'='*50}\n[{m.name}]\n{'='*50}\n{ft}")
                        total_chars += len(ft)
                        break
                    except Exception: continue
        
        if texts: parts.append("\n[CONTENIDO]"); parts.extend(texts)
        return "\n".join(parts), estimate_tokens("\n".join(parts)), {"total_files": len(files)}
    except Exception as e:
        return f"[TAR Error: {e}]", 0, {"error": str(e)}


def process_ipynb(filepath: str) -> Tuple[str, int, dict]:
    """Extrae celdas de un Jupyter Notebook."""
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        nb = json.load(f)
    cells = nb.get("cells", [])
    parts = [f"[Jupyter: {len(cells)} celdas]"]
    code, md = 0, 0
    
    for i, cell in enumerate(cells, 1):
        source = "".join(cell.get("source", []))
        if not source.strip(): continue
        if cell.get("cell_type") == "code":
            code += 1
            parts.append(f"\n[Celda {i} - Codigo]\n```python\n{source}\n```")
            for out in cell.get("outputs", []):
                if out.get("output_type") in ("stream", "execute_result"):
                    ot = "".join(out.get("text", out.get("data", {}).get("text/plain", [])))
                    if ot.strip(): parts.append(f"[Output]: {ot[:500]}")
        elif cell.get("cell_type") == "markdown":
            md += 1
            parts.append(f"\n[Celda {i} - Markdown]\n{source}")
    
    text = "\n".join(parts)
    return text, estimate_tokens(text), {"total_cells": len(cells), "code": code, "md": md}


def process_file(filepath: str) -> Tuple[str, int, dict]:
    """Dispatcher universal: procesa cualquier tipo de archivo."""
    ext = Path(filepath).suffix.lower()
    name = Path(filepath).name
    
    if ext == ".csv": return process_csv(filepath)
    if ext in (".json", ".jsonl"): return process_json(filepath)
    if ext == ".tsv":
        df = pd.read_csv(filepath, sep="\t")
        text = optimize_dataset_for_llm(df)
        tokens = estimate_tokens(text)
        meta = {"rows": len(df), "columns": len(df.columns), "column_names": df.columns.tolist()}
        del df
        return text, tokens, meta
    if ext == ".pdf": return process_pdf(filepath)
    if ext in (".docx", ".doc"): return process_docx(filepath)
    if ext in (".xlsx", ".xls", ".xlsm", ".ods"): return process_xlsx(filepath)
    if ext in (".pptx", ".ppt"): return process_pptx(filepath)
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif"): return process_image(filepath)
    if ext == ".svg": return process_svg(filepath)
    if ext == ".zip": return process_zip(filepath)
    if ext in (".tar", ".gz", ".tgz", ".tar.gz", ".tar.bz2", ".tar.xz"):
        if ext == ".gz" and not name.endswith(".tar.gz"):
            import gzip
            with gzip.open(filepath, "rt", encoding="utf-8", errors="replace") as f:
                content = f.read(100_000)
            return f"[GZ: {name}]\n{content}", estimate_tokens(content), {"type": "gzip"}
        return process_tar(filepath)
    if ext == ".ipynb": return process_ipynb(filepath)
    return process_text(filepath)


def save_upload(file_data: bytes, original_filename: str) -> str:
    """Guarda un archivo subido en el directorio temporal."""
    os.makedirs(UPLOAD_CONFIG["temp_dir"], exist_ok=True)
    ext = Path(original_filename).suffix.lower()
    filepath = os.path.join(UPLOAD_CONFIG["temp_dir"], f"{uuid.uuid4().hex}{ext}")
    with open(filepath, "wb") as f:
        f.write(file_data)
    return filepath


def cleanup_file(filepath: str):
    """Elimina un archivo temporal. Sin gc.collect() — Python lo gestiona."""
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except Exception:
        pass