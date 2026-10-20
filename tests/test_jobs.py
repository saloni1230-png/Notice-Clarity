from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import select

from notice.db import session_scope
from notice.jobs import claim, enqueue, owns, retry
from notice.models import Job, now


def test_concurrent_claims_are_exclusive():
    with session_scope() as db:
        for i in range(12):
            enqueue(db, f"test:{i}", "extract", "test", {})
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = list(pool.map(lambda _: claim("extract"), range(12)))
    assert len({job.id for job in jobs}) == 12


def test_expired_lease_is_reclaimed_and_stale_owner_fenced():
    with session_scope() as db:
        enqueue(db, "one", "extract", "test", {})
    old = claim("extract")
    with session_scope() as db:
        db.get(Job, old.id).lease_until = now() - timedelta(seconds=1)
    new = claim("extract")
    assert old.id == new.id and old.owner != new.owner and new.attempts == 2
    with session_scope() as db:
        assert owns(db, old) is False
        assert owns(db, new) is True


def test_idempotent_enqueue_and_dead_letter():
    with session_scope() as db:
        enqueue(db, "unique", "extract", "test", {"document_id": "no-document"})
        enqueue(db, "unique", "extract", "test", {"document_id": "no-document"})
    for _ in range(4):
        job = claim("extract")
        retry(job, RuntimeError("do not persist this sensitive detail"))
        with session_scope() as db:
            row = db.get(Job, job.id)
            row.available = now() - timedelta(seconds=1)
    with session_scope() as db:
        row = db.scalar(select(Job))
        assert row.status == "dead" and row.error == "RuntimeError"
        assert row.payload["document_id"] == "no-document"
