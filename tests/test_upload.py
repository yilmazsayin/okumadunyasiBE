"""Public API, compatibility, validation, and document resource budgets."""

import io
import logging
import multiprocessing
import time
import zipfile

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.actions import JavaScript

MIB = 1024 * 1024


def _synthetic_slow_worker(*args):
    """Importable multiprocessing target for deterministic timeout coverage."""
    time.sleep(2)


def assert_error(response, status):
    assert response.status_code == status
    assert response.is_json
    body = response.get_json()
    assert set(body) == {"error"}
    assert isinstance(body["error"], str)
    assert body["error"].strip()
    assert "Traceback" not in body["error"]
    assert "site-packages" not in body["error"]
    return body["error"]


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_docx_preserves_existing_response_and_paragraph_order(upload, make_docx):
    paragraphs = ["Birinci paragraf", "", "İkinci paragraf"]
    response = upload(make_docx(paragraphs))
    assert response.status_code == 200
    assert response.get_json() == {"text": "\n".join(paragraphs)}


def test_docx_preserves_turkish_characters(upload, make_docx):
    text = "Çığ, şüphe, öğrenci, Iğdır, İstanbul: ı İ ş Ş ğ Ğ ü Ü ö Ö ç Ç."
    response = upload(make_docx([text]))
    assert response.status_code == 200
    assert response.get_json() == {"text": text}


@pytest.mark.parametrize("filename", ["document.DOCX", "DOCUMENT.DoCx"])
def test_docx_extension_is_case_insensitive(upload, make_docx, filename):
    response = upload(make_docx(["Öğrenci okuyor."]), filename)
    assert response.status_code == 200
    assert response.get_json() == {"text": "Öğrenci okuyor."}


def test_pdf_returns_the_same_success_contract(upload, make_pdf):
    response = upload(make_pdf([["Bir kitap okudum."]]), "document.pdf")
    assert response.status_code == 200
    assert response.get_json() == {"text": "Bir kitap okudum."}


def test_pdf_preserves_turkish_characters(upload, make_pdf):
    text = "Çığ Şırnak Iğdır İstanbul öğrenci: ı İ ş Ş ğ Ğ ü Ü ö Ö ç Ç."
    response = upload(make_pdf([[text]]), "turkce.pdf")
    assert response.status_code == 200
    assert response.get_json() == {"text": text}


@pytest.mark.parametrize("filename", ["document.PDF", "DOCUMENT.PdF"])
def test_pdf_extension_is_case_insensitive(upload, make_pdf, filename):
    response = upload(make_pdf([["Bir kitap."]]), filename)
    assert response.status_code == 200
    assert response.get_json() == {"text": "Bir kitap."}


def test_pdf_page_boundaries_keep_word_order_and_separation(upload, make_pdf):
    response = upload(
        make_pdf([["Birinci sayfanın sonu"], ["İkinci sayfanın başı"]]),
        "multipage.pdf",
    )
    assert response.status_code == 200
    text = response.get_json()["text"]
    assert text.split() == ["Birinci", "sayfanın", "sonu", "İkinci", "sayfanın", "başı"]
    assert "sonuİkinci" not in text
    assert "\n" in text
    assert text == text.strip()
    assert "\n\n\n" not in text


def test_pdf_normalizes_excess_horizontal_whitespace(upload, make_pdf):
    response = upload(make_pdf([["Bir    kitap     okuyorum."]]), "spacing.pdf")
    assert response.status_code == 200
    assert response.get_json() == {"text": "Bir kitap okuyorum."}


def test_missing_file_returns_json_error(client):
    assert_error(client.post("/upload"), 400)


def test_two_files_in_the_same_field_are_rejected(client, make_docx):
    payload = make_docx(["Sentetik belge"])
    response = client.post(
        "/upload",
        data={
            "file": [
                (io.BytesIO(payload), "one.docx"),
                (io.BytesIO(payload), "two.docx"),
            ]
        },
    )
    assert_error(response, 400)


def test_extra_file_fields_are_rejected(client, make_docx):
    payload = make_docx(["Sentetik belge"])
    response = client.post(
        "/upload",
        data={
            "file": (io.BytesIO(payload), "one.docx"),
            "extra": (io.BytesIO(payload), "two.docx"),
        },
    )
    assert_error(response, 400)


def test_wrong_multipart_field_returns_json_error(client, make_docx):
    response = client.post(
        "/upload", data={"document": (io.BytesIO(make_docx(["Metin"])), "file.docx")}
    )
    assert_error(response, 400)


