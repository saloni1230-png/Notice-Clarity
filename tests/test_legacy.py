import hashlib
from sqlalchemy import select

from notice import legacy
from notice.crypto import seal, unseal
from notice.db import session_scope
from notice.models import Action, Case, Job, Org, Question


def test_legacy_labels_are_translated_without_overwriting_user_context(uploaded, monkeypatch):
    old = "Legacy workspace"
    mapping = {"labels": {hashlib.sha256(old.encode()).hexdigest(): "My workspace"}, "prefixes": []}
    monkeypatch.setattr(legacy, "legacy_map", lambda: mapping)
    with session_scope() as db:
        case = db.get(Case, uploaded)
        db.get(Org, case.org_id).name = old
        case.context = {"notes": "User-supplied context stays unchanged"}
        db.add(Action(case_id=case.id, title="User-created task"))
        db.flush()
        legacy.migrate_english_v1(db.connection())
        db.expire_all()
        assert db.get(Org, case.org_id).name == "My workspace"
        assert db.get(Case, uploaded).context["notes"] == "User-supplied context stays unchanged"
        assert db.scalar(select(Action).where(Action.title == "User-created task"))


def test_legacy_generated_answers_are_requeued_in_english(uploaded):
    with session_scope() as db:
        question = Question(
            case_id=uploaded,
            question="What is the deadline?",
            status="done",
            answer={"mode": "verified_ai", "missing_context": [], "claims": [{"text": chr(0x03B1) * 4}]},
        )
        db.add(question)
        db.flush()
        question_id = question.id
        legacy.migrate_english_v1(db.connection())
        db.expire_all()
        current = db.get(Question, question_id)
        assert current.status == "queued" and current.answer == {}
        queued = db.scalar(select(Job).where(Job.key == f"english-answer:{question_id}:v1"))
        assert queued.payload["question_id"] == question_id


def test_pending_mail_is_rewritten_in_english_and_stays_encrypted():
    link = "http://localhost:65030/#verify=test-token"
    with session_scope() as db:
        job = Job(
            key="legacy-mail",
            pool="mail",
            kind="mail",
            payload={
                "sealed": seal(
                    {
                        "to": "person@example.com",
                        "subject": chr(0x03B1) * 4,
                        "body": chr(0x03B1) * 4 + "\n\n" + link,
                    }
                )
            },
        )
        db.add(job)
        db.flush()
        job_id = job.id
        legacy.migrate_english_v1(db.connection())
        db.expire_all()
        current = db.get(Job, job_id)
        decoded = unseal(current.payload["sealed"])
        assert decoded["subject"] == "Email verification"
        assert decoded["body"].startswith("Open this link within 30 minutes.")
        assert decoded["body"].endswith(link)
        assert link not in current.payload["sealed"]
