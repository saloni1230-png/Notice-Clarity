import asyncio
import json
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from . import SCHEMA_REVISION, storage
from .config import settings
from .db import get_db
from .evidence import evidence_boxes
from .jobs import enqueue
from .models import (
    Action,
    Audit,
    Budget,
    Case,
    Document,
    Job,
    LoginSession,
    Member,
    Org,
    Page,
    Question,
    Review,
    Source,
    Token,
    User,
    now,
    uid,
)
from .schemas import (
    ActionInput,
    ActionUpdate,
    ContextInput,
    Credentials,
    EmailRequest,
    MemberInput,
    QuestionInput,
    ResetRequest,
    ReviewInput,
    TokenRequest,
)
from .security import (
    DUMMY,
    audit,
    create_session,
    fingerprint,
    identity,
    membership,
    password_ok,
    passwords,
    rate_limit,
    require_role,
    revoke_sessions,
    same_origin,
)
from .workspace import context_digest, schedule_summary

WEB = Path(__file__).parent / "web"
REQUESTS = Counter("notice_http_requests", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram("notice_http_seconds", "HTTP latency", ["route"])


@asynccontextmanager
async def lifespan(application):
    processor = None
    try:
        if settings().embedded_worker:
            from .embedded import EmbeddedProcessor

            processor = EmbeddedProcessor()
            application.state.embedded_processor = processor
            processor.start()
        yield
    finally:
        if processor is not None:
            await asyncio.to_thread(processor.stop)
            application.state.embedded_processor = None


app = FastAPI(
    title="Notice Clarity",
    version="1.0.1",
    docs_url=None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings().environment != "production" else None,
    lifespan=lifespan,
)


class BodyTooLarge(Exception):
    pass


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = settings().max_upload_mb * 1024 * 1024 + 65536 if scope["path"] == "/api/cases" else 65536
        total = 0
        started = False

        async def bounded_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > limit:
                raise BodyTooLarge()
            return message

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = limit + 1
        if length > limit:
            return await JSONResponse({"detail": "Upload too large"}, 413)(scope, receive, send)
        try:
            await self.app(scope, bounded_receive, tracking_send)
        except BodyTooLarge:
            if not started:
                await JSONResponse({"detail": "Upload too large"}, 413)(scope, receive, send)


app.add_middleware(BodyLimit)
if settings().environment == "production":
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(settings().public_url).hostname])


@app.middleware("http")
async def headers_and_metrics(request: Request, call_next):
    started = time.monotonic()
    response = await call_next(request)
    route = getattr(request.scope.get("route"), "path", "unmatched")
    REQUESTS.labels(request.method, route, response.status_code).inc()
    LATENCY.labels(route).observe(time.monotonic() - started)
    response.headers.update(
        {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'",
            "X-Request-ID": uid(),
        }
    )
    if settings().secure_cookies:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(IntegrityError)
async def integrity_error(request, exc):
    return JSONResponse({"detail": "Concurrent update; refresh and try again."}, status_code=409)


@app.get("/health/live")
def live():
    return {"status": "alive"}


@app.get("/health/ready")
def ready(db=Depends(get_db)):
    if settings().embedded_worker:
        processor = getattr(app.state, "embedded_processor", None)
        if processor is None or not processor.running:
            raise HTTPException(503, "Embedded processing unavailable")
    try:
        versions = list(db.scalars(text("SELECT version_num FROM alembic_version")))
    except SQLAlchemyError as error:
        raise HTTPException(503, "Database unavailable or migrations missing") from error
    if versions != [SCHEMA_REVISION]:
        raise HTTPException(503, "Database schema version mismatch")
    return {"status": "ready"}


@app.get("/metrics")
def metrics(request: Request, db=Depends(get_db)):
    token = settings().metrics_token
    if not token or not secrets.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
        raise HTTPException(403)
    # Queue/budget gauges are queried centrally; HTTP histograms are per process.
    base = generate_latest().decode()
    for pool, status, count in db.execute(
        select(Job.pool, Job.status, func.count()).group_by(Job.pool, Job.status)
    ):
        base += f'notice_jobs{{pool="{pool}",status="{status}"}} {count}\n'
    return Response(base, media_type=CONTENT_TYPE_LATEST)


