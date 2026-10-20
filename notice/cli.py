import argparse
import json
import shutil
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import select

from .config import settings
from .db import session_scope
from .jobs import enqueue
from .knowledge import ingest
from .models import Document, Job, Source, now, uid


def main():
    parser = argparse.ArgumentParser(
        description="Explicit local administration; database credentials required"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("sources-fetch")
    fetch.add_argument("--manifest", default="notice/data/official-sources.json")
    inspect = commands.add_parser("source-show")
    inspect.add_argument("id")
    approve = commands.add_parser("source-approve")
    approve.add_argument("id")
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--days", type=int, default=14)
    approve.add_argument("--valid-from")
    approve.add_argument("--valid-to")
    commands.add_parser("sources-status")
    commands.add_parser("queue-status")
    commands.add_parser("doctor")
    redrive = commands.add_parser("redrive")
    redrive.add_argument("id")
    commands.add_parser("cleanup")
    reconcile = commands.add_parser("storage-orphans")
    reconcile.add_argument("--delete", action="store_true")
    args = parser.parse_args()
    with session_scope() as db:
        if args.command == "doctor":
            cfg = settings()
            queues = {}
            for pool, status in db.execute(
                select(Job.pool, Job.status).where(Job.status.in_(["queued", "running", "dead"]))
            ):
                key = f"{pool}:{status}"
                queues[key] = queues.get(key, 0) + 1
            print(
                json.dumps(
                    {
                        "engine_version": cfg.pipeline_version,
                        "ai": {
                            "configured": bool(cfg.ai_api_key and cfg.ai_model),
                            "model": cfg.ai_model or None,
                            "mode": "structured_ai"
                            if cfg.ai_api_key and cfg.ai_model
                            else "document_facts_only",
                            "embedding_model": cfg.embedding_model or None,
                            "daily_token_budget": cfg.org_daily_tokens,
                            "daily_cost_usd": cfg.org_daily_cost_usd,
                        },
                        "ocr_executable_found": bool(shutil.which(cfg.tesseract_path)),
                        "scanner_configured": bool(cfg.scanner_host),
                        "queues": queues,
                        "approved_source_snapshots": len(
                            db.scalars(select(Source.id).where(Source.approved_until > now())).all()
                        ),
                        "provider_connection_tested": False,
                    },
                    indent=2,
                )
            )
        elif args.command == "sources-fetch":
            for item in json.loads(Path(args.manifest).read_text(encoding="utf-8")):
                try:
                    row = ingest(db, item["url"], item["title"], item["category"])
                    db.commit()
                    print(
                        json.dumps(
                            {
                                "id": row.id,
                                "title": row.title,
                                "digest": row.digest,
                                "status": "review_required",
                            },
                            ensure_ascii=False,
                        )
                    )
                except Exception as exc:
                    db.rollback()
                    print(json.dumps({"url": item["url"], "error": type(exc).__name__}))
        elif args.command == "source-show":
            row = db.get(Source, args.id)
            if not row:
                raise SystemExit("Source not found")
            print(
                json.dumps(
                    {"id": row.id, "url": row.url, "digest": row.digest, "text": row.text}, ensure_ascii=False
                )
            )
        elif args.command == "source-approve":
            if not 1 <= args.days <= 30 or len(args.reviewer.strip()) < 3:
                raise SystemExit("Approval requires named reviewer and freshness window <= 30 days")
            row = db.get(Source, args.id)
            if not row:
                raise SystemExit("Source not found")
            for value in (args.valid_from, args.valid_to):
                if value:
                    date.fromisoformat(value)
            if args.valid_from and args.valid_to and args.valid_from > args.valid_to:
                raise SystemExit("Invalid effective interval")
            # Only the latest fetched snapshot of each URL may be approved.
            latest = db.scalar(select(Source).where(Source.url == row.url).order_by(Source.fetched.desc()))
            if latest.id != row.id:
                raise SystemExit("Superseded source snapshot")
            row.approved_until = now() + timedelta(days=args.days)
            row.reviewer = args.reviewer
            row.valid_from, row.valid_to = args.valid_from, args.valid_to
            print("Approved", row.id, row.digest)
        elif args.command == "sources-status":
            for row in db.scalars(select(Source).order_by(Source.fetched.desc())):
                print(row.id, row.category, row.approved_until, row.title)
        elif args.command == "queue-status":
            for row in db.scalars(
                select(Job)
                .where(Job.status.in_(["dead", "running", "queued"]))
                .order_by(Job.created)
                .limit(500)
            ):
                print(row.id, row.pool, row.kind, row.status, row.attempts, row.error)
        elif args.command == "redrive":
            row = db.scalar(select(Job).where(Job.id == args.id).with_for_update())
            if not row or row.status != "dead":
                raise SystemExit("Only a dead job can be redriven")
            # Dead payloads contain only entity IDs except mail jobs, whose tokens are cleared.
            if row.kind == "mail" or not row.payload:
                raise SystemExit("Payload erased; issue a new account email or re-upload document")
            row.status, row.attempts, row.available, row.error = "queued", 0, now(), ""
            print("Queued", row.id)
        elif args.command == "cleanup":
            enqueue(db, f"manual-cleanup:{uid()}", "maintenance", "cleanup", {})
            print("Cleanup queued")
        elif args.command == "storage-orphans":
            if settings().storage != "local":
                raise SystemExit(
                    "For S3, use inventory joined to documents.object_key; instructions in operations.txt"
                )
            known = set(db.scalars(select(Document.object_key)))
            root = settings().local_storage.resolve()
            for path in root.rglob("original"):
                key = path.relative_to(root).as_posix()
                # Avoid racing in-progress uploads; only objects older than 24 h qualify.
                if key not in known and path.stat().st_mtime < now().timestamp() - 86400:
                    print(key)
                    if args.delete:
                        path.unlink()


if __name__ == "__main__":
    main()
