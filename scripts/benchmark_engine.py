"""Reproducible synthetic passage-search benchmark; not a service capacity claim."""

import argparse
import json
import statistics
import time
from pathlib import Path

from notice.passages import split_passages
from notice.retrieval import LexicalIndex


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="engine-benchmark.json")
    args = parser.parse_args()
    pages = []
    for number in range(80):
        filler = "Supporting records and correspondence are attached for checking. " * 280
        pages.append(filler + f"\nUnique reference TARGET-{number:03d}-2026 requires a response.")
    start = time.perf_counter()
    spans = [span for page in pages for span in split_passages(page)]
    index = LexicalIndex(span.text for span in spans)
    build_ms = (time.perf_counter() - start) * 1000
    durations, hits = [], 0
    for number in range(80):
        start = time.perf_counter()
        ranked = index.rank(f"TARGET-{number:03d}-2026", limit=5)
        durations.append((time.perf_counter() - start) * 1000)
        hits += bool(ranked and f"TARGET-{number:03d}-2026" in spans[ranked[0][0]].text)
    result = {
        "scope": "Synthetic in-process lexical passage index; excludes OCR, DB, models and HTTP",
        "pages": len(pages),
        "characters": sum(map(len, pages)),
        "passages": len(spans),
        "queries": len(durations),
        "top1_exact_identifier_hits": hits,
        "build_ms": round(build_ms, 3),
        "query_median_ms": round(statistics.median(durations), 3),
        "query_p95_ms": round(sorted(durations)[int(len(durations) * 0.95) - 1], 3),
    }
    Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    if hits != len(durations):
        raise SystemExit("Synthetic identifier retrieval regression")


if __name__ == "__main__":
    main()
