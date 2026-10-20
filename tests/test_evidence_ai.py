import json
from datetime import timedelta

import httpx
import pytest
import respx
from sqlalchemy import select

from notice import ai
from notice.config import settings
from notice.db import session_scope
from notice.evidence import extract_dates, verify_fact, verify_quote
from notice.models import AICall, Budget, Case, Page, Source, now
from notice.retrieval import cosine, fuse, lexical, retrieve
from notice.schemas import Answer, Evidence, Fact
from conftest import drain


def enable_ai(monkeypatch):
    cfg = settings()
    for key, value in {
        "ai_api_key": "fake-test-key",
        "ai_model": "test-model",
        "ai_price_input_per_million": 1,
        "ai_price_output_per_million": 2,
        "org_daily_tokens": 500000,
        "org_daily_cost_usd": 10,
    }.items():
        monkeypatch.setattr(cfg, key, value)


def response_for(content):
    return httpx.Response(
        200,
        json={
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}],
            "usage": {"total_tokens": 120},
        },
    )


def test_unicode_quote_and_ambiguous_date_validation():
    assert verify_quote("reply   31 October 2026 reply before", "reply 31 October 2026")
    assert not verify_quote("Pay INR 10", "Pay INR 10000")
    assert extract_dates("03/04/2026") == [("03/04/2026", None)]
    assert extract_dates("31/10/2026")[0][1] == "2026-10-31"
    assert extract_dates("31/02/2026")[0][1] is None


def test_numeric_fact_cannot_invent_amount_or_normalize_ambiguous_date():
    page = Page(number=1, text="Reply before 03/04/2026. Pay INR 10.")
    fact = Fact(
        kind="deadline",
        value="03/04/2026",
        evidence=Evidence(page=1, quote="Reply before 03/04/2026."),
        confidence=0.99,
        date_iso="2026-04-03",
    )
    assert not verify_fact(fact, {1: page})
    fact = Fact(
        kind="amount", value="INR 10000", evidence=Evidence(page=1, quote="Pay INR 10."), confidence=0.99
    )
    assert not verify_fact(fact, {1: page})


def test_english_lexical_and_rank_fusion():
    assert lexical("deadline", ["Bank name", "The reply deadline is tomorrow"]) == [1]
    assert fuse([1, 2], [2, 1])[0] in {1, 2}
    assert cosine([1, 0], [1, 0]) == 1


@respx.mock
def test_structured_output_and_budget_reservation(account, monkeypatch):
    enable_ai(monkeypatch)
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=response_for(
            {
                "claims": [],
                "missing_context": ["When did you receive the notice?"],
                "professional_review": True,
            }
        )
    )
    result = ai.structured(account["orgs"][0]["id"], Answer, "test", {"document": "untrusted"})
    assert result.professional_review and route.called
    request = json.loads(route.calls[0].request.content)
    assert request["response_format"]["json_schema"]["strict"]
    with session_scope() as db:
        budget = db.scalar(select(Budget))
        call = db.scalar(select(AICall))
        assert budget.tokens > 0 and budget.cost > 0 and call.actual_tokens == 120


@respx.mock
def test_malformed_provider_output_fails_closed(account, monkeypatch):
    enable_ai(monkeypatch)
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=response_for({"invented": "answer"})
    )
    with pytest.raises(ai.AIUnavailable):
        ai.structured(account["orgs"][0]["id"], Answer, "test", {"document": "data"})
    with session_scope() as db:
        assert db.scalar(select(AICall)).status == "failed"


def test_budget_is_enforced_before_provider_call(account, monkeypatch):
    enable_ai(monkeypatch)
    monkeypatch.setattr(settings(), "org_daily_tokens", 5)
    with pytest.raises(ai.AIUnavailable, match="budget"):
        ai.structured(account["orgs"][0]["id"], Answer, "test", {"document": "test"})


def test_source_freshness_and_temporal_applicability(client, uploaded):
    drain()
    with session_scope() as db:
        case = db.get(Case, uploaded)
        for title, approval, start in [
            ("fresh", now() + timedelta(days=1), "2026-01-01"),
            ("stale", now() - timedelta(days=1), None),
            ("future", now() + timedelta(days=1), "2027-01-01"),
        ]:
            db.add(
                Source(
                    url="https://www.incometax.gov.in/" + title,
                    title=title,
                    category="income_tax",
                    digest=title,
                    text="notice amount income tax reply date",
                    approved_until=approval,
                    valid_from=start,
                )
            )
        db.flush()
        _, sources = retrieve(db, case.org_id, case.id, "notice amount")
        assert [s.title for s in sources] == ["fresh"]


def test_unsupported_legal_interpretation_is_withheld(client, uploaded, monkeypatch):
    drain()
    from notice.schemas import Verification

    def fake(org, schema, task, data, **kwargs):
        if schema is Answer:
            return Answer.model_validate(
                {
                    "claims": [
                        {
                            "text": "You must pay this amount immediately.",
                            "basis": "document",
                            "evidence": {"page": 1, "quote": "INR 12,500.00"},
                            "source_id": None,
                            "source_quote": None,
                        }
                    ],
                    "missing_context": [],
                    "professional_review": False,
                }
            )
        return Verification(
            supported=False, unsupported_claims=[0], legal_interpretation=True, conflicting_dates=False
        )

    monkeypatch.setattr(ai, "structured", fake)
    assert (
        client.post(f"/api/cases/{uploaded}/questions", json={"question": "What amount?"}).status_code == 202
    )
    drain()
    q = client.get(f"/api/cases/{uploaded}").json()["questions"][0]
    assert q["answer"]["mode"] == "extracts"
    assert all("must pay" not in claim["text"] for claim in q["answer"]["claims"])
