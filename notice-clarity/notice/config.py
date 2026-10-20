from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NS_", env_file=".env", extra="ignore")
    environment: str = "development"
    database_url: str = "postgresql+psycopg://notice:notice@localhost:5432/notice"
    public_url: str = "http://localhost:8000"
    secret: str = "development-only-replace-with-64-random-characters-before-deployment"
    storage: str = "local"
    local_storage: Path = Path(".runtime/objects")
    s3_endpoint: str | None = None
    s3_bucket: str = "notice-private"
    s3_region: str = "ap-south-1"
    s3_kms_key: str | None = None
    secure_cookies: bool = False
    session_hours: int = 12
    session_idle_minutes: int = 45
    require_email_verification: bool = True
    max_upload_mb: int = 20
    max_pages: int = 80
    max_pixels: int = 24000000
    document_days: int = 90
    max_documents_per_day: int = 30
    max_jobs_per_org: int = 12
    org_daily_tokens: int = 80000
    org_daily_cost_usd: float = 3.0
    org_ai_concurrency: int = 2
    ai_api_key: str = ""
    ai_base_url: str = "https://api.openai.com/v1"
    ai_model: str = ""
    ai_strong_model: str = ""
    ai_price_input_per_million: float = 0.0
    ai_price_output_per_million: float = 0.0
    embedding_model: str = ""
    reranker_model: str = ""
    ai_timeout_seconds: int = 65
    scanner_host: str = ""
    scanner_port: int = 3310
    tesseract_path: str = "tesseract"
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_tls: bool = False
    smtp_username: str = ""
    smtp_password: str = ""
    mail_from: str = "Notice Clarity <noreply@example.test>"
    metrics_token: str = ""
    lease_seconds: int = 180
    pipeline_version: str = "2026-10-20.2"

    @model_validator(mode="after")
    def production_guard(self):
        if self.environment == "production":
            checks = [
                (self.database_url.startswith("postgresql"), "PostgreSQL required"),
                (
                    len(self.secret) >= 48 and not self.secret.startswith("development"),
                    "strong secret required",
                ),
                (
                    self.public_url.startswith("https://") and self.secure_cookies,
                    "HTTPS and secure cookies required",
                ),
                (self.storage == "s3" and bool(self.s3_kms_key), "private S3 with KMS required"),
                (bool(self.scanner_host), "malware scanner required"),
                (self.smtp_tls and bool(self.smtp_username), "authenticated TLS SMTP required"),
                (self.require_email_verification, "email verification required"),
                (len(self.metrics_token) >= 32, "metrics token required"),
            ]
            for ok, reason in checks:
                if not ok:
                    raise ValueError(reason)
        if self.ai_api_key and (
            not self.ai_model or self.ai_price_input_per_million <= 0 or self.ai_price_output_per_million <= 0
        ):
            raise ValueError("AI model and conservative maximum model prices must be configured")
        if (
            not 1 <= self.max_pages <= 200
            or self.lease_seconds < 60
            or not 1000000 <= self.max_pixels <= 24000000
        ):
            raise ValueError("invalid page or lease limit")
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
