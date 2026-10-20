"""Single API service entry point, with optional migrations before accepting traffic."""

import argparse
import os
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the Notice Clarity web service")
    parser.add_argument("--migrate", action="store_true", help="Apply database migrations before startup")
    args = parser.parse_args(argv)
    try:
        port = int(os.environ.get("PORT", "8000"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        parser.error("PORT must be an integer between 1 and 65535")
    if args.migrate:
        root = Path(__file__).resolve().parent.parent
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "migrations"))
        command.upgrade(config, "head")
    uvicorn.run(
        "notice.api:app",
        host="0.0.0.0",
        port=port,
        workers=1,
        lifespan="on",
        access_log=False,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
