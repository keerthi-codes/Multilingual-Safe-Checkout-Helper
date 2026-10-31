"""Pydantic data models for PayPal Sandbox payment orders and capture tracking."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class PaymentStatus(str, Enum):
    """Lifecycle state of a PayPal payment order."""
    CREATED = "CREATED"
    BUYER_APPROVED = "BUYER_APPROVED"
    CAPTURE_PENDING = "CAPTURE_PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    ORDER_CREATION_PENDING = "ORDER_CREATION_PENDING"
    ORDER_CREATION_UNKNOWN = "ORDER_CREATION_UNKNOWN"
    CAPTURE_UNKNOWN = "CAPTURE_UNKNOWN"


class WebhookEventStatus(str, Enum):
    """Lifecycle state of a stored webhook event."""
    RECEIVED = "RECEIVED"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    IGNORED = "IGNORED"


class WebhookEventRecord(BaseModel):
    """Durable record of an ingested PayPal webhook event."""

    model_config = ConfigDict(
        extra="forbid",
    )

    id: str = Field(..., description="PayPal event ID.")
    event_type: str = Field(..., description="PayPal event type name.")
    order_id: Optional[str] = Field(default=None, description="Associated PayPal order ID if resolved.")
    capture_id: Optional[str] = Field(default=None, description="Associated PayPal capture ID if resolved.")
    status: WebhookEventStatus = Field(default=WebhookEventStatus.RECEIVED, description="Event processing status.")
    payload: str = Field(..., description="Serialized raw event payload.")
    error_message: Optional[str] = Field(default=None, description="Safe sanitized error description if failed.")
    received_at: datetime = Field(..., description="Event reception UTC timestamp.")
    processed_at: Optional[datetime] = Field(default=None, description="Event processing UTC timestamp.")


class WebhookProcessResponse(BaseModel):
    """Outbound confirmation of webhook receipt and processing outcome."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    status: str = Field(..., description="Processing outcome (e.g., 'processed', 'duplicate', 'ignored').")
    event_id: str = Field(..., description="PayPal event ID.")
    event_type: str = Field(..., description="PayPal event type.")
    message: str = Field(..., description="Human-understandable outcome description.")


class CurrencyBreakdown(BaseModel):
    """Transparent disclosure of authoritative INR catalogue total vs. converted USD Sandbox amount."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    inr_total: Decimal = Field(..., ge=Decimal("0.00"), description="Authoritative purchase total in INR.")
    inr_currency: str = Field(default="INR", description="Authoritative catalogue currency.")
    usd_amount: Decimal = Field(..., ge=Decimal("0.00"), description="Calculated PayPal Sandbox test amount in USD.")
    usd_currency: str = Field(default="USD", description="PayPal payment currency.")
    exchange_rate: Decimal = Field(..., gt=Decimal("0.00"), description="Configured demo conversion rate (INR to USD).")
    disclaimer: str = Field(
        default="Configured demo exchange rate for PayPal Sandbox testing only; not a live market rate.",
        description="Explicit test disclosure.",
    )


class OrderCreateRequest(BaseModel):
    """Inbound request to initiate a PayPal Sandbox checkout for an approved proposal.

    Strict validation: Rejects any client-supplied prices, totals, currencies, or rates.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    proposal_id: str = Field(..., min_length=1, description="Internal purchase proposal identifier in USER_APPROVED state.")

    @field_validator("proposal_id")
    @classmethod
    def validate_proposal_id(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("proposal_id must not be empty.")
        return stripped


class OrderCreateResponse(BaseModel):
    """Outbound response providing the PayPal approval redirect and transparent currency breakdown."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    order_id: str = Field(..., description="Internal payment order identifier (matches PayPal order ID).")
    proposal_id: str = Field(..., description="Associated purchase proposal ID.")
    mandate_id: str = Field(..., description="Associated spending mandate ID.")
    status: PaymentStatus = Field(..., description="Current payment order status.")
    currency_breakdown: CurrencyBreakdown = Field(..., description="Transparent dual-currency details.")
    approve_url: str = Field(..., description="PayPal URL where buyer approves payment.")
    created_at: datetime = Field(..., description="Creation UTC timestamp.")


class OrderCaptureResponse(BaseModel):
    """Outbound response confirming verified payment capture."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    order_id: str = Field(..., description="PayPal order ID.")
    capture_id: Optional[str] = Field(default=None, description="Persistent PayPal capture transaction identifier.")
    proposal_id: str = Field(..., description="Associated proposal ID.")
    mandate_id: str = Field(..., description="Associated mandate ID.")
    status: PaymentStatus = Field(..., description="Final payment status (COMPLETED, FAILED, etc.).")
    amount_captured_usd: Optional[Decimal] = Field(default=None, description="Verified captured amount in USD.")
    currency: str = Field(default="USD", description="Captured currency.")
    captured_at: Optional[datetime] = Field(default=None, description="Capture completion timestamp.")
    message: str = Field(..., description="Human-understandable status explanation.")


class OrderCancelResponse(BaseModel):
    """Outbound response confirming cancellation and mandate reservation release."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    order_id: str = Field(..., description="PayPal order ID.")
    proposal_id: str = Field(..., description="Associated proposal ID.")
    mandate_id: str = Field(..., description="Associated mandate ID.")
    status: PaymentStatus = Field(default=PaymentStatus.CANCELLED, description="Payment order state.")
    message: str = Field(..., description="Confirmation that mandate reservation was released.")


class PaymentOrderRecord(BaseModel):
    """Persistent entity tracking internal payment order state, PayPal tokens, and capture details."""

    model_config = ConfigDict(
        extra="forbid",
    )

    id: str = Field(..., description="PayPal order ID.")
    proposal_id: str = Field(..., description="Internal purchase proposal ID.")
    mandate_id: str = Field(..., description="Internal spending mandate ID.")
    idempotency_key: str = Field(..., description="Stable PayPal-Request-Id used during order creation.")
    capture_idempotency_key: Optional[str] = Field(default=None, description="Stable PayPal-Request-Id used during capture.")
    inr_total: Decimal = Field(..., description="Authoritative INR total.")
    usd_amount: Decimal = Field(..., description="Expected USD amount for PayPal.")
    exchange_rate: Decimal = Field(..., description="Applied conversion rate.")
    approve_url: str = Field(..., description="Approval URL returned by PayPal.")
    status: PaymentStatus = Field(default=PaymentStatus.CREATED, description="Payment lifecycle state.")
    capture_id: Optional[str] = Field(default=None, description="PayPal capture transaction ID upon success.")
    created_at: datetime = Field(..., description="Creation UTC timestamp.")
    captured_at: Optional[datetime] = Field(default=None, description="Capture completion UTC timestamp.")
    cancelled_at: Optional[datetime] = Field(default=None, description="Cancellation UTC timestamp.")
