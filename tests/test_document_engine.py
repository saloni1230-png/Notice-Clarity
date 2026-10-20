import io
from types import SimpleNamespace

import pymupdf
import pytest
from PIL import Image

from notice import documents
from notice.evidence import (
    category_hint,
    evidence_boxes,
    extract_dates,
    extract_facts,
    verify_fact,
    verify_quote,
)
from notice.schemas import Fact


def page(text, number=1):
    return SimpleNamespace(number=number, text=text)


def test_deterministic_notice_fields_have_verifiable_page_evidence():
    sample = page(
        "Income Tax Department\nTo: Example Limited\nDIN: ITBA/AST/S/142(1)/2026-27/001\n"
        "Date of issue: 20 October 2026\nPlease furnish the supporting bank statement by 31 Oct. 2026.\n"
        "Amount mentioned: INR1,25,000.00 under section 142(1).",
        number=7,
    )
    facts = extract_facts(sample)
    assert {fact["kind"] for fact in facts} >= {
        "issuer",
        "recipient",
        "reference",
        "notice_date",
        "deadline",
        "request",
        "amount",
        "section",
    }
    assert next(fact for fact in facts if fact["kind"] == "deadline")["date_iso"] == "2026-10-31"
    assert all(verify_fact(Fact.model_validate(fact), {7: sample}) for fact in facts)
    assert all(fact["evidence"]["page"] == 7 for fact in facts)


def test_relative_deadline_remains_literal_without_computed_due_date():
    facts = extract_facts(
        page("You are required to reply within fifteen working days of receipt of this notice.")
    )
    deadlines = [fact for fact in facts if fact["kind"] == "deadline"]
    assert len(deadlines) == 1
    assert deadlines[0]["value"] == "within fifteen working days of receipt of this notice"
    assert deadlines[0]["date_iso"] is None


def test_wrapped_instruction_remains_one_verbatim_request():
    sample = page(
        "You are required to\nfurnish the supporting statement\nwithin 15 days of receipt of this notice."
    )
    facts = extract_facts(sample)
    requests = [fact for fact in facts if fact["kind"] == "request"]
    assert len(requests) == 1 and "\nfurnish" in requests[0]["value"]
    assert verify_fact(Fact.model_validate(requests[0]), {1: sample})
    assert any(fact["kind"] == "deadline" and fact["date_iso"] is None for fact in facts)


def test_issue_date_and_deadline_on_same_line_keep_separate_roles():
    facts = extract_facts(page("Notice issued on 20 October 2026 and reply before 31 October 2026."))
    dates = {fact["value"]: fact["kind"] for fact in facts if fact["date_iso"]}
    assert dates == {"20 October 2026": "notice_date", "31 October 2026": "deadline"}


def test_reporting_period_is_not_response_deadline():
    facts = extract_facts(
        page("Please submit bank statements for the period 1 April 2025 to 31 March 2026 by 31 October 2026.")
    )
    deadlines = [fact["value"] for fact in facts if fact["kind"] == "deadline"]
    assert deadlines == ["31 October 2026"]


@pytest.mark.parametrize(
    "label, expected",
    [
        ("31st Oct., 2026", "2026-10-31"),
        ("October 31, 2026", "2026-10-31"),
        ("29 Feb 2024", "2024-02-29"),
        ("29 Feb 2025", None),
        ("03/04/2026", None),
        ("31/10/2026", "2026-10-31"),
    ],
)
def test_date_formats_preserve_ambiguity_and_invalid_dates(label, expected):
    assert extract_dates(label) == [(label, expected)]


def test_date_verification_binds_normalized_date_to_value_not_other_quoted_date():
    sample = page("Reply by 03/04/2026. Previous payment was on 31 October 2026.")
    fact = Fact.model_validate(
        {
            "kind": "deadline",
            "value": "03/04/2026",
            "date_iso": "2026-10-31",
            "confidence": 0.99,
            "evidence": {"page": 1, "quote": sample.text},
        }
    )
    assert not verify_fact(fact, {1: sample})


def test_amount_quote_cannot_match_prefix_of_larger_amount():
    assert not verify_quote("Amount: INR 12500", "INR 125")
    assert not verify_quote("Amount: INR 12,500.00", "INR 12")
    assert not verify_quote("Amount: INR 12,500.00", "500.00")
    assert verify_quote("Amount: INR 125.00.", "INR 125.00")


def test_long_line_keeps_evidence_at_end_and_negative_requests_are_not_tasks():
    sample = page("Background " * 1000 + "Amount: Rs. 12,500/-\nYou are not required to submit a reply.")
    facts = extract_facts(sample)
    assert any(fact["kind"] == "amount" and fact["value"] == "Rs. 12,500/-" for fact in facts)
    assert not any(fact["kind"] == "request" for fact in facts)


def test_category_conflicts_remain_unknown():
    assert category_hint("CGST Department GSTIN: EXAMPLE") == "gst"
    assert category_hint("Income Tax Department. Supporting bank statement.") == "income_tax"
    assert category_hint("Income Tax Department. District Court summons.") == "unknown"


def test_evidence_highlight_selects_contiguous_quote_not_every_shared_word():
    blocks = [
        {"text": word, "bbox": [index / 10, 0, (index + 1) / 10, 1]}
        for index, word in enumerate("Pay INR 100 and later pay INR 500".split())
    ]
    sample = SimpleNamespace(blocks=blocks)
    assert evidence_boxes(sample, "pay INR 500") == [item["bbox"] for item in blocks[5:8]]
    assert evidence_boxes(sample, "pay INR 999") == []


def test_digital_pdf_preserves_layout_and_rejects_invalid_page():
    with pymupdf.open() as doc:
        pdf_page = doc.new_page()
        pdf_page.insert_text(
            (50, 50),
            "Income Tax Department\nPlease reply before 31 October 2026.\n" + "Supporting text. " * 15,
        )
        content = doc.tobytes()
    result = documents.digital_page(content, 1)
    assert not result["needs_ocr"]
    assert result["blocks"] and "line" in result["blocks"][0]
    assert all(0 <= value <= 1 for block in result["blocks"] for value in block["bbox"])
    with pytest.raises(ValueError, match="Page number"):
        documents.digital_page(content, 0)


def test_ocr_rejects_nonfinite_and_invalid_boxes(monkeypatch):
    bitmap = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(bitmap, "PNG")
    monkeypatch.setattr(documents, "render_page", lambda *args, **kwargs: bitmap.getvalue())
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t5\t5\t30\t10\t95\tNOTICE\n"
        "5\t1\t1\t1\t1\t2\tnan\t5\t30\t10\t99\tinvalid\n"
        "5\t1\t1\t1\t1\t3\t5\t5\t-3\t10\t99\tinvalid\n"
        "5\t1\t1\t1\t1\t4\t40\t5\t30\t10\tnan\tinvalid\n"
    )
    monkeypatch.setattr(
        documents.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=tsv.encode())
    )
    result = documents.ocr_page(b"unused", 1)
    assert result["text"] == "NOTICE"
    assert len(result["blocks"]) == 1 and result["quality"] == 0.95


def test_rotated_pdf_evidence_boxes_follow_rendered_page_orientation():
    with pymupdf.open() as doc:
        pdf_page = doc.new_page(width=400, height=600)
        pdf_page.insert_text((50, 80), "NOTICE")
        pdf_page.set_rotation(90)
        content = doc.tobytes()
    result = documents.digital_page(content, 1)
    assert (result["width"], result["height"]) == (600, 400)
    bbox = result["blocks"][0]["bbox"]
    assert bbox[0] > 0.8 and 0.1 < bbox[1] < 0.2
