import re
import unicodedata
from datetime import date


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value).split())


def verify_quote(text, quote):
    needle = normalize(quote)
    return len(needle) >= 3 and needle in normalize(text)


def verify_fact(fact, pages):
    page = pages.get(fact.evidence.page)
    if not page or not verify_quote(page.text, fact.evidence.quote):
        return False
    # Values cannot silently paraphrase/invent identifiers, figures or dates.
    if not verify_quote(fact.evidence.quote, fact.value):
        return False
    if fact.date_iso:
        try:
            parsed = date.fromisoformat(fact.date_iso)
        except ValueError:
            return False
        quoted_dates = extract_dates(fact.evidence.quote)
        if parsed.isoformat() not in {value for _, value in quoted_dates if value}:
            return False
    return True


def extract_dates(text):
    results = []
    for match in re.finditer(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d{2})\b", text):
        day, month, year = map(int, match.groups())
        # Ambiguous numeric dates are kept as quotes and never scheduled automatically.
        try:
            value = date(year, month, day).isoformat() if day > 12 or day == month else None
            if not 1 <= day <= 31 or not 1 <= month <= 12:
                value = None
        except ValueError:
            value = None
        results.append((match.group(), value))
    for match in re.finditer(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text):
        try:
            value = date.fromisoformat(match.group()).isoformat()
        except ValueError:
            value = None
        results.append((match.group(), value))
    months = {
        name.lower(): idx
        for idx, name in enumerate(
            [
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
            ],
            1,
        )
    }
    pattern = r"\b(\d{1,2})\s+(" + "|".join(months) + r")\s+(20\d{2})\b"
    for match in re.finditer(pattern, text, re.I):
        try:
            value = date(int(match[3]), months[match[2].lower()], int(match[1])).isoformat()
        except ValueError:
            value = None
        results.append((match.group(), value))
    return results


def quality(text):
    if not text.strip():
        return 0.0
    clean = sum(ch.isalnum() or ch.isspace() or ch in ",.():/-₹%" for ch in text) / len(text)
    replacement = text.count("\ufffd") / len(text)
    return round(max(0, min(1, clean - 5 * replacement)), 3)


def evidence_boxes(page, quote):
    # Word rectangles, normalized to page dimensions; approximate highlights are labelled in UI.
    words = {normalize(x).lower() for x in quote.split() if normalize(x)}
    return [block["bbox"] for block in page.blocks if normalize(block["text"]).lower() in words][:200]
