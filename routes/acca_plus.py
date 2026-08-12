import importlib.util
import io
import re
import zipfile
import os
import json
import base64
import urllib.request
import urllib.error

from flask import Blueprint, jsonify, render_template, request, send_file

from routes._helpers import NO_FILE_SINGLE, NO_FILE_MULTIPLE, log_error
from utils.capabilities import QUALITY_HIGH, QUALITY_UNAVAILABLE
from utils.pymupdf import import_pymupdf

bp = Blueprint("acca_plus", __name__)

fitz = import_pymupdf()

# ---------------------------------------------------------------------------
# Lazy pix2tex detection – heavy import is deferred until first request so
# the app starts instantly even when pix2tex is not installed.
# ---------------------------------------------------------------------------

HAS_PIX2TEX = importlib.util.find_spec("pix2tex") is not None

_latex_ocr_model = None  # cached singleton; loaded on first POST


def get_gemini_api_key():
    key = os.environ.get("GEMINI_API_KEY")
    if key: return key
    try:
        # Resolve relative to this file's directory (routes/) -> parent directory
        env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    return line.split("=", 1)[1]
    except Exception:
        pass
    return None


def _gemini_image_to_latex(image_bytes: bytes, mime_type: str) -> str:
    api_key = get_gemini_api_key()
    if not api_key:
        raise Exception("Gemini API key is not configured. Please set GEMINI_API_KEY in .env file.")
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent?key={api_key}"
    
    b64_data = base64.b64encode(image_bytes).decode('utf-8')
    
    prompt = (
        "You are an expert LaTeX typesetter. Convert the provided image into clean, compilable LaTeX code. "
        "Replicate the layout, tables, colored boxes, text formatting, and math formulas as accurately as possible. "
        "Use necessary packages like xcolor, booktabs, amsmath, array, tikz, etc., if needed. "
        "DO NOT output full document preamble (no \\documentclass or \\begin{document}), ONLY output the inner content (the tables, text, formulas, etc). "
        "Output ONLY the LaTeX code. Do not wrap in ```latex or markdown tags."
    )
    
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {
                    "inlineData": {
                        "mimeType": mime_type,
                        "data": b64_data
                    }
                }
            ]
        }],
        "generationConfig": {
            "temperature": 0.1
        }
    }
    
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as response:
            resp_data = response.read().decode('utf-8')
            data = json.loads(resp_data)
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode('utf-8')
        try:
            err_msg = json.loads(err_msg).get('error', {}).get('message', err_msg)
        except: pass
        raise Exception(f"Gemini API HTTP Error: {err_msg}")
    except Exception as e:
        raise Exception(f"Gemini API Network Error: {str(e)}")
        
    try:
        latex_text = data['candidates'][0]['content']['parts'][0]['text']
        if latex_text.startswith("```latex"): latex_text = latex_text.split("```latex", 1)[1]
        if latex_text.startswith("```tex"): latex_text = latex_text.split("```tex", 1)[1]
        if latex_text.startswith("```"): latex_text = latex_text.split("```", 1)[1]
        if latex_text.endswith("```"): latex_text = latex_text.rsplit("```", 1)[0]
        return latex_text.strip()
    except Exception as e:
        raise Exception("Failed to parse Gemini API response. Format might have changed.")


def _get_model():
    """Return a cached LatexOCR instance, loading it on first call."""
    global _latex_ocr_model
    if _latex_ocr_model is None:
        from pix2tex.cli import LatexOCR  # noqa: PLC0415

        _latex_ocr_model = LatexOCR()
    return _latex_ocr_model


# ---------------------------------------------------------------------------
# Formula to LaTeX
# ---------------------------------------------------------------------------

