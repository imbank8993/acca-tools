import io
import zipfile
from flask import Flask
import pytest

from routes import acca_plus as plus


def make_sample_bulk_pdf():
    """Create an in-memory PDF containing documents for 3 students with varying page lengths."""
    doc = plus.fitz.open()

    # Siswa 1 (2 halaman)
    p1 = doc.new_page()
    p1.insert_text((50, 50), "RAPOR PESERTA DIDIK\nNama Peserta Didik : Ahmad Fauzi\nNISN : 0081234567\nKelas : X-1")
    p2 = doc.new_page()
    p2.insert_text((50, 50), "Lampiran Nilai dan Capaian Kompetensi Semester Ganjil\nMatematika: 90\nFisika: 85")

    # Siswa 2 (2 halaman)
    p3 = doc.new_page()
    p3.insert_text((50, 50), "RAPOR PESERTA DIDIK\nNama Peserta Didik : Siti Nurhaliza\nNISN : 0087654321\nKelas : X-1")
    p4 = doc.new_page()
    p4.insert_text((50, 50), "Lampiran Nilai dan Capaian Kompetensi Semester Ganjil\nMatematika: 96\nFisika: 92")

    # Siswa 3 (1 halaman)
    p5 = doc.new_page()
    p5.insert_text((50, 50), "KARTU PESERTA UJIAN\nNama Siswa : Muhammad Rizky\nNISN : 0089998888\nRuang : 04")

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def make_sample_teacher_bulk_pdf():
    """Create an in-memory PDF containing documents for 2 teachers with NIP and Name."""
    doc = plus.fitz.open()

    # Guru 1 (2 halaman)
    p1 = doc.new_page()
    p1.insert_text((50, 50), "SASARAN KINERJA PEGAWAI (SKP)\nNama Guru : Nurmadinah\nNIP : 197805122005012003\nJabatan : Guru Ahli Madya")
    p2 = doc.new_page()
    p2.insert_text((50, 50), "Lampiran Bukti Kinerja Guru Matematika Lanjut")

    # Guru 2 (1 halaman)
    p3 = doc.new_page()
    p3.insert_text((50, 50), "SASARAN KINERJA PEGAWAI (SKP)\nNama Guru : Syamsuriadi\nNIP : 198403152009121002\nJabatan : Guru Ahli Muda")

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def make_sample_custom_bulk_pdf():
    """Create an in-memory PDF with non-standard custom labels (No. Peserta & Mata Pelajaran)."""
    doc = plus.fitz.open()

    p1 = doc.new_page()
    p1.insert_text((50, 50), "KARTU PESERTA ASESMEN\nNo. Peserta : 24-001-012\nMata Pelajaran : Fisika Terapan")

    p2 = doc.new_page()
    p2.insert_text((50, 50), "KARTU PESERTA ASESMEN\nNo. Peserta : 24-001-013\nMata Pelajaran : Kimia Anorganik")

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


@pytest.fixture
def client():
    app = Flask(__name__, template_folder="../templates")
    app.config["SECRET_KEY"] = "test-secret"
    app.register_blueprint(plus.bp, url_prefix="/acca-plus")
    return app.test_client()


def test_auto_detect_student_segments():
    pdf_bytes = make_sample_bulk_pdf()
    doc = plus.fitz.open(stream=pdf_bytes, filetype="pdf")

    segments, warnings = plus._detect_student_segments(doc, mode="auto", profile="siswa")
    doc.close()

    assert len(segments) == 3
    # Siswa 1
    assert segments[0]["start_page"] == 0
    assert segments[0]["end_page"] == 2
    assert segments[0]["pages_count"] == 2
    assert segments[0]["nisn"] == "0081234567"
    assert "AHMAD_FAUZI" in segments[0]["filename"]

    # Siswa 2
    assert segments[1]["start_page"] == 2
    assert segments[1]["end_page"] == 4
    assert segments[1]["pages_count"] == 2
    assert segments[1]["nisn"] == "0087654321"
    assert "SITI_NURHALIZA" in segments[1]["filename"]

    # Siswa 3
    assert segments[2]["start_page"] == 4
    assert segments[2]["end_page"] == 5
    assert segments[2]["pages_count"] == 1
    assert segments[2]["nisn"] == "0089998888"
    assert "MUHAMMAD_RIZKY" in segments[2]["filename"]


