import hashlib
import io
import socket
import struct
from functools import lru_cache
from pathlib import Path

import boto3
import pymupdf
from botocore.exceptions import ClientError
from PIL import Image, UnidentifiedImageError

from .config import settings


class InvalidUpload(ValueError):
    pass


def scan(data: bytes):
    cfg = settings()
    if not cfg.scanner_host:
        if cfg.environment == "production":
            raise InvalidUpload("Scanner unavailable")
        return
    with socket.create_connection((cfg.scanner_host, cfg.scanner_port), timeout=10) as sock:
        sock.settimeout(30)
        sock.sendall(b"zINSTREAM\0")
        for start in range(0, len(data), 65536):
            block = data[start : start + 65536]
            sock.sendall(struct.pack("!I", len(block)) + block)
        sock.sendall(struct.pack("!I", 0))
        response = sock.recv(4096)
        if b"OK" not in response or b"FOUND" in response or b"ERROR" in response:
            raise InvalidUpload("File rejected by malware scanner")


def validate_upload(data: bytes) -> tuple[str, int]:
    cfg = settings()
    if not data or len(data) > cfg.max_upload_mb * 1024 * 1024:
        raise InvalidUpload("File empty or too large")
    scan(data)
    if data.startswith(b"%PDF-"):
        try:
            with pymupdf.open(stream=data, filetype="pdf") as doc:
                if doc.needs_pass or not 1 <= len(doc) <= cfg.max_pages:
                    raise InvalidUpload("Encrypted PDF or page limit exceeded")
                if doc.embfile_count() or doc.get_xml_metadata() and len(doc.get_xml_metadata()) > 1000000:
                    raise InvalidUpload("Embedded files or excessive metadata not supported")
                # Do not pass interactive scripts/forms/attachments into the browser.
                return "application/pdf", len(doc)
        except (RuntimeError, pymupdf.FileDataError) as exc:
            raise InvalidUpload("Invalid PDF") from exc
    Image.MAX_IMAGE_PIXELS = cfg.max_pixels
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {"JPEG", "PNG"} or image.width * image.height > cfg.max_pixels:
                raise InvalidUpload("Only bounded PNG/JPEG images supported")
            if getattr(image, "n_frames", 1) != 1:
                raise InvalidUpload("Animated images not supported")
            image.verify()
        return ("image/png" if data.startswith(b"\x89PNG") else "image/jpeg"), 1
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise InvalidUpload("Valid PDF, PNG or JPEG required") from exc


@lru_cache
def s3():
    cfg = settings()
    return boto3.client("s3", endpoint_url=cfg.s3_endpoint, region_name=cfg.s3_region)


def local_path(key: str) -> Path:
    root = settings().local_storage.resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root) or ".." in key or path == root:
        raise ValueError("invalid object key")
    return path


def put(key: str, data: bytes, mime: str):
    cfg = settings()
    if cfg.storage == "s3":
        args = {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": cfg.s3_kms_key} if cfg.s3_kms_key else {}
        s3().put_object(Bucket=cfg.s3_bucket, Key=key, Body=data, ContentType=mime, **args)
    else:
        path = local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)


def get(key: str) -> bytes:
    if settings().storage == "s3":
        return s3().get_object(Bucket=settings().s3_bucket, Key=key)["Body"].read()
    return local_path(key).read_bytes()


def remove(key: str):
    if settings().storage == "s3":
        s3().delete_object(Bucket=settings().s3_bucket, Key=key)
    else:
        local_path(key).unlink(missing_ok=True)


def optional_get(key: str):
    try:
        return get(key)
    except FileNotFoundError:
        return None
    except ClientError as error:
        if error.response["Error"]["Code"] in {"NoSuchKey", "404"}:
            return None
        raise


def remove_document(key: str, pages: int):
    keys = [key] + [f"{key}.page{number}.png" for number in range(1, pages + 1)]
    if settings().storage == "s3":
        result = s3().delete_objects(
            Bucket=settings().s3_bucket, Delete={"Objects": [{"Key": item} for item in keys], "Quiet": True}
        )
        if result.get("Errors"):
            raise RuntimeError("Object deletion incomplete")
    else:
        for item in keys:
            remove(item)


def digest(data: bytes):
    return hashlib.sha256(data).hexdigest()
