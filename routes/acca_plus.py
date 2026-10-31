import importlib.util
import io
import json
import os
import re
import zipfile

from flask import Blueprint, jsonify, render_template, request, send_file, url_for

from routes._helpers import NO_FILE_SINGLE, NO_FILE_MULTIPLE, log_error
from utils.pymupdf import import_pymupdf

bp = Blueprint("acca_plus", __name__)

fitz = import_pymupdf()

# ---------------------------------------------------------------------------
# Lazy pix2tex detection – heavy import is deferred until first request so
# the app starts instantly even when pix2tex is not installed.
# ---------------------------------------------------------------------------

HAS_PIX2TEX = importlib.util.find_spec("pix2tex") is not None

_latex_ocr_model = None  # cached singleton; loaded on first POST




def _get_model():
    """Return a cached LatexOCR instance, loading it on first call."""
    global _latex_ocr_model
    if _latex_ocr_model is None:
        from pix2tex.cli import LatexOCR  # noqa: PLC0415

        _latex_ocr_model = LatexOCR()
    return _latex_ocr_model


def _unique_name(name: str, used_names: set[str]) -> str:
    """Ensure filename is unique within a zip archive to prevent overwrites."""
    if name not in used_names:
        used_names.add(name)
        return name
    stem, ext = os.path.splitext(name)
    counter = 1
    while f"{stem}_{counter}{ext}" in used_names:
        counter += 1
    unique = f"{stem}_{counter}{ext}"
    used_names.add(unique)
    return unique


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

    from PIL import Image, ImageOps  # noqa: PLC0415

    try:
        raw_img = Image.open(io.BytesIO(files[0].read()))
        raw_img = ImageOps.exif_transpose(raw_img)
        if raw_img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", raw_img.size, (255, 255, 255))
            rgba = raw_img.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[3])
            img = bg
        else:
            img = raw_img.convert("RGB")
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
    used_names: set[str] = set()
    processed = 0

    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for upload in files:
            original_filename = upload.filename or "unknown.pdf"
            stem = os.path.splitext(original_filename)[0]
            data = b""

            try:
                data = upload.read()
                if not data:
                    raise ValueError("Uploaded file is empty.")
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
                status = f"ERROR: {e}"

            unique_new_name = _unique_name(new_name, used_names)
            zf.writestr(unique_new_name, data)
            log_lines.append(f"{original_filename}  →  {unique_new_name}  [{status}]")
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

def _compress_pdf_at(data: bytes, jpeg_quality: int, max_dpi: int) -> bytes:
    """Optimize embedded images at their actual printed size, preserving masks.

    Text and vector paths are never rasterized. Shared logos are rewritten once
    at the largest size at which they appear anywhere in the document.
    """
    import math
    from PIL import Image

    with fitz.open(stream=data, filetype="pdf") as doc:
        images = {}
        masks = set()
        for page in doc:
            for info in page.get_images(full=True):
                xref, mask = info[:2]
                if mask:
                    masks.add(mask)
                item = images.setdefault(xref, {"page": page.number, "mask": mask,
                                                "width": 0.0, "height": 0.0})
                for rect in page.get_image_rects(xref):
                    item["width"] = max(item["width"], rect.width)
                    item["height"] = max(item["height"], rect.height)
        for xref, item in images.items():
            if xref in masks:
                continue
            try:
                # Color-key masks and stencils require separate handling.
                if doc.xref_get_key(xref, "Mask")[0] != "null":
                    continue
                if doc.xref_get_key(xref, "ImageMask")[1] == "true":
                    continue
                pix = fitz.Pixmap(doc, xref)
                if pix.colorspace is None:
                    continue
                if pix.colorspace.n >= 4:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                if item["mask"]:
                    pix = fitz.Pixmap(pix, fitz.Pixmap(doc, item["mask"]))
                with Image.open(io.BytesIO(pix.tobytes("png"))) as original:
                    image = original.copy()
                if max_dpi and item["width"] and item["height"]:
                    width = max(1, math.ceil(item["width"] * max_dpi / 72))
                    height = max(1, math.ceil(item["height"] * max_dpi / 72))
                    scale = min(1.0, max(width / image.width, height / image.height))
                    if scale < 1:
                        image = image.resize((max(1, math.ceil(image.width * scale)),
                                              max(1, math.ceil(image.height * scale))),
                                             Image.Resampling.LANCZOS)
                png = io.BytesIO()
                image.save(png, format="PNG", optimize=True)
                candidates = [png.getvalue()]
                # PNG retains alpha and avoids JPEG artifacts around logos/text.
                if image.mode not in ("RGBA", "LA", "P"):
                    jpeg = io.BytesIO()
                    image.save(jpeg, format="JPEG", quality=jpeg_quality,
                               optimize=False)
                    candidates.append(jpeg.getvalue())
                replacement = min(candidates, key=len)
                original_size = len(doc.xref_stream_raw(xref) or b"")
                if item["mask"]:
                    original_size += len(doc.xref_stream_raw(item["mask"]) or b"")
                if len(replacement) < original_size:
                    dummy = doc.new_page()
                    try:
                        new_xref = dummy.insert_image(dummy.rect, stream=replacement)
                        if new_xref != xref:
                            doc.xref_copy(new_xref, xref)
                    finally:
                        doc.delete_page(dummy.number)
            except Exception as exc:
                log_error(exc, f"compress_pdf image={xref}")
        return doc.tobytes(garbage=4, deflate=True,
                           deflate_images=True, deflate_fonts=True,
                           use_objstms=1, compression_effort=100)


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