def test_teacher_profile_segmentation():
    pdf_bytes = make_sample_teacher_bulk_pdf()
    doc = plus.fitz.open(stream=pdf_bytes, filetype="pdf")

    segments, warnings = plus._detect_student_segments(doc, mode="auto", profile="guru")
    doc.close()

    assert len(segments) == 2
    # Guru 1
    assert segments[0]["id_val"] == "197805122005012003"
    assert segments[0]["name_val"] == "NURMADINAH"
    assert segments[0]["pages_count"] == 2
    assert segments[0]["filename"] == "197805122005012003_NURMADINAH.pdf"

    # Guru 2
    assert segments[1]["id_val"] == "198403152009121002"
    assert segments[1]["name_val"] == "SYAMSURIADI"
    assert segments[1]["pages_count"] == 1
    assert segments[1]["filename"] == "198403152009121002_SYAMSURIADI.pdf"


def test_custom_label_segmentation():
    pdf_bytes = make_sample_custom_bulk_pdf()
    doc = plus.fitz.open(stream=pdf_bytes, filetype="pdf")

    segments, warnings = plus._detect_student_segments(
        doc,
        mode="auto",
        profile="custom",
        custom_label_id="No. Peserta",
        custom_label_name="Mata Pelajaran",
    )
    doc.close()

    assert len(segments) == 2
    assert segments[0]["id_val"] == "24-001-012"
    assert segments[0]["name_val"] == "FISIKA TERAPAN"
    assert "24-001-012" in segments[0]["filename"]
    assert "FISIKA_TERAPAN" in segments[0]["filename"]

    assert segments[1]["id_val"] == "24-001-013"
    assert segments[1]["name_val"] == "KIMIA ANORGANIK"
    assert "24-001-013" in segments[1]["filename"]


def test_fixed_pages_segmentation():
    pdf_bytes = make_sample_bulk_pdf()
    doc = plus.fitz.open(stream=pdf_bytes, filetype="pdf")

    segments, _ = plus._detect_student_segments(doc, mode="fixed", fixed_pages=1)
    doc.close()

    assert len(segments) == 5
    for i, seg in enumerate(segments):
        assert seg["pages_count"] == 1
        assert seg["start_page"] == i
        assert seg["end_page"] == i + 1


def test_naming_patterns():
    pdf_bytes = make_sample_bulk_pdf()
    doc = plus.fitz.open(stream=pdf_bytes, filetype="pdf")

    # Prefix pattern
    segments, _ = plus._detect_student_segments(
        doc,
        mode="auto",
        naming_pattern="prefix_nisn_nama",
        prefix="Rapor_X1",
    )
    doc.close()

    assert segments[0]["filename"] == "Rapor_X1_0081234567_AHMAD_FAUZI.pdf"
    assert segments[1]["filename"] == "Rapor_X1_0087654321_SITI_NURHALIZA.pdf"


def test_api_preview_endpoint(client):
    pdf_bytes = make_sample_bulk_pdf()
    res = client.post(
        "/acca-plus/split-pdf",
        data={
            "files": (io.BytesIO(pdf_bytes), "rapor_gabungan.pdf"),
            "action": "preview",
            "mode": "auto",
            "doc_profile": "siswa",
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["total_pages"] == 5
    assert data["total_students"] == 3
    assert len(data["segments"]) == 3
    assert data["segments"][0]["nisn"] == "0081234567"
    assert data["id_col_label"] == "NISN"


def test_api_split_teacher_profile(client):
    pdf_bytes = make_sample_teacher_bulk_pdf()
    res = client.post(
        "/acca-plus/split-pdf",
        data={
            "files": (io.BytesIO(pdf_bytes), "skp_guru_2026.pdf"),
            "action": "split",
            "mode": "auto",
            "doc_profile": "guru",
            "naming_pattern": "nisn_nama",
        },
        content_type="multipart/form-data",
    )

    assert res.status_code == 200
    assert res.mimetype == "application/zip"

    # Verify ZIP contents
    with zipfile.ZipFile(io.BytesIO(res.data), "r") as zf:
        namelist = zf.namelist()
        assert "_split_summary.txt" in namelist
        assert "197805122005012003_NURMADINAH.pdf" in namelist
        assert "198403152009121002_SYAMSURIADI.pdf" in namelist

        summary_text = zf.read("_split_summary.txt").decode("utf-8")
        assert "Guru / GTK / Pegawai" in summary_text
        assert "197805122005012003" in summary_text
        assert "NURMADINAH" in summary_text
