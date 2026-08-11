import importlib.util
import io
import re
import zipfile
import os

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
            '<strong>pix2tex siap digunakan.</strong> Model LaTeX-OCR terdeteksi.</p>'
        )
    else:
        status_html = (
            '<p><i class="bi bi-exclamation-triangle-fill" style="color:#ffb703"></i> '
            '<strong>pix2tex belum terinstal.</strong> Install di server dengan: '
            '<code>pip install pix2tex</code> lalu restart server. '
            'Membutuhkan PyTorch; bobot model (~300 MB) diunduh otomatis saat pertama kali dijalankan.</p>'
        )

    return render_template(
        "tools/latex_ocr.html",
        title="Formula to LaTeX",
        description="Ubah foto atau gambar rumus matematika menjadi kode LaTeX secara otomatis",
        status_html=status_html,
        has_pix2tex=HAS_PIX2TEX,
        endpoint="/acca-plus/latex-ocr",
        accept=".jpg,.jpeg,.png,.bmp,.tiff,.webp",
    )


@bp.route("/latex-ocr", methods=["POST"])
def latex_ocr():
    if not HAS_PIX2TEX:
        return jsonify(
            error="pix2tex belum terinstal. Jalankan: pip install pix2tex di server, lalu restart."
        ), 400

    files = request.files.getlist("files")
    if not files or not files[0].filename:
        return jsonify(error=NO_FILE_SINGLE), 400

    from PIL import Image  # noqa: PLC0415

    try:
        img = Image.open(io.BytesIO(files[0].read())).convert("RGB")
    except Exception as e:
        log_error(e, "latex_ocr.open")
        return jsonify(error="Gagal membuka gambar. Pastikan file adalah gambar yang valid."), 400

    try:
        model = _get_model()
        latex = model(img)
    except Exception as e:
        log_error(e, "latex_ocr.predict")
        return jsonify(error=f"Prediksi LaTeX gagal: {e}"), 500

    if not latex or not latex.strip():
        return jsonify(latex="(Tidak ada formula yang terdeteksi)")

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
    if HAS_PIX2TEX:
        status_html = (
            '<p><i class="bi bi-check-circle-fill" style="color:#16a34a"></i> '
            '<strong>pix2tex siap digunakan.</strong></p>'
        )
    else:
        status_html = (
            '<p><i class="bi bi-exclamation-triangle-fill" style="color:#ffb703"></i> '
            '<strong>pix2tex belum terinstal.</strong> '
            'Install di server: <code>/www/wwwroot/acca-tools/.venv/bin/pip install pix2tex</code> '
            'lalu restart server.</p>'
        )

    return render_template(
        "tools/images_to_tex.html",
        title="Gambar ke Dokumen TeX",
        description="Konversi beberapa gambar rumus menjadi satu dokumen .tex siap kompilasi",
        status_html=status_html,
        has_pix2tex=HAS_PIX2TEX,
        endpoint="/acca-plus/images-to-tex",
        accept=".jpg,.jpeg,.png,.bmp,.tiff,.webp",
    )


@bp.route("/images-to-tex", methods=["POST"])
def images_to_tex():
    if not HAS_PIX2TEX:
        return jsonify(
            error="pix2tex belum terinstal. Jalankan pip install pix2tex di server, lalu restart."
        ), 400

    files = request.files.getlist("files")
    if not files or not files[0].filename:
        return jsonify(error="Harap upload minimal satu gambar."), 400

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

    model = _get_model()
    formulas: list[dict] = []
    errors:   list[str]  = []

    for i, upload in enumerate(files):
        if not upload.filename:
            continue
        label = labels.get(i, f"Formula {i + 1}")
        try:
            img   = Image.open(io.BytesIO(upload.read())).convert("RGB")
            latex = model(img)
            if not latex or not latex.strip():
                latex = r"\text{(tidak terdeteksi)}"
            formulas.append({"label": label, "latex": latex.strip()})
        except Exception as e:
            log_error(e, f"images_to_tex.img{i}")
            errors.append(f"Gambar {i + 1} ({upload.filename}): {e}")
            formulas.append({"label": label,
                              "latex": r"\text{(gagal diproses)}"})

    if not formulas:
        return jsonify(error="Tidak ada gambar yang berhasil diproses."), 400

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
    # Replace filesystem-unsafe characters with underscore
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    # Collapse multiple spaces/underscores
    text = re.sub(r"[\s_]+", "_", text.strip())
    return text[:80]  # cap length to stay safe on all OSes


