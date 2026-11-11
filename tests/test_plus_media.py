import io
import wave

from app import app
from routes import plus_media


def test_plus_media_pages_and_upload_validation():
    client = app.test_client()
    assert client.get("/acca-plus/voice-recorder").status_code == 200
    assert client.get("/acca-plus/html-to-video").status_code == 200
    assert client.post("/acca-plus/html-to-video").status_code == 400

    invalid = client.post("/acca-plus/html-to-video", data={
        "file": (io.BytesIO(b"<html></html>"), "page.html"),
        "duration": "100", "fps": "15", "size": "landscape",
    })
    assert invalid.status_code == 400

    too_large = client.post("/acca-plus/html-to-video", data={
        "file": (io.BytesIO(b"x" * (2 * 1024 * 1024 + 1)), "page.html"),
        "duration": "1", "fps": "10", "size": "square",
    })
    assert too_large.status_code == 400


def test_voice_clone_requires_model_and_valid_reference(monkeypatch):
    client = app.test_client()
    monkeypatch.setattr(plus_media, "_voice_ready", lambda: False)
    assert client.get("/acca-plus/voice-clone").status_code == 200
    assert client.post("/acca-plus/voice-clone").status_code == 503

    monkeypatch.setattr(plus_media, "_voice_ready", lambda: True)
    invalid = client.post("/acca-plus/voice-clone", data={
        "text": "Halo semuanya.", "reference_text": "Halo.", "voice_permission": "yes",
        "reference": (io.BytesIO(b"not audio"), "sample.wav"),
    })
    assert invalid.status_code == 400

    wav_bytes = io.BytesIO()
    with wave.open(wav_bytes, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 16000)
    wav_bytes.seek(0)
    short = client.post("/acca-plus/voice-clone", data={
        "text": "Halo semuanya.", "reference_text": "Halo.", "voice_permission": "yes",
        "reference": (wav_bytes, "sample.wav"),
    })
    assert short.status_code == 400
