import io

from app import app
from routes import plus_media


def test_plus_media_pages_and_upload_validation():
    client = app.test_client()
    assert client.get("/acca-plus/voice-recorder").status_code == 200
    assert client.get("/acca-plus/html-to-video").status_code == 200
    assert client.get("/acca-plus/voice-clone").status_code == 404
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


def test_html_video_auto_size_and_one_minute_validation(monkeypatch):
    assert plus_media._fit_video_size(1080, 1920) == (1080, 1920)
    assert plus_media._fit_video_size(2160, 3840) == (1080, 1920)
    assert plus_media._fit_video_size(None, None) == (1280, 720)

    class TimelinePage:
        def evaluate(self, _):
            return {"duration": None, "fps": None, "seek": True}

    example = "const DUR=41; const stream=canvas.captureStream(30); window.__seek=t=>draw(t);"
    assert plus_media._html_video_timing(TimelinePage(), example) == (41, 30, True)

    client = app.test_client()
    monkeypatch.setattr(plus_media.shutil, "which", lambda _: None)
    minute = client.post("/acca-plus/html-to-video", data={
        "file": (io.BytesIO(b"<html></html>"), "page.html"),
        "duration": "60", "fps": "15", "size": "auto",
    })
    assert minute.status_code == 503  # Valid settings; renderer is intentionally unavailable.

    automatic = client.post("/acca-plus/html-to-video", data={
        "file": (io.BytesIO(b"<html></html>"), "page.html"),
        "duration": "auto", "fps": "auto", "size": "auto",
    })
    assert automatic.status_code == 503

    over_limit = client.post("/acca-plus/html-to-video", data={
        "file": (io.BytesIO(b"<html></html>"), "page.html"),
        "duration": "61", "fps": "15", "size": "auto",
    })
    assert over_limit.status_code == 400
