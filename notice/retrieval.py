import hashlib
import math
import re
import threading
import time
import unicodedata
from collections import Counter, OrderedDict, defaultdict
from dataclasses import replace
from functools import lru_cache

from sqlalchemy import select

from . import ai
from .config import settings
from .models import Case, Document, Page, Source, now
from .passages import DocumentHit, SourceHit, split_passages


STOP_WORDS = frozenset(
    "a an and are as at be been but by can could did do does for from had has have how "
    "i if in into is it its me my of on or our please should so than that the their them "
    "there these they this those to us was we were what when where which who why will "
    "with would you your".split()
)


def tokens(text):
    """Whole words and identifiers, without noisy character-substring matches."""
    words = re.findall(r"[^\W_]+(?:[-/][^\W_]+)*", unicodedata.normalize("NFKC", text).casefold())
    result = []
    for word in words:
        parts = re.split(r"[-/]", word)
        for term in [word] + (parts if len(parts) > 1 else []):
            if term in STOP_WORDS:
                continue
            if len(term) > 4 and term.endswith("ies"):
                term = term[:-3] + "y"
            elif len(term) > 4 and term.endswith("s") and not term.endswith(("ss", "us", "is")):
                term = term[:-1]
            result.append(term)
    return result


def expand_query(query):
    normalized = unicodedata.normalize("NFKC", query).casefold()
    expanded = [query]
    expansions = (
        (
            r"\b(deadline|respond|reply|date|dates|when|due|time limit)\b",
            "deadline reply respond due before dated issued hearing date",
        ),
        (
            r"\b(amount|amounts|money|pay|payment|demand|dues)\b",
            "amount INR Rs rupees payment demand payable",
        ),
        (
            r"\b(document|documents|record|records|provide|requested|required|submit)\b",
            "provide submit furnish required requested records documents",
        ),
        (r"\b(reference|identifier|din|cnr|number)\b", "reference DIN CNR number"),
        (r"\b(issuer|issued|department|authority|sender|who)\b", "issued department authority office"),
        (
            r"\b(explain|summary|summarize|summarise|overview)\b",
            "notice issued reference reply deadline hearing amount provide submit required",
        ),
    )
    for pattern, expansion in expansions:
        if re.search(pattern, normalized):
            expanded.append(expansion)
    return " ".join(expanded)


class LexicalIndex:
    """BM25 postings built once; query scoring touches only matching postings."""

    def __init__(self, documents):
        self.lengths = []
        self.postings = defaultdict(list)
        for index, text in enumerate(documents):
            counts = Counter(tokens(text))
            self.lengths.append(sum(counts.values()))
            for term, frequency in counts.items():
                self.postings[term].append((index, frequency))
        self.average = sum(self.lengths) / max(len(self.lengths), 1) or 1

    def rank(self, query, limit=None):
        scores = defaultdict(float)
        count = len(self.lengths)
        for term in sorted(set(tokens(query))):
            postings = self.postings.get(term, ())
            idf = math.log(1 + (count - len(postings) + 0.5) / (len(postings) + 0.5))
            for index, frequency in postings:
                norm = frequency + 1.2 * (0.25 + 0.75 * self.lengths[index] / self.average)
                scores[index] += idf * frequency * 2.2 / norm
        ranking = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return ranking if limit is None else ranking[:limit]


def lexical(query, documents):
    return [index for index, _ in LexicalIndex(documents).rank(query)]


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return -1.0
    try:
        if not all(math.isfinite(value) for value in (*a, *b)):
            return -1.0
        denominator = math.sqrt(sum(x * x for x in a) * sum(x * x for x in b))
        return sum(x * y for x, y in zip(a, b)) / denominator if denominator else -1.0
    except (TypeError, ValueError, OverflowError):
        return -1.0


def fuse(*rankings):
    scores = defaultdict(float)
    for ranking in rankings:
        for rank, item in enumerate(dict.fromkeys(ranking)):
            scores[item] += 1 / (60 + rank + 1)
    return sorted(scores, key=lambda item: (-scores[item], item))


class _IndexCache:
    # Short retention, content-versioned keys, and a character budget bound worker memory.
    def __init__(self):
        self.entries = OrderedDict()
        self.lock = threading.Lock()
        self.characters = 0

    def get(self, org_id, document_id, pages):
        digest = hashlib.sha256()
        for page in pages:
            digest.update(f"{page.id}:{page.number}:".encode())
            digest.update(page.text.encode())
        key = (org_id, document_id, digest.digest())
        current = time.monotonic()
        with self.lock:
            for cached_key, (_, _, expiry, size) in list(self.entries.items()):
                if expiry <= current:
                    del self.entries[cached_key]
                    self.characters -= size
            entry = self.entries.get(key)
            if entry:
                self.entries.move_to_end(key)
                return entry[:2]
        hits = tuple(
            DocumentHit(page.id, document_id, page.number, span.start, span.end, span.text)
            for page in pages
            for span in split_passages(page.text)
        )
        index = LexicalIndex(hit.text for hit in hits)
        size = sum(len(hit.text) for hit in hits)
        if size <= 2_000_000:
            with self.lock:
                previous = self.entries.pop(key, None)
                if previous:
                    self.characters -= previous[3]
                while self.entries and (len(self.entries) >= 8 or self.characters + size > 2_000_000):
                    _, removed = self.entries.popitem(last=False)
                    self.characters -= removed[3]
                self.entries[key] = (hits, index, current + 300, size)
                self.characters += size
        return hits, index


