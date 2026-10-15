"""Okuma Dünyası's compatible, bounded document upload API."""

import logging
import os
import re
from pathlib import PurePath
from time import perf_counter
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, request
from flask_cors import CORS
from werkzeug.exceptions import BadRequest, HTTPException, RequestEntityTooLarge

from document_parser import DEFAULT_LIMITS, DocumentError, extract_document

MAX_FILE_BYTES = 10 * 1024 * 1024
# Multipart headers/fields have a separate budget from the actual file.
MAX_REQUEST_BYTES = MAX_FILE_BYTES + 1024 * 1024
DEFAULT_ORIGINS = (
    "https://okumadunyasi.vercel.app",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
)


def configured_origins():
    """Exact origins only; never interpret preview addresses as regexes."""
    origins = list(DEFAULT_ORIGINS)
    for origin in os.environ.get("CORS_ORIGINS", "").split(","):
        origin = origin.strip()
        if not origin:
            continue
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or "*" in origin
        ):
            raise ValueError("CORS_ORIGINS yalnızca tam http/https origin içermeli.")
        # Accessing port validates malformed/non-numeric/out-of-range values.
        _ = parsed.port
        origins.append(origin)
    return [re.compile(re.escape(origin) + r"\Z") for origin in origins]


app = Flask(__name__)
app.config.update(
    MAX_CONTENT_LENGTH=MAX_REQUEST_BYTES,
    # Must exceed Werkzeug's multipart read buffer plus boundary/header overhead.
    MAX_FORM_MEMORY_SIZE=256 * 1024,
    MAX_FORM_PARTS=8,
    DOCUMENT_LIMITS=DEFAULT_LIMITS.copy(),
    PARSER_TIMEOUT_SECONDS=8,
)
CORS(
    app,
    resources={r"/upload": {"origins": configured_origins()}},
    methods=["POST", "OPTIONS"],
    allow_headers=["Content-Type"],
    supports_credentials=False,
)
app.logger.setLevel(logging.INFO)


@app.before_request
def begin_upload():
    if request.path == "/upload" and request.method == "POST":
        g.upload_started = perf_counter()
        g.upload_format = "unknown"
        g.timings = dict.fromkeys(
            ("receive", "validation", "extraction", "worker", "json"), 0.0
        )


@app.after_request
def finish_upload(response):
    if hasattr(g, "upload_started"):
        g.timings["total"] = (perf_counter() - g.upload_started) * 1000
        response.headers["Server-Timing"] = ", ".join(
            f"{stage};dur={duration:.2f}" for stage, duration in g.timings.items()
        )
        # Do not log filenames, contents, exception messages, or request bodies.
        app.logger.info(
            "upload status=%d format=%s %s",
            response.status_code,
            g.upload_format,
            " ".join(
                f"{stage}_ms={duration:.2f}" for stage, duration in g.timings.items()
            ),
        )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(DocumentError)
def document_error(error):
    return jsonify({"error": error.message}), error.status


@app.errorhandler(RequestEntityTooLarge)
def request_too_large(error):
    return jsonify(
        {"error": "Dosya en fazla 10 MiB olabilir; istek veya form sınırı aşıldı."}
    ), 413


@app.errorhandler(BadRequest)
def bad_request(error):
    return jsonify(
        {"error": "Yükleme isteği geçersiz veya eksik. Lütfen yeniden deneyin."}
    ), 400


@app.errorhandler(Exception)
def unexpected_error(error):
    if isinstance(error, HTTPException):
        return jsonify(
            {"error": "İstek bu adres veya yöntem için desteklenmiyor."}
        ), error.code
    # Exception text/tracebacks may contain private document data.
    app.logger.error("request_failed type=%s", type(error).__name__)
    return jsonify(
        {
            "error": "Belge işlenirken beklenmeyen bir hata oluştu. Lütfen yeniden deneyin."
        }
    ), 500


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/upload")
def upload_file():
    stage = perf_counter()
    try:
        files = request.files
        if "file" not in files:
            raise DocumentError("Dosya bulunamadı", 400)
        if len(files.getlist("file")) != 1 or len(files) != 1:
            raise DocumentError("Lütfen tek bir dosya gönderin.", 400)
        uploaded = files["file"]
        if not uploaded.filename:
            raise DocumentError("Dosya seçilmedi", 400)
        # Read once, bounded independently of client Content-Length/MIME.
        data = uploaded.stream.read(MAX_FILE_BYTES + 1)
    finally:
        g.timings["receive"] = (perf_counter() - stage) * 1000

    stage = perf_counter()
    try:
        extension = PurePath(uploaded.filename).suffix.lower()
        if extension not in {".pdf", ".docx"}:
            raise DocumentError(
                "Geçersiz dosya formatı. Lütfen .docx veya .pdf dosyası yükleyin.", 400
            )
        g.upload_format = extension[1:]
        if len(data) > MAX_FILE_BYTES:
            raise DocumentError("Dosya boyutu en fazla 10 MiB olabilir.", 413)
        if not data:
            raise DocumentError(
                "Dosya boş. Lütfen içerik bulunan bir belge yükleyin.", 400
            )
        if extension == ".pdf" and not data.startswith(b"%PDF-"):
            raise DocumentError("Geçersiz veya bozuk PDF dosyası.", 400)
        if extension == ".docx" and not data.startswith(b"PK\x03\x04"):
            raise DocumentError("Geçersiz veya bozuk DOCX dosyası.", 400)
    finally:
        g.timings["validation"] = (perf_counter() - stage) * 1000

    stage = perf_counter()
    try:
        text, parser_timings = extract_document(
            data,
            extension,
            app.config["DOCUMENT_LIMITS"],
            app.config["PARSER_TIMEOUT_SECONDS"],
        )
        g.timings["validation"] += parser_timings["validation"]
        g.timings["extraction"] = parser_timings["extraction"]
    finally:
        g.timings["worker"] = (perf_counter() - stage) * 1000

    stage = perf_counter()
    response = jsonify({"text": text})
    g.timings["json"] = (perf_counter() - stage) * 1000
    return response


if __name__ == "__main__":
    app.run()  # Development only; production uses gunicorn app:app.
