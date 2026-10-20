import re
import unicodedata
from datetime import date


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value).split())


def verify_quote(text, quote):
    needle = normalize(quote)
    if len(needle) < 3:
        return False
    haystack = normalize(text)
    for match in re.finditer(re.escape(needle), haystack):
        left_ok = not needle[0].isalnum() or match.start() == 0 or not haystack[match.start() - 1].isalnum()
        right_ok = (
            not needle[-1].isalnum() or match.end() == len(haystack) or not haystack[match.end()].isalnum()
        )
        if needle[0].isdigit() and re.search(r"\d[,.]$", haystack[: match.start()]):
            left_ok = False
        if needle[-1].isdigit() and re.match(r"[,.]\d", haystack[match.end() :]):
            right_ok = False
        if left_ok and right_ok:
            return True
    return False


def verify_fact(fact, pages):
    page = pages.get(fact.evidence.page)
    if not page or not verify_quote(page.text, fact.evidence.quote):
        return False
    if not verify_quote(fact.evidence.quote, fact.value):
        return False
    if fact.date_iso:
        try:
            parsed = date.fromisoformat(fact.date_iso)
        except ValueError:
            return False
        value_dates = {value for _, value in extract_dates(fact.value)}
        if value_dates != {parsed.isoformat()}:
            return False
    return True


_MONTHS = {
    name.lower(): idx
    for idx, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        1,
    )
}
_MONTHS.update({name[:3]: number for name, number in list(_MONTHS.items())})
_MONTHS["sept"] = 9
_MONTH_PATTERN = "(?:" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_YEAR = r"(?:19|20)\d{2}"
_DATE_PATTERNS = (
    (re.compile(r"\b(" + _YEAR + r")-(\d{2})-(\d{2})\b"), "iso"),
    (re.compile(r"\b(\d{1,2})([./-])(\d{1,2})\2(" + _YEAR + r")\b"), "numeric"),
    (
        re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + _MONTH_PATTERN + r"),?\s+(" + _YEAR + r")\b", re.I),
        "day_month",
    ),
    (
        re.compile(r"\b(" + _MONTH_PATTERN + r")\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(" + _YEAR + r")\b", re.I),
        "month_day",
    ),
)


def _date_matches(text):
    results = []
    for pattern, form in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            try:
                if form == "iso":
                    year, month, day = map(int, match.groups())
                elif form == "numeric":
                    day, month, year = int(match[1]), int(match[3]), int(match[4])
                elif form == "day_month":
                    day, month, year = int(match[1]), _MONTHS[match[2].lower().rstrip(".")], int(match[3])
                else:
                    month, day, year = _MONTHS[match[1].lower().rstrip(".")], int(match[2]), int(match[3])
                parsed = date(year, month, day).isoformat()
                # Day/month order cannot be proved from an ambiguous numeric date.
                if form == "numeric" and day <= 12 and day != month:
                    parsed = None
            except (ValueError, KeyError):
                parsed = None
            results.append((match.start(), match.end(), match.group(), parsed))
    return sorted(results, key=lambda item: item[0])


def extract_dates(text):
    return [(quote, parsed) for _, _, quote, parsed in _date_matches(text)]


