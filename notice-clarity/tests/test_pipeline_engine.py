from collections import Counter
from datetime import timedelta

from sqlalchemy import func, select

from conftest import drain
from notice import ai, pipeline
from notice.config import settings
from notice.db import session_scope
from notice.jobs import claim, retry
from notice.models import AnalysisChunk, Case, Document, Job, Page, Question, now
from notice.passages import split_passages
from notice.schemas import Answer, Extraction, Verification
from notice.worker import run_one
from notice.workspace import context_digest, schedule_summary


def ask(client, case_id, question):
    response = client.post(f"/api/cases/{case_id}/questions", json={"question": question})
    assert response.status_code == 202, response.text
    return response.json()["id"]


def answer(question_id):
    with session_scope() as db:
        row = db.get(Question, question_id)
        assert row.status == "done"
        return row.answer


def test_local_summary_explains_explicit_requests_without_a_model(client, uploaded):
    drain()
    summary = client.get(f"/api/cases/{uploaded}").json()["explanation"]
    assert summary["status"] == "done" and not summary["stale"]
    result = summary["answer"]
    assert result["mode"] == "extracts" and result["answer_type"] == "document_facts"
    requests = [
        claim for claim in result["claims"] if claim["text"].startswith("Information or action requested:")
    ]
    assert any("supporting bank statement" in claim["text"] for claim in requests)
    assert any("12,500" in claim["text"] for claim in result["claims"])
    assert all(claim["evidence"]["page"] == 1 for claim in result["claims"])
    assert result["professional_review"]


def test_local_amount_question_returns_amount_instead_of_the_page_header(client, uploaded):
    drain()
    question_id = ask(client, uploaded, "What amount is mentioned?")
    drain()
    result = answer(question_id)
    assert result["claims"] and all(
        claim["text"].startswith("Amount mentioned:") for claim in result["claims"]
    )
    assert any("12,500.00" in claim["text"] for claim in result["claims"])
    assert all("Income Tax Department" not in claim["text"] for claim in result["claims"])


def test_irrelevant_question_does_not_receive_unrelated_document_claims(client, uploaded):
    drain()
    question_id = ask(client, uploaded, "How can I grow tomatoes hydroponically?")
    drain()
    result = answer(question_id)
    assert result["answer_type"] == "no_evidence" and result["claims"] == []
    assert any("No verified document fact" in question for question in result["missing_context"])


def test_relevant_evidence_after_first_6500_characters_reaches_the_provider(client, uploaded, monkeypatch):
    drain()
    quote = "Inventory discrepancy: 78 units are listed for warehouse Delta."
    with session_scope() as db:
        case = db.get(Case, uploaded)
        page = db.scalar(select(Page).where(Page.document_id == case.document_id))
        page.text = "Administrative background material only. " * 400 + "\n" + quote
    supplied = []

    def provider(org_id, schema, task, data, **kwargs):
        if schema is Answer:
            supplied.extend(data["document"])
            assert any(item["start"] > 6500 and quote in item["text"] for item in data["document"])
            return Answer.model_validate(
                {
                    "claims": [
                        {
                            "text": "The document lists 78 units for warehouse Delta.",
                            "basis": "document",
                            "evidence": {"page": 1, "quote": quote},
                            "source_id": None,
                            "source_quote": None,
                        }
                    ],
                    "missing_context": [],
                    "professional_review": True,
                }
            )
        assert schema is Verification
        return Verification(
            supported=True, unsupported_claims=[], legal_interpretation=False, conflicting_dates=False
        )

    monkeypatch.setattr(ai, "structured", provider)
    question_id = ask(client, uploaded, "What inventory discrepancy is listed for warehouse Delta?")
    drain()
    result = answer(question_id)
    assert supplied and result["mode"] == "verified_ai"
    assert result["claims"][0]["evidence"]["quote"] == quote
    assert result["coverage"]["pages"] == [1]


def test_transient_midpage_failure_resumes_from_persisted_successful_chunks(client, uploaded, monkeypatch):
    assert run_one("extract")  # Inspect the upload.
    assert run_one("extract")  # Extract the digital page, leaving understanding queued.
    text = "First segment.\n" + "Administrative background. " * 260
    text += "\nSecond segment.\n" + "Detailed ledger narrative. " * 260
    spans = split_passages(text, max_chars=6500, overlap=300)
    assert len(spans) >= 2
    with session_scope() as db:
        case = db.get(Case, uploaded)
        page = db.scalar(select(Page).where(Page.document_id == case.document_id))
        page.text = text
    monkeypatch.setattr(settings(), "ai_api_key", "mock-provider-key")
    monkeypatch.setattr(settings(), "ai_model", "mock-extraction-model")
    calls = Counter()
    failed_segment = spans[1].text

    def provider(org_id, schema, task, data, **kwargs):
        assert schema is Extraction
        segment = data["document"]
        calls[segment] += 1
        if segment == failed_segment and calls[segment] == 1:
            raise ai.AIUnavailable("Temporary provider failure", code="provider_unavailable", retryable=True)
        return Extraction(category="unknown", facts=[], flags=[])

    monkeypatch.setattr(ai, "structured", provider)
    assert run_one("ai")
    with session_scope() as db:
        job = db.scalar(select(Job).where(Job.kind == "understand"))
        assert job.status == "queued" and job.attempts == 1
        assert db.scalar(select(func.count()).select_from(AnalysisChunk)) == 1
        job.available = now() - timedelta(seconds=1)
    assert run_one("ai")
    assert calls[spans[0].text] == 1 and calls[failed_segment] == 2
    assert sum(calls.values()) == len(spans) + 1
    with session_scope() as db:
        assert db.scalar(select(Page)).status == "done"
        assert db.scalar(select(func.count()).select_from(AnalysisChunk)) == len(spans)