def test_empty_filename_returns_json_error(upload, make_docx):
    assert_error(upload(make_docx(["Metin"]), ""), 400)


@pytest.mark.parametrize("extension", ["txt", "doc", "exe", "docx.exe", ""])
def test_unsupported_extension(upload, extension):
    assert_error(upload(b"Sentetik metin", f"document.{extension}"), 400)


@pytest.mark.parametrize("filename", ["empty.docx", "empty.pdf"])
def test_empty_files(upload, filename):
    assert_error(upload(b"", filename), 400)


@pytest.mark.parametrize("filename", ["pretend.docx", "pretend.pdf"])
def test_extension_without_matching_content_is_rejected(upload, filename):
    assert_error(
        upload(b"This is plain text, not the claimed document.", filename), 400
    )


def test_pdf_cannot_be_disguised_as_docx(upload, make_pdf):
    assert_error(upload(make_pdf([["Sentetik belge"]]), "pretend.docx"), 400)


def test_docx_cannot_be_disguised_as_pdf(upload, make_docx):
    assert_error(upload(make_docx(["Sentetik belge"]), "pretend.pdf"), 400)


def test_arbitrary_zip_is_not_docx(upload):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("readme.txt", "Bir ZIP dosyası Word belgesi değildir.")
    assert_error(upload(output.getvalue(), "archive.docx"), 400)


def test_corrupt_docx_is_reported_without_parser_internals(upload, make_docx):
    payload = make_docx(["Sentetik belge"])
    assert_error(upload(payload[:300], "broken.docx"), 400)


@pytest.mark.parametrize("payload", [b"%PDF-1.7\nnot a real PDF\n%%EOF", b"%PDF-1.4\n"])
def test_corrupt_pdf_is_reported_without_parser_internals(upload, payload):
    assert_error(upload(payload, "broken.pdf"), 400)


def test_pdf_with_password_is_rejected(upload, encrypted_pdf):
    message = assert_error(upload(encrypted_pdf, "encrypted.pdf"), 422)
    assert "şifre" in message.lower()


def test_pdf_with_empty_password_is_still_rejected(upload, make_pdf):
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(make_pdf([["Sentetik belge"]]))))
    writer.encrypt("")
    output = io.BytesIO()
    writer.write(output)
    message = assert_error(upload(output.getvalue(), "encrypted.pdf"), 422)
    assert "şifre" in message.lower()


@pytest.mark.parametrize("pages", [[[]], [["   "]], [[], []]])
def test_pdf_without_selectable_text_returns_ocr_guidance(upload, make_pdf, pages):
    message = assert_error(upload(make_pdf(pages), "blank.pdf"), 422)
    assert "metin" in message.lower()


def test_docx_without_text_has_useful_error(upload, make_docx):
    message = assert_error(upload(make_docx(["", "   "])), 422)
    assert "metin" in message.lower()


def test_image_only_pdf_has_useful_error(upload):
    # A one-pixel image, synthesized in memory without PIL or external files.
    import struct
    import zlib

    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
    png += chunk(b"IEND", b"")
    output = io.BytesIO()
    document = canvas.Canvas(output)
    document.drawImage(ImageReader(io.BytesIO(png)), 30, 30, width=100, height=100)
    document.showPage()
    document.save()
    message = assert_error(upload(output.getvalue(), "scan.pdf"), 422)
    assert "metin" in message.lower()


def test_file_larger_than_ten_mib_is_rejected(upload):
    assert_error(upload(b"%PDF-1.7\n" + b"x" * (10 * MIB), "large.pdf"), 413)


def test_exact_ten_mib_file_is_accepted_with_multipart_overhead(upload, make_pdf):
    document = make_pdf([["Tam sınırdaki sentetik belge."]])
    marker = document.rfind(b"startxref")
    padding_size = 10 * MIB - len(document)
    # A PDF comment before startxref preserves the original object/xref offsets.
    padding = b"\n%" + b"x" * (padding_size - 3) + b"\n"
    document = document[:marker] + padding + document[marker:]
    assert len(document) == 10 * MIB
    response = upload(document, "exact-limit.pdf")
    assert response.status_code == 200
    assert response.get_json() == {"text": "Tam sınırdaki sentetik belge."}


def test_request_body_cap_returns_json_error(upload):
    assert_error(upload(b"x" * (12 * MIB), "huge.pdf"), 413)


def test_multipart_field_memory_budget_is_separate_from_file_limit(
    application, client, make_docx
):
    response = client.post(
        "/upload",
        data={
            "file": (io.BytesIO(make_docx(["Sentetik belge"])), "small.docx"),
            "unnecessary-field": "x"
            * (application.config["MAX_FORM_MEMORY_SIZE"] + 1024),
        },
    )
    assert_error(response, 413)


