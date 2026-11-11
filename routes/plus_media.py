"""ACCA Plus audio recording, local voice synthesis, and HTML video export."""

import importlib.util
import io
import os
import shutil
import subprocess
import tempfile
import time
import wave
import math
from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template, request, send_file

bp = Blueprint("plus_media", __name__)

MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_VOICE_BYTES = 10 * 1024 * 1024
VOICE_MODEL_DIR = Path(os.environ.get("ACCA_VOICE_MODEL_DIR", Path(__file__).resolve().parent.parent / "voice_models"))
SIZES = {"square": (720, 720), "landscape": (1280, 720), "portrait": (720, 1280)}
FRAME_RATES = {10, 15, 24}


def _fit_video_size(width, height):
    """Keep the HTML aspect ratio within a practical H.264 frame budget."""
    try:
        width, height = int(width), int(height)
    except (TypeError, ValueError, OverflowError):
        return SIZES["landscape"]
    if width < 160 or height < 160:
        return SIZES["landscape"]
    scale = min(1, 1920 / max(width, height), math.sqrt((1920 * 1080) / (width * height)))
    return max(160, int(width * scale) // 2 * 2), max(160, int(height * scale) // 2 * 2)


def _html_video_size(page):
    """Read declared video dimensions, then the largest visible HTML region."""
    dimensions = page.evaluate(r"""() => {
      const root = document.documentElement, body = document.body;
      const pair = (w, h) => {
        w = Number(w); h = Number(h);
        return Number.isFinite(w) && Number.isFinite(h) && w >= 160 && h >= 160 ? [w, h] : null;
      };
      for (const el of [root, body]) {
        const declared = pair(el?.dataset.videoWidth, el?.dataset.videoHeight);
        if (declared) return declared;
      }
      const meta = document.querySelector('meta[name="video-size"]')?.content?.match(/(\d+)\s*[x×,]\s*(\d+)/i);
      if (meta) return pair(meta[1], meta[2]);
      const canvas = [...document.querySelectorAll('canvas[width][height]')]
        .map(el => pair(el.width, el.height)).filter(Boolean).sort((a, b) => b[0]*b[1] - a[0]*a[1])[0];
      if (canvas) return canvas;
      const blocks = [...body.children].filter(el => !['SCRIPT','STYLE','LINK'].includes(el.tagName))
        .map(el => { const r = el.getBoundingClientRect(); return pair(r.width, r.height); })
        .filter(Boolean).sort((a, b) => b[0]*b[1] - a[0]*a[1]);
      if (blocks.length) return blocks[0];
      return pair(root.scrollWidth, root.scrollHeight) || [1280, 720];
    }""")
    return _fit_video_size(*dimensions)


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
        duration = int(request.form.get("duration", "10"))
        fps = int(request.form.get("fps", "15"))
    except ValueError:
        return jsonify(error="Durasi dan FPS harus berupa angka."), 400
    size_mode = request.form.get("size", "auto")
    if (size_mode != "auto" and size_mode not in SIZES) or not 1 <= duration <= 60 or fps not in FRAME_RATES:
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
                    initial_size = SIZES.get(size_mode, SIZES["landscape"])
                    context = browser.new_context(viewport={"width": initial_size[0], "height": initial_size[1]},
                                                  device_scale_factor=1, service_workers="block")
                    context.route("**/*", lambda route: route.abort())
                    page = context.new_page()
                    page.set_content(html, wait_until="load", timeout=15000)
                    page.evaluate("document.fonts.ready")
                    size = _html_video_size(page) if size_mode == "auto" else initial_size
                    if size != initial_size:
                        page.set_viewport_size({"width": size[0], "height": size[1]})
                    output = os.path.join(workdir, "video.mp4")
                    stderr_path = os.path.join(workdir, "ffmpeg-error.txt")
                    with open(stderr_path, "wb+") as ffmpeg_errors:
                        encoder = subprocess.Popen(
                            [shutil.which("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                             "-f", "image2pipe", "-framerate", str(fps), "-vcodec", "png", "-i", "pipe:0",
                             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                             "-movflags", "+faststart", output],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=ffmpeg_errors,
                        )
                        try:
                            start = time.monotonic()
                            for frame in range(duration * fps):
                                if time.monotonic() - start > 540:
                                    raise TimeoutError("Proses render melewati batas 9 menit.")
                                target = start + frame / fps
                                delay = target - time.monotonic()
                                if delay > 0:
                                    time.sleep(delay)
                                frame_bytes = page.screenshot(type="png", full_page=False,
                                                              animations="allow", timeout=15000)
                                try:
                                    encoder.stdin.write(frame_bytes)
                                except BrokenPipeError as exc:
                                    ffmpeg_errors.seek(0)
                                    detail = ffmpeg_errors.read(400).decode("utf-8", "replace")
                                    raise RuntimeError("FFmpeg berhenti saat merender: " + detail) from exc
                            encoder.stdin.close()
                            encoder.wait(timeout=45)
                        finally:
                            if encoder.poll() is None:
                                encoder.kill()
                                encoder.wait()
                            if encoder.stdin and not encoder.stdin.closed:
                                encoder.stdin.close()
                        if encoder.returncode:
                            ffmpeg_errors.seek(0)
                            detail = ffmpeg_errors.read(400).decode("utf-8", "replace")
                            return jsonify(error="FFmpeg gagal membuat video: " + detail), 500
                    context.close()
                finally:
                    browser.close()
            if os.path.getsize(output) > 150 * 1024 * 1024:
                return jsonify(error="Video terlalu besar. Coba FPS atau durasi yang lebih rendah."), 400
            with open(output, "rb") as result:
                data = result.read()
    except Exception as exc:
        # Browser binaries may be missing even when the Python package is installed.
        return jsonify(error=f"Gagal merender HTML: {str(exc)[:300]}"), 500

    response = send_file(io.BytesIO(data), mimetype="video/mp4", as_attachment=True,
                         download_name="acca-html-video.mp4")
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Video-Width"] = str(size[0])
    response.headers["X-Video-Height"] = str(size[1])
    return response