def _lossless_pdf(data: bytes) -> bytes:
    """Optimize objects/fonts without rasterizing text or re-encoding images."""
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            # Preserve font programs: re-subsetting existing subset fonts can
            # change space metrics and text selection in ReportLab documents.
            optimized = doc.tobytes(garbage=4, deflate=True, deflate_images=True,
                                   deflate_fonts=True, use_objstms=1,
                                   compression_effort=100)
    except Exception as e:
        log_error(e, "lossless_pdf")
        optimized = data
    candidates = [data, optimized, _compress_with_pikepdf(optimized)]
    return min(candidates, key=len)


def _compress_to_target(data: bytes, target_bytes: int, force_render: bool = False,
                        lossless: bool = False) -> tuple[bytes, int, bool]:
    """Find an output within the target while retaining text and vector tables.

    force_render is a legacy API flag, now enabling the same safe image-only
    optimization. Whole-page rasterization is deliberately never performed.
    """
    if target_bytes <= 0:
        raise ValueError("Target must be positive.")
    if len(data) <= target_bytes:
        return data, len(data), True
    best = _lossless_pdf(data)
    if lossless or len(best) <= target_bytes:
        return best, len(best), len(best) <= target_bytes
    base = best
    with fitz.open(stream=base, filetype="pdf") as doc:
        images = {info[0]: info for page in doc for info in page.get_images(full=True)}
    if not images:
        return best, len(best), False
    # Begin at print resolution: small logos often have thousands of pixels
    # although they occupy only a few centimetres on the page.
    has_opaque = any(not info[1] for info in images.values())
    for dpi in (600, 450, 300, 240, 200, 150):
        trial = _compress_pdf_at(base, 95, dpi)
        if len(trial) < len(best):
            best = trial
        if len(trial) <= target_bytes:
            return trial, len(trial), True
        if has_opaque:
            # Do not sacrifice readability by driving JPEG quality toward zero.
            lo, hi = 60, 94
            fitting = None
            while lo <= hi:
                quality = (lo + hi) // 2
                trial = _compress_pdf_at(base, quality, dpi)
                if len(trial) < len(best):
                    best = trial
                if len(trial) <= target_bytes:
                    fitting = trial
                    lo = quality + 1
                else:
                    hi = quality - 1
            if fitting is not None:
                return fitting, len(fitting), True
    return best, len(best), len(best) <= target_bytes


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
    except (ValueError, TypeError, OverflowError):
        return jsonify(error="Invalid target size value."), 400

    if target_bytes <= 0:
        return jsonify(error="Target size must be greater than 0."), 400

    mode = request.form.get("mode") or ("render" if request.form.get("force_render") == "1" else "smart")
    if mode not in ("smart", "lossless", "balanced", "render"):
        return jsonify(error="Invalid compression mode."), 400
    if target_unit not in ("KB", "MB"):
        return jsonify(error="Invalid target size unit."), 400

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
        if chk.needs_pass or chk.page_count == 0:
            chk.close()
            return jsonify(error="Please upload a non-empty, unlocked PDF."), 400
        chk.close()
    except Exception:
        return jsonify(error="Could not open PDF. File may be corrupted or encrypted."), 400

    original_size = len(data)

    if request.form.get("async") == "1":
        from utils.pdf_jobs import start_job
        try:
            token = start_job(data, target_bytes, mode, f"{stem}_compressed.pdf")
        except RuntimeError as e:
            return jsonify(error=str(e)), 503
        except Exception as e:
            log_error(e, "compress_pdf_target.start")
            return jsonify(error="Unable to start PDF compression. Please retry."), 500
        return jsonify(status_url=url_for("acca_plus.compress_pdf_status", token=token)), 202

    try:
        result_data, achieved_size, target_met = _compress_to_target(
            data, target_bytes, force_render=mode == "render", lossless=mode == "lossless")
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


