"""Repeatable synthetic handler timings, excluding network/Render wake-up time."""

import argparse
import io
import json
import logging
import math
import platform
import statistics
import subprocess
import sys
from pathlib import Path
from time import perf_counter
from types import ModuleType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def documents():
    # Keep dev-only imports out of multiprocessing's child __main__ bootstrap.
    import reportlab
    from docx import Document
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    examples = []
    for paragraphs in (2, 200):
        document = Document()
        for index in range(paragraphs):
            document.add_paragraph(
                f"{index} Okuma dünyası, Türkçe bir deneme metni. İğüşöç."
            )
        output = io.BytesIO()
        document.save(output)
        examples.append((f"docx_{paragraphs}_paragraphs", "docx", output.getvalue()))
    font_path = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
    pdfmetrics.registerFont(TTFont("BenchmarkVera", str(font_path)))
    for pages in (1, 10):
        output = io.BytesIO()
        document = canvas.Canvas(output, pageCompression=1, invariant=1)
        for number in range(pages):
            document.setFont("BenchmarkVera", 12)
            document.drawString(50, 790, f"{number} Okuma dünyası: İğüşöç.")
            document.showPage()
        document.save()
        examples.append((f"pdf_{pages}_pages", "pdf", output.getvalue()))
    return examples


def measure(application, payload, extension, samples):
    totals = []
    stages = {}
    first = None
    with application.test_client() as client:
        for number in range(samples + 5):
            started = perf_counter()
            response = client.post(
                "/upload",
                data={"file": (io.BytesIO(payload), f"synthetic.{extension}")},
            )
            elapsed = (perf_counter() - started) * 1000
            assert response.status_code == 200, (response.status_code, response.json)
            assert set(response.json) == {"text"} and response.json["text"].strip()
            if first is None:
                first = elapsed
            if number < 5:
                continue
            totals.append(elapsed)
            for metric in response.headers.get("Server-Timing", "").split(", "):
                if ";dur=" in metric:
                    name, value = metric.split(";dur=")
                    stages.setdefault(name, []).append(float(value))
    return {
        "bytes": len(payload),
        "samples": samples,
        "first_ms": round(first, 3),
        "median_ms": round(statistics.median(totals), 3),
        "p95_ms": round(sorted(totals)[math.ceil(samples * 0.95) - 1], 3),
        "stage_medians_ms": {
            key: round(statistics.median(values), 3) for key, values in stages.items()
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline-ref", default="bfdb95ba721c3aef584fee995f33a5bd9d0a1faa"
    )
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be positive")
    # Read-only reference; never reset or modify the checkout to benchmark it.
    source = subprocess.check_output(
        ["git", "show", f"{args.baseline_ref}:app.py"], text=True
    )
    baseline = ModuleType("benchmark_baseline")
    exec(compile(source, "baseline_app.py", "exec"), baseline.__dict__)  # noqa: S102 - trusted local Git reference
    from app import app

    logging.disable(logging.CRITICAL)
    result = {
        "python": platform.python_version(),
        "platform": platform.system(),
        "baseline_ref": args.baseline_ref,
        "conditions": "Serial Flask test_client, same Python/dependencies, 5 warmups, no network or Render",
        "baseline": {},
        "v2": {},
    }
    for label, extension, payload in documents():
        if extension == "docx":
            result["baseline"][label] = measure(
                baseline.app, payload, extension, args.samples
            )
        result["v2"][label] = measure(app, payload, extension, args.samples)
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    print(serialized)
    if args.output:
        args.output.write_text(serialized + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
