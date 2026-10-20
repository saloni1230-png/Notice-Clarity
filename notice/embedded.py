"""Run the existing durable jobs inside one API process when explicitly enabled."""

import json
import threading

from .db import session_scope
from .jobs import enqueue
from .models import now
from .worker import run_one

POOLS = ("mail", "extract", "ocr", "ai", "maintenance")


class EmbeddedProcessor:
    def __init__(self, poll_seconds=1.5):
        self._poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread = None
        self._maintenance_hour = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def start(self):
        if self._thread is not None:
            raise RuntimeError("Processor instance already started")
        self._thread = threading.Thread(target=self._run, name="notice-embedded", daemon=True)
        self._thread.start()

    def stop(self, timeout=10):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                # Do not release a lease while the original handler can still write.
                # A terminated host leaves it to expire and the next process reclaims it.
                self._event("embedded_shutdown_pending")
                return False
        return True

    def _schedule_maintenance(self):
        hour = now().strftime("%Y-%m-%dT%H")
        if hour != self._maintenance_hour:
            with session_scope() as db:
                enqueue(db, "retention:" + hour, "maintenance", "cleanup", {})
            self._maintenance_hour = hour

    @staticmethod
    def _event(event, error=None, pool=None):
        payload = {"event": event}
        if error is not None:
            payload["error"] = type(error).__name__
        if pool is not None:
            payload["pool"] = pool
        print(json.dumps(payload), flush=True)

    def _run(self):
        failures = 0
        self._event("embedded_started")
        try:
            while not self._stop.is_set():
                worked = False
                failed = False
                try:
                    self._schedule_maintenance()
                except Exception as error:
                    failed = True
                    self._event("embedded_poll_failed", error)
                # One job per pool per round: OCR cannot create concurrent heavy jobs,
                # and a backlog in one pool cannot indefinitely starve the others.
                for pool in POOLS:
                    if self._stop.is_set():
                        break
                    try:
                        worked = run_one(pool) or worked
                    except Exception as error:
                        failed = True
                        self._event("embedded_poll_failed", error, pool)
                failures = min(failures + 1, 5) if failed else 0
                if failed or not worked:
                    delay = min(30, self._poll_seconds * 2**failures)
                    self._stop.wait(delay)
        finally:
            self._event("embedded_stopped")