def test_pdf_page_limit(upload, make_pdf, set_limits):
    set_limits(pdf_pages=1)
    assert_error(upload(make_pdf([["İlk sayfa"], ["İkinci sayfa"]]), "pages.pdf"), 413)


def test_pdf_page_limit_allows_exact_boundary(upload, make_pdf, set_limits):
    set_limits(pdf_pages=2)
    response = upload(make_pdf([["İlk"], ["İkinci"]]), "pages.pdf")
    assert response.status_code == 200
    assert response.get_json()["text"].split() == ["İlk", "İkinci"]


@pytest.mark.parametrize("extension", ["docx", "pdf"])
def test_extracted_text_limit(upload, make_docx, make_pdf, set_limits, extension):
    set_limits(text_chars=20)
    text = "Kaynak sınırını aşan sentetik örnek metindir."
    payload = make_docx([text]) if extension == "docx" else make_pdf([[text]])
    assert_error(upload(payload, f"text-limit.{extension}"), 413)


@pytest.mark.parametrize("extension", ["docx", "pdf"])
def test_text_limit_is_total_across_paragraphs_and_pages(
    upload, make_docx, make_pdf, set_limits, extension
):
    set_limits(text_chars=11)
    payload = (
        make_docx(["Birinci", "İkinci"])
        if extension == "docx"
        else make_pdf([["Birinci"], ["İkinci"]])
    )
    assert_error(upload(payload, f"total-text-limit.{extension}"), 413)


@pytest.mark.parametrize("extension", ["docx", "pdf"])
def test_text_limit_allows_exact_boundary(
    upload, make_docx, make_pdf, set_limits, extension
):
    text = "Tam sınır"
    set_limits(text_chars=len(text))
    payload = make_docx([text]) if extension == "docx" else make_pdf([[text]])
    response = upload(payload, f"text-boundary.{extension}")
    assert response.status_code == 200
    assert response.get_json() == {"text": text}


def test_pdf_decompressed_page_stream_budget(upload, make_pdf, set_limits):
    set_limits(pdf_stream_bytes=64)
    assert_error(upload(make_pdf([["Bir metin " * 50]]), "stream-budget.pdf"), 413)


def test_docx_archive_entry_limit(upload, make_docx, set_limits):
    set_limits(docx_entries=1)
    assert_error(upload(make_docx(["Bir cümle."])), 413)


def test_docx_archive_expanded_size_limit(upload, make_docx, set_limits):
    set_limits(docx_bytes=1024)
    assert_error(upload(make_docx(["Bir cümle."])), 413)


def test_docx_zip_bomb_is_rejected(upload, make_docx):
    output = io.BytesIO(make_docx(["Normal belge metni."]))
    with zipfile.ZipFile(output, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("synthetic-padding.bin", b"A" * MIB)
    assert_error(upload(output.getvalue(), "compressed-bomb.docx"), 413)


@pytest.mark.parametrize(
    "member_name", ["../outside.txt", "/outside.txt", "..\\outside.txt"]
)
def test_docx_with_unsafe_archive_paths_is_rejected(upload, make_docx, member_name):
    output = io.BytesIO(make_docx(["Normal belge metni."]))
    with zipfile.ZipFile(output, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member_name, b"Synthetic padding")
    assert_error(upload(output.getvalue(), "unsafe-path.docx"), 400)


def test_docx_duplicate_zip_members_are_rejected(upload, make_docx):
    output = io.BytesIO(make_docx(["Normal belge metni."]))
    with (
        zipfile.ZipFile(output, "a") as archive,
        pytest.warns(UserWarning, match="Duplicate name"),
    ):
        archive.writestr("word/document.xml", b"<not-a-real-document/>")
    assert_error(upload(output.getvalue(), "duplicate.docx"), 400)


def test_docx_crc_mismatch_is_rejected(upload, make_docx):
    original = io.BytesIO(make_docx(["Normal belge metni."]))
    output = io.BytesIO()
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(output, "w") as target:
        for name in source.namelist():
            target.writestr(name, source.read(name), compress_type=zipfile.ZIP_STORED)
        target.writestr("synthetic-sentinel.txt", b"SYNTHETIC_CRC_CHECK_87654")
    payload = output.getvalue().replace(
        b"SYNTHETIC_CRC_CHECK_87654", b"SYNTHETIC_CRC_CHECK_87655"
    )
    assert_error(upload(payload, "bad-crc.docx"), 400)


def test_docx_xml_external_entities_are_not_expanded(upload, make_docx, tmp_path):
    secret = tmp_path / "synthetic-secret.txt"
    secret.write_text("SYNTHETIC_ENTITY_SECRET_87654", encoding="utf-8")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE w:document [<!ENTITY external SYSTEM "'
        + secret.as_uri()
        + '">]><w:document '
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>&external;</w:t></w:r></w:p></w:body></w:document>"
    ).encode()
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(make_docx(["Normal metin"]))) as source,
        zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target,
    ):
        for name in source.namelist():
            target.writestr(
                name, xml if name == "word/document.xml" else source.read(name)
            )
    response = upload(output.getvalue(), "external-entity.docx")
    assert response.status_code in {400, 422}
    assert_error(response, response.status_code)
    assert "SYNTHETIC_ENTITY_SECRET" not in response.get_data(as_text=True)


