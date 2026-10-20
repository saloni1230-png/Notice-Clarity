import os

os.environ.setdefault("NS_DATABASE_URL", "postgresql+psycopg://notice:notice@localhost:5432/notice_test")
os.environ["NS_ENVIRONMENT"] = "test"
os.environ["NS_REQUIRE_EMAIL_VERIFICATION"] = "false"
os.environ["NS_SCANNER_HOST"] = ""
os.environ["NS_AI_API_KEY"] = ""
os.environ["NS_PUBLIC_URL"] = "http://localhost:8000"

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from notice.api import app
from notice.config import settings
from notice.db import Base, engine
from notice.worker import run_one


@pytest.fixture(autouse=True)
def clean_database(tmp_path, monkeypatch):
    if not settings().database_url.endswith("/notice_test"):
        raise RuntimeError("Destructive tests require a database named notice_test")
    Base.metadata.create_all(engine())
    with engine().begin() as conn:
        conn.execute(
            text("TRUNCATE " + ", ".join('"' + table + '"' for table in Base.metadata.tables) + " CASCADE")
        )
    monkeypatch.setattr(settings(), "local_storage", tmp_path / "objects")
    yield


@pytest.fixture
def client():
    with TestClient(app, base_url="http://localhost:8000") as browser:
        yield browser


def signup(client, email="person@example.com"):
    response = client.post("/api/auth/register", json={"email": email, "password": "a-strong-password-123"})
    assert response.status_code == 201, response.text
    client.headers["x-csrf-token"] = response.json()["csrf"]
    return client.get("/api/me").json()


@pytest.fixture
def account(client):
    return signup(client)


@pytest.fixture
def pdf_bytes():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text(
        (55, 60),
        "SYNTHETIC TEST NOTICE - NOT AN OFFICIAL DOCUMENT\nIncome Tax Department\n"
        "Reference number: TEST-2026-001\nDate of issue: 20 October 2026\n"
        "Please reply before 31 October 2026.\nAmount mentioned: INR 12,500.00\n"
        "Please provide a copy of the supporting bank statement.",
        fontsize=12,
    )
    content = doc.tobytes()
    doc.close()
    return content


@pytest.fixture
def uploaded(client, account, pdf_bytes):
    response = client.post("/api/cases", files={"file": ("notice.pdf", pdf_bytes, "application/pdf")})
    assert response.status_code == 202, response.text
    return response.json()["id"]


def drain():
    for _ in range(100):
        worked = False
        for pool in ("extract", "ocr", "ai", "maintenance"):
            worked = run_one(pool) or worked
        if not worked:
            return
    raise AssertionError("Queue did not drain")