def test_reprocessing_empty_page_uses_fresh_generation_for_all_stage_keys(client, uploaded):
    drain()
    with session_scope() as db:
        case = db.get(Case, uploaded)
        document_id = case.document_id
        page = db.scalar(select(Page).where(Page.document_id == document_id))
        page.text, page.status = "", "failed"
        old_understand = db.scalar(select(Job).where(Job.key == f"understand:{document_id}:1:1"))
        assert old_understand.status == "done"
    response = client.post(f"/api/cases/{uploaded}/reprocess")
    assert response.status_code == 202, response.text
    drain()
    with session_scope() as db:
        document = db.get(Document, document_id)
        assert document.generation == 2 and document.status in {"ready", "review_required"}
        assert db.scalar(select(Job).where(Job.key == f"understand:{document_id}:2:1")).status == "done"
        assert db.scalar(select(Job).where(Job.key == f"finalize:{document_id}:2")).status == "done"
        assert any(fact["kind"] == "request" for fact in document.facts)


def test_reprocessing_recovers_a_missing_page_row_from_the_original_file(client, uploaded):
    drain()
    with session_scope() as db:
        case = db.get(Case, uploaded)
        document_id = case.document_id
        page = db.scalar(select(Page).where(Page.document_id == document_id))
        old_id = page.id
        db.delete(page)
    response = client.post(f"/api/cases/{uploaded}/reprocess")
    assert response.status_code == 202, response.text
    drain()
    with session_scope() as db:
        recovered = db.scalar(select(Page).where(Page.document_id == document_id))
        assert recovered.id != old_id and recovered.status == "done"
        assert "supporting bank statement" in recovered.text
        assert db.get(Document, document_id).generation == 2
    assert client.get(f"/api/cases/{uploaded}").json()["explanation"]["status"] == "done"


def test_superseded_summary_job_cannot_answer_an_older_context_revision(client, uploaded, monkeypatch):
    drain()
    with session_scope() as db:
        case = db.get(Case, uploaded)
        schedule_summary(db, case, refresh=True)
    old_job = claim("ai")
    assert old_job and old_job.kind == "answer"
    response = client.patch(f"/api/cases/{uploaded}/context", json={"notes": "Updated receipt information"})
    assert response.status_code == 200, response.text
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(args)
        raise ai.AIUnavailable("AI not configured", code="not_configured")

    monkeypatch.setattr(ai, "structured", unavailable)
    pipeline.dispatch(old_job)
    assert calls == []
    with session_scope() as db:
        summary = db.scalar(select(Question).where(Question.case_id == uploaded, Question.kind == "summary"))
        assert summary.revision == 3 and summary.status == "queued" and summary.answer == {}
        assert db.get(Job, old_job.id).status == "done"
    assert run_one("ai")
    with session_scope() as db:
        summary = db.scalar(select(Question).where(Question.case_id == uploaded, Question.kind == "summary"))
        case = db.get(Case, uploaded)
        assert summary.status == "done" and summary.answer["context_hash"] == context_digest(case.context)
    assert len(calls) == 1


def test_question_waiting_for_extraction_does_not_consume_failure_retries(client, uploaded):
    question_id = ask(client, uploaded, "What amount is mentioned?")
    for _ in range(6):
        assert run_one("ai")
        with session_scope() as db:
            job = db.scalar(select(Job).where(Job.kind == "answer"))
            assert job.status == "queued" and job.attempts == 0
            assert job.payload["question_id"] == question_id
            job.available = now() - timedelta(seconds=1)
    drain()
    with session_scope() as db:
        waiting = db.scalar(select(Job).where(Job.key == f"answer:{question_id}"))
        if waiting.status == "queued":
            waiting.available = now() - timedelta(seconds=1)
    drain()
    assert answer(question_id)["claims"]


