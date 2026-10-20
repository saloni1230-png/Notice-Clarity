import csv
import io
import math
import subprocess

import pymupdf
from PIL import Image, ImageOps

from .config import settings
from .evidence import quality


def open_document(data):
    return pymupdf.open(stream=data, filetype="pdf" if data.startswith(b"%PDF-") else None)


def _checked_page(doc, number):
    if not isinstance(number, int) or not 1 <= number <= len(doc):
        raise ValueError("Page number is outside the document")
    page = doc[number - 1]
    if not (math.isfinite(page.rect.width) and math.isfinite(page.rect.height)) or page.rect.is_empty:
        raise ValueError("Invalid page dimensions")
    return page


def _box(x0, y0, x1, y1, width, height):
    values = (x0, y0, x1, y1)
    if not all(math.isfinite(value) for value in values) or x1 <= x0 or y1 <= y0:
        return None
    normalized = [max(0.0, min(1.0, value)) for value in (x0 / width, y0 / height, x1 / width, y1 / height)]
    return normalized if normalized[2] > normalized[0] and normalized[3] > normalized[1] else None


def digital_page(data, number):
    with open_document(data) as doc:
        page = _checked_page(doc, number)
        text = page.get_text(sort=True)
        width, height = page.rect.width, page.rect.height
        words = []
        for word in page.get_text("words", sort=True):
            # Text coordinates are unrotated; rendered images include page rotation.
            rect = pymupdf.Rect(word[:4]) * page.rotation_matrix
            bbox = _box(rect.x0, rect.y0, rect.x1, rect.y1, width, height)
            if bbox and word[4].strip():
                words.append({"text": word[4], "bbox": bbox, "block": word[5], "line": word[6]})
        # An image can conceal text absent from the text layer. Route image-heavy pages to OCR.
        # get_image_info includes inline images and avoids looking up repeated image xrefs.
        image_area = sum(
            ((pymupdf.Rect(item["bbox"]) * page.rotation_matrix) & page.rect).get_area()
            for item in page.get_image_info()
        )
        suspicious = image_area > width * height * 0.45 and len(text.strip()) < 600
        needs_ocr = len(text.strip()) < 60 or quality(text) < 0.82 or suspicious
        return {
            "text": text[:60000],
            "blocks": words[:15000],
            "width": width,
            "height": height,
            "quality": quality(text),
            "needs_ocr": needs_ocr,
        }


def render_page(data, number, dpi=110):
    with open_document(data) as doc:
        page = _checked_page(doc, number)
        # Bound bitmap allocations before rendering adversarial oversized pages.
        area = page.rect.width * page.rect.height
        if area <= 0:
            raise ValueError("Invalid page dimensions")
        scale = min(dpi / 72, (settings().max_pixels * 0.98 / area) ** 0.5)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        return pix.tobytes("png")


def ocr_page(data, number):
    image_data = render_page(data, number, dpi=180)
    with Image.open(io.BytesIO(image_data)) as original:
        image = ImageOps.autocontrast(ImageOps.grayscale(original))
        image = ImageOps.exif_transpose(image)
        out = io.BytesIO()
        image.save(out, format="PNG")
        width, height = image.size
    # No shell, no document-derived command arguments; stdin avoids PHI on temp disk.
    process = subprocess.run(
        [settings().tesseract_path, "stdin", "stdout", "-l", "eng", "--psm", "3", "tsv"],
        input=out.getvalue(),
        capture_output=True,
        timeout=110,
        check=True,
    )
    rows = csv.DictReader(io.StringIO(process.stdout.decode("utf-8", errors="replace")), delimiter="\t")
    blocks, confidences, lines, line_key = [], [], [], None
    for row in rows:
        word = row.get("text", "").strip()
        if not word:
            continue
        try:
            x, y, w, h = (float(row[key]) for key in ("left", "top", "width", "height"))
            confidence = float(row.get("conf", -1))
            key = (row["block_num"], row["par_num"], row["line_num"])
        except (KeyError, TypeError, ValueError):
            continue
        bbox = _box(x, y, x + w, y + h, width, height)
        if not bbox or not math.isfinite(confidence) or confidence < 0:
            continue
        if key != line_key:
            lines.append([])
            line_key = key
        lines[-1].append(word)
        blocks.append({"text": word, "bbox": bbox, "block": key[0], "line": key[2]})
        if confidence >= 0:
            confidences.append((min(confidence, 100) / 100, max(len(word), 1)))
    text = "\n".join(" ".join(line) for line in lines)[:60000]
    # Long misread identifiers count more than a series of confident one-letter words.
    confidence = sum(value * weight for value, weight in confidences) / max(1, sum(w for _, w in confidences))
    return {
        "text": text,
        "blocks": blocks[:15000],
        "width": width,
        "height": height,
        "quality": round(min(quality(text), confidence), 3),
    }
