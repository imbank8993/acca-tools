"""ACCA Plus audio recording and HTML video export."""

import importlib.util
import base64
import io
import os
import re
import shutil
import subprocess
import tempfile
import time
import math

from flask import Blueprint, jsonify, redirect, render_template, request, send_file, url_for

bp = Blueprint("plus_media", __name__)

MAX_HTML_BYTES = 2 * 1024 * 1024
SIZES = {"square": (720, 720), "landscape": (1280, 720), "portrait": (720, 1280)}
FRAME_RATES = {10, 15, 24, 30}


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


def _html_video_timing(page, html):
    """Prefer the page's timeline; fall back to finite CSS animations."""
    timing = page.evaluate(r"""() => {
      const root = document.documentElement, body = document.body;
      const positive = value => Number.isFinite(Number(value)) && Number(value) > 0 ? Number(value) : null;
      const meta = name => document.querySelector(`meta[name="${name}"]`)?.content;
      let duration = positive(root.dataset.videoDuration) || positive(body.dataset.videoDuration)
        || positive(meta('video-duration'));
      if (!duration) {
        for (const name of ['DUR', 'DURATION', 'TOTAL_DURATION', 'VIDEO_DURATION']) {
          try { duration = positive(eval(name)); if (duration) break; } catch (_) {}
        }
      }
      if (!duration) {
        try { if (Array.isArray(SCENES)) duration = positive(SCENES.at(-1)?.[1]); } catch (_) {}
      }
      if (!duration) {
        const ends = document.getAnimations().map(a => a.effect?.getComputedTiming()?.endTime)
          .filter(v => Number.isFinite(v) && v > 0);
        if (ends.length) duration = Math.max(...ends) / 1000;
      }
      let fps = positive(root.dataset.videoFps) || positive(body.dataset.videoFps)
        || positive(meta('video-fps'));
      if (!fps) {
        for (const name of ['FPS', 'FRAME_RATE', 'VIDEO_FPS']) {
          try { fps = positive(eval(name)); if (fps) break; } catch (_) {}
        }
      }
      return {duration, fps, seek: typeof window.__seek === 'function'};
    }""")
    if not timing["fps"]:
        match = re.search(r"\.captureStream\(\s*(\d+(?:\.\d+)?)\s*\)", html, re.IGNORECASE)
        if match:
            timing["fps"] = float(match.group(1))
    if not timing["duration"]:
        match = re.search(r"\b(?:const|let|var)\s+(?:DUR|DURATION|TOTAL_DURATION|VIDEO_DURATION)\s*=\s*(\d+(?:\.\d+)?)\b", html, re.IGNORECASE)
        if match:
            timing["duration"] = float(match.group(1))
    duration = min(60, max(1, math.ceil(timing["duration"]))) if timing["duration"] else 10
    fps = min(FRAME_RATES, key=lambda n: abs(n - timing["fps"])) if timing["fps"] else 15
    return duration, fps, timing["seek"]


@bp.get("/voice-recorder")
def voice_recorder():
    return redirect(url_for("media.voice_recorder"), code=301)


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
    duration_raw = request.form.get("duration", "auto")
    fps_raw = request.form.get("fps", "auto")
    try:
        manual_duration = None if duration_raw == "auto" else int(
            request.form.get("custom_duration", "") if duration_raw == "manual" else duration_raw
        )
        manual_fps = None if fps_raw == "auto" else int(fps_raw)
    except ValueError:
        return jsonify(error="Durasi dan FPS harus berupa angka."), 400
    size_mode = request.form.get("size", "auto")
    if ((size_mode != "auto" and size_mode not in SIZES) or
            (manual_duration is not None and not 1 <= manual_duration <= 60) or
            (manual_fps is not None and manual_fps not in FRAME_RATES)):
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
                    html_duration, html_fps, has_seek = _html_video_timing(page, html)
                    duration = manual_duration if manual_duration is not None else html_duration
                    fps = manual_fps if manual_fps is not None else html_fps
                    canvas_capture = has_seek and bool(page.evaluate("""() => {
                      const c = document.querySelector('canvas');
                      return c && c.width >= 160 && c.height >= 160;
                    }"""))
                    if size != initial_size:
                        page.set_viewport_size({"width": size[0], "height": size[1]})
                    output = os.path.join(workdir, "video.mp4")
                    stderr_path = os.path.join(workdir, "ffmpeg-error.txt")
                    with open(stderr_path, "wb+") as ffmpeg_errors:
                        encoder = subprocess.Popen(
                            [shutil.which("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                             "-f", "image2pipe", "-framerate", str(fps), "-vcodec", "png", "-i", "pipe:0",
                             "-vf", (f"scale={size[0]}:{size[1]}:force_original_aspect_ratio=decrease:flags=lanczos,"
                                      f"pad={size[0]}:{size[1]}:(ow-iw)/2:(oh-ih)/2") if canvas_capture
                                     else f"scale={size[0]}:{size[1]}:flags=lanczos",
                             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                             "-movflags", "+faststart", output],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=ffmpeg_errors,
                        )
                        try:
                            start = time.monotonic()
                            for frame in range(duration * fps):
                                if time.monotonic() - start > 540:
                                    raise TimeoutError("Proses render melewati batas 9 menit.")
                                if has_seek:
                                    page.evaluate("t => window.__seek(t)", frame / fps)
                                else:
                                    target = start + frame / fps
                                    delay = target - time.monotonic()
                                    if delay > 0:
                                        time.sleep(delay)
                                if canvas_capture:
                                    encoded = page.evaluate("() => document.querySelector('canvas').toDataURL('image/png').split(',')[1]")
                                    frame_bytes = base64.b64decode(encoded)
                                else:
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
    response.headers["X-Video-Duration"] = str(duration)
    response.headers["X-Video-Fps"] = str(fps)
    return response
