"""Evidence-backed document structure and question planning, independent of a provider."""

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Unit:
    role: str
    value: str
    page: int
    quote: str
    confidence: float = 0.9

    def fact(self):
        kinds = {
            "authority": "issuer",
            "signatory": "issuer",
            "requirement": "request",
            "submission": "request",
            "application": "request",
            "deadline": "deadline",
            "reference": "reference",
            "provision": "section",
            "recipient": "recipient",
        }
        return {
            "kind": kinds.get(self.role, "other"),
            "value": self.value[:500],
            "evidence": {"page": self.page, "quote": self.quote},
            "confidence": self.confidence,
            "date_iso": None,
        }


@dataclass(frozen=True, slots=True)
class Plan:
    intent: str
    roles: tuple[str, ...]
    legal: bool = False


def plan_question(question, summary=False):
    q = " ".join(re.findall(r"[a-z0-9]+", question.casefold()))
    legal = bool(
        re.search(
            r"^(?:must i|do i have to|am i required|should i)\b|\b(?:can i ignore|is it valid|is this valid|"
            r"legally|legal advice|challenge|appeal|consequences|liable|liability|applicable|respond on my behalf)\b",
            q,
        )
    )
    if summary:
        return Plan(
            "summary",
            (
                "authority",
                "signatory",
                "purpose",
                "event",
                "audience",
                "application",
                "requirement",
                "submission",
                "deadline",
            ),
            legal,
        )
    if legal:
        return Plan("legal", (), True)
    patterns = (
        (
            r"\b(?:who|which people|anyone|eligib\w*)\b.*\b(?:apply|participate|give evidence|give oral|submit evidence|can submit)\b",
            "audience",
            ("audience", "application"),
        ),
        (
            r"\b(?:who|whom|recipient|addressee)\b.*\b(?:addressed|intended|served|received|recipient|meant)\b",
            "recipient",
            ("recipient", "audience"),
        ),
        (
            r"\b(?:signatory|signed|signature|officer name|name of (?:the )?officer)\b",
            "signatory",
            ("signatory",),
        ),
        (
            r"\b(?:issuer|issuing|sender|department|authority)\b|\bwho\b.*\b(?:gave|give|sent|send|issued|issue|released|published|from)\b",
            "issuer",
            ("authority", "signatory"),
        ),
        (
            r"\b(?:when|date)\b.*\b(?:incident|collapse|happened|occurred|accident)\b|\bincident date\b",
            "event",
            ("event",),
        ),
        (r"\b(?:issue date|date of issue|notification date|when.*issued|dated)\b", "notice_date", ()),
        (
            r"\b(?:deadline|last date|closing date|due date|time limit|how long|how many days)\b|\bwhen\b.*\b(?:submit|apply|reply|respond|application|hearing)\b",
            "deadline",
            ("deadline",),
        ),
        (
            r"\b(?:where|how|email|e mail|address)\b.*\b(?:submit|send|apply|application|contact|evidence)\b|\b(?:submission address|email address)\b",
            "submission",
            ("submission", "application"),
        ),
        (
            r"\b(?:what|which|list|required|requested)\b.*\b(?:details|information|documents|materials|fields|provide|furnish|submit)\b|\bwhat.*ask\b",
            "requirements",
            ("requirement", "application"),
        ),
        (r"\b(?:amount|how much|money|rupees|payment)\b", "amount", ()),
        (
            r"\b(?:reference|din|cnr|notice number|notification number|file number)\b",
            "reference",
            ("reference",),
        ),
        (r"\b(?:section|provision|which law|what law|under.*law)\b", "section", ("provision",)),
        (r"\b(?:what happened|incident|collapse|accident)\b", "event", ("event", "purpose")),
        (
            r"\b(?:about|purpose|why|explain|summary|summarize|meaning|what is this|what does this mean)\b",
            "purpose",
            ("purpose", "audience", "application"),
        ),
        (r"\b(?:when|date)\b", "deadline", ("deadline",)),
    )
    for pattern, intent, roles in patterns:
        if re.search(pattern, q):
            return Plan(intent, roles)
    return Plan("search", ())


