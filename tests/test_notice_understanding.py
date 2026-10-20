import io
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select

from conftest import drain
from notice import ai, documents
from notice.db import session_scope
from notice.models import Case, Page, Question
from notice.worker import run_one
from notice.evidence import extract_facts, verify_quote
from notice.explanations import fallback
from notice.understanding import plan_question, structure

FIXTURES = Path(__file__).parent / "fixtures"
NOTICE = (FIXTURES / "revenue-notification.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "question,intent",
    [
        ("who gave this????", "issuer"),
        ("Who signed it?", "signatory"),
        ("Who can give evidence?", "audience"),
        ("What is this notice about?", "purpose"),
        ("Which details do I have to submit?", "requirements"),
        ("Do I have to participate?", "legal"),
        ("Can I ignore this?", "legal"),
        ("When did the accident happen?", "event"),
        ("When was it issued?", "notice_date"),
        ("Where do I submit the application?", "submission"),
        ("What amount is mentioned?", "amount"),
        ("Which law is mentioned?", "section"),
    ],
)
def test_question_planner_separates_factual_roles_and_legal_obligations(question, intent):
    assert plan_question(question).intent == intent


def sample(text=NOTICE):
    return SimpleNamespace(number=1, text=text, flags=[], blocks=[])


def answer(question, text=NOTICE, context=None):
    page = sample(text)
    result = fallback(question, extract_facts(page), [page], context or {})
    assert all(verify_quote(text, c["evidence"]["quote"]) for c in result["claims"])
    return result


@pytest.mark.parametrize(
    "question", ["who gave this????", "name of issuer", "Who issued this notice?", "Who signed it?"]
)
def test_issuer_questions_identify_printed_official_without_unrelated_context_or_review(question):
    result = answer(question)
    text = " ".join(c["text"] for c in result["claims"])
    assert "Neeraj" in text and "Divisional Commissioner" in text
    assert "Chief Minister" not in text
    assert not result["missing_context"] and not result["professional_review"]
    assert result["direct_answer"]


def test_public_notification_purpose_and_audience_are_not_confused_with_issuer():
    purpose = " ".join(c["text"] for c in answer("What is this notice about?")["claims"])
    assert "magisterial enquiry" in purpose and "building collapse" in purpose
    audience = answer("Who can give evidence?")
    assert audience["intent"] == "audience"
    assert any("People willing" in c["text"] for c in audience["claims"])
    assert not any("issuing office" in c["text"] for c in audience["claims"])


def test_all_eight_application_fields_survive_wrapped_ocr_and_damaged_bullets():
    text = NOTICE.replace("iii.", "iti.").replace("iv.", "iv")
    result = answer("Which details do I have to submit in the application?", text)
    combined = " ".join(c["text"] for c in result["claims"])
    assert len(result["claims"]) == 8
    for item in (
        "Full Name",
        "Father Name",
        "Mother Name",
        "Full address",
        "Birth & Age",
        "Contact",
        "Occupation",
        "Circumstances",
    ):
        assert item in combined
    assert not result["missing_context"]


def test_absolute_application_deadline_is_separate_from_incident_and_issue_dates():
    result = answer("What is the last date to apply?")
    assert "22 September 2026" in " ".join(c["text"] for c in result["claims"])
    assert not result["missing_context"]
    assert any("06.09.2026" in c["text"] for c in answer("When did the building collapse happen?")["claims"])


def test_corrupted_date_is_not_silently_changed_to_a_plausible_day():
    result = answer("What is the deadline?", NOTICE.replace("22nd", "224"))
    assert not any("22 September 2026" in c["text"] for c in result["claims"])
    assert any("not read reliably" in message for message in result["missing_context"])
    assert not result["direct_answer"]


def test_receipt_date_is_only_requested_when_the_answer_depends_on_it():
    text = NOTICE.replace("22nd\nSeptember 2026", "within 15 days of receipt of this notice")
    issuer = answer("Who gave this?", text)
    assert issuer["missing_context"] == []
    deadline = answer("What is the deadline?", text)
    assert any("received or served" in message for message in deadline["missing_context"])


