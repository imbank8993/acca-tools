import io
import json
import zipfile
from flask import Flask
import openpyxl
from PIL import Image
import pytest

from routes import acca_plus as plus


def make_sample_pdf_template():
    """Create a sample A4 landscape blank certificate PDF."""
    doc = plus.fitz.open()
    page = doc.new_page(width=842, height=595)  # A4 landscape
    # Draw simple border
    page.draw_rect(plus.fitz.Rect(20, 20, 822, 575), color=(0.1, 0.2, 0.4), width=3)
    page.insert_textbox(plus.fitz.Rect(50, 60, 792, 120), "SERTIFIKAT PENGHARGAAN", fontsize=26, fontname="hebo", align=plus.fitz.TEXT_ALIGN_CENTER)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def make_sample_image_template():
    """Create a sample PNG image template."""
    img = Image.new("RGB", (842, 595), color=(245, 247, 250))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_sample_excel_data():
    """Create sample Excel spreadsheet with recipient rows."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Nama", "Sebagai", "No_Piagam", "Predikat"])
    ws.append(["Ahmad Fauzi", "Peserta Terbaik", "001/ICG/2026", "Emas"])
    ws.append(["Siti Nurhaliza", "Juara 1 OSN Fisika", "002/ICG/2026", "Juara 1"])
    ws.append(["Muhammad Rizky", "Narasumber Workshop", "003/ICG/2026", "Pemateri"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def client():
    app = Flask(__name__, template_folder="../templates")
    app.config["SECRET_KEY"] = "test-secret"
    app.register_blueprint(plus.bp, url_prefix="/acca-plus")
    return app.test_client()


def test_parse_spreadsheet_rows():
    excel_bytes = make_sample_excel_data()
    headers, rows = plus._parse_spreadsheet_rows(excel_bytes, "peserta.xlsx")

    assert headers == ["Nama", "Sebagai", "No_Piagam", "Predikat"]
    assert len(rows) == 3
    assert rows[0]["Nama"] == "Ahmad Fauzi"
    assert rows[1]["Sebagai"] == "Juara 1 OSN Fisika"


def test_render_text_template():
    row = {"Nama": "Ahmad Fauzi", "Sebagai": "Juara 1", "No_Piagam": "001/ICG"}
    assert plus._render_text_template("{{Nama}}", row) == "Ahmad Fauzi"
    assert plus._render_text_template("Sebagai {{Sebagai}} (No: {No_Piagam})", row) == "Sebagai Juara 1 (No: 001/ICG)"


def test_hex_to_rgb():
    assert plus._hex_to_rgb("#000000") == (0.0, 0.0, 0.0)
    assert plus._hex_to_rgb("#ffffff") == (1.0, 1.0, 1.0)
    assert plus._hex_to_rgb("invalid") == (0.0, 0.0, 0.0)


def test_api_parse_data(client):
    excel_bytes = make_sample_excel_data()
    res = client.post(
        "/acca-plus/certificate-generator",
        data={
            "action": "parse_data",
            "data_file": (io.BytesIO(excel_bytes), "data.xlsx"),
        },
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert "Nama" in data["headers"]
    assert data["total_rows"] == 3


def test_api_preview(client):
    template_bytes = make_sample_pdf_template()
    excel_bytes = make_sample_excel_data()

    res = client.post(
        "/acca-plus/certificate-generator",
        data={
            "action": "preview",
            "template_file": (io.BytesIO(template_bytes), "template.pdf"),
            "data_file": (io.BytesIO(excel_bytes), "data.xlsx"),
            "fields_json": json.dumps([
                {"text": "{{Nama}}", "y": 42.0, "size": 30.0, "font": "hebo", "color": "#1a202c"},
                {"text": "Sebagai {{Sebagai}}", "y": 52.0, "size": 18.0, "font": "hebo", "color": "#333333"},
            ]),
            "qr_enabled": "1",
            "qr_content": "https://acca.icgowa.sch.id/verify/{{No_Piagam}}",
            "qr_position": "bottom-left",
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["image"].startswith("data:image/png;base64,")
    assert data["total_rows"] == 3


def test_api_generate_zip(client):
    template_bytes = make_sample_image_template()
    excel_bytes = make_sample_excel_data()

    res = client.post(
        "/acca-plus/certificate-generator",
        data={
            "action": "generate",
            "template_file": (io.BytesIO(template_bytes), "template.png"),
            "data_file": (io.BytesIO(excel_bytes), "data.xlsx"),
            "output_format": "zip",
            "fields_json": json.dumps([
                {"text": "{{Nama}}", "y": 42.0, "size": 30.0, "font": "hebo", "color": "#1a202c"},
            ]),
            "qr_enabled": "1",
            "qr_content": "{{No_Piagam}} - {{Nama}}",
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200
    assert res.mimetype == "application/zip"

    with zipfile.ZipFile(io.BytesIO(res.data), "r") as zf:
        namelist = zf.namelist()
        assert "_certificate_summary.txt" in namelist
        assert "_Semua_Piagam_Gabungan.pdf" in namelist
        assert any("Ahmad_Fauzi" in name for name in namelist)
        assert any("Siti_Nurhaliza" in name for name in namelist)

        # Check combined PDF page count
        merged_bytes = zf.read("_Semua_Piagam_Gabungan.pdf")
        with plus.fitz.open(stream=merged_bytes, filetype="pdf") as mdoc:
            assert len(mdoc) == 3


def test_api_generate_single_pdf(client):
    template_bytes = make_sample_pdf_template()
    excel_bytes = make_sample_excel_data()

    res = client.post(
        "/acca-plus/certificate-generator",
        data={
            "action": "generate",
            "template_file": (io.BytesIO(template_bytes), "template.pdf"),
            "data_file": (io.BytesIO(excel_bytes), "data.xlsx"),
            "output_format": "single_pdf",
            "fields_json": json.dumps([
                {"text": "{{Nama}}", "y": 42.0, "size": 30.0, "font": "hebo", "color": "#1a202c"},
            ]),
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200
    assert res.mimetype == "application/pdf"

    with plus.fitz.open(stream=res.data, filetype="pdf") as doc:
        assert len(doc) == 3
        # Check text in first page
        assert "Ahmad Fauzi" in doc[0].get_text()
        assert "Siti Nurhaliza" in doc[1].get_text()