def _extract_nisn_and_nama(text: str) -> tuple[str, str]:
    """Return (nisn, nama) extracted from PDF text, or empty strings."""
    # NISN ──────────────────────────────────────────────────────────────────
    nisn = ""
    m = _RE_NISN_LABELED.search(text)
    if m:
        nisn = m.group(1)
    else:
        m = _RE_NISN_BARE.search(text)
        if m:
            nisn = m.group(1)

    # Nama ──────────────────────────────────────────────────────────────────
    nama = ""
    m = _RE_NAMA.search(text)
    if m:
        nama = _sanitize(m.group(1))

    return nisn, nama


@bp.route("/rename-pdf")
def rename_pdf_page():
    return render_template(
        "tools/rename_pdf.html",
        title="Rename PDF (NISN & Nama Siswa)",
        description=(
            "Upload banyak file PDF sekaligus. NISN dan Nama Siswa akan diekstrak "
            "otomatis dari isi dokumen, lalu file diunduh sebagai ZIP dengan nama "
            "format: <code>namafile_NISN_NamaSiswa.pdf</code>"
        ),
        endpoint="/acca-plus/rename-pdf",
    )


@bp.route("/rename-pdf", methods=["POST"])
def rename_pdf():
    if fitz is None:
        return jsonify(error="PyMuPDF (fitz) belum terinstal di server."), 500

    files = request.files.getlist("files")
    if not files or not files[0].filename:
        return jsonify(error=NO_FILE_MULTIPLE), 400

    # Optional override patterns from the form
    custom_nisn_pattern = (request.form.get("nisn_pattern") or "").strip()
    custom_nama_pattern = (request.form.get("nama_pattern") or "").strip()

    try:
        re_nisn_custom = re.compile(custom_nisn_pattern, re.IGNORECASE) if custom_nisn_pattern else None
        re_nama_custom = re.compile(custom_nama_pattern, re.IGNORECASE) if custom_nama_pattern else None
    except re.error as e:
        return jsonify(error=f"Pola regex tidak valid: {e}"), 400

    zip_buf = io.BytesIO()
    log_lines: list[str] = []
    processed = 0

    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for upload in files:
            original_filename = upload.filename or "unknown.pdf"
            stem = os.path.splitext(original_filename)[0]

            try:
                data = upload.read()
                doc = fitz.open(stream=data, filetype="pdf")

                # Extract text from first 3 pages for speed; fall back to all pages
                pages_to_scan = min(3, doc.page_count)
                text = "\n".join(doc[i].get_text() for i in range(pages_to_scan))
                if not text.strip() and doc.page_count > 3:
                    text = "\n".join(page.get_text() for page in doc)

                doc.close()

                # NISN extraction
                nisn = ""
                if re_nisn_custom:
                    m = re_nisn_custom.search(text)
                    nisn = m.group(1) if (m and m.lastindex) else (m.group(0) if m else "")
                else:
                    nisn, _ = _extract_nisn_and_nama(text)
                    # re-extract nama separately so custom patterns don't interfere
                    _, nama_from_default = _extract_nisn_and_nama(text)

                # Nama extraction
                nama = ""
                if re_nama_custom:
                    m = re_nama_custom.search(text)
                    nama_raw = m.group(1) if (m and m.lastindex) else (m.group(0) if m else "")
                    nama = _sanitize(nama_raw)
                else:
                    _, nama = _extract_nisn_and_nama(text)

                # Build new filename
                parts = [stem]
                if nisn:
                    parts.append(nisn)
                if nama:
                    parts.append(nama)

                new_name = "_".join(parts) + ".pdf"
                status = "OK"
                if not nisn:
                    status += " [NISN tidak ditemukan]"
                if not nama:
                    status += " [Nama tidak ditemukan]"

            except Exception as e:
                log_error(e, f"rename_pdf.{original_filename}")
                new_name = original_filename  # keep original name on error
                data = upload.read() if not data else data
                status = f"ERROR: {e}"

            zf.writestr(new_name, data)
            log_lines.append(f"{original_filename}  →  {new_name}  [{status}]")
            processed += 1

        # Include a processing log in the ZIP
        log_text = "\n".join(log_lines)
        zf.writestr("_rename_log.txt", log_text.encode("utf-8"))

    if processed == 0:
        return jsonify(error="Tidak ada file PDF yang berhasil diproses."), 400

    zip_buf.seek(0)
    return send_file(
        zip_buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name="renamed_pdfs.zip",
    )
