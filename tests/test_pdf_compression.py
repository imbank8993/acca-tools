import io
import time

from flask import Flask
import pytest

from routes import acca_plus as plus
from utils import pdf_jobs


def sample_pdf():
    with plus.fitz.open() as doc:
        for index in range(3):
            page = doc.new_page()
            for row in range(40):
                page.insert_text((40, 40 + row * 16), f"Rekap Nilai Kelas {index} - Siswa {row}: 95")
        return doc.tobytes()


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(plus.bp, url_prefix="/acca-plus")
    return app.test_client()


def post(client, data, **fields):
    return client.post("/acca-plus/compress-pdf", data={
        "files": (io.BytesIO(data), "rekap.pdf"), "target_size": "500",
        **fields,
    })


def test_lossless_preserves_pixels_and_text():
    source = sample_pdf()
    result, size, met = plus._compress_to_target(source, 1, lossless=True)
    assert size == len(result) <= len(source)
    assert met is False
    with plus.fitz.open(stream=source, filetype="pdf") as before, plus.fitz.open(stream=result, filetype="pdf") as after:
        assert len(before) == len(after)
        for a, b in zip(before, after):
            assert a.get_text() == b.get_text()
            assert a.get_pixmap().samples == b.get_pixmap().samples


def test_legacy_render_flag_never_rasterizes_text(monkeypatch):
    source = sample_pdf()
    def forbid_render(*args, **kwargs):
        pytest.fail("Text and tables must never be rendered to images")
    monkeypatch.setattr(plus.fitz.Page, "get_pixmap", forbid_render)
    result, size, met = plus._compress_to_target(source, 1, force_render=True)
    assert not met and size == len(result)
    with plus.fitz.open(stream=source, filetype="pdf") as before, plus.fitz.open(stream=result, filetype="pdf") as after:
        assert len(after) == 3
        for a, b in zip(before, after):
            assert a.get_text("words") == b.get_text("words")
            assert b.get_images() == []


def test_impossible_target_is_reported():
    result, size, met = plus._compress_to_target(sample_pdf(), 1, force_render=True)
    assert not met and size == len(result) > 1


def test_image_pdf_reaches_500kb_while_retaining_text():
    import random
    from PIL import Image
    image = Image.frombytes("RGB", (1000, 1000), random.Random(7).randbytes(3000000))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    with plus.fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 40), "Rekap nilai: 95")
        page.insert_image(plus.fitz.Rect(40, 80, 540, 580), stream=stream.getvalue())
        source = doc.tobytes()
    result, size, met = plus._compress_to_target(source, 500 * 1024)
    assert met and size == len(result) <= 500 * 1024
    with plus.fitz.open(stream=result, filetype="pdf") as doc:
        assert "Rekap nilai: 95" in doc[0].get_text()


def test_106_pages_with_shared_transparent_logo_reach_500kb():
    import random
    from PIL import Image, ImageDraw
    logo = Image.frombytes("RGB", (600, 600), random.Random(12).randbytes(1080000))
    alpha = Image.new("L", logo.size, 0)
    ImageDraw.Draw(alpha).ellipse((10, 10, 590, 590), fill=255)
    logo.putalpha(alpha)
    stream = io.BytesIO()
    logo.save(stream, format="PNG")
    with plus.fitz.open() as doc:
        xref = 0
        for index in range(106):
            page = doc.new_page()
            xref = page.insert_image(plus.fitz.Rect(40, 40, 100, 100),
                                     stream=stream.getvalue() if not xref else None, xref=xref)
            for row in range(30):
                page.insert_text((40, 140 + row * 16), f"Kelas {index}, siswa {row}, nilai 95")
            page.draw_rect(plus.fitz.Rect(35, 120, 500, 640))
            # ReportLab exports a single content stream per page. Match that
            # structure instead of making one independent stream per text row.
            page.clean_contents()
        source = doc.tobytes()
    assert len(source) > 500 * 1024
    result, size, met = plus._compress_to_target(source, 500 * 1024)
    assert met and size == len(result) <= 500 * 1024
    with plus.fitz.open(stream=source, filetype="pdf") as before, plus.fitz.open(stream=result, filetype="pdf") as after:
        assert len(after) == 106
        for a, b in zip(before, after):
            assert a.get_text("words") == b.get_text("words")
            assert a.get_drawings() == b.get_drawings()
            # replace_image can leave multiple resource names for one image.
            images = {info[0]: info for info in b.get_images()}
            assert len(images) == 1 and next(iter(images.values()))[1] > 0
        assert len({p.get_images()[0][0] for p in after}) == 1  # logo stays shared


