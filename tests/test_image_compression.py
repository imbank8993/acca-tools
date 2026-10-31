import io
import zipfile
from flask import Flask
from PIL import Image, ImageDraw
import pytest

from routes import acca_plus as plus


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(plus.bp, url_prefix="/acca-plus")
    return app.test_client()


def make_opaque_image(width=800, height=800, color=(100, 150, 200)):
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def make_transparent_image(width=600, height=600):
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Draw non-trivial shapes so it's not a uniform color
    for i in range(15):
        d.ellipse(
            (i * 15, i * 15, width - i * 15, height - i * 15),
            outline=(i * 15, 120, 240, 200),
            width=3,
        )
    d.ellipse((width // 4, height // 4, 3 * width // 4, 3 * height // 4), fill=(255, 100, 50, 180))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_opaque_image_compress_to_target():
    source = make_opaque_image(1200, 1200)
    target = 50 * 1024
    res_data, size, met, ext, mime = plus._compress_image_to_target(source, target)
    assert met is True
    assert size == len(res_data) <= target
    assert ext == ".jpg"
    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(res_data)) as img:
        assert img.format == "JPEG"


def test_transparent_png_preserves_alpha_transparency():
    source = make_transparent_image(800, 800)
    target = 25 * 1024
    res_data, size, met, ext, mime = plus._compress_image_to_target(source, target)
    assert met is True
    assert size == len(res_data) <= target
    assert ext in (".webp", ".png")
    assert mime in ("image/webp", "image/png")
    with Image.open(io.BytesIO(res_data)) as img:
        assert img.mode in ("RGBA", "LA")
        alpha = img.getchannel("A")
        min_alpha, max_alpha = alpha.getextrema()
        assert min_alpha == 0, "Transparent pixels must be preserved, not converted to white"
        assert max_alpha > 0


def test_already_small_transparent_png_keeps_png():
    img = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle((10, 10, 40, 40), fill=(255, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    source = buf.getvalue()

    # Target is much larger than the image
    res_data, size, met, ext, mime = plus._compress_image_to_target(source, 100 * 1024)
    assert met is True
    assert ext == ".png"
    assert mime == "image/png"
    assert res_data == source


def test_compress_image_route_single_transparent(client):
    source = make_transparent_image(500, 500)
    res = client.post(
        "/acca-plus/compress-image",
        data={
            "files": (io.BytesIO(source), "logo.png"),
            "target_size": "30",
            "target_unit": "KB",
        },
    )
    assert res.status_code == 200
    assert res.headers["X-Target-Met"] == "1"
    assert int(res.headers["X-Achieved-Size"]) <= 30 * 1024
    content_disp = res.headers.get("Content-Disposition", "")
    assert "logo_compressed" in content_disp
    # Check that downloaded file retains alpha
    downloaded = res.data
    with Image.open(io.BytesIO(downloaded)) as img:
        assert img.mode in ("RGBA", "LA")
        assert img.getchannel("A").getextrema()[0] == 0


def test_compress_image_route_batch_zip(client):
    opaque = make_opaque_image(600, 600)
    transparent = make_transparent_image(500, 500)
    res = client.post(
        "/acca-plus/compress-image",
        data={
            "files": [
                (io.BytesIO(opaque), "photo.jpg"),
                (io.BytesIO(transparent), "icon.png"),
            ],
            "target_size": "40",
            "target_unit": "KB",
        },
    )
    assert res.status_code == 200
    assert res.mimetype == "application/zip"
    assert res.headers.get("X-Is-Zip") == "1"

    with zipfile.ZipFile(io.BytesIO(res.data)) as zf:
        names = zf.namelist()
        assert "_compression_log.txt" in names
        assert any("photo_compressed" in n for n in names)
        icon_entry = next(n for n in names if "icon_compressed" in n)
        with Image.open(io.BytesIO(zf.read(icon_entry))) as icon_img:
            assert icon_img.mode in ("RGBA", "LA")
            assert icon_img.getchannel("A").getextrema()[0] == 0


def test_compress_image_invalid_input(client):
    res = client.post("/acca-plus/compress-image", data={})
    assert res.status_code == 400

    res = client.post(
        "/acca-plus/compress-image",
        data={
            "files": (io.BytesIO(make_opaque_image()), "photo.jpg"),
            "target_size": "0",
        },
    )
    assert res.status_code == 400
