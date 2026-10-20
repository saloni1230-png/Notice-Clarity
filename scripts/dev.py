"""Start API and independent durable worker processes; Ctrl+C stops owned children."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from alembic import command
from alembic.config import Config
from notice.config import settings


def main():
    command.upgrade(Config("alembic.ini"), "head")
    processes = []
    stopped = False
    stop_file = Path(os.environ["NS_DEV_STOP_FILE"]) if os.environ.get("NS_DEV_STOP_FILE") else None
    if stop_file:
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        stop_file.unlink(missing_ok=True)

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        for pool in ("extract", "ocr", "ai", "mail", "maintenance"):
            processes.append(subprocess.Popen([sys.executable, "-m", "notice.worker", "--pool", pool]))
        port = os.environ.get("NS_DEV_PORT", str(urlsplit(settings().public_url).port or 8000))
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "notice.api:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    port,
                    "--no-access-log",
                    "--no-proxy-headers",
                ]
            )
        )
        print(f"Local: http://localhost:{port}", flush=True)
        while not stopped and all(p.poll() is None for p in processes):
            if stop_file and stop_file.exists():
                break
            time.sleep(0.5)
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    main()
