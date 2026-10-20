"""Persist completed model chunks so worker retries do not repeat successful calls."""

import hashlib
import json

from .config import settings
from .db import session_scope
from .jobs import owns
from .models import AnalysisChunk, Page


def key(page, kind, text, model):
    identity = [page.id, kind, text, model, settings().pipeline_version]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


def read(chunk_id):
    with session_scope() as db:
        row = db.get(AnalysisChunk, chunk_id)
        return row.result if row else None


def write(job, page_id, chunk_id, kind, result):
    with session_scope() as db:
        if not owns(db, job) or not db.get(Page, page_id):
            return False
        if not db.get(AnalysisChunk, chunk_id):
            db.add(AnalysisChunk(id=chunk_id, page_id=page_id, kind=kind, result=result))
        return True
