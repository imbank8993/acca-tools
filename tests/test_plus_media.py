import io

from app import app


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
