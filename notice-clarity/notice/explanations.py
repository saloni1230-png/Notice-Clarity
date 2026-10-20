"""Useful document-only answers with explicit provenance when a model is unavailable."""

import re

from .evidence import verify_fact
from .retrieval import lexical
from .schemas import Fact

LABELS = {
    "issuer": "Issuer named in the document",
    "reference": "Document reference",
    "notice_date": "Issue date stated",
    "deadline": "Response or hearing wording stated",
    "amount": "Amount mentioned",
    "section": "Provision referenced by the document",
    "recipient": "Recipient named in the document",
    "request": "Information or action requested",
}
PRIORITY = ("issuer", "reference", "notice_date", "deadline", "amount", "request", "section", "recipient")


def select_facts(facts, question, summary=False, max_chars=14000):
    """Bound prompts while balancing types and distant pages; never truncate evidence quotes."""
    if summary:
        groups = []
        for kind in PRIORITY:
            rows = [f for f in facts if f["kind"] == kind]
            # Alternate front/back so repeated early-page dates cannot hide a late-page request.
            ordered = []
            left, right = 0, len(rows) - 1
            while left <= right:
                ordered.append(rows[left])
                if left != right:
                    ordered.append(rows[right])
                left, right = left + 1, right - 1
            groups.append(ordered)
        ranked = [
            group[i] for i in range(max(map(len, groups), default=0)) for group in groups if len(group) > i
        ]
    else:
        kinds = requested_kinds(question)
        ranking = lexical(question, [f["evidence"]["quote"] for f in facts])
        ranked = [facts[i] for i in ranking]
        ranked = [f for f in ranked if f["kind"] in kinds] + [f for f in ranked if f["kind"] not in kinds]
    selected, size = [], 0
    for fact in ranked:
        cost = len(fact["value"]) + len(fact["evidence"]["quote"]) + 160
        if size + cost <= max_chars:
            selected.append(fact)
            size += cost
        if len(selected) == 40:
            break
    return selected


def requested_kinds(question):
    patterns = (
        (
            r"deadline|last date|due date|time limit|respond|reply.*(?:by|before)|how (?:long|many days)",
            {"deadline"},
        ),
        (r"\bwhen\b|\bdates?\b", {"deadline", "notice_date"}),
        (r"amount|how much|\bpay\b|payment|penalty|demand", {"amount"}),
        (r"reference|\bdin\b|\bcnr\b|notice number", {"reference"}),
        (r"\bwho\b|issuer|issued by|department", {"issuer", "recipient"}),
        (r"documents?|provide|submit|requested|required|ask.*(?:me|us)", {"request"}),
        (r"section|provision", {"section"}),
    )
    return set().union(*(kinds for pattern, kinds in patterns if re.search(pattern, question, re.I)))


def missing_context(context, facts, summary=False):
    questions = []
    if any(f["kind"] == "deadline" and not f.get("date_iso") for f in facts):
        questions.append("Confirm the response wording and any ambiguous date with a qualified professional.")
    if not context.get("received_on"):
        questions.append("When did you receive this notice? Add the receipt date in Context.")
    if summary and not any(f["kind"] == "deadline" for f in facts):
        questions.append(
            "No explicit response date was identified. Check the original pages for a time limit."
        )
    if summary and context.get("already_replied") is None:
        questions.append("Have you already replied or received a follow-up notice?")
    return questions[:6]


def fallback(question, facts, pages, context, *, summary=False, reason="unavailable"):
    page_map = {p.number: p for p in pages}
    valid = []
    seen = set()
    for raw in facts:
        try:
            fact = Fact.model_validate(raw)
        except ValueError:
            continue
        identity = (fact.kind, fact.value, fact.evidence.page)
        if identity not in seen and verify_fact(fact, page_map):
            seen.add(identity)
            valid.append(fact.model_dump())
    kinds = requested_kinds(question)
    if summary:
        # Round-robin across fact types prevents repeated dates from hiding requests/amounts.
        groups = [[f for f in valid if f["kind"] == kind] for kind in PRIORITY]
        selected = [group[i] for i in range(8) for group in groups if len(group) > i][:8]
    else:
        selected = [f for f in valid if f["kind"] in kinds][:8] if kinds else []
        if not kinds:
            ranked = lexical(question, [f["evidence"]["quote"] for f in valid])
            selected = [valid[i] for i in ranked[:4]]
    claims = [
        {
            "text": f"{LABELS.get(f['kind'], 'Document statement')}: {f['value']}",
            "basis": "document",
            "evidence": f["evidence"],
            "source_id": None,
            "source_quote": None,
        }
        for f in selected
    ]
    missing = missing_context(context, valid, summary)
    if not claims:
        missing.insert(
            0,
            "No verified document fact answers this question. Check the original notice or provide more context.",
        )
    return {
        "mode": "extracts",
        "answer_type": "document_facts" if claims else "no_evidence",
        "claims": claims,
        "missing_context": missing[:6],
        "professional_review": True,
        "fallback_reason": reason,
    }
