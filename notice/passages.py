from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class TextSpan:
    start: int
    end: int
    text: str


def split_passages(text, max_chars=1800, overlap=180):
    """Split without rewriting evidence; offsets always address the original text."""
    if max_chars < 200 or not 0 <= overlap < max_chars // 2:
        raise ValueError("Invalid passage size or overlap")
    spans = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            floor = start + max_chars // 2
            boundaries = [text.rfind(marker, floor, end) for marker in ("\n\n", ". ", "\n", " ")]
            boundary = next((value for value in boundaries if value >= floor), -1)
            if boundary >= floor:
                end = boundary + 1
        left, right = start, end
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if right > left:
            spans.append(TextSpan(left, right, text[left:right]))
        if end >= len(text):
            break
        next_start = max(start + 1, end - overlap)
        floor = max(start + 1, next_start - 80)
        while next_start > floor and not text[next_start - 1].isspace():
            next_start -= 1
        start = max(start + 1, next_start)
    return tuple(spans)


@dataclass(frozen=True, slots=True)
class DocumentHit:
    page_id: str
    document_id: str
    number: int
    start: int
    end: int
    text: str
    score: float = 0.0


@dataclass(frozen=True, slots=True)
class SourceHit:
    id: str
    url: str
    title: str
    digest: str
    fetched: datetime
    start: int
    end: int
    text: str
    score: float = 0.0