_document_indexes = _IndexCache()


@lru_cache(maxsize=1)
def reranker():
    from sentence_transformers import CrossEncoder

    return CrossEncoder(settings().reranker_model, device="cpu", trust_remote_code=False)


def _distinct(hits, limit):
    selected = []
    for hit in hits:
        identifier = getattr(hit, "page_id", getattr(hit, "id", None))
        overlaps = False
        for previous in selected:
            if identifier != getattr(previous, "page_id", getattr(previous, "id", None)):
                continue
            intersection = max(0, min(hit.end, previous.end) - max(hit.start, previous.start))
            if intersection > 0.35 * min(hit.end - hit.start, previous.end - previous.start):
                overlaps = True
                break
        if not overlaps:
            selected.append(hit)
        if len(selected) >= limit:
            break
    return selected


def _embedding_for_hit(page, hit):
    stored = page.embedding
    if isinstance(stored, dict):
        if stored.get("model") != settings().embedding_model:
            return None
        return next(
            (
                span.get("vector")
                for span in stored.get("passages", [])
                if span.get("start") == hit.start and span.get("end") == hit.end
            ),
            None,
        )
    # The legacy vector represented only the first 7000 characters of a page.
    return stored if isinstance(stored, list) and hit.start == 0 and hit.end <= 7000 else None


def retrieve(db, org_id, case_id, query, limit=5):
    # Authorization precedes text loading, indexing, provider calls and cache access.
    case = db.scalar(
        select(Case).where(
            Case.id == case_id, Case.org_id == org_id, Case.deleted.is_(False), Case.expires > now()
        )
    )
    if not case:
        return [], []
    document = db.scalar(select(Document).where(Document.id == case.document_id, Document.org_id == org_id))
    if not document:
        return [], []
    limit = max(1, min(int(limit), 8))
    pages = db.scalars(
        select(Page)
        .join(Document, Page.document_id == Document.id)
        .where(Document.id == document.id, Document.org_id == org_id, Page.status == "done")
        .order_by(Page.number)
    ).all()
    hits, index = _document_indexes.get(org_id, document.id, pages)
    ranked = index.rank(expand_query(query), limit=32)
    scores = dict(ranked)
    ranking = [idx for idx, _ in ranked]
    if settings().embedding_model and any(page.embedding for page in pages):
        try:
            vectors = ai.embed(org_id, [query])
            vector = vectors[0] if vectors else None
            page_map = {page.id: page for page in pages}
            semantic_scores = {
                idx: cosine(vector, _embedding_for_hit(page_map[hit.page_id], hit))
                for idx, hit in enumerate(hits)
            }
            semantic = [
                idx
                for idx in sorted(semantic_scores, key=semantic_scores.get, reverse=True)
                if semantic_scores[idx] >= 0.3
            ][:16]
            ranking = fuse(ranking, semantic)
            scores.update({idx: scores.get(idx, 0) + semantic_scores[idx] for idx in semantic})
        except ai.AIUnavailable:
            pass
    candidates = [replace(hits[idx], score=scores.get(idx, 0.0)) for idx in ranking[:24]]
    if settings().reranker_model and candidates:
        try:
            reranked = reranker().predict([(query, hit.text) for hit in candidates])
            candidates = [
                replace(hit, score=float(score))
                for score, hit in sorted(
                    zip(reranked, candidates), key=lambda row: float(row[0]), reverse=True
                )
                if math.isfinite(float(score))
            ]
        except (ImportError, OSError, RuntimeError, TypeError, ValueError):
            pass
    sources = db.scalars(
        select(Source).where(
            Source.category.in_([document.category, "general"]), Source.approved_until > now()
        )
    ).all()
    fact_dates = {
        fact["date_iso"]
        for fact in document.facts
        if fact.get("kind") == "notice_date" and fact.get("date_iso")
    }
    if len(fact_dates) != 1:
        sources = [source for source in sources if not source.valid_from and not source.valid_to]
    else:
        stamp = next(iter(fact_dates))
        sources = [
            source
            for source in sources
            if (not source.valid_from or source.valid_from <= stamp)
            and (not source.valid_to or source.valid_to >= stamp)
        ]
    source_hits = [
        SourceHit(
            source.id,
            source.url,
            source.title,
            source.digest,
            source.fetched,
            span.start,
            span.end,
            span.text,
        )
        for source in sources
        for span in split_passages(source.text)
    ]
    source_ranking = LexicalIndex(hit.text for hit in source_hits).rank(expand_query(query), limit=16)
    selected_sources = _distinct([replace(source_hits[idx], score=score) for idx, score in source_ranking], 3)
    return _distinct(candidates, limit), selected_sources
