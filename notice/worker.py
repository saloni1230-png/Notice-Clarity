import argparse
import json
import signal
import threading
import time

from .config import settings
from .db import session_scope
from .jobs import claim, enqueue, heartbeat, retry
from .models import now
from .pipeline import dispatch

stopping = threading.Event()


def run_one(pool):
    job = claim(pool)
    if not job:
        return False
    done = threading.Event()

    def keep_lease():
        while not done.wait(settings().lease_seconds / 3):
            try:
                if not heartbeat(job):
                    break
            except Exception:
                break

    keeper = threading.Thread(target=keep_lease, daemon=True)
    keeper.start()
    started = time.monotonic()
    outcome = "done"
    try:
        dispatch(job)
    except Exception as error:
        outcome = "retry"
        retry(job, error)
    finally:
        done.set()
        keeper.join(timeout=2)
        print(
            json.dumps(
                {
                    "job_id": job.id,
                    "kind": job.kind,
                    "pool": pool,
                    "outcome": outcome,
                    "seconds": round(time.monotonic() - started, 3),
                }
            ),
            flush=True,
        )
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", choices=["extract", "ocr", "ai", "mail", "maintenance"], required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopping.set())
    while not stopping.is_set():
        if args.pool == "maintenance":
            with session_scope() as db:
                hour = now().strftime("%Y-%m-%dT%H")
                enqueue(db, "retention:" + hour, "maintenance", "cleanup", {})
        worked = run_one(args.pool)
        if args.once:
            break
        if not worked:
            stopping.wait(1.5)


if __name__ == "__main__":
    main()