@bp.route("/compress-pdf/jobs/<token>")
def compress_pdf_status(token):
    from utils.pdf_jobs import status
    result = status(token)
    if result is None:
        return jsonify(error="Compression result expired or was not found."), 404
    if result["state"] == "done":
        result["download_url"] = url_for("acca_plus.compress_pdf_download", token=token)
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/compress-pdf/jobs/<token>/download")
def compress_pdf_download(token):
    from utils.pdf_jobs import job_path, status
    result = status(token)
    if result is None:
        return jsonify(error="Compression result expired or was not found."), 404
    if result["state"] != "done":
        return jsonify(error="Compression is not complete."), 409
    path = job_path(token)
    settings = json.loads((path / "settings.json").read_text(encoding="utf-8"))
    response = send_file(path / "output.pdf", mimetype="application/pdf",
                         as_attachment=True, download_name=settings["filename"])
    for name, value in {"Original-Size": settings["original"],
                        "Achieved-Size": result["achieved"],
                        "Target-Size": settings["target"],
                        "Target-Met": int(result["target_met"])}.items():
        response.headers[f"X-{name}"] = str(value)
    response.headers["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------------------
# Compress Image to Target Size
# ---------------------------------------------------------------------------

def _compress_image_to_target(img_data: bytes, target_bytes: int) -> tuple[bytes, int, bool]:
    """
    Binary-search JPEG compression settings to hit target_bytes for a single image.
    Ensures output is always a valid JPEG, respects EXIF orientation, and
    neatly composites transparent images on a white background.
    Returns (best_data, achieved_size, target_met).
    """
    from PIL import Image as PILImage, ImageOps

    if not img_data:
        raise ValueError("Image file is empty.")

    try:
        pil_raw = PILImage.open(io.BytesIO(img_data))
        is_orig_jpeg = (pil_raw.format == "JPEG")
        pil_img = ImageOps.exif_transpose(pil_raw)

        if pil_img.mode in ("RGBA", "LA", "P"):
            bg = PILImage.new("RGB", pil_img.size, (255, 255, 255))
            rgba = pil_img.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[3])
            pil_img = bg
        elif pil_img.mode not in ("RGB", "L"):
            pil_img = pil_img.convert("RGB")
    except Exception as e:
        raise ValueError(f"Invalid image: {e}")

    orig_w, orig_h = pil_img.size
    original_size = len(img_data)

    def _compress_at(quality: int, scale: float) -> bytes:
        img = pil_img
        if scale < 1.0:
            w = max(1, int(orig_w * scale))
            h = max(1, int(orig_h * scale))
            img = pil_img.resize((w, h), PILImage.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()

    # If the original file was already a JPEG, meets the target, and did not require
    # EXIF rotation, keep original bytes directly.
    if is_orig_jpeg and original_size <= target_bytes and pil_raw.size == pil_img.size:
        return img_data, original_size, True

    # Otherwise, generate initial full-res JPEG representation at high quality
    init_data = _compress_at(85, 1.0)
    best_data = init_data
    best_size = len(init_data)

    if best_size <= target_bytes:
        return best_data, best_size, True

    def _update(compressed: bytes) -> None:
        nonlocal best_data, best_size
        size = len(compressed)
        if size <= target_bytes:
            if best_size > target_bytes or size > best_size:
                best_data, best_size = compressed, size
        else:
            if best_size > target_bytes and size < best_size:
                best_data, best_size = compressed, size

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
        s = (lo_s + hi_s) / 2.0
        c = _compress_at(30, s)
        _update(c)
        if len(c) <= target_bytes:
            lo_s = s
        else:
            hi_s = s

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
    used_names: set[str] = set()
    processed = 0

    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for upload in files:
            orig_name = upload.filename or "unknown.jpg"
            stem = os.path.splitext(orig_name)[0]
            data = upload.read()
            
            try:
                res_data, achv, met = _compress_image_to_target(data, target_bytes)
                new_name = _unique_name(f"{stem}_compressed.jpg", used_names)
                zf.writestr(new_name, res_data)
                
                status = "OK" if met else f"Target not met (achieved {achv} bytes)"
                log_lines.append(f"{orig_name} → {new_name} [{status}]")
                processed += 1
            except Exception as e:
                log_error(e, f"compress_image.{orig_name}")
                fallback_name = _unique_name(orig_name, used_names)
                zf.writestr(fallback_name, data)
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
