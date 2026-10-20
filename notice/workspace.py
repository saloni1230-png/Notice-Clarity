import hashlib
import json

from sqlalchemy import select

from .jobs import enqueue
from .models import Question

SUMMARY = "Explain this notice in plain English. Identify what the document requests, the dates and amounts it states, and any missing context or professional review needed. Do not infer legal conclusions."


def context_digest(context):
    return hashlib.sha256(json.dumps(context, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def schedule_summary(db, case):
    row = db.scalar(select(Question).where(Question.case_id == case.id, Question.kind == "summary"))
    if not row:
        row = Question(case_id=case.id, question=SUMMARY, kind="summary")
        db.add(row)
        db.flush()
        enqueue(db, f"answer:{row.id}", "ai", "answer", {"question_id": row.id}, case.org_id)
