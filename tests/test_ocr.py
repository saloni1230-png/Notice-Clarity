import io
import shutil
import subprocess

import pytest
from PIL import Image, ImageDraw, ImageFont

from notice.config import settings
from notice.documents import ocr_page


def tesseract_available():
    executable = shutil.which(settings().tesseract_path)
    if not executable:
        return False
    result = subprocess.run([executable, "--list-langs"], capture_output=True, text=True)
    return all(lang in result.stdout.splitlines() for lang in ("eng",))


@pytest.mark.skipif(not tesseract_available(), reason="Tesseract with eng is not provisioned")
def test_real_ocr_extracts_scanned_notice_with_word_boxes():
    image = Image.new("RGB", (1400, 600), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 42)
    except OSError:
        font = ImageFont.load_default(size=42)
    draw.text(
        (60, 60),
        "NOTICE\nReply before 31 October 2026\nAmount INR 12500",
        fill="black",
        font=font,
        spacing=25,
    )
    out = io.BytesIO()
    image.save(out, format="PNG")
    result = ocr_page(out.getvalue(), 1)
    assert "12500" in result["text"] and "2026" in result["text"]
    assert result["blocks"] and result["quality"] > 0.5
