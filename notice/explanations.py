"""Question-specific answers from verified document structure; providers handle open reasoning."""

import re
from datetime import date

from .evidence import _date_kind, _date_matches, extract_dates, normalize, verify_fact, verify_quote
from .retrieval import lexical, tokens
from .schemas import Fact
from .understanding import collect, plan_question

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
PRIORITY = (
    "issuer",
    "reference",
    "notice_date",
    "deadline",
    "amount",
    "request",
    "section",
    "recipient",
    "other",
)
KINDS = {
    "issuer": {"issuer"},
    "signatory": {"issuer"},
    "recipient": {"recipient"},
    "deadline": {"deadline"},
    "notice_date": {"notice_date"},
    "amount": {"amount"},
    "reference": {"reference"},
    "section": {"section"},
    "requirements": {"request"},
    "submission": {"request"},
    "purpose": {"other", "request"},
    "event": {"other"},
}


def requested_kinds(question):
    return KINDS.get(plan_question(question).intent, set())


def select_facts(facts, question, summary=False, max_chars=14000):
    if summary:
        groups = []
        for kind in PRIORITY:
            rows = [f for f in facts if f["kind"] == kind]
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
        matching = [f for f in facts if f["kind"] in kinds]
        others = [facts[i] for i in lexical(question, [f["evidence"]["quote"] for f in facts])]
        ranked = matching + [f for f in others if f not in matching]
    selected, size = [], 0
    for fact in ranked:
        cost = len(fact["value"]) + len(fact["evidence"]["quote"]) + 160
        if size + cost <= max_chars:
            selected.append(fact)
            size += cost
        if len(selected) == 40:
            break
    return selected


def missing_context(context, facts, summary=False, question="", pages=()):
    plan = plan_question(question, summary)
    relevant_dates = summary or plan.intent in {"deadline", "notice_date", "legal"}
    questions = []
    deadlines = [f for f in facts if f["kind"] == "deadline"]
    if relevant_dates:
        relative = any(
            re.search(r"\b(?:within|from receipt|after receipt|date of service)\b", f["value"], re.I)
            for f in deadlines
        )
        if relative and not context.get("received_on"):
            questions.append(
                "What date was this notice received or served? Its time limit is relative to receipt/service."
            )
        if any(
            not f.get("date_iso")
            and not re.search(r"\bwithin\b", f["value"], re.I)
            and any(stamp is None for _, stamp in extract_dates(f["value"]))
            for f in deadlines
        ):
            questions.append(
                "The date format in the notice is ambiguous. Confirm the day and month on the original page."
            )
        if any(
            "ocr_critical_conflict" in (getattr(p, "flags", []) or [])
            or any(
                "ocr_critical_conflict" in b.get("ocr_diagnostics", [])
                for b in (getattr(p, "blocks", []) or [])
            )
            for p in pages
        ):
            questions.append(
                "OCR readings disagree on a critical field. Check the date or name against the original image."
            )
    if plan.legal:
        questions.append(
            "A qualified professional needs to check legal applicability and your circumstances before advising on this."
        )
    return questions[:6]


def _claim(text, page, quote):
    return {
        "text": text[:800],
        "basis": "document",
        "evidence": {"page": page, "quote": quote},
        "source_id": None,
        "source_quote": None,
    }


def _flat(value):
    return normalize(re.sub(r"-\s*\n\s*", "-", value))


def _unit_claim(unit, intent):
    value, quote = _flat(unit.value), unit.quote
    if unit.role == "authority":
        text = "The issuing office is " + value.rstrip(".") + "."
    elif unit.role == "signatory":
        text = (
            "The printed signatory is "
            + normalize(re.sub(r"^\(([^)]+)\)\s*", r"\1, ", value)).rstrip(".")
            + "."
        )
    elif unit.role == "purpose" and re.search(r"magisterial\s+enquir[ey]", quote, re.I):
        text = "This notification concerns a magisterial enquiry"
        text += (
            " into the building collapse described in the notice."
            if re.search(r"\bcollapse\b", quote, re.I)
            else "."
        )
    elif (
        unit.role in {"audience", "application"}
        and re.search(r"oral\s+evidence", quote, re.I)
        and re.search(r"\b(?:willing|desirous|invited)\b", quote, re.I)
        and re.search(r"\b(?:written|writing|application)\b", quote, re.I)
    ):
        text = "People willing to give oral evidence or produce relevant materials/documents are invited to submit information in writing."
    elif unit.role == "submission":
        if re.search(r"email|e-mail", value, re.I) and not re.search(
            r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", value
        ):
            value = re.split(
                r"\s+(?:or\s+)?(?:through\s+)?(?:email|e-mail)\s*:?", value, maxsplit=1, flags=re.I
            )[0]
        text = "The submission instruction is: " + value
    elif unit.role == "requirement":
        text = "Required application detail: " + value
    elif unit.role == "provision":
        text = "The provision cited by the notice is " + value.rstrip(".") + "."
    elif unit.role == "deadline":
        parsed = [
            (literal, stamp)
            for start, end, literal, stamp in _date_matches(quote)
            if stamp and _date_kind(quote, start, end) == "deadline"
        ]
        if len({stamp for _, stamp in parsed}) == 1:
            stamp = date.fromisoformat(parsed[-1][1])
            text = (
                "The stated application/response deadline is " + stamp.strftime("%d %B %Y").lstrip("0") + "."
            )
        else:
            text = "The closing-date wording reads: " + value
    elif unit.role == "event" and intent == "event":
        dates = extract_dates(quote)
        text = (
            ("The incident date printed in the document is " + dates[-1][0] + ".")
            if dates
            else "The document describes: " + value
        )
    else:
        text = "The document states: " + value
    return _claim(text, unit.page, quote)


