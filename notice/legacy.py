"""Upgrade application-generated labels and queued messages to English."""

import hashlib
import json
from pathlib import Path
from cryptography.exceptions import InvalidTag

from sqlalchemy import JSON, bindparam, text
from sqlalchemy.dialects.postgresql import insert

from .crypto import seal, unseal
from .language import english_narrative
from .models import Job, now, uid
from .workspace import SUMMARY


def legacy_map():
    return json.loads((Path(__file__).parent / "data" / "legacy-labels.json").read_text(encoding="utf-8"))


def translate_label(value, mapping):
    if not isinstance(value, str):
        return value
    digest = hashlib.sha256(value.encode()).hexdigest()
    if digest in mapping["labels"]:
        return mapping["labels"][digest]
    for item in mapping["prefixes"]:
        prefix = value[: item["length"]]
        if hashlib.sha256(prefix.encode()).hexdigest() == item["sha256"]:
            return item["replacement"] + value[item["length"] :]
    return value


def translate_known(value, mapping):
    if isinstance(value, str):
        return translate_label(value, mapping)
    if isinstance(value, list):
        return [translate_known(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: translate_known(item, mapping) for key, item in value.items()}
    return value


def migrate_english_v1(connection):
    mapping = legacy_map()
    for table, field in (("organizations", "name"), ("actions", "title")):
        for row in connection.execute(text(f"SELECT id, {field} FROM {table}")).mappings():
            translated = translate_label(row[field], mapping)
            if translated != row[field]:
                connection.execute(
                    text(f"UPDATE {table} SET {field}=:value WHERE id=:id"),
                    {"id": row["id"], "value": translated},
                )
    query = text(
        "SELECT q.id, q.kind, q.question, q.answer, c.org_id FROM questions q "
        "JOIN cases c ON c.id=q.case_id WHERE c.deleted=false"
    )
    update = text("UPDATE questions SET question=:question, answer=:answer, status=:status WHERE id=:id")
    update = update.bindparams(bindparam("answer", type_=JSON))
    for row in connection.execute(query).mappings():
        answer = translate_known(row["answer"] or {}, mapping)
        question = SUMMARY if row["kind"] == "summary" else row["question"]
        narratives = list(answer.get("missing_context", []))
        if answer.get("mode") == "verified_ai":
            narratives += [claim.get("text", "") for claim in answer.get("claims", [])]
        regenerate = any(not english_narrative(value) for value in narratives)
        if regenerate:
            answer = {}
            connection.execute(
                text(
                    "UPDATE jobs SET status='cancelled', payload='{}' "
                    "WHERE kind='answer' AND status IN ('queued','running') AND payload->>'question_id'=:id"
                ),
                {"id": row["id"]},
            )
            connection.execute(
                insert(Job)
                .values(
                    id=uid(),
                    key=f"english-answer:{row['id']}:v1",
                    org_id=row["org_id"],
                    pool="ai",
                    kind="answer",
                    payload={"question_id": row["id"]},
                    status="queued",
                    attempts=0,
                    available=now(),
                    error="",
                    created=now(),
                )
                .on_conflict_do_nothing(index_elements=[Job.key])
            )
        status = "queued" if regenerate else None
        if question != row["question"] or answer != row["answer"]:
            if status is None:
                status = connection.scalar(
                    text("SELECT status FROM questions WHERE id=:id"), {"id": row["id"]}
                )
            connection.execute(
                update, {"id": row["id"], "question": question, "answer": answer, "status": status}
            )
    mail_update = text("UPDATE jobs SET payload=:payload WHERE id=:id").bindparams(
        bindparam("payload", type_=JSON)
    )
    for row in connection.execute(
        text("SELECT id,payload FROM jobs WHERE kind='mail' AND status='queued'")
    ).mappings():
        if not row["payload"].get("sealed"):
            continue
        try:
            payload = unseal(row["payload"]["sealed"])
        except (ValueError, KeyError, InvalidTag):
            connection.execute(
                text("UPDATE jobs SET status='cancelled', payload='{}' WHERE id=:id"), {"id": row["id"]}
            )
            continue
        if english_narrative(payload.get("body", "")) and english_narrative(payload.get("subject", "")):
            continue
        links = [word for word in payload.get("body", "").split() if word.startswith(("https://", "http://"))]
        if links:
            payload["subject"] = "Email verification" if "#verify=" in links[-1] else "Password reset"
            payload["body"] = (
                "Open this link within 30 minutes. Ignore this email if you did not request it.\n\n"
                + links[-1]
            )
            connection.execute(mail_update, {"id": row["id"], "payload": {"sealed": seal(payload)}})
