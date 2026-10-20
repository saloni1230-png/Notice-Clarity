import hashlib
import ipaddress
import socket
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select

from .models import Source

OFFICIAL_HOSTS = {
    "www.incometax.gov.in",
    "incometax.gov.in",
    "www.incometaxindia.gov.in",
    "incometaxindia.gov.in",
    "tutorial.gst.gov.in",
    "www.gst.gov.in",
    "gst.gov.in",
    "www.rbi.org.in",
    "rbi.org.in",
    "services.ecourts.gov.in",
    "ecourts.gov.in",
    "www.indiacode.nic.in",
    "indiacode.nic.in",
    "cbic-gst.gov.in",
}


class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "nav", "header", "footer"}:
            self.skip += 1
        if tag in {"p", "div", "li", "h1", "h2", "h3", "br"} and not self.skip:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "nav", "header", "footer"}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def validate_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in OFFICIAL_HOSTS or parsed.port not in {None, 443}:
        raise ValueError("Only approved official HTTPS hosts may be ingested")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Invalid source URL")
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError("Private source address rejected")
    # Production also requires outbound firewall/DNS policy, documented in operations.txt.
    return url


def ingest(db, url, title, category):
    validate_url(url)
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        with client.stream("GET", url, headers={"User-Agent": "NoticeClarity/1.0 source-review"}) as response:
            response.raise_for_status()
            if response.is_redirect:
                raise ValueError("Review redirect URL explicitly")
            chunks, total = [], 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > 5000000:
                    raise ValueError("Source exceeds 5 MB")
                chunks.append(chunk)
            data = b"".join(chunks)
    digest = hashlib.sha256(data).hexdigest()
    old = db.scalar(select(Source).where(Source.url == url, Source.digest == digest))
    if old:
        return old
    if data.startswith(b"%PDF"):
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            text = "\n".join(p.get_text() for p in pdf)[:200000]
    else:
        parser = TextOnly()
        parser.feed(data.decode("utf-8", errors="replace"))
        text = "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())[:200000]
    if len(text) < 100:
        raise ValueError("Source text missing; manual inspection required")
    # New snapshots revoke prior approval. Nothing becomes legal knowledge without human approval.
    for prior in db.scalars(select(Source).where(Source.url == url)):
        prior.approved_until = None
    row = Source(url=url, title=title, category=category, digest=digest, text=text)
    db.add(row)
    db.flush()
    return row
