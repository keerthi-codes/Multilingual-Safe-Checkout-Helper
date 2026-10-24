"""Payment service coordinating PayPal Sandbox/Mock providers, currency conversion, and mandate finalization."""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
import logging
from typing import Any, Callable, Optional
import uuid

import httpx

from app.core.config import settings
from app.payment.currency import convert_inr_to_usd
from app.payment.models import (
    CurrencyBreakdown,
    OrderCaptureResponse,
    OrderCreateResponse,
    OrderCancelResponse,
    PaymentOrderRecord,
    PaymentStatus,
)
from app.payment.repository import PaymentRepository, in_memory_payment_repo
from app.policy.models import MandateStatus, ProposalStatus
from app.policy.service import PolicyService, policy_service

logger = logging.getLogger(__name__)


class BasePaymentProvider(ABC):
    """Abstract payment provider interface separating live PayPal Sandbox from offline tests."""

    @abstractmethod
    async def create_order(
        self,
        proposal_id: str,
        usd_amount: Decimal,
        idempotency_key: str,
        return_url: str,
        cancel_url: str,
    ) -> tuple[str, str]:
        """Create a payment order. Returns (paypal_order_id, approve_url)."""
        pass

    @abstractmethod
    async def get_order_details(self, paypal_order_id: str) -> dict[str, Any]:
        """Retrieve verified order details from the payment provider."""
        pass

    @abstractmethod
    async def capture_order(
        self,
        paypal_order_id: str,
        expected_usd_amount: Decimal,
        idempotency_key: str,
    ) -> tuple[str, str, Decimal]:
        """Capture payment. Returns (status, capture_id, captured_amount)."""
        pass


class MockPaymentProvider(BasePaymentProvider):
    """Deterministic, zero-network mock provider for automated tests and offline development."""

    def __init__(self) -> None:
        self._orders: dict[str, dict[str, Any]] = {}
        # Simulation flags for test coverage
        self.simulate_create_timeout: bool = False
        self.simulate_capture_error: bool = False
        self.simulate_amount_mismatch: bool = False

    async def create_order(
        self,
        proposal_id: str,
        usd_amount: Decimal,
        idempotency_key: str,
        return_url: str,
        cancel_url: str,
    ) -> tuple[str, str]:
        if self.simulate_create_timeout:
            raise RuntimeError("Simulated network timeout connecting to PayPal Orders API.")

        # Deterministic mock PayPal Order ID based on idempotency key
        order_hash = uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key).hex[:12]
        paypal_order_id = f"MOCK_ORD_{order_hash}"
        approve_url = f"https://sandbox.paypal.com/checkoutnow?token={paypal_order_id}"

        self._orders[paypal_order_id] = {
            "id": paypal_order_id,
            "status": "APPROVED",  # Mock simulates immediate buyer readiness
            "amount": usd_amount,
            "currency": "USD",
            "proposal_id": proposal_id,
        }
        return paypal_order_id, approve_url

    async def get_order_details(self, paypal_order_id: str) -> dict[str, Any]:
        order = self._orders.get(paypal_order_id)
        if not order:
            raise RuntimeError(f"Order '{paypal_order_id}' not found on mock provider.")
        return order

    async def capture_order(
        self,
        paypal_order_id: str,
        expected_usd_amount: Decimal,
        idempotency_key: str,
    ) -> tuple[str, str, Decimal]:
        if self.simulate_capture_error:
            raise RuntimeError("Simulated payment capture authorization declined by PayPal.")

        order = self._orders.get(paypal_order_id)
        if not order:
            raise RuntimeError(f"Order '{paypal_order_id}' was not found.")

        captured_amount = expected_usd_amount
        if self.simulate_amount_mismatch:
            captured_amount = expected_usd_amount + Decimal("5.00")

        cap_hash = uuid.uuid5(uuid.NAMESPACE_URL, f"{idempotency_key}_cap").hex[:12]
        capture_id = f"MOCK_CAP_{cap_hash}"
        order["status"] = "COMPLETED"
        order["capture_id"] = capture_id

        return "COMPLETED", capture_id, captured_amount


