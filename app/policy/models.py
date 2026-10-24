"""Pydantic data models for deterministic spending mandates and purchase proposals."""

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MandateStatus(str, Enum):
    """Lifecycle state of a spending mandate."""
    ACTIVE = "ACTIVE"
    RESERVED = "RESERVED"
    CONSUMED = "CONSUMED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class PolicyDecision(str, Enum):
    """Explicit deterministic policy evaluation outcome."""
    ALLOW_PENDING_APPROVAL = "ALLOW_PENDING_APPROVAL"
    BLOCKED = "BLOCKED"


class PolicyReasonCode(str, Enum):
    """Machine-readable policy evaluation decision codes."""
    POLICY_PASSED_PENDING_USER_APPROVAL = "POLICY_PASSED_PENDING_USER_APPROVAL"
    MANDATE_NOT_FOUND = "MANDATE_NOT_FOUND"
    MANDATE_EXPIRED = "MANDATE_EXPIRED"
    MANDATE_RESERVED = "MANDATE_RESERVED"
    MANDATE_ALREADY_CONSUMED = "MANDATE_ALREADY_CONSUMED"
    MANDATE_REVOKED = "MANDATE_REVOKED"
    PRODUCT_NOT_FOUND = "PRODUCT_NOT_FOUND"
    PRODUCT_UNAVAILABLE = "PRODUCT_UNAVAILABLE"
    CATEGORY_DISALLOWED = "CATEGORY_DISALLOWED"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    QUANTITY_EXCEEDS_LIMIT = "QUANTITY_EXCEEDS_LIMIT"
    AMOUNT_EXCEEDS_LIMIT = "AMOUNT_EXCEEDS_LIMIT"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    DATA_VALIDATION_ERROR = "DATA_VALIDATION_ERROR"


class ProposalStatus(str, Enum):
    """Lifecycle state of a purchase proposal."""
    PENDING_APPROVAL = "PENDING_APPROVAL"
    USER_APPROVED = "USER_APPROVED"
    PAID = "PAID"
    CANCELLED = "CANCELLED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    REJECTED = "REJECTED"


