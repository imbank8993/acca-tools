"""ACCA Plus audio recording, local voice synthesis, and HTML video export."""

import importlib.util
import io
import os
import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template, request, send_file

bp = Blueprint("plus_media", __name__)

MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_VOICE_BYTES = 10 * 1024 * 1024
VOICE_MODEL_DIR = Path(os.environ.get("ACCA_VOICE_MODEL_DIR", Path(__file__).resolve().parent.parent / "voice_models"))
SIZES = {"square": (720, 720), "landscape": (1280, 720), "portrait": (720, 1280)}
FRAME_RATES = {10, 15, 24}


@bp.get("/voice-recorder")
def voice_recorder():
    return render_template("tools/voice_recorder.html")


def _voice_ready():
    return importlib.util.find_spec("arbotts") is not None and (VOICE_MODEL_DIR / "READY").is_file()


@bp.route("/voice-clone", methods=["GET", "POST"])
def voice_clone():
    if request.method == "GET":
        return render_template("tools/voice_clone.html", ready=_voice_ready())

    if not _voice_ready():
        return jsonify(error="Model suara lokal belum siap di VPS. Lihat panduan instalasi di halaman ini."), 503
    text = request.form.get("text", "").strip()
    reference_text = request.form.get("reference_text", "").strip()
    upload = request.files.get("reference")
    if not text or len(text) > 500:
        return jsonify(error="Masukkan teks sepanjang 1–500 karakter."), 400
    if not reference_text or len(reference_text) > 300:
        return jsonify(error="Tuliskan isi rekaman contoh sepanjang 1–300 karakter."), 400
    if not upload or not upload.filename or not upload.filename.lower().endswith(".wav"):
        return jsonify(error="Unggah contoh suara dalam format WAV."), 400
    if request.form.get("voice_permission") != "yes":
        return jsonify(error="Pastikan Anda berhak menggunakan contoh suara tersebut."), 400
    raw = upload.stream.read(MAX_VOICE_BYTES + 1)
    if not raw or len(raw) > MAX_VOICE_BYTES:
        return jsonify(error="Contoh suara harus berukuran maksimal 10 MB."), 400
    try:
        with wave.open(io.BytesIO(raw), "rb") as wav:
            seconds = wav.getnframes() / wav.getframerate()
            valid = wav.getcomptype() == "NONE" and wav.getnchannels() in (1, 2) and 8000 <= wav.getframerate() <= 96000
    except (wave.Error, EOFError, ZeroDivisionError):
        valid, seconds = False, 0
    if not valid or not 3 <= seconds <= 30:
        return jsonify(error="Gunakan WAV PCM mono/stereo berdurasi 3–30 detik."), 400

    with tempfile.TemporaryDirectory(prefix="acca-voice-") as workdir:
        reference = os.path.join(workdir, "reference.wav")
        output = os.path.join(workdir, "speech.wav")
        with open(reference, "wb") as file:
            file.write(raw)
        try:
            worker_env = os.environ.copy()
            worker_env["HF_HUB_OFFLINE"] = "1"
            worker_env["TRANSFORMERS_OFFLINE"] = "1"
            proc = subprocess.run(
                [os.sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "voice_clone_worker.py"),
                 str(VOICE_MODEL_DIR), reference, output, text, reference_text],
                capture_output=True, timeout=240, env=worker_env,
            )
        except subprocess.TimeoutExpired:
            return jsonify(error="Pembuatan suara melewati batas waktu. Coba teks yang lebih pendek."), 504
        if proc.returncode or not os.path.isfile(output):
            current_app.logger.error("Voice synthesis failed: %s", proc.stderr.decode("utf-8", "replace")[-1500:])
            return jsonify(error="Pembuatan suara gagal. Periksa model suara dan log server."), 500
        if os.path.getsize(output) > 50 * 1024 * 1024:
            return jsonify(error="Hasil audio terlalu besar."), 500
        with open(output, "rb") as file:
            data = file.read()
    response = send_file(io.BytesIO(data), mimetype="audio/wav", as_attachment=True,
                         download_name="narasi-acca.wav")
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.route("/html-to-video", methods=["GET", "POST"])
def html_to_video():
    if request.method == "GET":
        return render_template(
            "tools/html_to_video.html",
            ready=bool(shutil.which("ffmpeg") and importlib.util.find_spec("playwright")),
        )

    upload = request.files.get("file")
    if not upload or not upload.filename or not upload.filename.lower().endswith((".html", ".htm")):
        return jsonify(error="Pilih berkas HTML (.html atau .htm)."), 400
    try:
        duration = int(request.form.get("duration", "5"))
        fps = int(request.form.get("fps", "15"))
    except ValueError:
        return jsonify(error="Durasi dan FPS harus berupa angka."), 400
    size = SIZES.get(request.form.get("size", "landscape"))
    if size is None or not 1 <= duration <= 10 or fps not in FRAME_RATES:
        return jsonify(error="Pilihan ukuran, durasi, atau FPS tidak valid."), 400
    raw = upload.stream.read(MAX_HTML_BYTES + 1)
    if not raw or len(raw) > MAX_HTML_BYTES:
        return jsonify(error="HTML harus berisi data dan berukuran maksimal 2 MB."), 400
    if not shutil.which("ffmpeg") or importlib.util.find_spec("playwright") is None:
        return jsonify(error="Server memerlukan FFmpeg dan Playwright Chromium. Lihat petunjuk instalasi di halaman ini."), 503

    try:
        html = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify(error="Simpan HTML dengan encoding UTF-8."), 400

    from playwright.sync_api import sync_playwright

    try:
        with tempfile.TemporaryDirectory(prefix="acca-html-video-") as workdir:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    context = browser.new_context(viewport={"width": size[0], "height": size[1]},
                                                  device_scale_factor=1, service_workers="block")
                    context.route("**/*", lambda route: route.abort())
                    page = context.new_page()
                    page.set_content(html, wait_until="load", timeout=15000)
                    page.evaluate("document.fonts.ready")
                    start = time.monotonic()
                    for frame in range(duration * fps):
                        target = start + frame / fps
                        delay = target - time.monotonic()
                        if delay > 0:
                            time.sleep(delay)
                        page.screenshot(path=os.path.join(workdir, f"frame-{frame:04d}.png"),
                                        animations="allow", timeout=10000)
                    context.close()
                finally:
                    browser.close()

            output = os.path.join(workdir, "video.mp4")
            proc = subprocess.run(
                [shutil.which("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                 "-framerate", str(fps), "-i", os.path.join(workdir, "frame-%04d.png"),
                 "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                 "-movflags", "+faststart", output],
                capture_output=True, timeout=180,
            )
            if proc.returncode:
                return jsonify(error="FFmpeg gagal membuat video: " + proc.stderr.decode("utf-8", "replace")[:300]), 500
            with open(output, "rb") as result:
                data = result.read()
    except Exception as exc:
        # Browser binaries may be missing even when the Python package is installed.
        return jsonify(error=f"Gagal merender HTML: {str(exc)[:300]}"), 500

    response = send_file(io.BytesIO(data), mimetype="video/mp4", as_attachment=True,
                         download_name="acca-html-video.mp4")
    response.headers["Cache-Control"] = "private, no-store"
    return response