def fallback(question, facts, pages, context, *, summary=False, reason="unavailable"):
    plan = plan_question(question, summary)
    page_map = {p.number: p for p in pages}
    valid, seen = [], set()
    for raw in facts:
        try:
            fact = Fact.model_validate(raw)
        except ValueError:
            continue
        identity = (fact.kind, fact.value, fact.evidence.page)
        if identity not in seen and verify_fact(fact, page_map):
            seen.add(identity)
            valid.append(fact.model_dump())
    units = [u for u in collect(pages) if verify_quote(page_map[u.page].text, u.quote)]
    claims = []
    relevant = [u for role in plan.roles for u in units if u.role == role]
    if plan.intent == "audience":
        audience = [u for u in relevant if u.role == "audience"]
        relevant = audience[:2] if audience else relevant[:1]
    if plan.intent == "purpose":
        purpose = next((u for u in relevant if u.role == "purpose"), None)
        audience = next((u for u in relevant if u.role == "audience"), None)
        relevant = [u for u in (purpose, audience) if u is not None] or relevant[:1]
    if plan.intent == "requirements":
        requirements = [u for u in relevant if u.role == "requirement"]
        if requirements:
            relevant = sorted(requirements, key=lambda u: (u.page, page_map[u.page].text.find(u.quote)))
    if summary:
        relevant = [
            next((u for u in units if u.role == role), None)
            for role in ("purpose", "authority", "signatory", "deadline", "application", "submission")
        ]
        relevant = [u for u in relevant if u is not None]
    for unit in relevant:
        candidate = _unit_claim(unit, plan.intent)
        if candidate not in claims:
            claims.append(candidate)
    if summary:
        requirements = [u for u in units if u.role == "requirement"]
        if requirements and len({u.page for u in requirements}) == 1:
            source = page_map[requirements[0].page].text
            spans = [(source.find(u.quote), u) for u in requirements]
            left = min(pos for pos, _ in spans)
            right = max(pos + len(u.quote) for pos, u in spans)
            quote = source[left:right]
            wording = "Application details requested: " + "; ".join(
                u.value for _, u in sorted(spans, key=lambda x: x[0])
            )
            if len(quote) <= 1200 and len(wording) <= 800:
                claims.append(_claim(wording, requirements[0].page, quote))
    if not claims or summary:
        kinds = requested_kinds(question)
        if summary:
            groups = [[f for f in valid if f["kind"] == kind] for kind in PRIORITY]
            selected = [group[i] for i in range(8) for group in groups if len(group) > i]
        else:
            selected = [f for f in valid if f["kind"] in kinds] if kinds else []
            if plan.intent == "signatory":
                selected = []
        for fact in selected:
            if (
                summary
                and fact["kind"] == "issuer"
                and any(u.role in {"authority", "signatory"} for u in relevant)
            ):
                continue
            if summary and fact["kind"] == "deadline" and any(u.role == "deadline" for u in relevant):
                continue
            if any(c["evidence"] == fact["evidence"] for c in claims):
                continue
            claims.append(
                _claim(
                    f"{LABELS.get(fact['kind'], 'Document statement')}: {_flat(fact['value'])}",
                    fact["evidence"]["page"],
                    fact["evidence"]["quote"],
                )
            )
    if not claims and plan.intent == "search":
        candidates = [u for u in units if u.role in {"purpose", "event", "application", "submission"}]
        query_terms = set(tokens(question))
        for index in lexical(question, [u.quote for u in candidates])[:3]:
            unit = candidates[index]
            if len(query_terms.intersection(tokens(unit.quote))) >= min(2, max(1, len(query_terms))):
                claims.append(_claim("The document states: " + _flat(unit.value), unit.page, unit.quote))
    claims = claims[:8]
    missing = missing_context(context, valid, summary, question, pages)
    unreadable_deadline = any(
        u.role == "deadline" and not extract_dates(u.quote) and not re.search(r"\bwithin\b", u.quote, re.I)
        for u in relevant
    )
    if unreadable_deadline and (summary or plan.intent == "deadline"):
        missing.append(
            "The closing date was not read reliably. Check the original closing-date paragraph; no corrected day has been assumed."
        )
    if summary or plan.intent == "submission":
        email_units = [
            u for u in units if u.role == "submission" and re.search(r"email|e-mail", u.quote, re.I)
        ]
        if any(not re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", u.quote) for u in email_units):
            missing.append(
                "The email address was not read reliably. Check it on the original page before sending anything."
            )
    if not claims:
        messages = {
            "amount": "No payment amount was identified in the extracted text of this notice.",
            "signatory": "The printed signatory could not be identified reliably. Check the signature block on the original page.",
            "requirements": "No specific required items were identified reliably in the extracted text.",
            "deadline": "The response/application deadline could not be read reliably. Check the closing-date paragraph on the original page.",
            "legal": "The document alone cannot establish your legal obligation or the correct response strategy.",
        }
        missing.insert(
            0,
            messages.get(
                plan.intent,
                "No verified document fact answers this question. Check the original notice or provide more context.",
            ),
        )
    direct = (
        bool(claims) and not summary and not unreadable_deadline and plan.intent not in {"legal", "search"}
    )
    return {
        "mode": "extracts",
        "answer_type": "document_facts" if claims else "no_evidence",
        "claims": claims,
        "missing_context": missing[:6],
        "professional_review": summary or plan.legal,
        "fallback_reason": reason,
        "intent": plan.intent,
        "direct_answer": direct,
    }
