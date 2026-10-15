"""Synthetic documents only: fixtures never contain student information."""

import io
import sys
from pathlib import Path

import pytest
import reportlab
from docx import Document
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def docx_bytes(paragraphs):
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def pdf_bytes(pages):
    """Produce actual PDFs with a portable font and a Unicode extraction map."""
    font_name = "SyntheticTestVera"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        font_path = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
        pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
    output = io.BytesIO()
    document = canvas.Canvas(output, pageCompression=1, invariant=1)
    for lines in pages:
        document.setFont(font_name, 12)
        for number, line in enumerate(lines):
            document.drawString(50, 790 - number * 20, line)
        document.showPage()
    document.save()
    return output.getvalue()


@pytest.fixture
def make_docx():
    return docx_bytes


@pytest.fixture
def make_pdf():
    return pdf_bytes


@pytest.fixture
def encrypted_pdf(make_pdf):
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(make_pdf([["Sentetik gizli belge"]]))))
    writer.encrypt("synthetic-test-password")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture
def application():
    from app import app

    original_config = app.config.copy()
    app.config.update(TESTING=True)
    yield app
    app.config.clear()
    app.config.update(original_config)


@pytest.fixture
def client(application):
    return application.test_client()


@pytest.fixture
def upload(client):
    def send(payload, filename="document.docx", **kwargs):
        return client.post(
            "/upload",
            data={"file": (io.BytesIO(payload), filename)},
            content_type="multipart/form-data",
            **kwargs,
        )

    return send


@pytest.fixture
def set_limits(application):
    def configure(**overrides):
        application.config["DOCUMENT_LIMITS"] = {
            **application.config["DOCUMENT_LIMITS"],
            **overrides,
        }

    return configure
