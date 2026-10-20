"""Small read-path smoke measurement, not a capacity/SLA benchmark."""

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--output", default="smoke-load-results.json")
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=30) as client:
        result = client.post(
            "/api/auth/register",
            json={"email": f"load-{time.time_ns()}@example.com", "password": "synthetic-load-password-123"},
        )
        result.raise_for_status()
        if not result.json()["verified"]:
            raise SystemExit("Use isolated synthetic profile with verification disabled")

        def read(_):
            start = time.perf_counter()
            response = client.get("/api/cases")
            return response.status_code, (time.perf_counter() - start) * 1000

        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=8) as workers:
            results = list(workers.map(read, range(100)))
        elapsed = time.perf_counter() - start
    times = sorted(duration for _, duration in results)
    output = {
        "scope": "100 synthetic empty-workspace reads, one account, 8 threads, local host; not capacity proof",
        "requests": len(results),
        "success": sum(status == 200 for status, _ in results),
        "seconds": round(elapsed, 3),
        "median_ms": round(statistics.median(times), 2),
        "p95_ms": round(times[int(len(times) * 0.95) - 1], 2),
    }
    Path(args.output).write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output))
    if output["success"] != output["requests"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
