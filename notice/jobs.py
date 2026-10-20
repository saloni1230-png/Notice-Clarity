from datetime import timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import aliased
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .config import settings
from .db import session_scope
from .models import Job, Org, now, uid


def enqueue(db, key, pool, kind, payload, org_id=None):
    values = dict(
        id=uid(),
        key=key,
        pool=pool,
        kind=kind,
        payload=payload,
        org_id=org_id,
        status="queued",
        attempts=0,
        available=now(),
        error="",
        created=now(),
    )
    if db.bind.dialect.name == "postgresql":
        db.execute(pg_insert(Job).values(**values).on_conflict_do_nothing(index_elements=[Job.key]))
    elif not db.scalar(select(Job).where(Job.key == key)):
        db.add(Job(**values))


def claim(pool):
    with session_scope() as db:
        current = now()
        query = select(Job).where(
            Job.pool == pool,
            Job.available <= current,
            or_(Job.status == "queued", (Job.status == "running") & (Job.lease_until < current)),
        )
        active_job = aliased(Job)
        active_count = (
            select(func.count())
            .select_from(active_job)
            .where(
                active_job.org_id == Job.org_id,
                active_job.status == "running",
                active_job.lease_until > current,
            )
            .correlate(Job)
            .scalar_subquery()
        )
        query = query.where(or_(Job.org_id.is_(None), active_count < settings().max_jobs_per_org))
        candidates = db.scalars(
            query.order_by(Job.available, Job.created).with_for_update(skip_locked=True).limit(1)
        ).all()
        for job in candidates:
            if job.org_id:
                db.scalar(select(Org).where(Org.id == job.org_id).with_for_update())
                active = db.scalar(
                    select(func.count())
                    .select_from(Job)
                    .where(Job.org_id == job.org_id, Job.status == "running", Job.lease_until > current)
                )
                if active >= settings().max_jobs_per_org:
                    continue
            job.owner = uid()
            job.status = "running"
            job.attempts += 1
            job.lease_until = current + timedelta(seconds=settings().lease_seconds)
            db.flush()
            return job
    return None


def owns(db, job):
    # Row lock prevents a stale worker from committing results after a new claim.
    row = db.scalar(select(Job).where(Job.id == job.id).with_for_update())
    return bool(
        row
        and row.status == "running"
        and row.owner == job.owner
        and row.lease_until
        and row.lease_until > now()
    )


def heartbeat(job):
    with session_scope() as db:
        result = db.execute(
            update(Job)
            .where(Job.id == job.id, Job.owner == job.owner, Job.status == "running")
            .values(lease_until=now() + timedelta(seconds=settings().lease_seconds))
        )
        return bool(result.rowcount)


def finish(db, job):
    row = db.get(Job, job.id)
    row.status = "done"
    row.lease_until = None
    row.payload = {}  # Recovery/verification email tokens must not persist after delivery.


def retry(job, error):
    with session_scope() as db:
        if not owns(db, job):
            return
        row = db.get(Job, job.id)
        row.error = type(error).__name__[:100]  # Never store provider bodies, document text or secrets.
        row.status = "dead" if row.attempts >= 4 else "queued"
        row.available = now() + timedelta(seconds=min(300, 2**row.attempts * 5))
        row.lease_until = None
        if row.status == "dead":
            from .pipeline import mark_failed

            mark_failed(db, row)
            if row.kind == "mail":
                row.payload = {}