def test_default_route_uses_smart_mode(client, monkeypatch):
    seen = {}
    def compress(data, target_bytes, **options):
        seen.update(options)
        return data, len(data), True
    monkeypatch.setattr(plus, "_compress_to_target", compress)
    assert post(client, sample_pdf()).status_code == 200
    assert seen["lossless"] is False


def test_busy_job_limit_and_failed_spawn_release_slot(monkeypatch, tmp_path):
    monkeypatch.setattr(pdf_jobs, "ROOT", tmp_path)
    for index in range(2):
        (tmp_path / f"slot-{index}").mkdir()
    with pytest.raises(RuntimeError, match="busy"):
        pdf_jobs.start_job(b"pdf", 1000, "lossless", "test.pdf")
    (tmp_path / "slot-1").rmdir()

    def fail(*args, **kwargs):
        raise OSError("cannot start process")

    monkeypatch.setattr(pdf_jobs.subprocess, "Popen", fail)
    with pytest.raises(OSError):
        pdf_jobs.start_job(b"pdf", 1000, "lossless", "test.pdf")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["slot-0"]


@pytest.mark.parametrize("fields", [
    {"target_size": "nan"}, {"target_size": "inf"}, {"target_size": "0"},
    {"target_unit": "GB"}, {"mode": "unknown"},
])
def test_invalid_input(client, fields):
    assert post(client, sample_pdf(), **fields).status_code == 400


def test_async_returns_before_compression(client, monkeypatch):
    monkeypatch.setattr(pdf_jobs, "start_job", lambda *args: "a" * 32)
    monkeypatch.setattr(plus, "_compress_to_target", lambda *a, **kw: pytest.fail("must not compress in request"))
    response = post(client, sample_pdf(), **{"async": "1", "mode": "lossless"})
    assert response.status_code == 202
    assert response.json["status_url"].endswith("a" * 32)


def test_job_poll_download_and_expiry(client, monkeypatch, tmp_path):
    monkeypatch.setattr(pdf_jobs, "ROOT", tmp_path)
    token = "b" * 32
    path = tmp_path / token
    path.mkdir()
    source = sample_pdf()
    (path / "output.pdf").write_bytes(source)
    pdf_jobs._write(path / "settings.json", dict(original=len(source), target=512000, filename="rekap_compressed.pdf"))
    pdf_jobs._write(path / "status.json", dict(state="processing"))
    url = f"/acca-plus/compress-pdf/jobs/{token}"
    assert client.get(url).json["state"] == "processing"
    assert client.get(url + "/download").status_code == 409
    pdf_jobs._write(path / "status.json", dict(state="done", achieved=len(source), target_met=True))
    assert client.get(url).json["download_url"] == url + "/download"
    response = client.get(url + "/download")
    assert response.data == source
    assert response.headers["X-Achieved-Size"] == str(len(source))
    response.close()
    monkeypatch.setattr(pdf_jobs, "TTL", -1)
    assert client.get(url).status_code == 404


def test_job_process_end_to_end():
    # A real subprocess uses the module's default shared directory.
    token = pdf_jobs.start_job(sample_pdf(), 512000, "lossless", "rekap.pdf")
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = pdf_jobs.status(token)
            if result["state"] != "processing":
                break
            time.sleep(0.1)
        assert result["state"] == "done", result
        assert result["target_met"]
        with plus.fitz.open(pdf_jobs.job_path(token) / "output.pdf") as doc:
            assert len(doc) == 3
    finally:
        import shutil
        path = pdf_jobs.job_path(token)
        if path:
            # Let the supervisor finish releasing files after status is written.
            time.sleep(0.3)
            shutil.rmtree(path)
