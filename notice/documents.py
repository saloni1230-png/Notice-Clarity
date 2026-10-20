import csv
import io
import math
import re
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


def _parse_ocr(tsv, width, height):
    rows = csv.DictReader(io.StringIO(tsv.decode("utf-8", errors="replace")), delimiter="\t")
    blocks = []
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
        blocks.append(
            {
                "text": word,
                "bbox": bbox,
                "block": key[0],
                "line": ":".join(key),
                "confidence": min(confidence, 100) / 100,
                "ocr_version": "3",
            }
        )
    return blocks[:15000]


def _ocr_text(blocks):
    lines, previous = [], None
    for word in blocks:
        if word["line"] != previous:
            lines.append([])
            previous = word["line"]
        lines[-1].append(word["text"])
    return "\n".join(" ".join(line) for line in lines)[:60000]


def _ocr_quality(blocks):
    text = _ocr_text(blocks)
    total = sum(max(1, len(w["text"])) for w in blocks)
    confidence = sum(w["confidence"] * max(1, len(w["text"])) for w in blocks) / max(1, total)
    return round(min(quality(text), confidence), 3)


def _recognize(image, mode=3, whitelist=None):
    out = io.BytesIO()
    image.save(out, format="PNG")
    args = [settings().tesseract_path, "stdin", "stdout", "-l", "eng", "--psm", str(mode), "--dpi", "300"]
    if whitelist:
        args += ["-c", "tessedit_char_whitelist=" + whitelist]
    args += ["tsv"]
    process = subprocess.run(args, input=out.getvalue(), capture_output=True, timeout=110, check=True)
    return _parse_ocr(process.stdout, *image.size)


_MONTH_TOKEN = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
    re.I,
)


def _critical_words(blocks):
    by_line = {}
    for index, word in enumerate(blocks):
        by_line.setdefault(word["line"], []).append(index)
    lines = list(by_line.values())
    targets = []
    for pos, indexes in enumerate(lines):
        text = " ".join(blocks[i]["text"] for i in indexes)
        following = " ".join(blocks[i]["text"] for i in lines[pos + 1]) if pos + 1 < len(lines) else ""
        is_date = bool(_MONTH_TOKEN.search(text + " " + following))
        is_email = bool(re.search(r"email|e-mail|\bnic\.in\b|\bgov\.in\b|@", text, re.I))
        for i in indexes:
            word = blocks[i]
            if is_date and re.fullmatch(r"\d{2,4}[A-Za-z0-9-]*", word["text"]):
                # Invalid day forms are a reason to reread pixels, never to strip digits with regex.
                digits = re.match(r"\d+", word["text"])[0]
                if len(digits) > 2 and not re.fullmatch(r"(?:19|20)\d{2}", digits):
                    targets.append((0, i))
            if is_email and word["confidence"] < 0.75 and re.search(r"[A-Za-z].*(?:\.|@)", word["text"]):
                targets.append((1, i))
    return list(dict.fromkeys(i for _, i in sorted(targets)))[:6]


def _superscript_base(image, bbox):
    """Find full-height connected glyphs beneath an elevated ordinal suffix."""
    x0, y0, x1, y1 = [
        round(value * dimension) for value, dimension in zip(bbox, (image.width, image.height) * 2)
    ]
    crop = image.crop((x0, y0, x1, y1))
    if crop.width > 250 or crop.height > 120 or min(crop.size) < 5:
        return None
    pixels = crop.load()
    active = {(x, y) for y in range(crop.height) for x in range(crop.width) if pixels[x, y] < 200}
    components = []
    while active:
        seed = active.pop()
        pending, points = [seed], [seed]
        while pending:
            x, y = pending.pop()
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (1, -1), (-1, 1)):
                point = x + dx, y + dy
                if point in active:
                    active.remove(point)
                    pending.append(point)
                    points.append(point)
        if len(points) >= 5:
            components.append(
                (
                    min(p[0] for p in points),
                    min(p[1] for p in points),
                    max(p[0] for p in points) + 1,
                    max(p[1] for p in points) + 1,
                )
            )
    if len(components) < 3:
        return None
    bottom = max(c[3] for c in components)
    height = max(c[3] - c[1] for c in components)
    base = [c for c in components if c[3] >= bottom - 0.15 * height and c[3] - c[1] >= 0.7 * height]
    elevated = [c for c in components if c[3] < bottom - 0.2 * height and c not in base]
    if not 1 <= len(base) <= 2 or not elevated or min(c[0] for c in elevated) < max(c[2] for c in base) - 2:
        return None
    region = (
        x0 + min(c[0] for c in base),
        y0 + min(c[1] for c in base),
        x0 + max(c[2] for c in base),
        y0 + max(c[3] for c in base),
    )
    return region


