"""Application configuration loaded from environment variables."""

from decimal import Decimal
import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Base directory: project root
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load environment variables from .env file if present
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)


class Settings:
    PROJECT_NAME: str = os.getenv("PROJECT_NAME", "Multilingual Safe-Checkout Helper")
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    DEBUG: bool = os.getenv("DEBUG", "true").lower() in ("true", "1", "t", "yes")
    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "8000"))

    # AI Configuration (mock by default for offline / test safety)
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "mock").lower()
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY", None)
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

    # PayPal Configuration (mock by default for offline / test safety)
    PAYPAL_MODE: str = os.getenv("PAYPAL_MODE", "mock").lower()
    PAYPAL_CLIENT_ID: Optional[str] = os.getenv("PAYPAL_CLIENT_ID", None)
    PAYPAL_CLIENT_SECRET: Optional[str] = os.getenv("PAYPAL_CLIENT_SECRET", None)
    PAYPAL_SANDBOX_BASE_URL: str = os.getenv("PAYPAL_SANDBOX_BASE_URL", "https://api-m.sandbox.paypal.com")
    
    @property
    def PAYPAL_DEMO_EXCHANGE_RATE_INR_TO_USD(self) -> "Decimal":
        from decimal import Decimal
        raw = os.getenv("PAYPAL_DEMO_EXCHANGE_RATE_INR_TO_USD", "0.012").strip()
        try:
            rate = Decimal(raw)
            if rate <= Decimal("0.00"):
                raise ValueError("Rate must be strictly positive.")
            return rate
        except Exception:
            return Decimal("0.012")


settings = Settings()
