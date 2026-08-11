import importlib.util
import io

from flask import Blueprint, jsonify, render_template, request

from routes._helpers import NO_FILE_SINGLE, log_error
from utils.capabilities import QUALITY_HIGH, QUALITY_UNAVAILABLE

bp = Blueprint("acca_plus", __name__)

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
