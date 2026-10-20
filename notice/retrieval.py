import math
import re
from collections import Counter, defaultdict
from functools import lru_cache

from sqlalchemy import select

from . import ai
from .config import settings
from .models import Case, Document, Page, Source, now


def tokens(text):
    # Word tokens and character trigrams support inflections and document identifiers.
    words = re.findall(r"\w+", text.lower())
    return words + [word[i : i + 3] for word in words if len(word) > 4 for i in range(len(word) - 2)]


def lexical(query, documents):
    query_tokens = set(tokens(query))
    counts = [Counter(tokens(text)) for text in documents]
    n = len(counts)
    avg = sum(sum(c.values()) for c in counts) / max(n, 1) or 1
    scores = []
    for index, count in enumerate(counts):
        score = 0
        length = sum(count.values())
        for word in query_tokens:
            freq = count[word]
            if not freq:
                continue
            df = sum(word in row for row in counts)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            score += idf * freq * 2.2 / (freq + 1.2 * (0.25 + 0.75 * length / avg))
        scores.append((index, score))
    return [idx for idx, score in sorted(scores, key=lambda row: row[1], reverse=True) if score > 0]


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return -1
    denom = math.sqrt(sum(x * x for x in a) * sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / denom if denom else 0


def fuse(*rankings):
    scores = defaultdict(float)
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] += 1 / (60 + rank + 1)
    return sorted(scores, key=scores.get, reverse=True)


@lru_cache(maxsize=1)
def reranker():
    from sentence_transformers import CrossEncoder

    return CrossEncoder(settings().reranker_model, device="cpu", trust_remote_code=False)


def retrieve(db, org_id, case_id, query, limit=5):
    # Authorization is applied in SQL before ANY text enters retrieval, embeddings or reranking.
    case = db.scalar(
        select(Case).where(
            Case.id == case_id, Case.org_id == org_id, Case.deleted.is_(False), Case.expires > now()
        )
    )
    if not case:
        return [], []
    pages = db.scalars(
        select(Page)
        .join(Document, Page.document_id == Document.id)
        .where(Document.id == case.document_id, Document.org_id == org_id, Page.status == "done")
        .order_by(Page.number)
    ).all()
    expanded = query
    for term, expansion in {
        "notice": "notice",
        "date": "date deadline reply",
        "amount": "amount INR Rs",
        "documents": "documents provide required",
        "requested": "required provide reply",
        "income tax": "income tax",
        "bank": "bank account",
    }.items():
        if term in query.lower():
            expanded += " " + expansion
    ranking = lexical(expanded, [p.text for p in pages])
    if settings().embedding_model and any(p.embedding for p in pages):
        try:
            vector = ai.embed(org_id, [query])[0]
            semantic = sorted(
                range(len(pages)), key=lambda idx: cosine(vector, pages[idx].embedding), reverse=True
            )
            ranking = fuse(ranking, [idx for idx in semantic if pages[idx].embedding])
        except ai.AIUnavailable:
            pass
    candidates = [pages[idx] for idx in ranking[:12]]
    if settings().reranker_model and candidates:
        scores = reranker().predict([(query, p.text[:6000]) for p in candidates])
        candidates = [p for _, p in sorted(zip(scores, candidates), key=lambda x: float(x[0]), reverse=True)]
    document = db.get(Document, case.document_id)
    sources = db.scalars(
        select(Source).where(
            Source.category.in_([document.category, "general"]), Source.approved_until > now()
        )
    ).all()
    # Never silently choose a law's temporal applicability when notice date/context is absent.
    fact_dates = [f["date_iso"] for f in document.facts if f["kind"] == "notice_date" and f.get("date_iso")]
    if len(set(fact_dates)) != 1:
        sources = [s for s in sources if not s.valid_from and not s.valid_to]
    else:
        stamp = fact_dates[0]
        sources = [
            s
            for s in sources
            if (not s.valid_from or s.valid_from <= stamp) and (not s.valid_to or s.valid_to >= stamp)
        ]
    source_rank = lexical(query, [s.text for s in sources])
    return candidates[:limit], [sources[idx] for idx in source_rank[:3]]