def test_submission_includes_postal_channel_and_does_not_invent_unreadable_email():
    result = answer("Where do I submit the application?")
    assert any("Sham Nath Marg" in c["text"] for c in result["claims"])
    damaged = answer("Where do I submit the application?", NOTICE.replace("divcom@nic.in", "divcomwnic.in"))
    assert any("email address was not read reliably" in m for m in damaged["missing_context"])
    assert not any("divcomwnic.in" in c["text"] for c in damaged["claims"])


def test_legal_obligation_question_is_not_answered_by_repeating_a_money_or_issuer_field():
    result = answer("Do I have to participate or can I ignore this?")
    assert result["professional_review"] and not result["direct_answer"]
    assert result["intent"] == "legal" and result["claims"] == []


def test_deadline_does_not_become_a_later_reporting_period_date():
    text = "NOTIFICATION\nAll applications should reach this office by 31 October 2026, enclosing statements for the period 1 April 2025 to 31 March 2026."
    result = answer("What is the deadline?", text)
    assert any("31 October 2026" in c["text"] for c in result["claims"])
    assert not any("deadline is 31 March" in c["text"] for c in result["claims"])


def test_evidence_rule_is_not_rewritten_as_an_invitation_to_apply():
    result = answer(
        "Who can give evidence?", "NOTIFICATION\nAny person giving oral evidence must tell the truth."
    )
    assert not any("invited to submit" in c["text"] for c in result["claims"])


def test_summary_keeps_deadline_and_application_fields_instead_of_duplicate_issuer_lines():
    page = sample()
    result = fallback("Explain this notice", extract_facts(page), [page], {}, summary=True)
    text = " ".join(c["text"] for c in result["claims"])
    assert "22 September 2026" in text and "Mother Name" in text and "magisterial enquiry" in text


def test_copy_distribution_official_is_never_classified_as_the_signatory():
    page = sample(NOTICE.replace("(Neeraj Sernwal)\nSecretary (Revenue) /\nDivisional Commissioner\n", ""))
    assert not any(u.role == "signatory" for u in structure(page))


def test_normal_three_digit_number_is_never_shortened_as_an_ordinal_suffix():
    image = Image.new("L", (220, 70), 255)
    ImageDraw.Draw(image).text((15, 10), "224", font=ImageFont.load_default(size=40), fill=0)
    assert documents._superscript_base(image, [0, 0, 1, 1]) is None


def test_reanalysis_refreshes_existing_factual_answers_without_retyping(client, uploaded):
    drain()
    response = client.post(f"/api/cases/{uploaded}/questions", json={"question": "Who issued this?"})
    question_id = response.json()["id"]
    drain()
    with session_scope() as db:
        row = db.get(Question, question_id)
        row.answer = {
            "claims": [],
            "professional_review": True,
            "missing_context": ["When did you receive it?"],
        }
    assert client.post(f"/api/cases/{uploaded}/reprocess").status_code == 202
    drain()
    with session_scope() as db:
        row = db.get(Question, question_id)
        assert row.revision == 2 and row.status == "done"
        assert "Income Tax Department" in " ".join(c["text"] for c in row.answer["claims"])
        assert not row.answer["professional_review"] and row.answer["missing_context"] == []


def test_upgrading_an_old_inflight_ocr_job_uses_a_fresh_understanding_key(client, uploaded, monkeypatch):
    assert run_one("extract") and run_one("extract")
    with session_scope() as db:
        case = db.get(Case, uploaded)
        page = db.scalar(select(Page).where(Page.document_id == case.document_id))
        page.method, page.blocks = "ocr", []
        text = page.text
    monkeypatch.setattr(
        documents,
        "ocr_page",
        lambda *args: {
            "text": text,
            "blocks": [{"text": "NOTICE", "bbox": [0, 0, 1, 1], "ocr_version": "3"}],
            "width": 100,
            "height": 100,
            "quality": 0.9,
            "flags": [],
        },
    )
    assert run_one("ai")  # The old understanding job redirects to refined OCR.
    drain()
    result = client.get(f"/api/cases/{uploaded}").json()
    assert result["status"] in {"ready", "review_required"}
    assert result["pages"][0]["status"] == "done"