def account_token(db, user, purpose):
    db.execute(delete(Token).where(Token.user_id == user.id, Token.purpose == purpose))
    raw = secrets.token_urlsafe(40)
    db.add(
        Token(id=fingerprint(raw), user_id=user.id, purpose=purpose, expires=now() + timedelta(minutes=30))
    )
    from .crypto import seal

    payload = {
        "to": user.email,
        "subject": "Email verification" if purpose == "verify" else "Password reset",
        "body": "Open this link within 30 minutes. Ignore this email if you did not request it.\n\n"
        + settings().public_url
        + "/#"
        + purpose
        + "="
        + raw,
    }
    enqueue(db, f"mail:{uid()}", "mail", "mail", {"sealed": seal(payload)})


def ip(request):
    # Forwarded headers are trusted only by a deliberately configured reverse proxy.
    return request.client.host if request.client else "unknown"


@app.post("/api/auth/register", status_code=201)
def register(body: Credentials, request: Request, response: Response, db=Depends(get_db)):
    same_origin(request)
    rate_limit(db, "register", ip(request), 5, 3600)
    if db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(409, "This email is already registered. Sign in or reset your password.")
    user = User(
        email=body.email,
        password_hash=passwords.hash(body.password),
        verified=not settings().require_email_verification,
    )
    org = Org(name="My workspace")
    db.add_all([user, org])
    db.flush()
    db.add(Member(user_id=user.id, org_id=org.id, role="owner"))
    if not user.verified:
        account_token(db, user, "verify")
    raw, session = create_session(db, user)
    audit(db, org.id, user.id, "account.created", user.id)
    set_cookie(response, raw)
    return {"csrf": session.csrf, "verified": user.verified}


def set_cookie(response, raw):
    response.set_cookie(
        "ns_session",
        raw,
        httponly=True,
        secure=settings().secure_cookies,
        samesite="strict",
        max_age=settings().session_hours * 3600,
        path="/",
    )


@app.post("/api/auth/login")
def login(body: Credentials, request: Request, response: Response, db=Depends(get_db)):
    same_origin(request)
    rate_limit(db, "login-ip", ip(request), 30, 900)
    rate_limit(db, "login-email", body.email, 10, 900)
    user = db.scalar(select(User).where(User.email == body.email))
    valid = password_ok(user.password_hash if user else DUMMY, body.password)
    if not user or not valid:
        raise HTTPException(401, "Incorrect email or password.")
    if passwords.check_needs_rehash(user.password_hash):
        user.password_hash = passwords.hash(body.password)
    raw, session = create_session(db, user)
    set_cookie(response, raw)
    return {"csrf": session.csrf, "verified": user.verified}


@app.get("/api/me")
def me(auth=Depends(identity), db=Depends(get_db)):
    user, session = auth
    orgs = [
        {"id": member.org_id, "name": org.name, "role": member.role}
        for member, org in db.execute(
            select(Member, Org).join(Org, Org.id == Member.org_id).where(Member.user_id == user.id)
        )
    ]
    return {
        "email": user.email,
        "user_id": user.id,
        "verified": user.verified or not settings().require_email_verification,
        "email_verified": user.verified,
        "email_verification_required": settings().require_email_verification,
        "csrf": session.csrf,
        "orgs": orgs,
    }


@app.post("/api/auth/logout")
def logout(response: Response, auth=Depends(identity), db=Depends(get_db)):
    db.delete(auth[1])
    response.delete_cookie("ns_session", path="/")
    return {"ok": True}


@app.get("/api/auth/sessions")
def sessions(auth=Depends(identity), db=Depends(get_db)):
    return [
        {"id": row.id, "created": row.created, "expires": row.expires, "current": row.id == auth[1].id}
        for row in db.scalars(select(LoginSession).where(LoginSession.user_id == auth[0].id))
    ]


@app.delete("/api/auth/sessions/{session_id}")
def revoke(session_id: str, auth=Depends(identity), db=Depends(get_db)):
    row = db.get(LoginSession, session_id)
    if row and row.user_id == auth[0].id:
        db.delete(row)
    return {"ok": True}


@app.post("/api/auth/recover", status_code=202)
def recover(body: EmailRequest, request: Request, db=Depends(get_db)):
    same_origin(request)
    rate_limit(db, "recover-ip", ip(request), 10, 3600)
    rate_limit(db, "recover-email", body.email.lower(), 3, 3600)
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user:
        account_token(db, user, "reset")
    return {"message": "If an account exists, a password reset link will be emailed."}


