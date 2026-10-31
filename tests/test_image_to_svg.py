import io
from PIL import Image
import pytest
from flask import Flask

from routes import image_tools


@pytest.fixture
def client():
    app = Flask(__name__, template_folder="../templates")
    app.config["SECRET_KEY"] = "test-secret"
    app.register_blueprint(image_tools.bp, url_prefix="/image")
    return app.test_client()


def make_sample_png():
    """Create a sample PNG with a black circle on a white background."""
    img = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
    for x in range(30, 70):
        for y in range(30, 70):
            if (x - 50) ** 2 + (y - 50) ** 2 <= 400:
                img.putpixel((x, y), (0, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_sample_color_png():
    """Create a sample 3-color PNG."""
    img = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
    for x in range(20, 80):
        for y in range(20, 80):
            img.putpixel((x, y), (255, 0, 0, 255)) # Red square
    for x in range(40, 60):
        for y in range(40, 60):
            img.putpixel((x, y), (0, 0, 255, 255)) # Blue center
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_vectorize_bw_mode():
    img_bytes = make_sample_png()
    svg_str, w, h = image_tools._vectorize_image_to_svg(
        img_bytes,
        mode="bw",
        threshold=128,
        fill_color="#1e3a8a",
        transparent_bg=True,
    )

    assert w == 100
    assert h == 100
    assert "<svg" in svg_str
    assert "</svg>" in svg_str
    assert 'fill="#1e3a8a"' in svg_str
    assert 'viewBox="0 0 100 100"' in svg_str


def test_vectorize_bw_opaque_bg():
    img_bytes = make_sample_png()
    svg_str, w, h = image_tools._vectorize_image_to_svg(
        img_bytes,
        mode="bw",
        transparent_bg=False,
    )
    assert '<rect width="100" height="100" fill="#ffffff"/>' in svg_str


def test_vectorize_color_mode():
    img_bytes = make_sample_color_png()
    svg_str, w, h = image_tools._vectorize_image_to_svg(
        img_bytes,
        mode="color",
        colors=3,
        transparent_bg=True,
    )

    assert w == 100
    assert h == 100
    assert "<svg" in svg_str
    assert "</svg>" in svg_str
    assert "<path fill=" in svg_str


def test_image_to_svg_routes(client):
    # GET page
    res_get = client.get("/image/image-to-svg")
    assert res_get.status_code == 200
    assert b"Image to SVG" in res_get.data

    # POST preview action
    png_bytes = make_sample_png()
    res_prev = client.post(
        "/image/image-to-svg",
        data={
            "file": (io.BytesIO(png_bytes), "sample.png"),
            "action": "preview",
            "mode": "bw",
            "fill_color": "#000000",
        },
        content_type="multipart/form-data",
    )
    assert res_prev.status_code == 200
    data = res_prev.get_json()
    assert data["success"] is True
    assert "<svg" in data["svg"]
    assert data["width"] == 100
    assert data["height"] == 100

    # POST download action
    res_dl = client.post(
        "/image/image-to-svg",
        data={
            "file": (io.BytesIO(png_bytes), "sample.png"),
            "action": "download",
            "mode": "bw",
        },
        content_type="multipart/form-data",
    )
    assert res_dl.status_code == 200
    assert "image/svg+xml" in res_dl.headers.get("Content-Type", "")
    assert "sample.svg" in res_dl.headers.get("Content-Disposition", "")
    assert b"<svg" in res_dl.data
