from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from notice.config import settings
from notice.db import Base
from notice.models import Case, Document, Org, Page, Source, User, now
from notice.passages import split_passages
from notice.retrieval import LexicalIndex, _IndexCache, cosine, expand_query, lexical, retrieve


@pytest.fixture(autouse=True)
def clean_database():
    # These engine tests run in an isolated in-memory store, with no external services.
    yield


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(settings(), "embedding_model", "")
    monkeypatch.setattr(settings(), "reranker_model", "")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="retrieval@example.test", password_hash="unused")
        org = Org(name="First")
        db.add_all([user, org])
        db.flush()
        document = Document(
            org_id=org.id,
            digest="a" * 64,
            version="test",
            object_key="test",
            filename="notice.pdf",
            mime="application/pdf",
            bytes=100,
            page_count=1,
            category="income_tax",
            facts=[],
        )
        db.add(document)
        db.flush()
        case = Case(
            org_id=org.id,
            document_id=document.id,
            created_by=user.id,
            title="Notice",
            expires=now() + timedelta(days=1),
        )
        db.add(case)
        db.flush()
        yield db, org, document, case
    engine.dispose()


def add_page(db, document, text, number=1, embedding=None):
    page = Page(document_id=document.id, number=number, text=text, status="done", embedding=embedding)
    db.add(page)
    db.flush()
    return page


def test_passages_preserve_offsets_and_cover_late_and_unbroken_text():
    text = (
        "  Header.\n\n"
        + "Background line with no action. " * 600
        + "x" * 12000
        + " Last deadline: 31 October 2026."
    )
    spans = split_passages(text)
    assert len(spans) < len(text) // 700
    assert all(span.text == text[span.start : span.end] and len(span.text) <= 1800 for span in spans)
    assert spans[-1].end == len(text)
    assert "Last deadline" in spans[-1].text
    assert all(left.end >= right.start for left, right in zip(spans, spans[1:]))


def test_lexical_does_not_return_stopword_or_substring_false_matches():
    assert (
        lexical("What is the weather?", ["The assessment has been issued.", "Documents are requested."]) == []
    )
    assert lexical("assessment", ["A statement was requested", "Assessment completed"]) == [1]
    assert lexical("TEST-2026-987", ["Reference TEST-2026-001", "Reference TEST-2026-987"])[0] == 1