@app.post("/api/auth/reset")
def reset(body: ResetRequest, request: Request, db=Depends(get_db)):
    same_origin(request)
    rate_limit(db, "reset", ip(request), 10, 3600)
    token = db.scalar(
        select(Token).where(Token.id == fingerprint(body.token), Token.purpose == "reset").with_for_update()
    )
    if not token or token.expires <= now():
        raise HTTPException(400, "The link is invalid or has expired.")
    user = db.get(User, token.user_id)
    user.password_hash = passwords.hash(body.password)
    revoke_sessions(db, user.id)
    db.execute(delete(Token).where(Token.user_id == user.id))
    return {"ok": True}


@app.post("/api/auth/verify")
def verify(body: TokenRequest, request: Request, db=Depends(get_db)):
    same_origin(request)
    rate_limit(db, "verify", ip(request), 20, 3600)
    token = db.scalar(
        select(Token).where(Token.id == fingerprint(body.token), Token.purpose == "verify").with_for_update()
    )
    if not token or token.expires <= now():
        raise HTTPException(400, "The verification link is invalid or has expired.")
    db.get(User, token.user_id).verified = True
    db.delete(token)
    return {"ok": True}


@app.post("/api/auth/resend")
def resend(auth=Depends(identity), db=Depends(get_db)):
    user = auth[0]
    rate_limit(db, "resend", user.id, 3, 3600)
    if not user.verified:
        account_token(db, user, "verify")
    return {"ok": True}


def get_case(db, case_id, member):
    case = db.scalar(
        select(Case).where(
            Case.id == case_id, Case.org_id == member.org_id, Case.deleted.is_(False), Case.expires > now()
        )
    )
    if not case:
        raise HTTPException(404, "Case not found.")
    return case


@app.get("/api/cases")
def list_cases(offset: int = 0, auth=Depends(membership), db=Depends(get_db)):
    offset = max(0, min(offset, 100000))
    return [
        {
            "id": case.id,
            "title": case.title,
            "created": case.created,
            "status": doc.status,
            "category": doc.category,
            "pages": doc.page_count,
        }
        for case, doc in db.execute(
            select(Case, Document)
            .join(Document)
            .where(Case.org_id == auth[1].org_id, Case.deleted.is_(False), Case.expires > now())
            .order_by(Case.created.desc())
            .offset(offset)
            .limit(50)
        )
    ]


@app.post("/api/cases", status_code=202)
def upload(file: UploadFile, request: Request, auth=Depends(membership), db=Depends(get_db)):
    user, member = auth
    rate_limit(db, "upload", member.org_id, settings().max_documents_per_day, 86400)
    data = file.file.read(settings().max_upload_mb * 1024 * 1024 + 1)
    try:
        mime, pages = storage.validate_upload(data)
    except (storage.InvalidUpload, OSError) as error:
        raise HTTPException(422, str(error)) from error
    digest = storage.digest(data)
    # Serialize tenant deduplication and reference creation against concurrent uploads/GC.
    db.scalar(select(Org).where(Org.id == member.org_id).with_for_update())
    doc = db.scalar(
        select(Document).where(
            Document.org_id == member.org_id,
            Document.digest == digest,
            Document.version == settings().pipeline_version,
        )
    )
    filename = re.sub(r"[^\w.()\- ]", "_", file.filename or "notice")[:150]
    duplicate = bool(doc)
    if not doc:
        doc_id = uid()
        key = f"{member.org_id}/{doc_id}/original"
        # Upload failures cannot create a DB case. Orphan objects after DB failure are reconciled by CLI.
        storage.put(key, data, mime)
        doc = Document(
            id=doc_id,
            org_id=member.org_id,
            digest=digest,
            version=settings().pipeline_version,
            analysis_version=settings().pipeline_version,
            object_key=key,
            filename=filename,
            mime=mime,
            bytes=len(data),
            page_count=pages,
        )
        db.add(doc)
        db.flush()
        enqueue(db, f"inspect:{doc.id}", "extract", "inspect", {"document_id": doc.id}, member.org_id)
    case = Case(
        org_id=member.org_id,
        document_id=doc.id,
        created_by=user.id,
        title=filename,
        expires=now() + timedelta(days=settings().document_days),
    )
    db.add(case)
    db.flush()
    for title in [
        "Verify the notice on the official portal",
        "Keep the original notice and supporting documents",
        "Ask your accountant or lawyer to confirm the context and dates before responding",
    ]:
        db.add(Action(case_id=case.id, title=title))
    db.add(Review(case_id=case.id, reasons=doc.flags + ["legal_applicability_requires_review"]))
    if doc.status in {"ready", "review_required"}:
        schedule_summary(db, case)
    audit(db, member.org_id, user.id, "case.uploaded", case.id)
    return {"id": case.id, "deduplicated": duplicate}


