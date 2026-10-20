from datetime import timedelta

from sqlalchemy import select

from notice.db import session_scope
from notice.models import Case, Question, Source, now
from notice.workspace import context_digest
from conftest import drain


def test_automatic_summary_refreshes_after_context_changes(client, uploaded):
    drain()
    first = client.get(f"/api/cases/{uploaded}").json()["explanation"]
    assert first and first["status"] == "done" and not first["stale"]
    assert first["answer"]["coverage"]["total"] == 1
    assert (
        client.patch(f"/api/cases/{uploaded}/context", json={"notes": "Additional context"}).status_code
        == 200
    )
    assert client.get(f"/api/cases/{uploaded}").json()["explanation"]["status"] == "queued"
    drain()
    refreshed = client.get(f"/api/cases/{uploaded}").json()["explanation"]
    assert refreshed["status"] == "done" and not refreshed["stale"]


def test_expired_source_marks_existing_answer_stale(client, uploaded):
    drain()
    with session_scope() as db:
        case = db.get(Case, uploaded)
        source = Source(
            url="https://www.incometax.gov.in/test",
            title="Example public source",
            category="income_tax",
            text="test public source",
            digest="test",
            approved_until=now() - timedelta(seconds=1),
        )
        db.add(source)
        db.flush()
        summary = db.scalar(select(Question).where(Question.case_id == case.id, Question.kind == "summary"))
        summary.answer = {
            "context_hash": context_digest(case.context),
            "sources": {source.id: {"url": source.url}},
            "mode": "verified_ai",
            "claims": [],
            "professional_review": True,
        }
    assert client.get(f"/api/cases/{uploaded}").json()["explanation"]["stale"]
