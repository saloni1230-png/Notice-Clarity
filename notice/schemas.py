from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(Strict):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        return value.lower()


class EmailRequest(Strict):
    email: EmailStr


class TokenRequest(Strict):
    token: str = Field(min_length=24, max_length=200)


class ResetRequest(TokenRequest):
    password: str = Field(min_length=12, max_length=128)


class Evidence(Strict):
    page: int = Field(ge=1)
    quote: str = Field(min_length=3, max_length=1200)


class Fact(Strict):
    kind: Literal[
        "issuer", "reference", "notice_date", "deadline", "amount", "section", "recipient", "request", "other"
    ]
    value: str = Field(min_length=1, max_length=500)
    evidence: Evidence
    confidence: float = Field(ge=0, le=1)
    date_iso: str | None = None


class Extraction(Strict):
    category: Literal["income_tax", "gst", "bank", "court", "unknown"]
    facts: list[Fact] = Field(max_length=40)
    flags: list[str] = Field(max_length=15)


class Claim(Strict):
    text: str = Field(min_length=3, max_length=800)
    basis: Literal["document", "official_source"]
    evidence: Evidence | None = None
    source_id: str | None = None
    source_quote: str | None = Field(default=None, max_length=1200)


class Answer(Strict):
    claims: list[Claim] = Field(max_length=8)
    missing_context: list[str] = Field(max_length=6)
    professional_review: bool


class Verification(Strict):
    supported: bool
    unsupported_claims: list[int] = Field(max_length=8)
    legal_interpretation: bool
    conflicting_dates: bool


class ContextInput(Strict):
    received_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    jurisdiction: str = Field(default="", max_length=100)
    already_replied: bool | None = None
    notes: str = Field(default="", max_length=3000)


class QuestionInput(Strict):
    question: str = Field(min_length=3, max_length=1200)


class ActionInput(Strict):
    title: str = Field(min_length=3, max_length=300)


class ActionUpdate(Strict):
    done: bool


class ReviewInput(Strict):
    status: Literal["open", "resolved"]
    note: str = Field(min_length=10, max_length=5000)


class MemberInput(Strict):
    email: EmailStr
    role: Literal["member", "reviewer", "admin"]
