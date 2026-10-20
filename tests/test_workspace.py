import io
from datetime import timedelta

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from notice.api import app
from notice.db import session_scope
from notice.models import Case, Document, Job, Page, now
from notice.retrieval import retrieve
from conftest import drain, signup


def test_complete_upload_pipeline_evidence_actions_and_question(client, uploaded):
    drain()
    result = client.get(f"/api/cases/{uploaded}").json()
    assert result["status"] == "review_required"
    assert result["category"] == "income_tax"
    assert result["pages"][0]["method"] == "digital"
    assert any(f["kind"] == "amount" and "12,500" in f["value"] for f in result["facts"])
    assert any(f["kind"] == "deadline" and f["date_iso"] == "2026-10-31" for f in result["facts"])
    page = client.get(f"/api/cases/{uploaded}/pages/1", params={"quote": "INR 12,500.00"}).json()
    assert page["boxes"]
    image = client.get(f"/api/cases/{uploaded}/pages/1/image")
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    action = result["actions"][0]
    assert (
        client.patch(f"/api/cases/{uploaded}/actions/{action['id']}", json={"done": True}).status_code == 200
    )
    assert (
        client.patch(
            f"/api/cases/{uploaded}/context",
            json={
                "received_on": "2026-10-20",
                "jurisdiction": "Delhi",
                "already_replied": False,
                "notes": "Not yet replied",
            },
        ).status_code
        == 200
    )
    q = client.post(f"/api/cases/{uploaded}/questions", json={"question": "What amount is mentioned?"})
    assert q.status_code == 202
    drain()
    final = client.get(f"/api/cases/{uploaded}").json()
    assert final["questions"][0]["status"] == "done"
    assert final["questions"][0]["answer"]["mode"] == "extracts"
    assert final["questions"][0]["answer"]["professional_review"] is False
    assert client.get(f"/api/cases/{uploaded}/export").headers["content-disposition"].startswith("attachment")


def test_tenant_isolation_applies_before_retrieval(client, uploaded):
    drain()
    with TestClient(app, base_url="http://localhost:8000") as other:
        other_account = signup(other, "other@example.com")
        for path in [
            f"/api/cases/{uploaded}",
            f"/api/cases/{uploaded}/pages/1",
            f"/api/cases/{uploaded}/pages/1/image",
            f"/api/cases/{uploaded}/export",
        ]:
            assert other.get(path).status_code == 404
        assert other.post(f"/api/cases/{uploaded}/questions", json={"question": "amount?"}).status_code == 404
        with session_scope() as db:
            assert retrieve(db, other_account["orgs"][0]["id"], uploaded, "amount") == ([], [])
    assert client.get(f"/api/cases/{uploaded}").status_code == 200


def test_dedup_is_scoped_to_organization(client, account, pdf_bytes):
    first = client.post("/api/cases", files={"file": ("first.pdf", pdf_bytes)}).json()
    second = client.post("/api/cases", files={"file": ("second.pdf", pdf_bytes)}).json()
    assert not first["deduplicated"] and second["deduplicated"]
    with TestClient(app, base_url="http://localhost:8000") as other:
        signup(other, "other@example.com")
        third = other.post("/api/cases", files={"file": ("third.pdf", pdf_bytes)}).json()
        assert third["deduplicated"] is False
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Document)) == 2


def test_deletion_preserves_other_case_then_erases_document(client, uploaded, pdf_bytes):
    second = client.post("/api/cases", files={"file": ("copy.pdf", pdf_bytes)}).json()["id"]
    drain()
    assert client.delete(f"/api/cases/{uploaded}").status_code == 202
    assert client.get(f"/api/cases/{uploaded}").status_code == 404
    drain()
    assert client.get(f"/api/cases/{second}").status_code == 200
    assert client.delete(f"/api/cases/{second}").status_code == 202
    drain()
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Document)) == 0
        assert db.scalar(select(func.count()).select_from(Page)) == 0


def test_expired_case_is_immediately_inaccessible(client, uploaded):
    with session_scope() as db:
        db.get(Case, uploaded).expires = now() - timedelta(seconds=1)
    assert client.get(f"/api/cases/{uploaded}").status_code == 404


def test_member_role_cannot_delete_or_self_review(client, uploaded, account):
    with TestClient(app, base_url="http://localhost:8000") as member:
        user = signup(member, "member@example.com")
        assert (
            client.post("/api/org/members", json={"email": user["email"], "role": "member"}).status_code
            == 200
        )
        member.headers["x-org-id"] = account["orgs"][0]["id"]
        assert member.get(f"/api/cases/{uploaded}").status_code == 200
        assert member.delete(f"/api/cases/{uploaded}").status_code == 403
        assert (
            member.post(
                f"/api/cases/{uploaded}/review", json={"status": "resolved", "note": "Checked the evidence."}
            ).status_code
            == 403
        )
        assert (
            client.post("/api/org/members", json={"email": user["email"], "role": "reviewer"}).status_code
            == 200
        )
        assert (
            member.post(
                f"/api/cases/{uploaded}/review", json={"status": "resolved", "note": "Checked the evidence."}
            ).status_code
            == 200
        )
        assert client.delete(f"/api/org/members/{user['user_id']}").status_code == 200
        assert member.get(f"/api/cases/{uploaded}").status_code == 403


def test_invalid_uploads_and_encrypted_pdf(client, account):
    import pymupdf

    assert client.post("/api/cases", files={"file": ("evil.pdf", b"<script>bad</script>")}).status_code == 422
    pdf = pymupdf.open()
    pdf.new_page()
    encrypted = pdf.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="secret", user_pw="secret")
    assert client.post("/api/cases", files={"file": ("locked.pdf", encrypted)}).status_code == 422
    assert (
        client.post("/api/cases", content=b"x", headers={"content-length": str(30 * 1024 * 1024)}).status_code
        == 413
    )


def test_ocr_is_queued_only_for_image_pages(client, account):
    out = io.BytesIO()
    Image.new("RGB", (600, 400), "white").save(out, format="PNG")
    case_id = client.post("/api/cases", files={"file": ("scan.png", out.getvalue())}).json()["id"]
    from notice.worker import run_one

    assert run_one("extract")
    assert run_one("extract")
    with session_scope() as db:
        case = db.get(Case, case_id)
        page = db.scalar(select(Page).where(Page.document_id == case.document_id))
        assert page.status == "ocr"
        assert db.scalar(select(Job).where(Job.kind == "ocr")).pool == "ocr"


def test_xss_stays_data_in_api(client, uploaded):
    value = "<img src=x onerror=alert(1)>"
    assert client.patch(f"/api/cases/{uploaded}/context", json={"notes": value}).status_code == 200
    assert client.get(f"/api/cases/{uploaded}").json()["context"]["notes"] == value
    response = client.get("/")
    assert "script-src 'self'" in response.headers["content-security-policy"]