def test_postings_index_is_reused_without_retokenizing_documents(monkeypatch):
    index = LexicalIndex(["Deadline 31 October 2026", "Amount INR 12500"])
    from notice import retrieval

    original = retrieval.tokens
    calls = []

    def counted(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(retrieval, "tokens", counted)
    assert index.rank("deadline")[0][0] == 0
    assert calls == ["deadline"]


def test_query_expansion_handles_reply_language_without_legal_inference():
    assert lexical(expand_query("When must I respond?"), ["Please reply before 31 October 2026."])
    assert "penalty" not in expand_query("What is requested?")


def test_retrieval_finds_evidence_after_old_page_truncation(store):
    db, org, document, case = store
    text = "Background unrelated material. " * 400 + "\nPlease furnish the missing ledger by 31 October 2026."
    page = add_page(db, document, text)
    hits, _ = retrieve(db, org.id, case.id, "Which ledger is missing?")
    assert hits and "missing ledger" in hits[0].text
    assert hits[0].start > 6500
    assert hits[0].page_id == page.id and hits[0].number == 1
    assert text[hits[0].start : hits[0].end] == hits[0].text


def test_retrieval_returns_no_pages_for_zero_signal(store):
    db, org, document, case = store
    add_page(db, document, "The assessment has been issued by the department.")
    assert retrieve(db, org.id, case.id, "What is the weather?") == ([], [])


@pytest.mark.parametrize("condition", ["other_org", "deleted", "expired", "wrong_document_org"])
def test_retrieval_authorization_blocks_before_index_or_provider(store, monkeypatch, condition):
    db, org, document, case = store
    add_page(db, document, "Secret ledger and deadline.")
    org_id = org.id
    if condition == "other_org":
        org_id = "not-this-org"
    elif condition == "deleted":
        case.deleted = True
    elif condition == "expired":
        case.expires = now() - timedelta(seconds=1)
    else:
        document.org_id = "not-this-org"
    db.flush()
    from notice import retrieval

    def forbidden(*args, **kwargs):
        raise AssertionError("Unauthorized text reached retrieval or a provider")

    monkeypatch.setattr(retrieval._document_indexes, "get", forbidden)
    monkeypatch.setattr(retrieval.ai, "embed", forbidden)
    assert retrieve(db, org_id, case.id, "ledger") == ([], [])


def test_official_source_late_passage_and_temporal_approval_filters(store):
    db, org, document, case = store
    add_page(db, document, "Notice issued.")
    document.facts = [{"kind": "notice_date", "date_iso": "2026-10-20"}]
    source_text = (
        "Background public information. " * 400
        + "\nA special acknowledgement code is available on the portal."
    )
    for suffix, expiry, valid_from, valid_to in (
        ("current", 1, "2026-01-01", None),
        ("future", 1, "2027-01-01", None),
        ("expired", -1, None, None),
        ("old", 1, None, "2025-12-31"),
    ):
        db.add(
            Source(
                url=f"https://www.incometax.gov.in/{suffix}",
                title=suffix,
                digest=suffix,
                category="income_tax",
                text=source_text,
                approved_until=now() + timedelta(days=expiry),
                valid_from=valid_from,
                valid_to=valid_to,
            )
        )
    db.flush()
    _, sources = retrieve(db, org.id, case.id, "special acknowledgement code")
    assert len(sources) == 1 and sources[0].title == "current"
    assert sources[0].start > 6500 and "acknowledgement code" in sources[0].text
    assert source_text[sources[0].start : sources[0].end] == sources[0].text


def test_new_passage_embeddings_find_nonliteral_question_and_reject_model_mismatch(store, monkeypatch):
    db, org, document, case = store
    text = "Background unrelated material. " * 400 + "\nFurnish the supporting ledger."
    spans = split_passages(text)
    page = add_page(
        db,
        document,
        text,
        embedding={
            "model": "test-embedding",
            "passages": [
                {
                    "start": span.start,
                    "end": span.end,
                    "vector": [1.0, 0.0] if idx == len(spans) - 1 else [0.0, 1.0],
                }
                for idx, span in enumerate(spans)
            ],
        },
    )
    monkeypatch.setattr(settings(), "embedding_model", "test-embedding")
    from notice import retrieval

    monkeypatch.setattr(retrieval.ai, "embed", lambda *args: [[1.0, 0.0]])
    hits, _ = retrieve(db, org.id, case.id, "financial reconciliation?")
    assert hits and hits[0].start > 6500 and "supporting ledger" in hits[0].text
    page.embedding = {**page.embedding, "model": "incompatible-model"}
    db.flush()
    assert retrieve(db, org.id, case.id, "financial reconciliation?") == ([], [])


def test_cache_invalidates_changed_content_and_isolates_organizations():
    cache = _IndexCache()
    pages = [SimpleNamespace(id="page", number=1, text="Original ledger")]
    first = cache.get("org-a", "doc", pages)
    assert cache.get("org-a", "doc", pages)[1] is first[1]
    assert cache.get("org-b", "doc", pages)[1] is not first[1]
    pages[0].text = "Updated repayment"
    changed = cache.get("org-a", "doc", pages)
    assert changed[1] is not first[1] and changed[1].rank("repayment")


@pytest.mark.parametrize("vector", [[float("nan"), 0.0], [float("inf"), 1.0], ["bad", 0], [0.0, 0.0]])
def test_invalid_vectors_never_rank(vector):
    assert cosine([1.0, 0.0], vector) == -1.0