def test_empty_ocr_result_does_not_redirect_forever(client, uploaded):
    assert run_one("extract") and run_one("extract")
    with session_scope() as db:
        case = db.get(Case, uploaded)
        page = db.scalar(select(Page).where(Page.document_id == case.document_id))
        page.method, page.text, page.blocks, page.quality = "ocr", "", [], 0
    drain()
    result = client.get(f"/api/cases/{uploaded}").json()
    assert result["pages"][0]["status"] == "done" and "low_ocr_quality" in result["flags"]


def native_ocr(monkeypatch):
    if shutil.which(documents.settings().tesseract_path):
        return "Tesseract CLI"
    tessdata = os.getenv("NS_TEST_TESSDATA")
    if not tessdata:
        pytest.skip("Real scan test requires Tesseract CLI or NS_TEST_TESSDATA with native tesserocr")
    tesserocr = pytest.importorskip("tesserocr")

    def run(args, input, **kwargs):
        mode = int(args[args.index("--psm") + 1])
        with tesserocr.PyTessBaseAPI(path=tessdata, lang="eng", psm=mode) as api:
            for index, item in enumerate(args):
                if item == "-c":
                    key, value = args[index + 1].split("=", 1)
                    api.SetVariable(key, value)
            api.SetImage(Image.open(io.BytesIO(input)))
            api.SetSourceResolution(300)
            api.Recognize()
            header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            return SimpleNamespace(stdout=(header + api.GetTSVText(0)).encode())

    monkeypatch.setattr(documents.subprocess, "run", run)
    return "Native " + tesserocr.tesseract_version().splitlines()[0]


def test_real_attached_scan_ocr_and_question_answering_without_a_paid_model(client, account, monkeypatch):
    backend = native_ocr(monkeypatch)
    content = (FIXTURES / "revenue-notification-scan.png").read_bytes()
    response = client.post("/api/cases", files={"file": ("notification.png", content, "image/png")})
    assert response.status_code == 202, response.text
    case_id = response.json()["id"]
    drain()
    detail = client.get(f"/api/cases/{case_id}").json()
    assert detail["status"] in {"ready", "review_required"}, (backend, detail)
    text = client.get(f"/api/cases/{case_id}/pages/1").json()["text"]
    assert "22\nSeptember 2026" in text or "22 September 2026" in text, text
    assert any(f["kind"] == "deadline" and f["date_iso"] == "2026-09-22" for f in detail["facts"])

    def no_provider(*args, **kwargs):
        raise AssertionError("An exact document-fact question must not spend model tokens")

    monkeypatch.setattr(ai, "structured", no_provider)
    answers = []
    for question, expected in (
        ("who gave this????", "Neeraj"),
        ("What is the deadline?", "22 September 2026"),
        ("Which details are required?", "Mother Name"),
        ("What is this notice about?", "magisterial enquiry"),
        ("Who can give evidence?", "People willing"),
        ("Which law is mentioned?", "BNSS"),
    ):
        response = client.post(f"/api/cases/{case_id}/questions", json={"question": question})
        assert response.status_code == 202
        drain()
        q = client.get(f"/api/cases/{case_id}").json()["questions"][0]
        assert q["status"] == "done", q
        assert expected in " ".join(c["text"] for c in q["answer"]["claims"]), q
        assert not q["answer"]["professional_review"] and not q["answer"]["missing_context"]
        answers.append({"question": question, "answer": q["answer"]})
    report_path = os.getenv("NS_SCAN_REPORT")
    if report_path:
        Path(report_path).write_text(
            json.dumps(
                {
                    "ocr_backend": backend,
                    "scan": "User-attached revenue notification crop",
                    "model_calls_for_factual_questions": 0,
                    "answers": answers,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
