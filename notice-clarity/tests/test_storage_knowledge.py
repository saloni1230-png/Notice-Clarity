import socket
from unittest.mock import MagicMock

import pytest

from notice import knowledge, storage
from notice.config import settings
from notice.crypto import seal, unseal


def test_scanner_rejects_infection_and_connection_failure(monkeypatch):
    monkeypatch.setattr(settings(), "scanner_host", "scanner")
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.recv.return_value = b"stream: Eicar-Test-Signature FOUND\0"
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: connection)
    with pytest.raises(storage.InvalidUpload):
        storage.scan(b"fake malicious bytes")
    connection.recv.return_value = b"stream: OK\0"
    storage.scan(b"clean")


def test_source_url_restrictions_and_dns_private_address(monkeypatch):
    for url in ("http://www.gst.gov.in/", "https://attacker.example/", "https://www.gst.gov.in:8443/"):
        with pytest.raises(ValueError):
            knowledge.validate_url(url)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="Private"):
        knowledge.validate_url("https://www.gst.gov.in/")


def test_outbox_encryption_detects_tampering():
    sealed = seal({"token": "test-secret"})
    assert "test-secret" not in sealed and unseal(sealed)["token"] == "test-secret"
    with pytest.raises(Exception):
        unseal(sealed[:20] + "AAAA" + sealed[24:])


def test_local_roundtrip_and_idempotent_delete():
    storage.put("org/doc/original", b"contents", "application/pdf")
    assert storage.get("org/doc/original") == b"contents"
    storage.remove("org/doc/original")
    storage.remove("org/doc/original")