@app.get("/api/cases/{case_id}")
def detail(case_id: str, auth=Depends(membership), db=Depends(get_db)):
    user, member = auth
    case = get_case(db, case_id, member)
    doc = db.get(Document, case.document_id)
    pages = db.scalars(select(Page).where(Page.document_id == doc.id).order_by(Page.number)).all()
    review = db.scalar(select(Review).where(Review.case_id == case.id))
    budget = db.get(Budget, f"{member.org_id}:{now().date().isoformat()}")
    summary = db.scalar(select(Question).where(Question.case_id == case.id, Question.kind == "summary"))
    signature = context_digest(case.context)
    fresh_sources = set(db.scalars(select(Source.id).where(Source.approved_until > now())))

    def is_stale(answer):
        return bool(answer) and (
            answer.get("context_hash") != signature
            or answer.get("generation", doc.generation) != doc.generation
            or answer.get("pipeline_version", doc.analysis_version) != doc.analysis_version
            or any(source_id not in fresh_sources for source_id in answer.get("sources", {}))
        )

    return {
        "id": case.id,
        "title": case.title,
        "created": case.created,
        "expires": case.expires,
        "context": case.context,
        "status": doc.status,
        "category": doc.category,
        "flags": doc.flags,
        "facts": doc.facts,
        "pipeline_version": doc.analysis_version or doc.version,
        "engine_version": settings().pipeline_version,
        "ai_enabled": bool(settings().ai_api_key),
        "search": {
            "lexical": True,
            "semantic": bool(settings().embedding_model),
            "reranker": bool(settings().reranker_model),
        },
        "explanation": {
            "id": summary.id,
            "status": summary.status,
            "answer": summary.answer,
            "stale": is_stale(summary.answer),
        }
        if summary
        else None,
        "pages": [
            {"number": p.number, "status": p.status, "quality": p.quality, "method": p.method} for p in pages
        ],
        "page_count": doc.page_count,
        "actions": [
            {"id": a.id, "title": a.title, "done": a.done, "due": a.due, "evidence": a.evidence}
            for a in db.scalars(select(Action).where(Action.case_id == case.id).order_by(Action.id))
        ],
        "questions": [
            {
                "id": q.id,
                "question": q.question,
                "status": q.status,
                "answer": q.answer,
                "stale": is_stale(q.answer),
            }
            for q in db.scalars(
                select(Question)
                .where(Question.case_id == case.id, Question.kind == "question")
                .order_by(Question.created.desc())
                .limit(30)
            )
        ],
        "review": {"status": review.status, "reasons": review.reasons, "note": review.note}
        if review
        else None,
        "budget": {
            "reserved_tokens": budget.tokens if budget else 0,
            "reserved_usd": budget.cost if budget else 0,
            "daily_usd": settings().org_daily_cost_usd,
        },
    }


@app.get("/api/cases/{case_id}/pages/{number}")
def page_text(case_id: str, number: int, quote: str = "", auth=Depends(membership), db=Depends(get_db)):
    case = get_case(db, case_id, auth[1])
    page = db.scalar(select(Page).where(Page.document_id == case.document_id, Page.number == number))
    if not page:
        raise HTTPException(404)
    audit(db, auth[1].org_id, auth[0].id, "page.read", f"{case.id}:{number}")
    return {
        "text": page.text,
        "quality": page.quality,
        "method": page.method,
        "boxes": evidence_boxes(page, quote[:1200]) if quote else [],
    }