@bp.route("/latex-ocr")
def latex_ocr_page():
    if HAS_PIX2TEX:
        status_html = (
            '<p><i class="bi bi-check-circle-fill" style="color:#16a34a"></i> '
            '<strong>pix2tex ready.</strong> LaTeX-OCR model detected.</p>'
        )
    else:
        status_html = (
            '<p><i class="bi bi-exclamation-triangle-fill" style="color:#ffb703"></i> '
            '<strong>pix2tex not installed.</strong> Run on the server: '
            '<code>pip install pix2tex</code> then restart. '
            'Requires PyTorch; model weights (~300 MB) are downloaded automatically on first use.</p>'
        )

    return render_template(
        "tools/latex_ocr.html",
        title="Formula to LaTeX",
        description="Convert a formula image to LaTeX code automatically",
        status_html=status_html,
        has_pix2tex=HAS_PIX2TEX,
        endpoint="/acca-plus/latex-ocr",
        accept=".jpg,.jpeg,.png,.bmp,.tiff,.webp",
    )


@bp.route("/latex-ocr", methods=["POST"])
def latex_ocr():
    if not HAS_PIX2TEX:
        return jsonify(
            error="pix2tex is not installed. Run: pip install pix2tex on the server, then restart."
        ), 400

    files = request.files.getlist("files")
    if not files or not files[0].filename:
        return jsonify(error=NO_FILE_SINGLE), 400

    from PIL import Image  # noqa: PLC0415

    try:
        img = Image.open(io.BytesIO(files[0].read())).convert("RGB")
    except Exception as e:
        log_error(e, "latex_ocr.open")
        return jsonify(error="Failed to open image. Make sure the file is a valid image."), 400

    try:
        model = _get_model()
        latex = model(img)
    except Exception as e:
        log_error(e, "latex_ocr.predict")
        return jsonify(error=f"LaTeX prediction failed: {e}"), 500

    if not latex or not latex.strip():
        return jsonify(latex="(No formula detected)")

    return jsonify(latex=latex.strip())


# ---------------------------------------------------------------------------
# Images to TeX Document
# ---------------------------------------------------------------------------

_PAPER_SIZES = {"a4paper", "a5paper", "b5paper", "letterpaper", "legalpaper"}
_SECTION_STYLES = {"section", "subsection", "none"}
_DOC_CLASSES = {"article", "report", "beamer"}

# Maps form value → (open_tag, close_tag)
_MATH_DELIMS = {
    "display":       (r"\[",                  r"\]"),
    "display_dbl":   ("$$",                    "$$"),
    "equation":      (r"\begin{equation}",     r"\end{equation}"),
    "equation_star": (r"\begin{equation*}",    r"\end{equation*}"),
    "align_star":    (r"\begin{align*}",       r"\end{align*}"),
    "inline":        ("$",                     "$"),
}


def _build_tex(formulas: list[dict], title: str, author: str,
               paper: str, docclass: str, section_style: str,
               math_delim: str = "display") -> str:
    """Assemble a full LaTeX document from a list of {label, latex} dicts."""
    paper         = paper         if paper         in _PAPER_SIZES   else "a4paper"
    docclass      = docclass      if docclass      in _DOC_CLASSES    else "article"
    section_style = section_style if section_style in _SECTION_STYLES else "section"
    open_tag, close_tag = _MATH_DELIMS.get(math_delim, _MATH_DELIMS["display"])
    is_inline = (math_delim == "inline")

    is_beamer = (docclass == "beamer")

    lines: list[str] = []
    if is_beamer:
        lines.append(r"\documentclass{beamer}")
    else:
        lines.append(rf"\documentclass[{paper},12pt]{{article}}" if docclass == "article"
                     else rf"\documentclass[{paper},12pt]{{report}}")

    lines += [
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage{amsmath}",
        r"\usepackage{amssymb}",
        r"\usepackage{amsfonts}",
        r"\usepackage{mathtools}",
        "",
    ]

    if not is_beamer:
        lines += [
            rf"\title{{{title or 'Dokumen LaTeX'}}}",
            rf"\author{{{author or ''}}}",
            r"\date{\today}",
            "",
        ]

    lines.append(r"\begin{document}")
    if not is_beamer:
        if title:
            lines.append(r"\maketitle")
        lines.append("")

    for i, item in enumerate(formulas, start=1):
        label = item.get("label", "").strip() or f"Formula {i}"
        latex = item.get("latex", "").strip()

        if is_beamer:
            lines.append(r"\begin{frame}")
            lines.append(rf"\frametitle{{{label}}}")
            if is_inline:
                lines.append(f"${latex}$")
            else:
                lines.append(open_tag)
                lines.append(f"  {latex}")
                lines.append(close_tag)
            lines.append(r"\end{frame}")
        else:
            if section_style == "section":
                lines.append(rf"\section*{{{label}}}")
            elif section_style == "subsection":
                lines.append(rf"\subsection*{{{label}}}")

            if is_inline:
                lines += [f"${latex}$", ""]
            else:
                lines += [open_tag, f"  {latex}", close_tag, ""]

    lines.append(r"\end{document}")
    return "\n".join(lines)