def ocr_page(data, number):
    import time

    image_data = render_page(data, number, dpi=300)
    with Image.open(io.BytesIO(image_data)) as original:
        image = ImageOps.autocontrast(ImageOps.grayscale(ImageOps.exif_transpose(original)))
    # A small uploaded image needs readable glyph height, not a nominal DPI change alone.
    scale = min(
        3.0,
        max(1.0, 1800 / image.width),
        (settings().max_pixels * 0.98 / (image.width * image.height)) ** 0.5,
    )
    if abs(scale - 1) > 0.01:
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
    width, height = image.size
    started = time.monotonic()
    blocks = _recognize(image, 3)
    candidates = _critical_words(blocks)
    flags = []
    if _ocr_quality(blocks) < 0.88 or candidates:
        try:
            alternate = _recognize(image, 6)
            from .evidence import extract_dates

            first_dates = {stamp for _, stamp in extract_dates(_ocr_text(blocks)) if stamp}
            second_dates = {stamp for _, stamp in extract_dates(_ocr_text(alternate)) if stamp}
            if (
                first_dates
                and second_dates
                and len(first_dates) == len(second_dates)
                and first_dates != second_dates
            ):
                flags.append("ocr_critical_conflict")
            if len(alternate) >= 0.75 * len(blocks) and _ocr_quality(alternate) > _ocr_quality(blocks):
                blocks = alternate
        except (subprocess.SubprocessError, OSError):
            flags.append("ocr_alternative_unavailable")
    for index in _critical_words(blocks):
        if time.monotonic() - started > 85:
            flags.append("ocr_refinement_budget_reached")
            break
        word = blocks[index]
        original_text = word["text"]
        region = _superscript_base(image, word["bbox"]) if re.match(r"\d", original_text) else None
        try:
            if region:
                crop = ImageOps.expand(image.crop(region), border=12, fill=255)
                reads = [_recognize(crop, mode, "0123456789") for mode in (7, 8)]
                values = ["".join(w["text"] for w in result) for result in reads]
                scores = [_ocr_quality(result) for result in reads]
                if (
                    values[0] == values[1]
                    and values[0].isdigit()
                    and 1 <= int(values[0]) <= 31
                    and max(scores) >= 0.65
                ):
                    word["alternatives"] = [{"text": original_text, "confidence": word["confidence"]}]
                    word["text"], word["confidence"] = values[0], max(scores)
                    word["bbox"] = [
                        region[0] / width,
                        region[1] / height,
                        region[2] / width,
                        region[3] / height,
                    ]
                    flags.append("ocr_superscript_date_resolved")
                    continue
            x0, y0, x1, y1 = [round(v * d) for v, d in zip(word["bbox"], (width, height) * 2)]
            crop = image.crop((max(0, x0 - 12), max(0, y0 - 12), min(width, x1 + 12), min(height, y1 + 12)))
            crop = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.LANCZOS)
            read = _recognize(crop, 7)
            value = " ".join(w["text"] for w in read).strip(" |[]()")
            score = _ocr_quality(read)
            if re.fullmatch(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", value) and score > max(
                0.3, word["confidence"] + 0.1
            ):
                word["alternatives"] = [{"text": original_text, "confidence": word["confidence"]}]
                word["text"], word["confidence"] = value, score
            if word["confidence"] < 0.75 or re.fullmatch(r"\d{3,}[A-Za-z0-9-]*", word["text"]):
                word["uncertain"] = True
                flags.append("ocr_critical_field_uncertain")
        except (subprocess.SubprocessError, OSError):
            flags.append("ocr_critical_field_uncertain")
    uncertain = any(f in flags for f in ("ocr_critical_field_uncertain", "ocr_critical_conflict"))
    if blocks:
        blocks[0]["ocr_diagnostics"] = sorted(set(flags))
    return {
        "text": _ocr_text(blocks),
        "blocks": blocks,
        "width": width,
        "height": height,
        "quality": min(0.79, _ocr_quality(blocks)) if uncertain else _ocr_quality(blocks),
        "flags": ["low_ocr_quality"] if uncertain else [],
        "diagnostics": sorted(set(flags)),
    }