class SpendingMandate(BaseModel):
    """Immutable spending mandate entity enforcing deterministic budget constraints."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    id: str = Field(..., description="Unique mandate identifier.")
    max_amount: Decimal = Field(..., ge=Decimal("0.01"), description="Maximum total spending limit in Decimal.")
    currency: str = Field(default="INR", description="Supported currency code, strictly INR.")
    permitted_categories: list[str] = Field(..., description="Non-empty list of allowed product categories.")
    max_quantity: int = Field(..., ge=1, description="Maximum permitted items per purchase.")
    created_at: datetime = Field(..., description="Creation UTC timestamp.")
    expires_at: datetime = Field(..., description="Expiry UTC timestamp.")
    requires_user_approval: bool = Field(default=True, description="Strict explicit user approval requirement.")
    status: MandateStatus = Field(default=MandateStatus.ACTIVE, description="Current lifecycle state.")
    reserved_by_proposal_id: Optional[str] = Field(default=None, description="Proposal ID currently holding a reservation on this mandate.")
    consumed_by_proposal_id: Optional[str] = Field(default=None, description="Proposal ID that consumed this mandate.")

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, v: Any) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Mandate ID must be a non-empty string.")
        return v.strip()

    @field_validator("max_amount", mode="before")
    @classmethod
    def validate_max_amount(cls, v: Any) -> Decimal:
        if isinstance(v, float):
            raise ValueError("Binary floating-point amounts are prohibited for money. Use Decimal or string.")
        try:
            dec = Decimal(str(v))
        except Exception:
            raise ValueError("max_amount must be convertible to a valid Decimal.")
        if dec <= Decimal("0.00"):
            raise ValueError("max_amount must be strictly positive.")
        return dec.quantize(Decimal("0.01"))

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        code = v.strip().upper()
        if code != "INR":
            raise ValueError("Only 'INR' currency is supported for spending mandates.")
        return code

    @field_validator("permitted_categories")
    @classmethod
    def validate_categories(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("permitted_categories list cannot be empty.")
        seen: set[str] = set()
        cleaned: list[str] = []
        for cat in v:
            if not isinstance(cat, str) or not cat.strip():
                raise ValueError("Each permitted category must be a non-empty string.")
            normalized = cat.strip()
            if normalized.lower() in seen:
                raise ValueError(f"Duplicate category detected in permitted_categories: '{normalized}'.")
            seen.add(normalized.lower())
            cleaned.append(normalized)
        return cleaned

    @field_validator("created_at", "expires_at")
    @classmethod
    def validate_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("Timestamps must be timezone-aware (UTC).")
        return v

    @field_validator("requires_user_approval")
    @classmethod
    def enforce_approval_flag(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("requires_user_approval must always be True.")
        return True

    @model_validator(mode="after")
    def validate_expiry_after_creation(self) -> "SpendingMandate":
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be strictly after created_at.")
        return self


class MandateCreateRequest(BaseModel):
    """Client request schema for establishing a spending mandate."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    max_amount: Decimal = Field(..., description="Maximum budget amount.")
    currency: str = Field(default="INR", description="Must be INR.")
    permitted_categories: list[str] = Field(..., description="Allowed product categories.")
    max_quantity: int = Field(default=1, ge=1, description="Maximum items per purchase (>= 1).")
    expires_in_seconds: Optional[int] = Field(
        default=3600,
        ge=1,
        le=2592000,
        description="Validity window in seconds (default: 3600s, max 30 days).",
    )
    expires_at: Optional[datetime] = Field(
        default=None,
        description="Explicit UTC expiration datetime.",
    )
    requires_user_approval: bool = Field(
        default=True,
        description="Must be True; clients cannot disable user approval.",
    )

    @field_validator("max_amount", mode="before")
    @classmethod
    def validate_amount(cls, v: Any) -> Decimal:
        if isinstance(v, float):
            raise ValueError("Binary floating-point amounts are prohibited.")
        try:
            dec = Decimal(str(v))
        except Exception:
            raise ValueError("max_amount must be a valid Decimal value.")
        if dec <= Decimal("0.00"):
            raise ValueError("max_amount must be strictly positive.")
        return dec.quantize(Decimal("0.01"))

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        if v.strip().upper() != "INR":
            raise ValueError("Only 'INR' currency is supported.")
        return "INR"

    @field_validator("permitted_categories")
    @classmethod
    def validate_categories(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("permitted_categories list cannot be empty.")
        seen: set[str] = set()
        cleaned: list[str] = []
        for cat in v:
            if not isinstance(cat, str) or not cat.strip():
                raise ValueError("Categories must be non-empty strings.")
            if cat.strip().lower() in seen:
                raise ValueError(f"Duplicate category '{cat.strip()}'.")
            seen.add(cat.strip().lower())
            cleaned.append(cat.strip())
        return cleaned

    @field_validator("requires_user_approval")
    @classmethod
    def enforce_user_approval(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("requires_user_approval cannot be disabled.")
        return True


class PurchaseProposal(BaseModel):
    """Authoritative purchase proposal constructed strictly from backend catalogue data."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    id: str = Field(..., description="Unique proposal identifier.")
    mandate_id: str = Field(..., description="Associated mandate ID.")
    product_id: str = Field(..., description="Product ID from authoritative catalogue.")
    product_name: str = Field(..., description="Product name from authoritative catalogue.")
    product_category: str = Field(..., description="Product category from authoritative catalogue.")
    unit_price: Decimal = Field(..., ge=Decimal("0.00"), description="Authoritative unit price.")
    quantity: int = Field(..., ge=1, description="Validated item quantity.")
    total_amount: Decimal = Field(..., ge=Decimal("0.00"), description="Server-calculated total (unit_price * quantity).")
    currency: str = Field(default="INR", description="Authoritative catalogue currency.")
    status: ProposalStatus = Field(default=ProposalStatus.PENDING_APPROVAL, description="Proposal lifecycle state.")
    requires_user_approval: bool = Field(default=True, description="Strict requirement for user confirmation.")
    is_user_approved: bool = Field(default=False, description="Whether explicit approval has occurred.")
    approval_secret_hash: Optional[str] = Field(
        default=None,
        exclude=True,
        repr=False,
        description="SHA-256 hash of one-time approval secret.",
    )
    created_at: datetime = Field(..., description="Proposal creation UTC timestamp.")
    user_approved_at: Optional[datetime] = Field(default=None, description="Explicit user approval UTC timestamp.")


class ProposalApprovalRequest(BaseModel):
    """Client request schema for explicitly approving a purchase proposal."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    approval_secret: str = Field(
        ...,
        min_length=16,
        description="One-time cryptographically random approval secret issued during evaluation.",
    )


class ProposalEvaluationRequest(BaseModel):
    """Client request schema for evaluating a purchase against a mandate.

    Client CANNOT forge prices, totals, or categories (extra='forbid').
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    product_id: str = Field(..., description="Target catalogue product ID.")
    quantity: int = Field(..., ge=1, description="Purchase quantity (must be a positive integer >= 1).")
    mandate_id: Optional[str] = Field(default=None, description="Mandate ID if not provided in URL path.")

    @field_validator("product_id")
    @classmethod
    def validate_product_id(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("product_id must not be empty.")
        return v.strip()


class PolicyEvaluationResult(BaseModel):
    """Structured deterministic policy evaluation response."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    decision: PolicyDecision = Field(..., description="Deterministic decision: ALLOW_PENDING_APPROVAL or BLOCKED.")
    reason_code: PolicyReasonCode = Field(..., description="Machine-readable decision code.")
    reason_message: str = Field(..., description="Human-understandable safe explanation.")
    mandate_id: str = Field(..., description="Mandate ID evaluated against.")
    product_id: str = Field(..., description="Product ID evaluated.")
    quantity: int = Field(..., description="Requested quantity.")
    total_amount: Optional[Decimal] = Field(default=None, description="Server-calculated total amount if product resolved.")
    currency: Optional[str] = Field(default=None, description="Currency of calculation.")
    proposal: Optional[PurchaseProposal] = Field(default=None, description="Generated proposal if policy allowed.")
    approval_secret: Optional[str] = Field(
        default=None,
        description="One-time secret returned only upon eligible proposal creation for explicit approval.",
    )
    evaluated_at: datetime = Field(..., description="Evaluation UTC timestamp.")