@app.get("/api/cases/{case_id}/pages/{number}/image")
def page_image(case_id: str, number: int, auth=Depends(membership), db=Depends(get_db)):
    case = get_case(db, case_id, auth[1])
    doc = db.scalar(select(Document).where(Document.id == case.document_id).with_for_update())
    if number < 1 or number > doc.page_count:
        raise HTTPException(404)
    from .documents import render_page

    audit(db, auth[1].org_id, auth[0].id, "page.image", f"{case.id}:{number}")
    key = f"{doc.object_key}.page{number}.png"
    image = storage.optional_get(key)
    if image is None:
        image = render_page(storage.get(doc.object_key), number)
        storage.put(key, image, "image/png")
    return Response(image, media_type="image/png")


@app.patch("/api/cases/{case_id}/context")
def context(case_id: str, body: ContextInput, auth=Depends(membership), db=Depends(get_db)):
    case = get_case(db, case_id, auth[1])
    if body.received_on:
        try:
            if date.fromisoformat(body.received_on) > now().date():
                raise ValueError()
        except ValueError as exc:
            raise HTTPException(422, "Enter a valid receipt date.") from exc
    case.context = body.model_dump()
    review = db.scalar(select(Review).where(Review.case_id == case.id))
    if review:
        review.status = "open"
        review.updated = now()
    # Recompute immediately; saving context must not leave a permanently stale summary.
    schedule_summary(db, case, refresh=True)
    audit(db, auth[1].org_id, auth[0].id, "context.updated", case.id)
    return {"ok": True}


@app.post("/api/cases/{case_id}/reprocess", status_code=202)
def reprocess(case_id: str, auth=Depends(membership), db=Depends(get_db)):
    case = get_case(db, case_id, auth[1])
    if auth[1].role not in {"owner", "admin"}:
        raise HTTPException(403, "Only a workspace owner or admin can reanalyze a notice.")
    rate_limit(db, "reprocess", case.document_id, 3, 3600)
    doc = db.scalar(select(Document).where(Document.id == case.document_id).with_for_update())
    if doc.status in {"queued", "extracting"}:
        raise HTTPException(409, "This notice is still processing.")
    if db.scalar(
        select(Page.id).where(Page.document_id == doc.id, Page.status.not_in(["done", "failed"])).limit(1)
    ):
        raise HTTPException(409, "Some pages are still processing.")
    doc.generation += 1
    doc.analysis_version = settings().pipeline_version
    doc.status = "extracting"
    existing = {p.number: p for p in db.scalars(select(Page).where(Page.document_id == doc.id))}
    for number in range(1, doc.page_count + 1):
        page = existing.get(number)
        if page is None:
            page = Page(document_id=doc.id, number=number, text="")
            db.add(page)
        kind = "understand" if page.status == "done" and page.text.strip() else "extract"
        pool = "ai" if kind == "understand" else "extract"
        page.status = "understanding" if kind == "understand" else "pending"
        enqueue(
            db,
            f"reprocess:{doc.id}:{doc.generation}:{page.number}",
            pool,
            kind,
            {"document_id": doc.id, "page": page.number, "generation": doc.generation},
            doc.org_id,
        )
    audit(db, auth[1].org_id, auth[0].id, "case.reprocessed", case.id)
    return {"ok": True}


@app.post("/api/cases/{case_id}/questions", status_code=202)
def ask(case_id: str, body: QuestionInput, auth=Depends(membership), db=Depends(get_db)):
    case = get_case(db, case_id, auth[1])
    rate_limit(db, "questions", auth[1].org_id, 15, 600)
    row = Question(case_id=case.id, question=body.question)
    db.add(row)
    db.flush()
    enqueue(db, f"answer:{row.id}", "ai", "answer", {"question_id": row.id}, auth[1].org_id)
    audit(db, auth[1].org_id, auth[0].id, "question.created", row.id)
    return {"id": row.id}


@app.post("/api/cases/{case_id}/actions", status_code=201)
def action(case_id: str, body: ActionInput, auth=Depends(membership), db=Depends(get_db)):
    case = get_case(db, case_id, auth[1])
    rate_limit(db, "actions", case.id, 100, 3600)
    db.add(Action(case_id=case.id, title=body.title))
    return {"ok": True}


@app.patch("/api/cases/{case_id}/actions/{action_id}")
def action_update(
    case_id: str, action_id: str, body: ActionUpdate, auth=Depends(membership), db=Depends(get_db)
):
    case = get_case(db, case_id, auth[1])
    row = db.scalar(select(Action).where(Action.id == action_id, Action.case_id == case.id))
    if not row:
        raise HTTPException(404)
    row.done = body.done
    return {"ok": True}


