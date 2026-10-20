import hashlib
import hmac
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .config import settings
from .db import get_db
from .models import Audit, LoginSession, Member, RateBucket, User, now

passwords = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
DUMMY = passwords.hash("constant-dummy-password-for-timing")


def fingerprint(value: str) -> str:
    return hmac.new(settings().secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def password_ok(encoded: str, value: str) -> bool:
    try:
        return passwords.verify(encoded, value)
    except (VerificationError, InvalidHashError):
        return False


def audit(db, org: str, actor: str, event: str, target: str):
    db.add(Audit(org_id=org, actor=actor, event=event, target=target))


def aware(value):
    return value.replace(tzinfo=now().tzinfo) if value.tzinfo is None else value


def rate_limit(db, scope: str, key: str, limit: int, seconds: int = 60):
    stamp = int(now().timestamp()) // seconds
    bid = f"{scope}:{fingerprint(key)[:40]}:{stamp}"
    values = dict(id=bid, count=0, expires=now() + timedelta(seconds=seconds * 2))
    if db.bind.dialect.name == "postgresql":
        db.execute(pg_insert(RateBucket).values(**values).on_conflict_do_nothing())
    elif not db.get(RateBucket, bid):
        db.add(RateBucket(**values))
        db.flush()
    bucket = db.scalar(select(RateBucket).where(RateBucket.id == bid).with_for_update())
    bucket.count += 1
    db.commit()  # Failed attempts must consume a slot too.
    if bucket.count > limit:
        raise HTTPException(
            429, "Too many requests. Please try again later.", headers={"Retry-After": str(seconds)}
        )


def same_origin(request: Request):
    expected = urlsplit(settings().public_url)
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != f"{expected.scheme}://{expected.netloc}":
        raise HTTPException(403, "Origin rejected")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site request rejected")


def identity(request: Request, db=Depends(get_db)):
    raw = request.cookies.get("ns_session", "")
    session = db.get(LoginSession, fingerprint(raw)) if raw else None
    current = now()
    if (
        not session
        or aware(session.expires) <= current
        or (current - aware(session.touched) > timedelta(minutes=settings().session_idle_minutes))
    ):
        raise HTTPException(401, "Please sign in again.")
    user = db.get(User, session.user_id)
    if not user:
        raise HTTPException(401)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        same_origin(request)
        if not secrets.compare_digest(session.csrf, request.headers.get("x-csrf-token", "")):
            raise HTTPException(403, "CSRF token missing or invalid")
    session.touched = current
    request.state.actor = user.id
    return user, session


def membership(request: Request, auth=Depends(identity), db=Depends(get_db)):
    user, _ = auth
    if settings().require_email_verification and not user.verified:
        raise HTTPException(403, "Verify your email to continue.")
    org_id = request.headers.get("x-org-id")
    query = select(Member).where(Member.user_id == user.id)
    if org_id:
        query = query.where(Member.org_id == org_id)
    member = db.scalar(query.order_by(Member.org_id))
    if not member:
        raise HTTPException(403, "Workspace access denied")
    return user, member


def require_role(member, *roles):
    if member.role not in roles:
        raise HTTPException(403, "You do not have permission to perform this action.")


def create_session(db, user):
    # Cap concurrent sessions, preserving the latest five.
    sessions = db.scalars(
        select(LoginSession).where(LoginSession.user_id == user.id).order_by(LoginSession.created.desc())
    ).all()
    for old in sessions[4:]:
        db.delete(old)
    raw = secrets.token_urlsafe(48)
    row = LoginSession(
        id=fingerprint(raw),
        user_id=user.id,
        csrf=secrets.token_urlsafe(32),
        expires=now() + timedelta(hours=settings().session_hours),
    )
    db.add(row)
    return raw, row


def revoke_sessions(db, user_id):
    db.execute(delete(LoginSession).where(LoginSession.user_id == user_id))