_DEADLINE_CUE = re.compile(
    r"\b(?:reply|respond|response|deadline|due\s+date|last\s+date|hearing|appear(?:ance)?|"
    r"attend|submit|furnish|provide|produce|pay(?:ment)?|remit|comply|compliance)\b",
    re.I,
)
_ISSUE_CUE = re.compile(
    r"\b(?:date\s+of\s+issue|issue\s+date|issued(?:\s+on)?|dated|notice\s+date)\b|^\s*date\s*:", re.I
)
_REFERENCE = re.compile(
    r"\b(?:DIN|CNR|Document\s+Identification\s+Number|(?:notice|reference|ref\.?|case|file|order)"
    r"\s*(?:no\.?|number|#))\s*[:#/-]?\s*[A-Z0-9][A-Z0-9()/._-]{2,100}",
    re.I,
)
_AMOUNT = re.compile(
    r"(?<![\w])(?:\u20b9\s*|INR\s*|Rs\.?\s*|Rupees\s+)[0-9]+(?:,[0-9]{2,3})*"
    r"(?:\.[0-9]{1,2})?(?:\s*/-)?(?![\w])",
    re.I,
)
_SECTION = re.compile(r"\b(?:section|sec\.?|u/s)\s+\d+[A-Z]?(?:\s*\([A-Z0-9]+\)){0,4}", re.I)
_RELATIVE = re.compile(
    r"\bwithin\s+(?:\d{1,4}|one|two|three|five|seven|ten|fifteen|twenty|thirty|forty[- ]five|sixty|ninety)"
    r"\s+(?:(?:working|business|calendar|clear)\s+)?(?:days?|weeks?|months?)"
    r"(?:\s+(?:of|from|after)\s+(?:the\s+)?(?:date\s+of\s+)?"
    r"(?:receipt|receiving|service|issue|issuance|delivery)(?:\s+of\s+(?:this|the)\s+notice)?)?",
    re.I,
)
_REQUEST = re.compile(
    r"(?:\b(?:you\s+(?:(?:are|is)\s+)?(?:(?:hereby|further)\s+)?"
    r"(?:requested|required|directed|called\s+upon)\s+to|you\s+(?:must|shall)|please)\s+"
    r"(?:submit|provide|furnish|produce|appear|attend|pay|remit|respond|reply|explain|show\s+cause|file|upload)\b|"
    r"^\s*(?:submit|provide|furnish|produce|attend|appear|respond|reply|upload)\b)",
    re.I | re.M,
)
_ISSUER = re.compile(
    r"\b(?:income\s+tax\s+department|department\s+of\s+(?:income\s+tax|revenue)|"
    r"office\s+of\s+(?:the\s+)?|commissioner(?:ate)?\s+of\s+|"
    r"(?:district|high|supreme|civil|commercial|magistrate)[\w ]*\s+court|"
    r"court\s+of\s+|[\w &.]+\s+bank(?:\s+limited|\s+ltd\.?)?)\b",
    re.I,
)


def _quote_window(text, start, end, maximum=1100):
    # A contiguous source span must include the complete value even on very long lines.
    left = max(text.rfind("\n", 0, start), text.rfind(";", 0, start)) + 1
    ends = [pos for pos in (text.find("\n", end), text.find(";", end)) if pos >= 0]
    right = min(ends) if ends else len(text)
    left, right = max(left, start - 350), min(right, end + 500)
    right = min(right, left + maximum)
    return text[left:right].strip()


def _date_kind(text, start, end):
    left = max(text.rfind("\n", 0, start), text.rfind(";", 0, start)) + 1
    before = text[max(left, start - 160) : start]
    boundaries = list(re.finditer(r"[.!?](?=\s+[A-Z])", before))
    if boundaries:
        before = before[boundaries[-1].end() :]
    cues = [(match.start(), "deadline") for match in _DEADLINE_CUE.finditer(before)]
    cues += [(match.start(), "notice_date") for match in _ISSUE_CUE.finditer(before)]
    if cues:
        position, kind = max(cues)
        tail = before[position:]
        if kind == "deadline":
            # A requested statement's reporting period is not the response deadline.
            unrelated = re.search(
                r"\b(?:period|since|between|starting|ending|received|already|completed)\b", tail, re.I
            )
            if unrelated and not re.search(r"\b(?:by|before|deadline|due)\b", tail[unrelated.end() :], re.I):
                return "other"
        elif re.search(r"\b(?:previous|earlier|prior)\s+(?:notice|order|letter)\b", before, re.I):
            return "other"
        return kind
    after = re.split(r"[\n;]", text[end : end + 65], maxsplit=1)[0]
    if re.match(
        r"\s*[:(-]\s*(?:(?:reply|response|payment)\s+(?:deadline|due\s+date)|"
        r"hearing(?:\s+date)?|deadline|due\s+date)\b",
        after,
        re.I,
    ):
        return "deadline"
    return "other"


