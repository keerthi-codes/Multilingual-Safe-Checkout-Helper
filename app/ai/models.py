"""Pydantic request and response models for the multilingual AI assistant."""

from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.policy.models import PolicyEvaluationResult


class ChatMessage(BaseModel):
    """Single turn in a chat conversation."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    role: str = Field(..., description="Role of the message sender ('user', 'assistant', 'system').")
    content: str = Field(..., min_length=1, max_length=4000, description="Message text content.")

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        role_lower = v.strip().lower()
        if role_lower not in ("user", "assistant", "system"):
            raise ValueError("Role must be 'user', 'assistant', or 'system'.")
        return role_lower


class AIChatRequest(BaseModel):
    """Inbound chat request to the AI assistant."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    message: str = Field(..., min_length=1, max_length=2000, description="User's query.")
    conversation_history: list[ChatMessage] = Field(
        default_factory=list,
        max_length=20,
        description="Prior conversation turns (up to 20).",
    )
    mandate_id: Optional[str] = Field(
        default=None,
        description="Optional spending mandate identifier for policy evaluation.",
    )
    language: str = Field(
        default="en",
        description="Target language code: 'en' (English), 'es' (Spanish), or 'fr' (French).",
    )

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Message must not be empty or whitespace only.")
        return stripped

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        lang_lower = v.strip().lower()
        if lang_lower not in ("en", "es", "fr"):
            return "en"  # Graceful fallback to English
        return lang_lower


class AIProposalIntent(BaseModel):
    """Strict model-generated proposal recommendation intent.

    Notice: AI does NOT provide prices, totals, or currencies.
    All financial calculations and checks are performed deterministically by the backend.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    product_id: str = Field(..., description="Target product ID from the catalogue.")
    quantity: int = Field(default=1, ge=1, le=10, description="Recommended quantity (1 to 10).")
    recommendation_reason: str = Field(
        ...,
        min_length=5,
        max_length=1000,
        description="Multilingual rationale explaining why this product fits user needs.",
    )

    @field_validator("product_id")
    @classmethod
    def validate_product_id(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("product_id must not be empty.")
        return stripped


class AIChatResponse(BaseModel):
    """Outbound structured assistant response."""

    model_config = ConfigDict(
        extra="forbid",
    )

    reply_text: str = Field(..., description="Conversational explanation tailored in user language.")
    language: str = Field(default="en", description="Language of the response.")
    recommended_proposal: Optional[AIProposalIntent] = Field(
        default=None,
        description="Populated when a concrete purchase proposal is recommended.",
    )
    evaluated_policy_result: Optional[PolicyEvaluationResult] = Field(
        default=None,
        description="Evaluated policy engine result if a mandate was provided and proposal recommended.",
    )
    tool_calls_executed: list[str] = Field(
        default_factory=list,
        description="List of read-only catalogue tool names invoked during generation.",
    )