class PayPalSandboxPaymentProvider(BasePaymentProvider):
    """Live PayPal Sandbox API client using OAuth2 client credentials and Orders v2 REST API."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self.client_id = client_id or settings.PAYPAL_CLIENT_ID
        self.client_secret = client_secret or settings.PAYPAL_CLIENT_SECRET
        self.base_url = (base_url or settings.PAYPAL_SANDBOX_BASE_URL).rstrip("/")
        self._cached_token: Optional[str] = None
        self._token_expiry: float = 0.0

    async def _get_access_token(self) -> str:
        """Obtain or reuse OAuth2 Bearer token using client credentials."""
        now_ts = datetime.now(timezone.utc).timestamp()
        if self._cached_token and now_ts < self._token_expiry - 60:
            return self._cached_token

        if not self.client_id or not self.client_secret:
            raise RuntimeError(
                "PayPal Sandbox credentials missing: PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET "
                "must be configured when PAYPAL_MODE=sandbox."
            )

        token_url = f"{self.base_url}/v1/oauth2/token"
        headers = {"Accept": "application/json", "Accept-Language": "en_US"}

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.post(
                    token_url,
                    auth=(self.client_id, self.client_secret),
                    data={"grant_type": "client_credentials"},
                    headers=headers,
                )
            except httpx.TimeoutException:
                raise RuntimeError("Timeout requesting PayPal OAuth2 access token.")
            except Exception as exc:
                raise RuntimeError(f"Network error contacting PayPal OAuth2: {exc}")

            if resp.status_code != 200:
                raise RuntimeError(f"PayPal OAuth2 error HTTP {resp.status_code}: {resp.text}")

            data = resp.json()
            token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)
            if not token:
                raise RuntimeError("PayPal OAuth2 response did not contain access_token.")

            self._cached_token = token
            self._token_expiry = now_ts + expires_in
            return token

    async def create_order(
        self,
        proposal_id: str,
        usd_amount: Decimal,
        idempotency_key: str,
        return_url: str,
        cancel_url: str,
    ) -> tuple[str, str]:
        token = await self._get_access_token()
        orders_url = f"{self.base_url}/v2/checkout/orders"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "PayPal-Request-Id": idempotency_key,
            "Prefer": "return=representation",
        }

        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": proposal_id,
                    "amount": {
                        "currency_code": "USD",
                        "value": f"{usd_amount:.2f}",
                    },
                }
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "payment_method_preference": "IMMEDIATE_PAYMENT_REQUIRED",
                        "user_action": "PAY_NOW",
                        "return_url": return_url,
                        "cancel_url": cancel_url,
                    }
                }
            },
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.post(orders_url, json=payload, headers=headers)
            except httpx.TimeoutException:
                raise RuntimeError("Timeout connecting to PayPal Orders API during order creation.")
            except Exception as exc:
                raise RuntimeError(f"Network error creating PayPal order: {exc}")

            if resp.status_code not in (200, 201):
                raise RuntimeError(f"PayPal Create Order error HTTP {resp.status_code}: {resp.text}")

            data = resp.json()
            order_id = data.get("id")
            if not order_id:
                raise RuntimeError("PayPal Orders API response missing 'id'.")

            approve_url = None
            for link in data.get("links", []):
                if link.get("rel") in ("approve", "payer-action"):
                    approve_url = link.get("href")
                    break

            if not approve_url:
                raise RuntimeError("PayPal Orders API response missing approval link.")

            return order_id, approve_url

    async def get_order_details(self, paypal_order_id: str) -> dict[str, Any]:
        token = await self._get_access_token()
        url = f"{self.base_url}/v2/checkout/orders/{paypal_order_id}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.get(url, headers=headers)
            except httpx.TimeoutException:
                raise RuntimeError("Timeout querying PayPal order status.")
            except Exception as exc:
                raise RuntimeError(f"Network error querying PayPal order: {exc}")

            if resp.status_code != 200:
                raise RuntimeError(f"PayPal Get Order error HTTP {resp.status_code}: {resp.text}")

            return resp.json()

    async def capture_order(
        self,
        paypal_order_id: str,
        expected_usd_amount: Decimal,
        idempotency_key: str,
    ) -> tuple[str, str, Decimal]:
        # Pre-capture status check
        order_details = await self.get_order_details(paypal_order_id)
        current_status = order_details.get("status")

        if current_status == "COMPLETED":
            # Already captured
            captures = order_details.get("purchase_units", [{}])[0].get("payments", {}).get("captures", [])
            if captures:
                cap = captures[0]
                return "COMPLETED", cap["id"], Decimal(cap["amount"]["value"])

        if current_status not in ("APPROVED", "SAVED"):
            raise RuntimeError(f"Cannot capture order in status '{current_status}'. Buyer must approve first.")

        token = await self._get_access_token()
        capture_url = f"{self.base_url}/v2/checkout/orders/{paypal_order_id}/capture"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "PayPal-Request-Id": idempotency_key,
            "Prefer": "return=representation",
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.post(capture_url, json={}, headers=headers)
            except httpx.TimeoutException:
                raise RuntimeError("Timeout during PayPal capture request.")
            except Exception as exc:
                raise RuntimeError(f"Network error capturing PayPal payment: {exc}")

            if resp.status_code not in (200, 201):
                raise RuntimeError(f"PayPal Capture error HTTP {resp.status_code}: {resp.text}")

            data = resp.json()
            top_status = data.get("status")
            captures = data.get("purchase_units", [{}])[0].get("payments", {}).get("captures", [])

            if not captures:
                raise RuntimeError("PayPal capture response contains no capture records.")

            cap = captures[0]
            cap_status = cap.get("status")
            cap_id = cap.get("id")
            cap_currency = cap.get("amount", {}).get("currency_code")
            cap_value = Decimal(str(cap.get("amount", {}).get("value", "0.00")))

            if top_status != "COMPLETED" or cap_status != "COMPLETED":
                raise RuntimeError(f"PayPal capture was not completed: top={top_status}, capture={cap_status}.")

            if cap_currency != "USD":
                raise RuntimeError(f"Captured currency mismatch: expected USD, received {cap_currency}.")

            if cap_value != expected_usd_amount:
                raise RuntimeError(
                    f"Captured amount mismatch: expected USD {expected_usd_amount}, received USD {cap_value}."
                )

            return "COMPLETED", cap_id, cap_value


class PaymentService:
    """Orchestrator for PayPal Sandbox checkout, currency derivation, and mandate lifecycle."""

    def __init__(
        self,
        provider: Optional[BasePaymentProvider] = None,
        policy: Optional[PolicyService] = None,
        repository: Optional[PaymentRepository] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.policy_service = policy or policy_service
        self.repository = repository or in_memory_payment_repo
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.provider = provider or self._resolve_provider()

    def _resolve_provider(self) -> BasePaymentProvider:
        mode = settings.PAYPAL_MODE.lower()
        if mode == "sandbox":
            return PayPalSandboxPaymentProvider()
        return MockPaymentProvider()

    async def create_payment_order(
        self,
        proposal_id: str,
        return_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
    ) -> OrderCreateResponse:
        """Create a PayPal order for an approved purchase proposal with transparent USD conversion.

        Guarantees:
        - Proposal must exist and be in USER_APPROVED state.
        - Associated mandate must be in RESERVED state held by this proposal.
        - Derive USD amount on server using configured demo rate and Decimal arithmetic.
        - Deterministic idempotency key via PayPal-Request-Id.
        - Retries return existing order rather than creating duplicate orders.
        """
        now = self.clock()

        # 1. Fetch and validate proposal
        proposal = self.policy_service.get_proposal(proposal_id)
        if proposal is None:
            raise ValueError(f"Purchase proposal '{proposal_id}' was not found.")

        if proposal.status != ProposalStatus.USER_APPROVED:
            raise ValueError(
                f"Proposal cannot be paid in its current state '{proposal.status}'. "
                "Explicit user approval is required first."
            )

        # 2. Fetch and validate mandate
        mandate = self.policy_service.get_mandate(proposal.mandate_id)
        if mandate is None:
            raise ValueError(f"Mandate '{proposal.mandate_id}' was not found.")

        if mandate.status != MandateStatus.RESERVED or mandate.reserved_by_proposal_id != proposal.id:
            raise ValueError(
                f"Mandate '{mandate.id}' is not reserved for this proposal (status: {mandate.status})."
            )

        # 3. Check for existing order (Idempotency)
        existing_order = self.repository.get_order_by_proposal_id(proposal.id)
        rate = settings.PAYPAL_DEMO_EXCHANGE_RATE_INR_TO_USD
        usd_amount = convert_inr_to_usd(proposal.total_amount, rate)

        breakdown = CurrencyBreakdown(
            inr_total=proposal.total_amount,
            inr_currency="INR",
            usd_amount=usd_amount,
            usd_currency="USD",
            exchange_rate=rate,
        )

        if existing_order:
            return OrderCreateResponse(
                order_id=existing_order.id,
                proposal_id=existing_order.proposal_id,
                mandate_id=existing_order.mandate_id,
                status=existing_order.status,
                currency_breakdown=breakdown,
                approve_url=existing_order.approve_url,
                created_at=existing_order.created_at,
            )

        # 4. Generate stable deterministic idempotency key
        order_idempotency_key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"order_{proposal.id}"))

        default_return = return_url or "http://127.0.0.1:8000/?payment=success"
        default_cancel = cancel_url or "http://127.0.0.1:8000/?payment=cancel"

        paypal_order_id, approve_url = await self.provider.create_order(
            proposal_id=proposal.id,
            usd_amount=usd_amount,
            idempotency_key=order_idempotency_key,
            return_url=default_return,
            cancel_url=default_cancel,
        )

        record = PaymentOrderRecord(
            id=paypal_order_id,
            proposal_id=proposal.id,
            mandate_id=mandate.id,
            idempotency_key=order_idempotency_key,
            inr_total=proposal.total_amount,
            usd_amount=usd_amount,
            exchange_rate=rate,
            approve_url=approve_url,
            status=PaymentStatus.CREATED,
            created_at=now,
        )
        self.repository.save_order(record)

        return OrderCreateResponse(
            order_id=record.id,
            proposal_id=record.proposal_id,
            mandate_id=record.mandate_id,
            status=record.status,
            currency_breakdown=breakdown,
            approve_url=record.approve_url,
            created_at=record.created_at,
        )

    async def capture_payment_order(self, order_id: str) -> OrderCaptureResponse:
        """Capture an approved PayPal order, verify completion, and finalize mandate to CONSUMED."""
        now = self.clock()

        record = self.repository.get_order(order_id)
        if record is None:
            raise ValueError(f"Payment order '{order_id}' was not found.")

        # Idempotent response if already completed
        if record.status == PaymentStatus.COMPLETED:
            return OrderCaptureResponse(
                order_id=record.id,
                capture_id=record.capture_id,
                proposal_id=record.proposal_id,
                mandate_id=record.mandate_id,
                status=PaymentStatus.COMPLETED,
                amount_captured_usd=record.usd_amount,
                currency="USD",
                captured_at=record.captured_at,
                message="Payment was already captured and completed.",
            )

        if record.status == PaymentStatus.CANCELLED:
            raise ValueError("Cannot capture a cancelled payment order.")

        # Deterministic capture idempotency key
        capture_key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"capture_{record.id}"))

        try:
            status, capture_id, captured_amount = await self.provider.capture_order(
                paypal_order_id=record.id,
                expected_usd_amount=record.usd_amount,
                idempotency_key=capture_key,
            )
        except Exception as exc:
            logger.error("Capture failure for order %s: %s", record.id, exc)
            raise ValueError(f"Payment capture verification failed: {exc}")

        # Finalize mandate to CONSUMED and proposal to PAID
        self.policy_service.finalize_mandate_consumed(record.mandate_id, record.proposal_id)

        # Update order record
        updated_record = record.model_copy(
            update={
                "status": PaymentStatus.COMPLETED,
                "capture_id": capture_id,
                "capture_idempotency_key": capture_key,
                "captured_at": now,
            }
        )
        self.repository.update_order(updated_record)

        return OrderCaptureResponse(
            order_id=updated_record.id,
            capture_id=capture_id,
            proposal_id=updated_record.proposal_id,
            mandate_id=updated_record.mandate_id,
            status=PaymentStatus.COMPLETED,
            amount_captured_usd=captured_amount,
            currency="USD",
            captured_at=now,
            message="PayPal payment successfully verified and captured. Spending mandate consumed.",
        )

    def cancel_payment_order(self, order_id: str) -> OrderCancelResponse:
        """Cancel an order and release the mandate reservation back to ACTIVE."""
        now = self.clock()

        record = self.repository.get_order(order_id)
        if record is None:
            raise ValueError(f"Payment order '{order_id}' was not found.")

        if record.status == PaymentStatus.COMPLETED:
            raise ValueError("Cannot cancel an already completed payment order.")

        # Release mandate reservation
        self.policy_service.release_mandate_reservation(record.mandate_id, record.proposal_id)

        updated_record = record.model_copy(
            update={
                "status": PaymentStatus.CANCELLED,
                "cancelled_at": now,
            }
        )
        self.repository.update_order(updated_record)

        return OrderCancelResponse(
            order_id=record.id,
            proposal_id=record.proposal_id,
            mandate_id=record.mandate_id,
            status=PaymentStatus.CANCELLED,
            message="Payment order cancelled. Spending mandate reservation released back to ACTIVE.",
        )

    def get_order_record(self, order_id: str) -> PaymentOrderRecord:
        """Retrieve stored payment order record."""
        record = self.repository.get_order(order_id)
        if record is None:
            raise ValueError(f"Payment order '{order_id}' was not found.")
        return record


# Shared singleton payment service
payment_service = PaymentService()
