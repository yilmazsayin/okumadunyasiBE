"""Document parsing in disposable Linux workers with CPU, memory and time limits.

The forkserver preloads parsers once, avoiding interpreter/import startup per upload
without forking a multithreaded web worker. No URLs or embedded actions are opened.
"""

import io
import logging
import multiprocessing
import re
import resource
import signal
from time import perf_counter
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile

import docx
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.exceptions import InvalidXmlError
from lxml.etree import XMLSyntaxError
from pypdf import PdfReader
from pypdf.errors import PyPdfError

DEFAULT_LIMITS = {
    "pdf_pages": 100,
    "text_chars": 1_000_000,
    "pdf_stream_bytes": 8 * 1024 * 1024,
    "docx_bytes": 32 * 1024 * 1024,
    "docx_entries": 1000,
    "docx_ratio": 200,
}
WORKER_MEMORY_BYTES = 256 * 1024 * 1024
WORKER_CPU_SECONDS = 5
RESOURCE_MESSAGE = "Belge güvenli işlem sınırını aşıyor. Lütfen daha küçük veya daha sade bir belge yükleyin."


class DocumentError(Exception):
    def __init__(self, message, status):
        super().__init__(message)
        self.message = message
        self.status = status


def _validate_docx(data, limits):
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > limits["docx_entries"]:
                raise DocumentError(RESOURCE_MESSAGE, 413)
            names = set()
            total = 0
            for member in members:
                name = member.filename
                if (
                    name in names
                    or name.startswith(("/", "\\"))
                    or ".." in name.replace("\\", "/").split("/")
                    or member.flag_bits & 1
                    or member.compress_type not in {ZIP_STORED, ZIP_DEFLATED}
                ):
                    raise DocumentError("Geçersiz veya bozuk DOCX dosyası.", 400)
                names.add(name)
                total += member.file_size
                if (
                    total > limits["docx_bytes"]
                    or member.file_size / max(member.compress_size, 1)
                    > limits["docx_ratio"]
                ):
                    raise DocumentError(RESOURCE_MESSAGE, 413)
            if not {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= names:
                raise DocumentError("Geçersiz veya bozuk DOCX dosyası.", 400)
            # Verify actual expansion and CRCs in bounded chunks, not just metadata.
            expanded = 0
            for member in members:
                with archive.open(member) as entry:
                    while chunk := entry.read(64 * 1024):
                        expanded += len(chunk)
                        if expanded > limits["docx_bytes"]:
                            raise DocumentError(RESOURCE_MESSAGE, 413)
    except (BadZipFile, NotImplementedError, RuntimeError, EOFError) as exc:
        raise DocumentError("Geçersiz veya bozuk DOCX dosyası.", 400) from exc


def _extract_docx(data, limits):
    document = docx.Document(io.BytesIO(data))
    paragraphs = []
    length = 0
    # Preserve the original DOCX paragraph/newline contract, including empty lines.
    for paragraph in document.paragraphs:
        text = paragraph.text
        length += len(text) + (1 if paragraphs else 0)
        if length > limits["text_chars"]:
            raise DocumentError(RESOURCE_MESSAGE, 413)
        paragraphs.append(text)
    text = "\n".join(paragraphs)
    if not text.strip():
        raise DocumentError("DOCX dosyasında okunabilir metin bulunamadı.", 422)
    return text


def _pdf_text(raw):
    # Keep paragraph boundaries; flatten visual line wrapping within paragraphs.
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    raw = raw.replace("\x00", "").replace("\u00ad", "")
    paragraphs = re.split(r"\n[ \t]*\n+", raw)
    return "\n\n".join(
        cleaned
        for paragraph in paragraphs
        if (cleaned := re.sub(r"\s+", " ", paragraph).strip())
    )


def _validate_pdf(data, limits):
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted:
        raise DocumentError(
            "Şifreli PDF dosyaları desteklenmiyor. Lütfen şifresiz bir PDF yükleyin.",
            422,
        )
    if len(reader.pages) > limits["pdf_pages"]:
        raise DocumentError(f"PDF en fazla {limits['pdf_pages']} sayfa olabilir.", 413)
    return reader


def _extract_pdf(reader, limits):
    pages = []
    length = 0
    for page in reader.pages:
        contents = page.get_contents()
        # Decompression itself is protected by OS memory/CPU and wall limits.
        if (
            contents is not None
            and len(contents.get_data()) > limits["pdf_stream_bytes"]
        ):
            raise DocumentError(RESOURCE_MESSAGE, 413)
        raw = page.extract_text() or ""
        # Extraction buffers remain OS-bounded; allow whitespace cleanup at the
        # exact output limit without permitting arbitrarily large raw strings.
        if len(raw) > max(limits["text_chars"] * 4, limits["text_chars"] + 1024):
            raise DocumentError(RESOURCE_MESSAGE, 413)
        text = _pdf_text(raw)
        if not text:
            continue
        length += len(text) + (2 if pages else 0)
        if length > limits["text_chars"]:
            raise DocumentError(RESOURCE_MESSAGE, 413)
        pages.append(text)
    text = "\n\n".join(pages)
    if not text.strip():
        raise DocumentError(
            "PDF dosyasında seçilebilir metin bulunamadı. Taranmış belgeler için OCR desteği henüz yok.",
            422,
        )
    return text


def _send_result(sender, result):
    try:
        sender.send(result)
    except (BrokenPipeError, EOFError):
        # The request may have timed out/disconnected; never dump a traceback.
        pass


def _worker(data, extension, limits, sender):
    try:
        resource.setrlimit(
            resource.RLIMIT_AS, (WORKER_MEMORY_BYTES, WORKER_MEMORY_BYTES)
        )
        resource.setrlimit(
            resource.RLIMIT_CPU, (WORKER_CPU_SECONDS, WORKER_CPU_SECONDS)
        )
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        # Parser diagnostics may include text fragments; never emit them.
        logging.disable(logging.CRITICAL)
        started = perf_counter()
        if extension == ".docx":
            _validate_docx(data, limits)
            validated = perf_counter()
            text = _extract_docx(data, limits)
        else:
            reader = _validate_pdf(data, limits)
            validated = perf_counter()
            text = _extract_pdf(reader, limits)
        _send_result(
            sender,
            (
                "ok",
                text,
                {
                    "validation": (validated - started) * 1000,
                    "extraction": (perf_counter() - validated) * 1000,
                },
            ),
        )
    except DocumentError as exc:
        _send_result(sender, ("error", exc.message, exc.status))
    except (MemoryError, RecursionError):
        _send_result(sender, ("error", RESOURCE_MESSAGE, 413))
    except (
        PyPdfError,
        BadZipFile,
        ValueError,
        KeyError,
        TypeError,
        OSError,
        EOFError,
        PackageNotFoundError,
        InvalidXmlError,
        XMLSyntaxError,
    ):
        label = "PDF" if extension == ".pdf" else "DOCX"
        _send_result(sender, ("error", f"Geçersiz veya bozuk {label} dosyası.", 400))
    except Exception:  # noqa: BLE001 - untrusted parser boundary; return safe JSON
        # Never disclose exception messages or document data across the boundary.
        _send_result(
            sender, ("error", "Belge işlenirken beklenmeyen bir hata oluştu.", 500)
        )
    finally:
        sender.close()


def extract_document(data, extension, limits, timeout):
    # Linux/Render: initialize lazily in the web worker, never in Gunicorn master.
    context = multiprocessing.get_context("forkserver")
    # Preserve Python's default main-module preload alongside the parsers.
    context.set_forkserver_preload(["__main__", "document_parser"])
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(data, extension, limits, sender))
    try:
        process.start()
        sender.close()
        if not receiver.poll(timeout):
            raise DocumentError(
                "Belge işleme süresi aşıldı. Lütfen daha küçük bir belge yükleyin.", 422
            )
        try:
            result = (
                receiver.recv()
            )  # Receive before join: large results fill the pipe.
        except EOFError as exc:
            process.join(timeout=0.1)
            if process.exitcode in {-signal.SIGKILL, -signal.SIGXCPU}:
                raise DocumentError(RESOURCE_MESSAGE, 413) from exc
            raise DocumentError(
                "Belge işlenemedi. Lütfen daha küçük veya geçerli bir belge yükleyin.",
                422,
            ) from exc
        if result[0] == "error":
            raise DocumentError(result[1], result[2])
        return result[1], result[2]
    finally:
        if process.pid is not None:
            process.join(timeout=0.1)
            if process.is_alive():
                process.kill()
                process.join()
            process.close()
        receiver.close()
        sender.close()