@bp.route("/images-to-tex")
def images_to_tex_page():
    status_parts = []
    
    if get_gemini_api_key():
        status_parts.append('<i class="bi bi-stars" style="color:#8b5cf6"></i> <strong>Gemini AI ready.</strong>')
    else:
        status_parts.append('<i class="bi bi-stars" style="color:#9ca3af"></i> <strong>Gemini AI not configured.</strong>')
        
    if HAS_PIX2TEX:
        status_parts.append('<i class="bi bi-check-circle-fill" style="color:#16a34a"></i> <strong>pix2tex ready.</strong>')
    else:
        status_parts.append('<i class="bi bi-exclamation-triangle-fill" style="color:#ffb703"></i> <strong>pix2tex not installed.</strong>')

    status_html = f"<p>{' &nbsp;|&nbsp; '.join(status_parts)}</p>"

    return render_template(
        "tools/images_to_tex.html",
        title="Images to TeX",
        description="Batch convert formula images into a single compilable .tex document",
        status_html=status_html,
        has_pix2tex=HAS_PIX2TEX,
        endpoint="/acca-plus/images-to-tex",
        accept=".jpg,.jpeg,.png,.bmp,.tiff,.webp",
    )


@bp.route("/images-to-tex", methods=["POST"])
def images_to_tex():
    engine = request.form.get("engine", "pix2tex").strip()
    
    if engine == "pix2tex" and not HAS_PIX2TEX:
        return jsonify(
            error="pix2tex is not installed. Run: pip install pix2tex on the server, then restart."
        ), 400

    files = request.files.getlist("files")
    if not files or not files[0].filename:
        return jsonify(error="Please upload at least one image."), 400

    # Document options
    title   = (request.form.get("doc_title")   or "Modul Matematika").strip()
    author  = (request.form.get("doc_author")  or "").strip()
    paper   = (request.form.get("paper_size")  or "a4paper").strip()
    docclass      = (request.form.get("doc_class")     or "article").strip()
    section_style = (request.form.get("section_style") or "section").strip()
    math_delim    = (request.form.get("math_delim")    or "display").strip()

    # Per-image custom labels sent as label_0, label_1, ...
    labels = {}
    for key, val in request.form.items():
        if key.startswith("label_"):
            try:
                idx = int(key.split("_", 1)[1])
                labels[idx] = val.strip()
            except ValueError:
                pass

    from PIL import Image  # noqa: PLC0415

    engine = request.form.get("engine", "pix2tex").strip()
    
    if engine == "pix2tex":
        model = _get_model()
    else:
        model = None

    formulas: list[dict] = []
    errors:   list[str]  = []

    for i, upload in enumerate(files):
        if not upload.filename:
            continue
        label = labels.get(i, f"Formula {i + 1}")
        try:
            image_bytes = upload.read()
            if engine == "gemini":
                mime_type = upload.mimetype or "image/jpeg"
                if not mime_type.startswith("image/"):
                    mime_type = "image/jpeg"
                latex = _gemini_image_to_latex(image_bytes, mime_type)
            else:
                img   = Image.open(io.BytesIO(image_bytes)).convert("RGB")
                latex = model(img)
                
            if not latex or not latex.strip():
                latex = r"\text{(not detected)}"
            formulas.append({"label": label, "latex": latex.strip()})
        except Exception as e:
            log_error(e, f"images_to_tex.img{i}")
            errors.append(f"Image {i + 1} ({upload.filename}): {e}")
            formulas.append({"label": label,
                              "latex": r"\text{(processing failed)}"})

    if not formulas:
        return jsonify(error="No images could be processed."), 400

    tex = _build_tex(formulas, title, author, paper, docclass, section_style, math_delim)

    return jsonify(
        tex=tex,
        formulas=formulas,
        errors=errors,
        count=len(formulas),
    )


