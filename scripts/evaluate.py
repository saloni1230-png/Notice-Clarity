import json
import statistics
import time
from pathlib import Path

from notice.models import Page
from notice.pipeline import baseline


def main():
    cases = json.loads(Path("evals/cases.json").read_text(encoding="utf-8"))
    results, times = [], []
    for item in cases:
        start = time.perf_counter()
        facts = baseline(Page(number=1, text=item["text"]))
        times.append((time.perf_counter() - start) * 1000)
        passed = (
            (not facts)
            if item["kind"] is None
            else any(f["kind"] == item["kind"] and f["date_iso"] == item["date"] for f in facts)
        )
        results.append({"name": item["name"], "passed": passed})
    output = {
        "suite": "synthetic deterministic extraction regression; NOT legal/domain validation",
        "cases": len(results),
        "passed": sum(r["passed"] for r in results),
        "median_ms": round(statistics.median(times), 3),
        "results": results,
    }
    Path("eval-results.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False))
    if not all(row["passed"] for row in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
