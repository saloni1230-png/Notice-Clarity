import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import settings


def seal(payload):
    nonce = os.urandom(12)
    key = hashlib.sha256(("mail-outbox:" + settings().secret).encode()).digest()
    encrypted = AESGCM(key).encrypt(
        nonce, json.dumps(payload, ensure_ascii=False).encode(), b"notice-outbox-v1"
    )
    return base64.urlsafe_b64encode(nonce + encrypted).decode()


def unseal(value):
    blob = base64.urlsafe_b64decode(value)
    key = hashlib.sha256(("mail-outbox:" + settings().secret).encode()).digest()
    return json.loads(AESGCM(key).decrypt(blob[:12], blob[12:], b"notice-outbox-v1"))
