import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import httpx
import pytest
import respx
from sqlalchemy import select

from notice import ai
from notice.config import settings
from notice.db import session_scope
from notice.models import AICall, Budget, now
from notice.schemas import Answer


@pytest.fixture
def provider(account, monkeypatch):
    for key, value in {
        "ai_api_key": "test-provider-key",
        "ai_model": "test-model",
        "embedding_model": "test-embedding",
        "ai_price_input_per_million": 1.0,
        "ai_price_output_per_million": 2.0,
        "org_daily_tokens": 200000,
        "org_daily_cost_usd": 10.0,
    }.items():
        monkeypatch.setattr(settings(), key, value)
    return account["orgs"][0]["id"]


def completion_response(*, content=None, finish="stop", refusal=None, usage=None):
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "finish_reason": finish,
                    "message": {
                        "content": json.dumps(
                            content
                            if content is not None
                            else {
                                "claims": [],
                                "missing_context": [],
                                "professional_review": True,
                            }
                        ),
                        "refusal": refusal,
                    },
                }
            ],
            "usage": usage
            if usage is not None
            else {
                "prompt_tokens": 90,
                "completion_tokens": 30,
                "total_tokens": 120,
            },
        },
    )


@respx.mock
def test_actual_usage_releases_unused_allowance_and_settlement_is_idempotent(provider):
    respx.post("https://api.openai.com/v1/chat/completions").mock(return_value=completion_response())
    ai.structured(provider, Answer, "Explain explicit facts", {"document": "Synthetic notice."})
    with session_scope() as db:
        budget, call = db.scalar(select(Budget)), db.scalar(select(AICall))
        assert budget.tokens == call.actual_tokens == 120
        assert budget.cost == pytest.approx(0.00015)
        assert call.reserved_tokens > budget.tokens
        call_id = call.id
    ai.complete(call_id, "done", usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
    with session_scope() as db:
        assert db.scalar(select(Budget)).tokens == 120


def test_settlement_serializes_with_new_reservations(provider):
    first = ai.reserve(provider, "test-model", 1000, 500)

    def settle():
        ai.complete(first, "done", usage={"prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 100})

    with ThreadPoolExecutor(max_workers=3) as workers:
        jobs = [
            workers.submit(settle),
            workers.submit(settle),
            workers.submit(ai.reserve, provider, "test-model", 500, 100),
        ]
        for job in jobs:
            job.result()
    with session_scope() as db:
        budget = db.scalar(select(Budget))
        assert budget.tokens == 700
        assert budget.cost == pytest.approx(0.00082)


def test_settlement_uses_original_day_when_request_crosses_midnight(provider, monkeypatch):
    yesterday = now() - timedelta(days=1)
    monkeypatch.setattr(ai, "now", lambda: yesterday)
    call_id = ai.reserve(provider, "test-model", 900, 200)
    monkeypatch.setattr(ai, "now", now)
    ai.complete(call_id, "done", usage={"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60})
    with session_scope() as db:
        budget = db.scalar(select(Budget))
        assert budget.day == yesterday.date().isoformat() and budget.tokens == 60


@respx.mock
def test_timeout_makes_one_request_and_preserves_unknown_charge(provider):
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        side_effect=httpx.ReadTimeout("timeout")
    )
    with pytest.raises(ai.AIUnavailable) as failure:
        ai.structured(provider, Answer, "Explain", {"document": "Synthetic notice."})
    assert failure.value.code == "provider_timeout" and failure.value.retryable
    assert route.call_count == 1
    with session_scope() as db:
        budget, call = db.scalar(select(Budget)), db.scalar(select(AICall))
        assert call.status == "failed" and call.actual_tokens is None
        assert budget.tokens == call.reserved_tokens and budget.cost == call.reserved_cost


@respx.mock
@pytest.mark.parametrize(
    "status, code, retryable",
    [
        (401, "provider_auth", False),
        (400, "provider_rejected", False),
        (429, "provider_rate_limit", True),
        (503, "provider_unavailable", True),
    ],
)
def test_http_failures_have_safe_actionable_diagnostics(provider, status, code, retryable):
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(status, headers={"retry-after": "23"}, text="PRIVATE PROVIDER ERROR")
    )
    with pytest.raises(ai.AIUnavailable) as failure:
        ai.structured(provider, Answer, "Explain", {"document": "Synthetic notice."})
    assert failure.value.code == code and failure.value.retryable is retryable
    assert "PRIVATE" not in str(failure.value) and route.call_count == 1
    if status == 429:
        assert failure.value.retry_after == 23


@respx.mock
@pytest.mark.parametrize(
    "finish, refusal, code",
    [
        ("length", None, "output_truncated"),
        ("stop", "Cannot process", "provider_refused"),
    ],
)
def test_refused_and_truncated_results_never_reach_the_workspace(provider, finish, refusal, code):
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=completion_response(finish=finish, refusal=refusal)
    )
    with pytest.raises(ai.AIUnavailable) as failure:
        ai.structured(provider, Answer, "Explain", {"document": "Synthetic notice."})
    assert failure.value.code == code and not failure.value.retryable
    with session_scope() as db:
        assert db.scalar(select(AICall)).status == "failed"
        assert db.scalar(select(Budget)).tokens == 120  # Billed output is still accounted for.


@respx.mock
def test_malformed_usage_does_not_release_reserved_cost(provider):
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=completion_response(
            usage={"prompt_tokens": -1, "completion_tokens": 30, "total_tokens": 29}
        )
    )
    ai.structured(provider, Answer, "Explain", {"document": "Synthetic notice."})
    with session_scope() as db:
        budget, call = db.scalar(select(Budget)), db.scalar(select(AICall))
        assert budget.tokens == call.reserved_tokens and budget.cost == call.reserved_cost


@respx.mock
def test_embeddings_are_reordered_by_unique_provider_indexes(provider):
    respx.post("https://api.openai.com/v1/embeddings").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}],
                "usage": {"prompt_tokens": 8, "total_tokens": 8},
            },
        )
    )
    assert ai.embed(provider, ["reply deadline", "amount due"]) == [[1, 0], [0, 1]]
    with session_scope() as db:
        assert db.scalar(select(Budget)).tokens == 8