def test_terminal_failure_of_last_page_still_finalizes_document_and_summary(client, uploaded):
    assert run_one("extract")
    failed_job = claim("extract")
    assert failed_job and failed_job.kind == "extract"
    with session_scope() as db:
        row = db.get(Job, failed_job.id)
        row.status, row.attempts = "dead", 4
        pipeline.mark_failed(db, row)
        page = db.scalar(select(Page).where(Page.document_id == row.payload["document_id"]))
        assert page.status == "failed"
        page.facts = [{"kind": "amount", "value": "Unreliable old extraction"}]
        finalize = db.scalar(select(Job).where(Job.kind == "finalize"))
        assert finalize and finalize.status == "queued"
    drain()
    with session_scope() as db:
        case = db.get(Case, uploaded)
        document = db.get(Document, case.document_id)
        assert document.status == "review_required" and "processing_failed" in document.flags
        assert document.facts == []
        summary = db.scalar(select(Question).where(Question.case_id == uploaded, Question.kind == "summary"))
        assert summary.status == "done"
        assert summary.answer["answer_type"] == "no_evidence"


def test_failed_ocr_with_partial_text_restarts_extraction_instead_of_understanding(client, uploaded):
    drain()
    with session_scope() as db:
        case = db.get(Case, uploaded)
        document_id = case.document_id
        page = db.scalar(select(Page).where(Page.document_id == document_id))
        page.text = "Unreliable partial OCR text."
        page.status, page.method, page.quality = "failed", "ocr", 0.2
        page.flags = ["processing_failed"]
        db.get(Document, document_id).status = "review_required"
    response = client.post(f"/api/cases/{uploaded}/reprocess")
    assert response.status_code == 202, response.text
    with session_scope() as db:
        job = db.scalar(select(Job).where(Job.key == f"reprocess:{document_id}:2:1"))
        assert job.kind == "extract" and job.pool == "extract"
        assert db.scalar(select(Page).where(Page.document_id == document_id)).status == "pending"
    drain()
    with session_scope() as db:
        recovered = db.scalar(select(Page).where(Page.document_id == document_id))
        assert recovered.status == "done" and "supporting bank statement" in recovered.text
        assert "Unreliable partial" not in recovered.text


def test_terminal_failure_of_obsolete_answer_job_preserves_newer_summary_revision(client, uploaded):
    drain()
    with session_scope() as db:
        schedule_summary(db, db.get(Case, uploaded), refresh=True)
    old_job = claim("ai")
    assert old_job and old_job.payload["revision"] == 2
    with session_scope() as db:
        db.get(Job, old_job.id).attempts = 4
    response = client.patch(f"/api/cases/{uploaded}/context", json={"notes": "A newer account of receipt"})
    assert response.status_code == 200, response.text
    retry(old_job, RuntimeError("Terminal failure of obsolete revision"))
    with session_scope() as db:
        summary = db.scalar(select(Question).where(Question.case_id == uploaded, Question.kind == "summary"))
        assert summary.revision == 3 and summary.status == "queued" and summary.answer == {}
        assert db.get(Job, old_job.id).status == "dead"
        queued = db.scalars(select(Job).where(Job.kind == "answer", Job.status == "queued")).all()
        assert len(queued) == 1 and queued[0].payload["revision"] == 3


def test_context_change_during_provider_call_keeps_refreshed_summary_queued(client, uploaded, monkeypatch):
    drain()
    with session_scope() as db:
        schedule_summary(db, db.get(Case, uploaded), refresh=True)
    old_job = claim("ai")
    assert old_job and old_job.payload["revision"] == 2
    calls = []

    def provider(org_id, schema, task, data, **kwargs):
        calls.append(schema)
        if schema is Answer:
            response = client.patch(
                f"/api/cases/{uploaded}/context",
                json={"notes": "Context changed while the model was working"},
            )
            assert response.status_code == 200, response.text
            return Answer.model_validate(
                {
                    "claims": [
                        {
                            "text": "The document mentions INR 12,500.00.",
                            "basis": "document",
                            "evidence": {"page": 1, "quote": "Amount mentioned: INR 12,500.00"},
                            "source_id": None,
                            "source_quote": None,
                        }
                    ],
                    "missing_context": [],
                    "professional_review": True,
                }
            )
        assert schema is Verification
        return Verification(
            supported=True, unsupported_claims=[], legal_interpretation=False, conflicting_dates=False
        )

    monkeypatch.setattr(ai, "structured", provider)
    pipeline.dispatch(old_job)
    assert calls == [Answer, Verification]
    with session_scope() as db:
        summary = db.scalar(select(Question).where(Question.case_id == uploaded, Question.kind == "summary"))
        assert summary.revision == 3 and summary.status == "queued" and summary.answer == {}
        assert db.get(Job, old_job.id).status == "done"
        queued = db.scalars(select(Job).where(Job.kind == "answer", Job.status == "queued")).all()
        assert len(queued) == 1 and queued[0].payload["revision"] == 3