# ---------------------------------------------------------------------------
# Rename PDF by Content (NISN & Nama Siswa)
# ---------------------------------------------------------------------------

# ── Regex patterns ───────────────────────────────────────────────────────────
# NISN: exactly 10 digits, optionally preceded by a common label
_RE_NISN_LABELED = re.compile(
    r"NISN\s*[:\-]?\s*(\d{10})",
    re.IGNORECASE,
)
_RE_NISN_BARE = re.compile(r"\b(\d{10})\b")

# Nama Siswa: common label variants used in Indonesian school documents
_RE_NAMA = re.compile(
    r"(?:Nama\s+(?:Siswa|Peserta\s+Didik|Lengkap|Murid)|Nama)\s*[:\-]\s*([^\n\r,;]{3,80})",
    re.IGNORECASE,
)


def _sanitize(text: str) -> str:
    """Remove characters that are invalid in filenames and trim whitespace."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"[\s_]+", "_", text.strip())
    return text[:80]


def _extract_nisn_and_nama(text: str) -> tuple[str, str]:
    """Return (nisn, nama) extracted from PDF text, or empty strings."""
    nisn = ""
    m = _RE_NISN_LABELED.search(text)
    if m:
        nisn = m.group(1)
    else:
        m = _RE_NISN_BARE.search(text)
        if m:
            nisn = m.group(1)

    nama = ""
    m = _RE_NAMA.search(text)
    if m:
        nama = _sanitize(m.group(1))

    return nisn, nama


@bp.route("/rename-pdf")
def rename_pdf_page():
    return render_template(
        "tools/rename_pdf.html",
        title="Rename PDFs",
        description=(
            "Upload multiple PDFs at once. NISN and student name are extracted "
            "automatically from each document and used to rename the files: "
            "<code>filename_NISN_StudentName.pdf</code>"
        ),
        endpoint="/acca-plus/rename-pdf",
    )


@bp.route("/rename-pdf", methods=["POST"])
def rename_pdf():
    if fitz is None:
        return jsonify(error="PyMuPDF (fitz) is not installed on the server."), 500

    files = request.files.getlist("files")
    if not files or not files[0].filename:
        return jsonify(error=NO_FILE_MULTIPLE), 400

    custom_nisn_pattern = (request.form.get("nisn_pattern") or "").strip()
    custom_nama_pattern = (request.form.get("nama_pattern") or "").strip()

    try:
        re_nisn_custom = re.compile(custom_nisn_pattern, re.IGNORECASE) if custom_nisn_pattern else None
        re_nama_custom = re.compile(custom_nama_pattern, re.IGNORECASE) if custom_nama_pattern else None
    except re.error as e:
        return jsonify(error=f"Invalid regex pattern: {e}"), 400

    zip_buf = io.BytesIO()
    log_lines: list[str] = []
    processed = 0

    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for upload in files:
            original_filename = upload.filename or "unknown.pdf"
            stem = os.path.splitext(original_filename)[0]
            data = b""

            try:
                data = upload.read()
                doc = fitz.open(stream=data, filetype="pdf")

                pages_to_scan = min(3, doc.page_count)
                text = "\n".join(doc[i].get_text() for i in range(pages_to_scan))
                if not text.strip() and doc.page_count > 3:
                    text = "\n".join(page.get_text() for page in doc)
                doc.close()

                nisn = ""
                if re_nisn_custom:
                    m = re_nisn_custom.search(text)
                    nisn = m.group(1) if (m and m.lastindex) else (m.group(0) if m else "")
                else:
                    nisn, _ = _extract_nisn_and_nama(text)

                nama = ""
                if re_nama_custom:
                    m = re_nama_custom.search(text)
                    nama_raw = m.group(1) if (m and m.lastindex) else (m.group(0) if m else "")
                    nama = _sanitize(nama_raw)
                else:
                    _, nama = _extract_nisn_and_nama(text)

                parts = [stem]
                if nisn:
                    parts.append(nisn)
                if nama:
                    parts.append(nama)

                new_name = "_".join(parts) + ".pdf"
                status = "OK"
                if not nisn:
                    status += " [NISN not found]"
                if not nama:
                    status += " [Name not found]"

            except Exception as e:
                log_error(e, f"rename_pdf.{original_filename}")
                new_name = original_filename
                if not data:
                    data = upload.read()
                status = f"ERROR: {e}"

            zf.writestr(new_name, data)
            log_lines.append(f"{original_filename}  →  {new_name}  [{status}]")
            processed += 1

        zf.writestr("_rename_log.txt", "\n".join(log_lines).encode("utf-8"))

    if processed == 0:
        return jsonify(error="No PDF files could be processed."), 400

    zip_buf.seek(0)
    return send_file(
        zip_buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name="renamed_pdfs.zip",
    )


# ---------------------------------------------------------------------------
# Compress PDF to Target Size
# ---------------------------------------------------------------------------

def _compress_pdf_at(data: bytes, jpeg_quality: int, max_dim: int) -> bytes:
    """Re-compress all raster images in PDF at given JPEG quality and max dimension.
    max_dim=0 means no downscaling.
    Safely skips images with transparency or SMasks to prevent breaking layouts.
    """
    from PIL import Image as PILImage

    doc = fitz.open(stream=data, filetype="pdf")
    
    # 1. Collect all SMask xrefs so we don't accidentally compress the masks themselves
    smask_xrefs = set()
    for page in doc:
        for img_info in page.get_images(full=True):
            if img_info[1] > 0:
                smask_xrefs.add(img_info[1])

    seen: set[int] = set()
    try:
        for page in doc:
            for img_info in page.get_images(full=True):
                xref = img_info[0]
                smask_ref = img_info[1]
                
                if xref in seen:
                    continue
                seen.add(xref)
                
                # SKIP transparent images & masks entirely to prevent corruption
                if smask_ref > 0 or xref in smask_xrefs:
                    continue

                try:
                    pix = fitz.Pixmap(doc, xref)
                    # If the image natively has an alpha channel, skip it too
                    if pix.alpha:
                        pix = None
                        continue
                        
                    # Convert CMYK/complex colorspaces to RGB
                    if pix.colorspace and pix.colorspace.n >= 4:
                        new_pix = fitz.Pixmap(fitz.csRGB, pix)
                        pix = new_pix

                    img_bytes = pix.tobytes("png")
                    pix = None
                    
                    with PILImage.open(io.BytesIO(img_bytes)) as pil:
                        # Double check for transparency in PIL
                        if pil.mode in ("RGBA", "LA", "P") and ("transparency" in pil.info or pil.mode != "P"):
                            continue
                            
                        if pil.mode not in ("RGB", "L"):
                            pil = pil.convert("RGB")
                            
                        w, h = pil.size
                        if max_dim > 0 and max(w, h) > max_dim:
                            scale = max_dim / max(w, h)
                            pil = pil.resize(
                                (max(1, int(w * scale)), max(1, int(h * scale))),
                                PILImage.LANCZOS,
                            )

                        buf = io.BytesIO()
                        pil.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)

                    page.replace_image(xref, stream=buf.getvalue())
                except Exception as e:
                    log_error(e, f"_compress_pdf_at xref={xref}")
                    continue
        out = io.BytesIO()
        doc.save(out, garbage=4, deflate=True, clean=True)
        out.seek(0)
        return out.read()
    finally:
        doc.close()


def _compress_with_pikepdf(data: bytes) -> bytes:
    """Lossless compression using pikepdf object stream compression."""
    if importlib.util.find_spec("pikepdf") is None:
        return data

    try:
        import pikepdf
        with pikepdf.open(io.BytesIO(data)) as pdf:
            out = io.BytesIO()
            pdf.save(out, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
            return out.getvalue()
    except Exception as e:
        log_error(e, "_compress_with_pikepdf")
        return data


def _render_pdf_at(doc, zoom: float, jpeg_quality: int) -> bytes:
    """Renders all pages of a PDF to JPEG images and builds a new PDF."""
    mat = fitz.Matrix(zoom, zoom)
    new_doc = fitz.open()
    try:
        for page in doc:
            pix = page.get_pixmap(matrix=mat, alpha=False, colorspace=fitz.csRGB)
            img_bytes = pix.tobytes("jpeg", jpeg_quality)
            
            rect = page.rect
            new_page = new_doc.new_page(width=rect.width, height=rect.height)
            new_page.insert_image(rect, stream=img_bytes)
            
        out = io.BytesIO()
        new_doc.save(out, garbage=4, deflate=True)
        return out.getvalue()
    finally:
        new_doc.close()


def _compress_to_target(data: bytes, target_bytes: int, force_render: bool = False) -> tuple[bytes, int, bool]:
    """
    Binary-search compression settings to reach target_bytes.
    Phase 0: pikepdf lossless compression (if installed).
    Phase 1: binary-search JPEG quality (5-85), full image resolution.
    Phase 2: if target still not met, binary-search max image dimension (200-1800px) at quality=30.
    Phase 3: (Optional) if force_render=True, convert pages to images and binary search resolution.
    Returns (best_data, achieved_size, target_met).
    """
    if len(data) <= target_bytes:
        return data, len(data), True

    best_data = data
    best_size = len(data)

    def _update(compressed: bytes) -> None:
        nonlocal best_data, best_size
        size = len(compressed)
        # Prefer the largest result that still fits the target;
        # otherwise track the smallest overall result.
        if size <= target_bytes:
            if best_size > target_bytes or size > best_size:
                best_data, best_size = compressed, size
        else:
            if best_size > target_bytes and size < best_size:
                best_data, best_size = compressed, size

    # ── Phase 0: pikepdf lossless compression ─────────────
    data_pike = _compress_with_pikepdf(data)
    _update(data_pike)
    if best_size <= target_bytes:
        return best_data, best_size, True

    # Use data_pike as the base for further raster compression to stack savings
    # if it actually reduced the size.
    if len(data_pike) < len(data):
        data = data_pike

    # ── Phase 1: binary-search on JPEG quality (full resolution) ─────────────
    lo_q, hi_q = 5, 85
    for _ in range(12):
        if lo_q > hi_q:
            break
        q = (lo_q + hi_q) // 2
        c = _compress_pdf_at(data, q, 0)
        _update(c)
        if len(c) <= target_bytes:
            lo_q = q + 1   # Can afford higher quality
        else:
            hi_q = q - 1   # Need more compression

    if best_size <= target_bytes:
        return best_data, best_size, True

    # ── Phase 2: binary-search on max image dimension at quality=30 ───────────
    lo_d, hi_d = 200, 1800
    for _ in range(8):
        if lo_d > hi_d:
            break
        d = (lo_d + hi_d) // 2
        c = _compress_pdf_at(data, 30, d)
        _update(c)
        if len(c) <= target_bytes:
            lo_d = d + 1   # Can afford larger images
        else:
            hi_d = d - 1   # Need smaller images

    # ── Phase 3: Force render to image if target not met and force_render=True ──
    if force_render and best_size > target_bytes:
        try:
            doc_render = fitz.open(stream=data, filetype="pdf")
            # We want the highest zoom that fits. 
            # Zoom levels: 2.0 (High), 1.5 (Med), 1.0 (Low), 0.75 (Very Low).
            # We binary search JPEG quality (20 - 80) for each zoom level.
            for z in [2.0, 1.5, 1.0, 0.75]:
                lo_q, hi_q = 20, 80
                found_at_this_zoom = False
                for _ in range(6):
                    if lo_q > hi_q:
                        break
                    q = (lo_q + hi_q) // 2
                    c = _render_pdf_at(doc_render, z, q)
                    _update(c)
                    if len(c) <= target_bytes:
                        found_at_this_zoom = True
                        lo_q = q + 1
                    else:
                        hi_q = q - 1
                
                # If we found a configuration at this high zoom that fits the target, 
                # we don't need to drop to a lower, blurrier zoom level!
                if found_at_this_zoom:
                    break
                    
            doc_render.close()
        except Exception as e:
            log_error(e, "_compress_to_target phase 3")

    return best_data, best_size, best_size <= target_bytes


@bp.route("/compress-pdf")
def compress_pdf_page():
    return render_template(
        "tools/compress_pdf_plus.html",
        title="Compress PDF to Target Size",
        description=(
            "Upload a PDF and set your desired output file size. "
            "The tool automatically finds the best compression settings "
            "to get as close as possible to your target."
        ),
        endpoint="/acca-plus/compress-pdf",
    )


@bp.route("/compress-pdf", methods=["POST"])
def compress_pdf_target():
    if fitz is None:
        return jsonify(error="PyMuPDF (fitz) is not installed on the server."), 500

    files = request.files.getlist("files")
    if not files or not files[0].filename:
        return jsonify(error="Please upload a PDF file."), 400

    # Parse target size from form
    try:
        target_value = float(request.form.get("target_size", "0") or "0")
        target_unit = (request.form.get("target_unit", "KB") or "KB").upper()
        target_bytes = int(target_value * (1024 * 1024 if target_unit == "MB" else 1024))
    except (ValueError, TypeError):
        return jsonify(error="Invalid target size value."), 400

    if target_bytes <= 0:
        return jsonify(error="Target size must be greater than 0."), 400

    upload = files[0]
    stem = os.path.splitext(upload.filename or "document.pdf")[0]

    try:
        data = upload.read()
    except Exception as e:
        log_error(e, "compress_pdf_target.read")
        return jsonify(error="Failed to read uploaded file."), 400

    if not data:
        return jsonify(error="Uploaded file is empty."), 400

    try:
        chk = fitz.open(stream=data, filetype="pdf")
        chk.close()
    except Exception:
        return jsonify(error="Could not open PDF. File may be corrupted or encrypted."), 400

    original_size = len(data)

    force_render = request.form.get("force_render") == "1"

    try:
        result_data, achieved_size, target_met = _compress_to_target(data, target_bytes, force_render=force_render)
    except Exception as e:
        log_error(e, "compress_pdf_target.process")
        return jsonify(error=f"Compression failed: {e}"), 500

    out_buf = io.BytesIO(result_data)
    out_buf.seek(0)

    resp = send_file(
        out_buf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{stem}_compressed.pdf",
    )
    # Expose size metadata via response headers for the JS result display
    resp.headers["X-Original-Size"] = str(original_size)
    resp.headers["X-Achieved-Size"] = str(achieved_size)
    resp.headers["X-Target-Size"] = str(target_bytes)
    resp.headers["X-Target-Met"] = "1" if target_met else "0"
    resp.headers["Access-Control-Expose-Headers"] = (
        "X-Original-Size, X-Achieved-Size, X-Target-Size, X-Target-Met"
    )
    return resp


# ---------------------------------------------------------------------------
# Compress Image to Target Size
# ---------------------------------------------------------------------------

def _compress_image_to_target(img_data: bytes, target_bytes: int) -> tuple[bytes, int, bool]:
    """
    Binary-search JPEG compression settings to hit target_bytes for a single image.
    Returns (best_data, achieved_size, target_met).
    """
    from PIL import Image as PILImage
    original_size = len(img_data)
    if original_size <= target_bytes:
        return img_data, original_size, True

    try:
        pil_img = PILImage.open(io.BytesIO(img_data))
        if pil_img.mode not in ("RGB", "L"):
            pil_img = pil_img.convert("RGB")
    except Exception as e:
        raise ValueError(f"Invalid image: {e}")

    orig_w, orig_h = pil_img.size
    best_data = img_data
    best_size = original_size

    def _update(compressed: bytes) -> None:
        nonlocal best_data, best_size
        size = len(compressed)
        if size <= target_bytes:
            if best_size > target_bytes or size > best_size:
                best_data, best_size = compressed, size
        else:
            if best_size > target_bytes and size < best_size:
                best_data, best_size = compressed, size

    def _compress_at(quality: int, scale: float) -> bytes:
        img = pil_img
        if scale < 1.0:
            w = max(1, int(orig_w * scale))
            h = max(1, int(orig_h * scale))
            img = pil_img.resize((w, h), PILImage.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()

    # ── Phase 1: binary-search on JPEG quality (full resolution) ─────────────
    lo_q, hi_q = 5, 85
    for _ in range(12):
        if lo_q > hi_q:
            break
        q = (lo_q + hi_q) // 2
        c = _compress_at(q, 1.0)
        _update(c)
        if len(c) <= target_bytes:
            lo_q = q + 1
        else:
            hi_q = q - 1

    if best_size <= target_bytes:
        return best_data, best_size, True

    # ── Phase 2: binary-search on resolution scaling at quality=30 ────────────
    lo_s, hi_s = 0.1, 1.0
    for _ in range(8):
        if lo_s > hi_s:
            break
        s = (lo_s + hi_s) / 2.0
        c = _compress_at(30, s)
        _update(c)
        if len(c) <= target_bytes:
            lo_s = s + 0.05
        else:
            hi_s = s - 0.05

    return best_data, best_size, best_size <= target_bytes


@bp.route("/compress-image")
def compress_image_page():
    return render_template(
        "tools/compress_image_plus.html",
        title="Compress Images",
        description="Upload images and set your exact desired output file size.",
        endpoint="/acca-plus/compress-image",
        accept=".jpg,.jpeg,.png,.webp,.bmp",
    )


@bp.route("/compress-image", methods=["POST"])
def compress_image_target():
    files = request.files.getlist("files")
    if not files or not files[0].filename:
        return jsonify(error="Please upload at least one image."), 400

    try:
        target_value = float(request.form.get("target_size", "0") or "0")
        target_unit = (request.form.get("target_unit", "KB") or "KB").upper()
        target_bytes = int(target_value * (1024 * 1024 if target_unit == "MB" else 1024))
    except (ValueError, TypeError):
        return jsonify(error="Invalid target size value."), 400

    if target_bytes <= 0:
        return jsonify(error="Target size must be greater than 0."), 400

    # If single file, return it directly
    if len(files) == 1:
        upload = files[0]
        original_name = upload.filename or "image.jpg"
        stem = os.path.splitext(original_name)[0]
        data = upload.read()
        
        try:
            res_data, achv, met = _compress_image_to_target(data, target_bytes)
        except Exception as e:
            log_error(e, f"compress_image.{original_name}")
            return jsonify(error=f"Failed to compress image: {e}"), 400
            
        out_buf = io.BytesIO(res_data)
        out_buf.seek(0)
        resp = send_file(
            out_buf,
            mimetype="image/jpeg",
            as_attachment=True,
            download_name=f"{stem}_compressed.jpg",
        )
        resp.headers["X-Original-Size"] = str(len(data))
        resp.headers["X-Achieved-Size"] = str(achv)
        resp.headers["X-Target-Size"] = str(target_bytes)
        resp.headers["X-Target-Met"] = "1" if met else "0"
        resp.headers["Access-Control-Expose-Headers"] = "X-Original-Size, X-Achieved-Size, X-Target-Size, X-Target-Met"
        return resp

    # If multiple files, return a ZIP
    zip_buf = io.BytesIO()
    log_lines: list[str] = []
    processed = 0

    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for upload in files:
            orig_name = upload.filename or "unknown.jpg"
            stem = os.path.splitext(orig_name)[0]
            data = upload.read()
            
            try:
                res_data, achv, met = _compress_image_to_target(data, target_bytes)
                new_name = f"{stem}_compressed.jpg"
                zf.writestr(new_name, res_data)
                
                status = "OK" if met else f"Target not met (achieved {achv} bytes)"
                log_lines.append(f"{orig_name} → {new_name} [{status}]")
                processed += 1
            except Exception as e:
                log_error(e, f"compress_image.{orig_name}")
                zf.writestr(orig_name, data)
                log_lines.append(f"{orig_name} → ERROR: {e} (kept original)")
                
        zf.writestr("_compression_log.txt", "\n".join(log_lines).encode("utf-8"))

    if processed == 0:
        return jsonify(error="No images could be processed."), 400

    zip_buf.seek(0)
    resp = send_file(
        zip_buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name="compressed_images.zip",
    )
    # Give a generic header so the frontend knows it's a zip response
    resp.headers["X-Is-Zip"] = "1"
    resp.headers["Access-Control-Expose-Headers"] = "X-Is-Zip"
    return resp