def _sentences(text):
    # Preserve common abbreviations and numbered provisions; join wrapped OCR lines as source spans.
    boundary = re.compile(r"(?<=[.!?])\s+(?=[A-Z])|\n\s*\n|\n(?=\s*\d{1,2}[.)]?\s+[A-Z])")
    start = 0
    for match in boundary.finditer(text):
        if re.search(r"\b(?:No|Com|Lt|Mr|Ms|Dr|Sec|Rs)\.$", text[start : match.start()]):
            continue
        if text[start : match.start()].strip():
            yield text[start : match.start()].strip()
        start = match.end()
    if text[start:].strip():
        yield text[start:].strip()


def structure(page):
    """Separate headings, operative body, signature, lists and distribution recipients."""
    text = page.text or ""
    body_end = re.search(r"(?im)^\s*(?:copy\s+to|distribution|forwarded\s+to)\s*:", text)
    body = text[: body_end.start()] if body_end else text
    lines = list(re.finditer(r"[^\n]+", body))
    units = []

    def add(role, quote, value=None, confidence=0.9):
        quote = quote.strip()
        value = (value or quote).strip()
        if 3 <= len(quote) <= 1200 and 1 <= len(value) <= 500 and quote in text:
            units.append(Unit(role, value, page.number, quote, confidence))

    # A wrapped institutional heading is one authority, not several competing names.
    header_end = re.search(r"(?im)^\s*(?:notification|public notice|notice|whereas|subject)\b", body)
    header = body[: header_end.start()] if header_end else body[:1400]
    header_lines = [m for m in lines if m.start() < len(header)][:15]
    authority_start = next(
        (
            i
            for i, m in enumerate(header_lines)
            if re.search(
                r"\boffice\s+of\b|\b(?:revenue|income tax|tax|recovery)\s+department\b|\bdepartment\s+of\b|\b(?:high|district|supreme)\s+court\b|\bbank\b",
                m.group(),
                re.I,
            )
        ),
        None,
    )
    if authority_start is not None:
        first = header_lines[authority_start]
        last = first
        for line in header_lines[authority_start + 1 : authority_start + 4]:
            value = line.group().strip()
            if re.search(r"^(?:No\.?|Ref\.?|Dated|Date|\d|[A-Z]-?BLOCK)\b", value, re.I):
                break
            if re.search(
                r"department|commissioner|secretary|government|court|bank|office|ministry", value, re.I
            ) or (
                value.isupper() and len(value) < 90 and not re.search(r"marg|road|address|\d{6}", value, re.I)
            ):
                last = line
            else:
                break
        add("authority", body[first.start() : last.end()])
    for match in re.finditer(r"(?im)^\s*(?:No\.|Ref\.?\s*(?:No\.?)?)\s*[:.-]?\s*[^\n]{3,180}", header):
        value = re.split(r"\bDated\s*[:.-]?", match.group(), maxsplit=1, flags=re.I)[0].strip()
        add("reference", value)

    # A printed name directly next to an official designation, before the copy list.
    for i, line in enumerate(lines):
        name = line.group().strip()
        if not re.fullmatch(r"\(?[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,4}\)?", name):
            continue
        if i < max(2, len(lines) // 3):
            continue
        following = lines[i + 1 : i + 4]
        designation = [
            m
            for m in following
            if len(m.group().strip()) < 130
            and re.match(
                r"\s*(?:(?:divisional|district|additional|joint|deputy|assistant|chief|senior|enquiry|revenue|branch)\s+){0,3}"
                r"(?:secretary|commissioner|officer|magistrate|registrar|manager|judge|director|collector)\b",
                m.group(),
                re.I,
            )
        ]
        if designation:
            quote = body[line.start() : designation[-1].end()]
            add("signatory", quote, confidence=0.85)

    operative = body[header_end.end() :] if header_end else body
    for match in re.finditer(
        r"\b(?:section|sec\.?|u/s)\s+\d+[A-Z]?(?:\s*\([A-Z0-9]+\)){0,4}"
        r"(?:\s+of\s+(?:the\s+)?[A-Z][A-Za-z .'-]{1,80}(?:,?\s*(?:19|20)\d{2})?)?",
        operative,
        re.I,
    ):
        add("provision", match.group().strip())
    for sentence in _sentences(operative):
        clean = " ".join(sentence.split())
        if re.search(
            r"\b(?:enquir[ey]|inquir[ey]|investigation|summons|show cause|demand|recovery|hearing)\b",
            clean,
            re.I,
        ):
            if re.search(r"\b(?:whereas|directed|conduct|initiated|subject)\b", clean, re.I):
                add("purpose", sentence, sentence[:500])
        if re.search(r"\b(?:collapse|accident|incident|fire|demolition)\b", clean, re.I) and re.search(
            r"\b(?:on|dated)\s+\d", clean, re.I
        ):
            add("event", sentence, sentence[:500])
        if re.search(
            r"\b(?:any person|persons?\s+willing|public|witnesses|interested persons)\b", clean, re.I
        ):
            add("audience", sentence, sentence[:500])
        if re.search(r"\b(?:requested|required|invited|should|shall|must)\b", clean, re.I) and re.search(
            r"\b(?:submit|produce|provide|furnish|apply|application|evidence)\b", clean, re.I
        ):
            add("application", sentence, sentence[:500])
        if re.search(r"\b(?:applications?|reply|response|documents?|payment)\b", clean, re.I) and re.search(
            r"\b(?:on or before|no later than|last date|deadline|should reach|must reach|by\s+\d)\b",
            clean,
            re.I,
        ):
            add("deadline", sentence, sentence[:500])
        if re.search(
            r"\b(?:addressed to|through email|by email|e-mail|submit.*(?:at|to)|send.*(?:at|to))\b",
            clean,
            re.I,
        ):
            # Keep the submission instruction contiguous, excluding the unrelated preceding narrative.
            match = re.search(
                r"(?:application[^.]{0,70}addressed to|addressed to|submit|send|through email|by email|e-mail)\b",
                sentence,
                re.I,
            )
            if match:
                span = sentence[match.start() :].strip()
                span = re.split(r"\n\s*(?:i[.):]|1[.):])\s+", span, maxsplit=1)[0].strip()
                add("submission", span, span[:500])

    # Roman/Arabic form fields before the signature; distribution list is already excluded.
    list_pattern = re.compile(r"(?im)^\s*(?:(?:[ivxl]{1,5}|\d{1,2})[.):]|[a-h][.)])\s+(.{3,230})$")
    for match in list_pattern.finditer(body):
        value = match[1].strip()
        if re.search(
            r"\b(?:name|address|date of birth|age|contact|occupation|circumstances|reason|identity|statement|evidence)\b",
            value,
            re.I,
        ):
            if not re.search(
                r"\b(?:secretary to|copy to|staff officer|government|commissioner municipal)\b", value, re.I
            ):
                add("requirement", match.group().strip(), value)
    # OCR can drop the punctuation of a form bullet ("iv Full address ...").
    for match in re.finditer(
        r"(?im)^\s*(?:[A-Za-z0-9]{1,5}[.,:)]?\s+)?"
        r"((?:Full\s+Name|Father\s*(?:'s)?\s+Name|Mother\s*(?:'s)?\s+Name|"
        r"Full\s+address|Date\s+of\s+Birth|Contact\s+No\.?|Occupation|Circumstances\s+prompting)[^\n]{0,200})$",
        body,
    ):
        if not any(u.role == "requirement" and u.value == match[1].strip() for u in units):
            add("requirement", match.group().strip(), match[1].strip())
    return units


def collect(pages):
    return [unit for page in pages for unit in structure(page)]
