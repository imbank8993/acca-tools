"""Disk-backed PDF jobs, shared across Gunicorn workers on a single host.

Unpredictable tokens protect downloads; files expire after one hour. Separate
processes avoid PyMuPDF's thread-safety restrictions and enforce a hard timeout.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(tempfile.gettempdir()) / "acca-pdf-jobs"
TTL = 3600
TIMEOUT = 300


def _write(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value), encoding="utf-8")
    temp.replace(path)


def job_path(token):
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        return None
    path = ROOT / token
    if not path.is_dir() or time.time() - path.stat().st_mtime > TTL:
        return None
    return path


def start_job(data, target, mode, filename):
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    for path in ROOT.iterdir():
        try:
            expiry = TIMEOUT + 60 if path.name.startswith("slot-") else TTL
            if path.is_dir() and time.time() - path.stat().st_mtime > expiry:
                shutil.rmtree(path, ignore_errors=True)
        except FileNotFoundError:
            pass  # another worker cleaned it up
    # Atomic directories cap concurrency across all web workers.
    slot = None
    for index in range(2):
        candidate = ROOT / f"slot-{index}"
        try:
            candidate.mkdir(mode=0o700)
            slot = candidate
            break
        except FileExistsError:
            continue
    if slot is None:
        raise RuntimeError("PDF processors are busy. Please try again shortly.")
    token = uuid.uuid4().hex
    path = ROOT / token
    try:
        path.mkdir(mode=0o700)
        (path / "input.pdf").write_bytes(data)
        _write(path / "settings.json", dict(target=target, mode=mode,
                                            filename=filename, original=len(data)))
        _write(path / "status.json", dict(state="processing"))
        subprocess.Popen([sys.executable, "-m", "utils.pdf_jobs", token, str(slot)],
                         cwd=str(Path(__file__).resolve().parents[1]),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        shutil.rmtree(path, ignore_errors=True)
        shutil.rmtree(slot, ignore_errors=True)
        raise
    return token


def status(token):
    path = job_path(token)
    if path is None:
        return None
    result = json.loads((path / "status.json").read_text(encoding="utf-8"))
    if result["state"] == "processing" and time.time() - path.stat().st_mtime > TIMEOUT + 30:
        return dict(state="error", error="Processing was interrupted. Please try again.")
    return result


def _run(token):
    from routes.acca_plus import _compress_to_target
    path = job_path(token)
    # Keep render caches inside the job so the supervisor can clean them even
    # when the worker is terminated by its hard timeout.
    tempfile.tempdir = str(path)
    settings = json.loads((path / "settings.json").read_text(encoding="utf-8"))
    data, size, met = _compress_to_target(
        (path / "input.pdf").read_bytes(), settings["target"],
        force_render=settings["mode"] == "render", lossless=settings["mode"] == "lossless")
    (path / "output.pdf").write_bytes(data)
    _write(path / "status.json", dict(state="done", achieved=size, target_met=met))


if __name__ == "__main__":
    token = sys.argv[1]
    if sys.argv[2] == "--run":
        _run(token)
    else:
        path = job_path(token)
        try:
            subprocess.run([sys.executable, "-m", "utils.pdf_jobs", token, "--run"],
                           check=True, timeout=TIMEOUT,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            _write(path / "status.json", dict(state="error", error=
                   "Compression failed or exceeded 5 minutes. Try lossless mode or a smaller PDF."))
        finally:
            (path / "input.pdf").unlink(missing_ok=True)
            for cache in path.glob("acca-render-*"):
                shutil.rmtree(cache, ignore_errors=True)
            shutil.rmtree(Path(sys.argv[2]), ignore_errors=True)