@respx.mock
@pytest.mark.parametrize(
    "rows",
    [
        [{"index": 0, "embedding": [1]}, {"index": 0, "embedding": [2]}],
        [{"index": 0, "embedding": [1]}, {"index": 1, "embedding": [1, 2]}],
        [{"index": 0, "embedding": [0, 0]}, {"index": 1, "embedding": [1, 0]}],
        [{"index": 0, "embedding": [True]}, {"index": 1, "embedding": [1]}],
    ],
)
def test_malformed_embedding_vectors_fail_closed(provider, rows):
    respx.post("https://api.openai.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": rows})
    )
    with pytest.raises(ai.AIUnavailable) as failure:
        ai.embed(provider, ["reply deadline", "amount due"])
    assert failure.value.code == "invalid_embeddings"


def test_nonfinite_embedding_values_are_rejected():
    for value in (float("nan"), float("inf"), 10**400):
        with pytest.raises(ai.AIUnavailable, match="values"):
            ai._vectors({"data": [{"index": 0, "embedding": [value]}]}, 1)


def test_concurrency_and_budget_fail_before_provider_requests(provider, monkeypatch):
    monkeypatch.setattr(settings(), "org_ai_concurrency", 1)
    first = ai.reserve(provider, "test-model", 100, 50)
    with pytest.raises(ai.AIUnavailable) as failure:
        ai.reserve(provider, "test-model", 100, 50)
    assert failure.value.code == "concurrency_limit" and failure.value.retryable
    ai.complete(first, "failed")
    monkeypatch.setattr(settings(), "org_daily_tokens", 151)
    with pytest.raises(ai.AIUnavailable) as failure:
        ai.reserve(provider, "test-model", 100, 50)
    assert failure.value.code == "budget_exhausted" and not failure.value.retryable


def test_shared_client_pool_is_reused_without_caching_authorization():
    client = ai._client(65)
    assert client is ai._client(65)
    assert "authorization" not in client.headers


def test_model_tokenizer_does_not_charge_json_bytes_as_tokens():
    tiktoken = pytest.importorskip("tiktoken")
    document = "Please reply before 31 October 2026. " * 500
    encoder = tiktoken.encoding_for_model("gpt-4o-mini")
    assert ai.token_count(document, "gpt-4o-mini") == len(encoder.encode(document))
    assert ai.token_count(document, "gpt-4o-mini") < len(document.encode()) / 2
