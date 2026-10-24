"""Deterministic monetary conversion utilities for PayPal Sandbox USD checkout."""

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from app.core.config import settings


def convert_inr_to_usd(inr_amount: Decimal, rate: Optional[Decimal] = None) -> Decimal:
    """Convert an authoritative INR Decimal amount to USD Decimal rounded to 2 decimal places.

    Uses strict Decimal arithmetic with ROUND_HALF_UP rounding.
    Rejects negative amounts and non-positive exchange rates.
    """
    if rate is None:
        rate = settings.PAYPAL_DEMO_EXCHANGE_RATE_INR_TO_USD

    if not isinstance(inr_amount, Decimal):
        try:
            inr_amount = Decimal(str(inr_amount))
        except Exception as exc:
            raise ValueError(f"Invalid monetary amount: {exc}")

    if inr_amount < Decimal("0.00"):
        raise ValueError(f"Monetary amount cannot be negative: received {inr_amount}.")

    if not isinstance(rate, Decimal):
        try:
            rate = Decimal(str(rate))
        except Exception as exc:
            raise ValueError(f"Invalid exchange rate: {exc}")

    if rate <= Decimal("0.00"):
        raise ValueError(f"Exchange rate must be strictly positive: received {rate}.")

    # Accurate Decimal multiplication followed by explicit half-up rounding to cents
    usd_unrounded = inr_amount * rate
    usd_amount = usd_unrounded.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return usd_amount
