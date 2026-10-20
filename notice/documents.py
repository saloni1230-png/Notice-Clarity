import csv
import io
import subprocess

import pymupdf
from PIL import Image, ImageOps

from .config import settings
from .evidence import quality


def open_document(data):
    return pymupdf.open(stream=data, filetype="pdf" if data.startswith(b"%PDF-") else None)


def digital_page(data, number):
    with open_document(data) as doc:
        page = doc[number - 1]
        text = page.get_text(sort=True)
        width, height = page.rect.width, page.rect.height
        words = [
            {"text": word[4], "bbox": [word[0] / width, word[1] / height, word[2] / width, word[3] / height]}
            for word in page.get_text("words", sort=True)
        ]
        # An image can conceal text absent from the text layer. Route image-heavy pages to OCR.
        image_area = sum(
            rect.width * rect.height for image in page.get_images() for rect in page.get_image_rects(image[0])
        )
        suspicious = image_area > width * height * 0.45 and len(text.strip()) < 300
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
        page = doc[number - 1]
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
        x, y, w, h = (float(row[key]) for key in ("left", "top", "width", "height"))
        confidence = float(row.get("conf", -1))
        key = (row["block_num"], row["par_num"], row["line_num"])
        if key != line_key:
            lines.append([])
            line_key = key
        lines[-1].append(word)
        blocks.append({"text": word, "bbox": [x / width, y / height, (x + w) / width, (y + h) / height]})
        if confidence >= 0:
            confidences.append(confidence / 100)
    text = "\n".join(" ".join(line) for line in lines)[:60000]
    confidence = sum(confidences) / max(1, len(confidences))
    return {
        "text": text,
        "blocks": blocks[:15000],
        "width": width,
        "height": height,
        "quality": round(min(quality(text), confidence), 3),
    }