def test_pdf_scripts_and_attachments_are_not_executed_or_returned(upload, make_pdf):
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(make_pdf([["Görünen kitap metni."]]))))
    writer.add_open_action(JavaScript("app.alert('SYNTHETIC_SCRIPT_SENTINEL');"))
    writer.add_attachment("synthetic-hidden.txt", b"SYNTHETIC_ATTACHMENT_SENTINEL")
    output = io.BytesIO()
    writer.write(output)
    response = upload(output.getvalue(), "active-content.pdf")
    assert response.status_code == 200
    assert response.get_json() == {"text": "Görünen kitap metni."}


def test_successful_upload_does_not_log_document_or_filename(upload, make_docx, caplog):
    caplog.set_level(logging.INFO)
    text = "SYNTHETIC_PRIVATE_DOCUMENT_CONTENT_87654"
    filename = "SYNTHETIC_PRIVATE_FILENAME_87654.docx"
    response = upload(make_docx([text]), filename)
    assert response.status_code == 200
    assert text not in caplog.text
    assert filename not in caplog.text


def test_failed_upload_does_not_log_filename_or_payload(upload, caplog):
    caplog.set_level(logging.INFO)
    payload = b"%PDF-1.7\nSYNTHETIC_PRIVATE_MALFORMED_CONTENT_87654"
    filename = "SYNTHETIC_PRIVATE_BAD_FILENAME_87654.pdf"
    assert_error(upload(payload, filename), 400)
    assert "SYNTHETIC_PRIVATE" not in caplog.text


def test_unexpected_failure_hides_exception_details(
    upload, make_docx, monkeypatch, caplog
):
    import app as module

    def fail(*args, **kwargs):
        raise RuntimeError("SYNTHETIC_PRIVATE_EXCEPTION_87654")

    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(module, "extract_document", fail)
    response = upload(make_docx(["Sentetik belge"]))
    message = assert_error(response, 500)
    assert "SYNTHETIC_PRIVATE" not in message
    assert "SYNTHETIC_PRIVATE" not in caplog.text


def test_parser_timeout_is_bounded_and_worker_is_cleaned_up(
    application, upload, make_docx, monkeypatch
):
    import document_parser

    existing_children = {child.pid for child in multiprocessing.active_children()}
    monkeypatch.setattr(document_parser, "_worker", _synthetic_slow_worker)
    application.config["PARSER_TIMEOUT_SECONDS"] = 0.02
    started = time.perf_counter()
    message = assert_error(upload(make_docx(["Sentetik belge"])), 422)
    assert "süre" in message.lower()
    assert time.perf_counter() - started < 1.5
    assert {
        child.pid for child in multiprocessing.active_children()
    } == existing_children


def test_upload_exposes_separate_server_timings(upload, make_docx):
    response = upload(make_docx(["Sentetik belge"]))
    assert response.status_code == 200
    timings = {}
    for value in response.headers["Server-Timing"].split(", "):
        stage, duration = value.split(";dur=")
        timings[stage] = float(duration)
    assert set(timings) == {
        "receive",
        "validation",
        "extraction",
        "worker",
        "json",
        "total",
    }
    assert all(value >= 0 for value in timings.values())
    assert timings["total"] >= timings["extraction"]


def test_document_responses_disable_caching_and_mime_sniffing(upload, make_docx):
    response = upload(make_docx(["Sentetik belge"]))
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_upload_does_not_write_document_to_working_directory(
    upload, make_docx, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    response = upload(make_docx(["Sentetik belge"]), "local-file.docx")
    assert response.status_code == 200
    assert list(tmp_path.iterdir()) == []