@app.post("/api/cases/{case_id}/review")
def review_case(case_id: str, body: ReviewInput, auth=Depends(membership), db=Depends(get_db)):
    require_role(auth[1], "reviewer")  # Owners cannot self-certify as a qualified professional.
    case = get_case(db, case_id, auth[1])
    row = db.scalar(select(Review).where(Review.case_id == case.id).with_for_update())
    if not row:
        raise HTTPException(404)
    row.status, row.note, row.reviewer, row.updated = body.status, body.note, auth[0].id, now()
    audit(db, auth[1].org_id, auth[0].id, "review." + body.status, case.id)
    return {"ok": True}


@app.delete("/api/cases/{case_id}", status_code=202)
def delete_case(case_id: str, auth=Depends(membership), db=Depends(get_db)):
    require_role(auth[1], "owner", "admin")
    case = get_case(db, case_id, auth[1])
    case.deleted = True
    audit(db, auth[1].org_id, auth[0].id, "case.deleted", case.id)
    enqueue(db, f"cleanup:{uid()}", "maintenance", "cleanup", {}, auth[1].org_id)
    return {"ok": True}


@app.get("/api/cases/{case_id}/export")
def export_case(case_id: str, auth=Depends(membership), db=Depends(get_db)):
    result = detail(case_id, auth, db)
    audit(db, auth[1].org_id, auth[0].id, "case.exported", case_id)
    return Response(
        json.dumps(result, default=str, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="notice-workspace.json"'},
    )


@app.get("/api/sources")
def sources(auth=Depends(membership), db=Depends(get_db)):
    return [
        {
            "id": row.id,
            "title": row.title,
            "url": row.url,
            "category": row.category,
            "fetched": row.fetched,
            "digest": row.digest,
            "approved_until": row.approved_until,
            "valid_from": row.valid_from,
            "valid_to": row.valid_to,
            "status": "approved" if row.approved_until and row.approved_until > now() else "review_pending",
        }
        for row in db.scalars(select(Source).order_by(Source.fetched.desc()).limit(100))
    ]


@app.get("/api/org/members")
def members(auth=Depends(membership), db=Depends(get_db)):
    require_role(auth[1], "owner", "admin")
    return [
        {"user_id": row.user_id, "email": user.email, "role": row.role}
        for row, user in db.execute(
            select(Member, User).join(User, User.id == Member.user_id).where(Member.org_id == auth[1].org_id)
        )
    ]


@app.post("/api/org/members")
def add_member(body: MemberInput, auth=Depends(membership), db=Depends(get_db)):
    require_role(auth[1], "owner")
    query = select(User).where(User.email == body.email.lower())
    if settings().require_email_verification:
        query = query.where(User.verified.is_(True))
    user = db.scalar(query)
    if not user:
        raise HTTPException(404, "Create and verify the account first.")
    old = db.get(Member, (auth[1].org_id, user.id))
    if old and old.role == "owner":
        raise HTTPException(400, "Owner role cannot be replaced")
    if old:
        old.role = body.role
    else:
        db.add(Member(org_id=auth[1].org_id, user_id=user.id, role=body.role))
    audit(db, auth[1].org_id, auth[0].id, "member." + body.role, user.id)
    return {"ok": True}


@app.delete("/api/org/members/{user_id}")
def remove_member(user_id: str, auth=Depends(membership), db=Depends(get_db)):
    require_role(auth[1], "owner")
    row = db.get(Member, (auth[1].org_id, user_id))
    if row and row.role != "owner":
        db.delete(row)
        audit(db, auth[1].org_id, auth[0].id, "member.removed", user_id)
    return {"ok": True}


@app.get("/api/org/audit")
def audit_log(auth=Depends(membership), db=Depends(get_db)):
    require_role(auth[1], "owner", "admin")
    return [
        {"event": row.event, "actor": row.actor, "target": row.target, "created": row.created}
        for row in db.scalars(
            select(Audit).where(Audit.org_id == auth[1].org_id).order_by(Audit.created.desc()).limit(200)
        )
    ]


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/assets", StaticFiles(directory=WEB), name="assets")
