import io
import math
import struct
import wave

import pytest

from app import TOOL_CATEGORIES, app
from routes import media_tools


def _sine_wav(seconds=2.0, rate=24000, freq=220.0) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = int(seconds * rate)
        w.writeframes(b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / rate))) for i in range(frames)
        ))
    return buf.getvalue()


def test_atempo_chain_stays_within_ffmpeg_limits():
    assert media_tools.atempo_chain(1.25) == "atempo=1.25"
    assert media_tools.atempo_chain(0.5) == "atempo=0.5"
    assert media_tools.atempo_chain(2.0) == "atempo=2"
    assert media_tools.atempo_chain(3.0) == "atempo=2,atempo=1.5"
    assert media_tools.atempo_chain(0.3) == "atempo=0.5,atempo=0.6"
    for speed in (0.25, 0.3, 0.9, 1.1, 4.0, 5.0):
        factors = [float(p.split("=")[1]) for p in media_tools.atempo_chain(speed).split(",")]
        assert all(0.5 <= f <= 2.0 for f in factors)
        assert math.isclose(math.prod(factors), speed, rel_tol=1e-5)


def test_audio_speed_page_is_in_media_menu():
    media = next(c for c in TOOL_CATEGORIES if c["id"] == "media")
    assert any(t["id"] == "audio-speed" for t in media["tools"])
    resp = app.test_client().get("/media/audio-speed")
    assert resp.status_code == 200
    assert b"speed-tool" in resp.data and b"audio_speed.js" in resp.data


def test_audio_speed_requires_file():
    assert app.test_client().post("/media/audio-speed").status_code == 400


@pytest.mark.skipif(not media_tools.FFMPEG, reason="FFmpeg not installed")
def test_audio_speed_keeps_wav_format_and_shortens_duration():
    resp = app.test_client().post("/media/audio-speed", data={
        "files": (io.BytesIO(_sine_wav(2.0)), "narasi.wav"),
        "speed": "1.25",
    })
    assert resp.status_code == 200, resp.get_json()
    assert "narasi_1.25x.wav" in resp.headers["Content-Disposition"]
    with wave.open(io.BytesIO(resp.data)) as w:
        assert w.getframerate() == 24000
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert abs(w.getnframes() / w.getframerate() - 1.6) < 0.05
