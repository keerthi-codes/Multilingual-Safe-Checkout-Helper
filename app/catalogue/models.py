"""Pydantic data models for the product catalogue."""

from decimal import Decimal
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Product(BaseModel):
    """Immutable, typed product entity in the catalogue.

    Prices are strictly modeled with Decimal to avoid binary floating-point inaccuracy.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    id: str = Field(..., description="Stable, unique product identifier.")
    name: str = Field(..., description="Non-empty product name.")
    description: str = Field(..., description="Non-empty product description.")
    category: str = Field(..., description="Non-empty product category.")
    price: Decimal = Field(..., ge=Decimal("0.00"), description="Accurate non-negative price in Decimal.")
    currency: str = Field(default="INR", description="ISO-style 3-letter currency code.")
    available: bool = Field(default=True, description="Availability flag.")
    supported_languages: list[str] = Field(
        default_factory=lambda: ["en", "es", "fr"],
        description="List of supported language codes without duplicates.",
    )

    @field_validator("id", "name", "description", "category", mode="before")
    @classmethod
    def validate_non_empty_strings(cls, v: Any, info) -> str:
        """Ensure required string fields are non-empty and non-whitespace."""
        if not isinstance(v, str):
            raise ValueError(f"Field '{info.field_name}' must be a string.")
        stripped = v.strip()
        if not stripped:
            raise ValueError(f"Field '{info.field_name}' must not be empty or whitespace only.")
        return stripped

    @field_validator("price", mode="before")
    @classmethod
    def validate_price_representation(cls, v: Any) -> Decimal:
        """Reject binary floating-point values to enforce accurate money representation."""
        if isinstance(v, float):
            raise ValueError(
                "Binary floating-point values are prohibited for monetary amounts. "
                "Supply a Decimal or decimal-compatible string."
            )
        try:
            dec = Decimal(str(v))
        except Exception:
            raise ValueError("Price must be convertible to a valid Decimal value.")

        if dec < Decimal("0.00"):
            raise ValueError("Price must be a non-negative value.")

        # Standardize to 2 decimal places
        return dec.quantize(Decimal("0.01"))

    @field_validator("currency")
    @classmethod
    def validate_currency_code(cls, v: str) -> str:
        """Enforce standard ISO-style currency code (INR for initial products)."""
        code = v.strip().upper()
        if code != "INR":
            raise ValueError("All initial catalogue products must use 'INR' currency.")
        return code

    @field_validator("supported_languages")
    @classmethod
    def validate_supported_languages(cls, v: list[str]) -> list[str]:
        """Validate language codes and prevent duplicates."""
        if not v:
            raise ValueError("supported_languages list must not be empty.")
        seen: set[str] = set()
        cleaned: list[str] = []
        for lang in v:
            if not isinstance(lang, str) or not lang.strip():
                raise ValueError("Each language code must be a non-empty string.")
            code = lang.strip().lower()
            if code in seen:
                raise ValueError(f"Duplicate language code detected: '{code}'.")
            seen.add(code)
            cleaned.append(code)
        return cleaned