def extract_facts(page):
    """Extract explicit quoted fields, without a model or calculated legal obligations."""
    from .schemas import Fact

    text = page.text or ""
    candidates = []

    def add(kind, value, start, end, confidence=0.85, date_iso=None):
        value = value.strip()
        if not value or len(value) > 500:
            return
        quote = _quote_window(text, start, end)
        candidate = {
            "kind": kind,
            "value": value,
            "evidence": {"page": page.number, "quote": quote},
            "confidence": confidence,
            "date_iso": date_iso,
        }
        if verify_fact(Fact.model_validate(candidate), {page.number: page}):
            candidates.append((start, candidate))

    for start, end, value, parsed in _date_matches(text):
        add(_date_kind(text, start, end), value, start, end, 0.9 if parsed else 0.55, parsed)
    for pattern, kind in ((_REFERENCE, "reference"), (_AMOUNT, "amount"), (_SECTION, "section")):
        for match in pattern.finditer(text):
            add(kind, match.group(), match.start(), match.end())
    for match in _RELATIVE.finditer(text):
        # Instructions often wrap onto a new OCR line before the relative time limit.
        before = re.split(r"[.!?;]\s+|\n\s*\n", text[max(0, match.start() - 250) : match.start()])[-1]
        nearby = before + text[match.start() : match.end()]
        if _DEADLINE_CUE.search(nearby) and not re.search(
            r"\b(?:already|completed|was\s+received)\b", nearby, re.I
        ):
            add("deadline", match.group(), match.start(), match.end(), 0.8)
    for match in _REQUEST.finditer(text):
        end_match = re.search(r"[;!?]|\.(?=\s+[A-Z]|\s*$)|\n\s*\n", text[match.end() : match.start() + 500])
        end = match.end() + end_match.end() if end_match else min(len(text), match.start() + 500)
        value = text[match.start() : end].strip()
        add("request", value, match.start(), end, 0.85)
    offset = 0
    for line_number, line in enumerate(text.splitlines(keepends=True)):
        raw_line = line.rstrip("\r\n")
        if line_number < 20 and len(raw_line.strip()) <= 250 and _ISSUER.search(raw_line):
            if not re.search(
                r"\b(?:your|supporting|statement|account|copy|submit|provide)\b", raw_line, re.I
            ):
                add("issuer", raw_line.strip(), offset, offset + len(raw_line), 0.75)
        recipient = re.match(
            r"\s*(?:To|Recipient|Name\s+of\s+(?:taxpayer|assessee|addressee))\s*:\s*(\S.{1,240})",
            raw_line,
            re.I,
        )
        if recipient:
            add("recipient", recipient[1], offset + recipient.start(1), offset + recipient.end(1), 0.8)
        offset += len(line)
    seen, facts = set(), []
    for _, fact in sorted(candidates, key=lambda item: item[0]):
        key = (fact["kind"], normalize(fact["value"]), fact.get("date_iso"))
        if key not in seen:
            seen.add(key)
            facts.append(fact)
    return facts[:80]


def category_hint(text):
    cues = {
        "income_tax": r"\bincome[ -]tax\b|\bassessment\s+year\b|\bCBDT\b",
        "gst": r"\b(?:GST|CGST|SGST|IGST|GSTIN)\b|goods\s+and\s+services\s+tax",
        "court": r"\b(?:court|summons|CNR|plaintiff|defendant)\b",
        "bank": r"\b(?:bank\s+notice|loan\s+recall|recovery\s+notice|SARFAESI|banking|loan\s+account)\b",
    }
    matched = [name for name, pattern in cues.items() if re.search(pattern, text, re.I)]
    return matched[0] if len(matched) == 1 else "unknown"


def quality(text):
    if not text.strip():
        return 0.0
    clean = sum(ch.isalnum() or ch.isspace() or ch in ",.():/-\u20b9%'&;!?\"+=" for ch in text) / len(text)
    replacement = text.count("\ufffd") / len(text)
    controls = sum(unicodedata.category(ch) == "Cc" and ch not in "\n\r\t" for ch in text) / len(text)
    return round(max(0, min(1, clean - 5 * replacement - 5 * controls)), 3)


def evidence_boxes(page, quote):
    # Exact consecutive words prevent highlighting unrelated copies elsewhere on the page.
    needle = re.findall(r"\w+|[^\w\s]", normalize(quote).casefold())
    if not needle:
        return []
    stream, owners = [], []
    for index, block in enumerate(page.blocks or []):
        pieces = re.findall(r"\w+|[^\w\s]", normalize(block.get("text", "")).casefold())
        stream.extend(pieces)
        owners.extend([index] * len(pieces))
    # KMP makes repeated words and very long pages linear instead of quadratic.
    prefix, matched = [0] * len(needle), 0
    for index in range(1, len(needle)):
        while matched and needle[index] != needle[matched]:
            matched = prefix[matched - 1]
        if needle[index] == needle[matched]:
            matched += 1
        prefix[index] = matched
    matched = 0
    for index, token in enumerate(stream):
        while matched and token != needle[matched]:
            matched = prefix[matched - 1]
        if token == needle[matched]:
            matched += 1
        if matched == len(needle):
            start = index - len(needle) + 1
            indexes = dict.fromkeys(owners[start : index + 1])
            return [page.blocks[item]["bbox"] for item in list(indexes)[:200]]
    return []
