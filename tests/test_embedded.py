import json
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from conftest import signup
from notice import embedded, serve, worker
from notice.api import app
from notice.config import settings
from notice.db import session_scope
from notice.embedded import EmbeddedProcessor
from notice.jobs import claim, enqueue, owns
from notice.models import Job, now


def eventually(check, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        threading.Event().wait(0.03)
    raise AssertionError("Background processing did not complete before the test deadline")


def completed_case(browser, case_id):
    result = browser.get(f"/api/cases/{case_id}").json()
    return result if result.get("status") in {"ready", "review_required"} else None


def completed_answer(browser, case_id):
    result = browser.get(f"/api/cases/{case_id}").json()
    questions = result.get("questions", [])
    return questions[0] if questions and questions[0]["status"] == "done" else None


def test_default_api_does_not_start_embedded_processing(client):
    assert settings().embedded_worker is False
    assert getattr(app.state, "embedded_processor", None) is None
    assert client.get("/health/live").status_code == 200


def test_database_dates_use_utc_even_when_host_and_database_defaults_differ():
    with session_scope() as db:
        assert db.scalar(text("SHOW timezone")) == "UTC"


def test_embedded_upload_and_qa_use_existing_engine_without_external_workers(monkeypatch, pdf_bytes):
    monkeypatch.setattr(settings(), "embedded_worker", True)
    started, release = threading.Event(), threading.Event()
    original = worker.dispatch

    def dispatch(job):
        if job.kind == "inspect":
            started.set()
            if not release.wait(10):
                raise RuntimeError("Test did not release job")
        return original(job)

    monkeypatch.setattr(worker, "dispatch", dispatch)
    with TestClient(app, base_url="http://localhost:8000") as browser:
        processor = app.state.embedded_processor
        signup(browser)
        response = browser.post("/api/cases", files={"file": ("notice.pdf", pdf_bytes, "application/pdf")})
        assert response.status_code == 202
        case_id = response.json()["id"]
        try:
            assert started.wait(5)
            assert browser.get("/health/live").status_code == 200
            assert browser.get("/health/ready").status_code == 200
            assert not release.is_set()  # HTTP served while a handler is blocked.
        finally:
            release.set()
        case = eventually(lambda: completed_case(browser, case_id))
        assert case["pages"][0]["method"] == "digital"
        assert any(f["kind"] == "deadline" and f["date_iso"] == "2026-10-31" for f in case["facts"])
        assert browser.post(
            f"/api/cases/{case_id}/questions", json={"question": "What amount is mentioned?"}
        ).status_code == 202
        question = eventually(lambda: completed_answer(browser, case_id))
        assert "12,500" in " ".join(claim["text"] for claim in question["answer"]["claims"])
        assert question["answer"]["professional_review"] is False
        assert question["answer"]["claims"][0]["evidence"]["page"] == 1
    assert not processor.running
    assert app.state.embedded_processor is None


def test_embedded_mode_recovers_expired_jobs_after_restart(monkeypatch):
    with session_scope() as db:
        enqueue(db, "before-restart", "extract", "inspect", {"document_id": "deleted-document"})
    old = claim("extract")
    with session_scope() as db:
        db.get(Job, old.id).lease_until = now() - timedelta(seconds=1)
    monkeypatch.setattr(settings(), "embedded_worker", True)

    def recovered():
        with session_scope() as db:
            row = db.get(Job, old.id)
            return row.status == "done" and row.attempts == 2 and row.owner != old.owner

    with TestClient(app, base_url="http://localhost:8000"):
        eventually(recovered)
        with session_scope() as db:
            assert not owns(db, old)


def test_round_robin_polls_all_pools_serially_and_stops(monkeypatch):
    seen = []
    identities = set()
    visited = threading.Event()
    processor = EmbeddedProcessor(poll_seconds=0.01)

    def run(pool):
        seen.append(pool)
        identities.add(threading.get_ident())
        if len(seen) == len(embedded.POOLS):
            visited.set()
        return False

    monkeypatch.setattr(embedded, "run_one", run)
    processor.start()
    try:
        assert visited.wait(3)
        assert seen[:5] == list(embedded.POOLS)
        assert len(identities) == 1
        assert threading.get_ident() not in identities
        with pytest.raises(RuntimeError, match="already started"):
            processor.start()
    finally:
        assert processor.stop()
    before = len(seen)
    assert processor.stop()
    assert len(seen) == before
    with session_scope() as db:
        assert len(db.scalars(select(Job).where(Job.kind == "cleanup")).all()) == 1


def test_poll_failure_is_redacted_and_does_not_starve_other_pools(monkeypatch, capsys):
    recovered = threading.Event()
    processor = EmbeddedProcessor(poll_seconds=0.01)
    failures = 0

    def run(pool):
        nonlocal failures
        if pool == "mail" and not failures:
            failures += 1
            raise RuntimeError("secret database password")
        if pool == "ai" and failures:
            recovered.set()
        return False

    monkeypatch.setattr(embedded, "run_one", run)
    processor.start()
    try:
        assert recovered.wait(3)
    finally:
        assert processor.stop()
    logs = capsys.readouterr().out
    assert "secret database password" not in logs
    events = [json.loads(line) for line in logs.splitlines()]
    assert {"event": "embedded_poll_failed", "error": "RuntimeError", "pool": "mail"} in events


def test_maintenance_enqueue_recovers_from_transient_database_failure(monkeypatch):
    processor = EmbeddedProcessor(poll_seconds=0.01)
    original = processor._schedule_maintenance
    calls = 0
    recovered = threading.Event()

    def schedule():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("sensitive connection details")
        original()
        recovered.set()

    monkeypatch.setattr(processor, "_schedule_maintenance", schedule)

    def cleanup_done():
        with session_scope() as db:
            row = db.scalar(select(Job).where(Job.kind == "cleanup"))
            return row is not None and row.status == "done"

    processor.start()
    try:
        assert recovered.wait(3)
        eventually(cleanup_done)
    finally:
        assert processor.stop()
    with session_scope() as db:
        assert db.scalar(select(Job).where(Job.kind == "cleanup")).status == "done"


def test_shutdown_leaves_running_job_lease_owned_until_handler_exits(monkeypatch):
    started, release = threading.Event(), threading.Event()
    processor = EmbeddedProcessor(poll_seconds=0.01)

    def run(pool):
        started.set()
        release.wait(5)
        return True

    monkeypatch.setattr(embedded, "run_one", run)
    processor.start()
    try:
        assert started.wait(3)
        assert processor.stop(timeout=0.01) is False
    finally:
        release.set()
        assert processor.stop()


def test_service_entry_point_applies_migrations_then_binds_platform_port(monkeypatch):
    events = []
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.setattr(serve.command, "upgrade", lambda cfg, rev: events.append(("migrate", cfg, rev)))
    monkeypatch.setattr(serve.uvicorn, "run", lambda target, **kwargs: events.append((target, kwargs)))
    serve.main(["--migrate"])
    assert events[0][0] == "migrate" and events[0][2] == "head"
    assert Path(events[0][1].get_main_option("script_location")).is_absolute()
    assert events[1] == (
        "notice.api:app",
        dict(host="0.0.0.0", port=10000, workers=1, lifespan="on", access_log=False, proxy_headers=False),
    )


@pytest.mark.parametrize("port", ["abc", "0", "65536"])
def test_service_entry_point_rejects_invalid_port(monkeypatch, port):
    monkeypatch.setenv("PORT", port)
    with pytest.raises(SystemExit) as error:
        serve.main([])
    assert error.value.code == 2


def test_migration_failure_does_not_start_server(monkeypatch):
    def failed(*args):
        raise RuntimeError("Migration failed")

    monkeypatch.setattr(serve.command, "upgrade", failed)
    monkeypatch.setattr(serve.uvicorn, "run", lambda *a, **kw: pytest.fail("Server must not start"))
    with pytest.raises(RuntimeError, match="Migration failed"):
        serve.main(["--migrate"])


def test_real_scan_and_qa_complete_inside_api_without_standalone_workers(monkeypatch):
    from test_notice_understanding import FIXTURES, native_ocr

    native_ocr(monkeypatch)
    monkeypatch.setattr(settings(), "embedded_worker", True)
    with TestClient(app, base_url="http://localhost:8000") as browser:
        signup(browser)
        content = (FIXTURES / "revenue-notification-scan.png").read_bytes()
        response = browser.post("/api/cases", files={"file": ("notification.png", content, "image/png")})
        assert response.status_code == 202
        case_id = response.json()["id"]
        result = eventually(lambda: completed_case(browser, case_id), seconds=100)
        assert result["pages"][0]["method"] == "ocr"
        assert any(f["kind"] == "deadline" and f["date_iso"] == "2026-09-22" for f in result["facts"])
        assert browser.post(
            f"/api/cases/{case_id}/questions", json={"question": "who gave this????"}
        ).status_code == 202
        question = eventually(lambda: completed_answer(browser, case_id))
        assert "Neeraj" in " ".join(claim["text"] for claim in question["answer"]["claims"])
        assert not question["answer"]["professional_review"]
        assert question["answer"]["missing_context"] == []
