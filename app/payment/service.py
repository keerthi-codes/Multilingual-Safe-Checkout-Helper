"""Payment service coordinating PayPal Sandbox/Mock providers, currency conversion, and mandate finalization."""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
import json
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
    WebhookEventRecord,
    WebhookEventStatus,
    WebhookProcessResponse,
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

    @abstractmethod
    async def verify_webhook_signature(
        self,
        headers: dict[str, str],
        raw_body: bytes,
        webhook_id: Optional[str] = None,
    ) -> bool:
        """Verify the authenticity of an inbound PayPal webhook notification."""
        pass

    @abstractmethod
    async def get_capture_details(self, capture_id: str) -> dict[str, Any]:
        """Retrieve verified capture details from the payment provider."""
        pass


class MockPaymentProvider(BasePaymentProvider):
    """Deterministic, zero-network mock provider for automated tests and offline development."""

    def __init__(self) -> None:
        self._orders: dict[str, dict[str, Any]] = {}
        # Simulation flags for test coverage
        self.simulate_create_timeout: bool = False
        self.simulate_capture_error: bool = False
        self.simulate_capture_timeout: bool = False
        self.simulate_amount_mismatch: bool = False
        self.simulate_provider_down: bool = False
        self.simulate_invalid_signature: bool = False
        self.simulate_webhook_verify_network_error: bool = False

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
        if self.simulate_provider_down:
            raise RuntimeError("Simulated network failure connecting to PayPal Orders API.")
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
        if self.simulate_capture_timeout:
            raise RuntimeError("Simulated network timeout during payment capture.")
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

    async def verify_webhook_signature(
        self,
        headers: dict[str, str],
        raw_body: bytes,
        webhook_id: Optional[str] = None,
    ) -> bool:
        if self.simulate_webhook_verify_network_error:
            raise RuntimeError("Simulated network error contacting PayPal webhook verification endpoint.")

        lower_headers = {k.lower(): v for k, v in headers.items()}
        required = [
            "paypal-transmission-id",
            "paypal-transmission-time",
            "paypal-cert-url",
            "paypal-auth-algo",
            "paypal-transmission-sig",
        ]
        for h in required:
            if not lower_headers.get(h):
                return False

        if self.simulate_invalid_signature:
            return False

        sig = lower_headers.get("paypal-transmission-sig", "")
        if sig == "INVALID_SIGNATURE":
            return False

        return True

    async def get_capture_details(self, capture_id: str) -> dict[str, Any]:
        if self.simulate_provider_down:
            raise RuntimeError("Simulated network failure connecting to PayPal Captures API.")
        for ord_data in self._orders.values():
            if ord_data.get("capture_id") == capture_id:
                return {
                    "id": capture_id,
                    "status": "COMPLETED",
                    "amount": {"value": f"{ord_data['amount']:.2f}", "currency_code": "USD"},
                    "order_id": ord_data["id"],
                }
        return {
            "id": capture_id,
            "status": "COMPLETED",
            "amount": {"value": "12.00", "currency_code": "USD"},
        }


class PayPalSandboxPaymentProvider(BasePaymentProvider):
    """Live PayPal Sandbox API client using OAuth2 client credentials and Orders v2 REST API."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        base_url: Optional[str] = None,
        webhook_id: Optional[str] = None,
    ) -> None:
        self.client_id = client_id or settings.PAYPAL_CLIENT_ID
        self.client_secret = client_secret or settings.PAYPAL_CLIENT_SECRET
        self.base_url = (base_url or settings.PAYPAL_SANDBOX_BASE_URL).rstrip("/")
        self.webhook_id = webhook_id or settings.PAYPAL_WEBHOOK_ID
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

    async def verify_webhook_signature(
        self,
        headers: dict[str, str],
        raw_body: bytes,
        webhook_id: Optional[str] = None,
    ) -> bool:
        configured_webhook_id = webhook_id or self.webhook_id or settings.PAYPAL_WEBHOOK_ID
        if not configured_webhook_id:
            raise ValueError(
                "PAYPAL_WEBHOOK_ID is not configured. Webhook signature verification requires a registered webhook ID."
            )

        lower_headers = {k.lower(): v for k, v in headers.items()}
        required = [
            "paypal-transmission-id",
            "paypal-transmission-time",
            "paypal-cert-url",
            "paypal-auth-algo",
            "paypal-transmission-sig",
        ]
        for h in required:
            if not lower_headers.get(h):
                return False

        try:
            event_json = json.loads(raw_body.decode("utf-8"))
        except Exception:
            return False

        token = await self._get_access_token()
        verify_url = f"{self.base_url}/v1/notifications/verify-webhook-signature"
        req_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        payload = {
            "auth_algo": lower_headers.get("paypal-auth-algo"),
            "cert_url": lower_headers.get("paypal-cert-url"),
            "transmission_id": lower_headers.get("paypal-transmission-id"),
            "transmission_sig": lower_headers.get("paypal-transmission-sig"),
            "transmission_time": lower_headers.get("paypal-transmission-time"),
            "webhook_id": configured_webhook_id,
            "webhook_event": event_json,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.post(verify_url, json=payload, headers=req_headers)
            except httpx.TimeoutException:
                raise RuntimeError("Timeout contacting PayPal webhook verification API.")
            except Exception as exc:
                raise RuntimeError(f"Network error verifying PayPal webhook signature: {exc}")

            if resp.status_code != 200:
                logger.warning("PayPal webhook verification returned HTTP %s: %s", resp.status_code, resp.text)
                return False

            data = resp.json()
            return data.get("verification_status") == "SUCCESS"

    async def get_capture_details(self, capture_id: str) -> dict[str, Any]:
        token = await self._get_access_token()
        url = f"{self.base_url}/v2/payments/captures/{capture_id}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.get(url, headers=headers)
            except httpx.TimeoutException:
                raise RuntimeError("Timeout querying PayPal capture details.")
            except Exception as exc:
                raise RuntimeError(f"Network error querying PayPal capture: {exc}")

            if resp.status_code != 200:
                raise RuntimeError(f"PayPal Get Capture error HTTP {resp.status_code}: {resp.text}")

            return resp.json()


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
            if existing_order.status == PaymentStatus.ORDER_CREATION_UNKNOWN:
                return await self.reconcile_uncertain_order_creation(existing_order)
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

        # Make external provider call OUTSIDE of database write transaction
        try:
            paypal_order_id, approve_url = await self.provider.create_order(
                proposal_id=proposal.id,
                usd_amount=usd_amount,
                idempotency_key=order_idempotency_key,
                return_url=default_return,
                cancel_url=default_cancel,
            )
        except Exception as exc:
            err_msg = str(exc).lower()
            if "timeout" in err_msg or "timed out" in err_msg:
                # Store uncertain state so recovery can resolve
                uncertain_record = PaymentOrderRecord(
                    id=f"UNKNOWN_{order_idempotency_key[:12]}",
                    proposal_id=proposal.id,
                    mandate_id=mandate.id,
                    idempotency_key=order_idempotency_key,
                    inr_total=proposal.total_amount,
                    usd_amount=usd_amount,
                    exchange_rate=rate,
                    approve_url="",
                    status=PaymentStatus.ORDER_CREATION_UNKNOWN,
                    created_at=now,
                )
                try:
                    self.repository.save_order(uncertain_record)
                except Exception:
                    pass
            raise

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

        # Atomic insertion with race-condition idempotency guard
        with self.repository.db.transaction() as conn:
            existing_concurrent = self.repository.get_order_by_proposal_id(proposal.id, conn=conn)
            if existing_concurrent:
                return OrderCreateResponse(
                    order_id=existing_concurrent.id,
                    proposal_id=existing_concurrent.proposal_id,
                    mandate_id=existing_concurrent.mandate_id,
                    status=existing_concurrent.status,
                    currency_breakdown=breakdown,
                    approve_url=existing_concurrent.approve_url,
                    created_at=existing_concurrent.created_at,
                )
            self.repository.save_order(record, conn=conn)

        return OrderCreateResponse(
            order_id=record.id,
            proposal_id=record.proposal_id,
            mandate_id=record.mandate_id,
            status=record.status,
            currency_breakdown=breakdown,
            approve_url=record.approve_url,
            created_at=record.created_at,
        )

    async def reconcile_uncertain_order_creation(
        self, order: PaymentOrderRecord
    ) -> OrderCreateResponse:
        """Recover an order in ORDER_CREATION_UNKNOWN by verifying provider state."""
        rate = settings.PAYPAL_DEMO_EXCHANGE_RATE_INR_TO_USD
        breakdown = CurrencyBreakdown(
            inr_total=order.inr_total,
            inr_currency="INR",
            usd_amount=order.usd_amount,
            usd_currency="USD",
            exchange_rate=rate,
        )

        # Check provider using original deterministic idempotency key
        default_return = "http://127.0.0.1:8000/?payment=success"
        default_cancel = "http://127.0.0.1:8000/?payment=cancel"
        paypal_order_id, approve_url = await self.provider.create_order(
            proposal_id=order.proposal_id,
            usd_amount=order.usd_amount,
            idempotency_key=order.idempotency_key,
            return_url=default_return,
            cancel_url=default_cancel,
        )

        with self.repository.db.transaction() as conn:
            conn.execute("DELETE FROM payment_orders WHERE id = ?;", (order.id,))
            updated_record = order.model_copy(
                update={
                    "id": paypal_order_id,
                    "approve_url": approve_url,
                    "status": PaymentStatus.CREATED,
                }
            )
            self.repository.save_order(updated_record, conn=conn)

        return OrderCreateResponse(
            order_id=updated_record.id,
            proposal_id=updated_record.proposal_id,
            mandate_id=updated_record.mandate_id,
            status=updated_record.status,
            currency_breakdown=breakdown,
            approve_url=updated_record.approve_url,
            created_at=updated_record.created_at,
        )

    async def capture_payment_order(self, order_id: str) -> OrderCaptureResponse:
        """Capture an approved PayPal order, verify completion, and atomically finalize mandate to CONSUMED."""
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

        if record.status == PaymentStatus.CAPTURE_UNKNOWN:
            return await self.reconcile_uncertain_capture(order_id)

        # Deterministic capture idempotency key
        capture_key = record.capture_idempotency_key or str(uuid.uuid5(uuid.NAMESPACE_URL, f"capture_{record.id}"))

        # External capture verification occurs OUTSIDE SQLite write transaction
        try:
            status, capture_id, captured_amount = await self.provider.capture_order(
                paypal_order_id=record.id,
                expected_usd_amount=record.usd_amount,
                idempotency_key=capture_key,
            )
        except Exception as exc:
            err_msg = str(exc).lower()
            if "timeout" in err_msg or "timed out" in err_msg:
                uncertain_record = record.model_copy(
                    update={
                        "status": PaymentStatus.CAPTURE_UNKNOWN,
                        "capture_idempotency_key": capture_key,
                    }
                )
                self.repository.update_order(uncertain_record)
            logger.error("Capture failure for order %s: %s", record.id, exc)
            raise ValueError(f"Payment capture verification failed: {exc}")

        # Atomic finalization: update order, proposal, and mandate within single transaction
        with self.repository.db.transaction() as conn:
            # Re-check inside transaction for concurrent capture race
            cur_record = self.repository.get_order(order_id, conn=conn)
            if cur_record and cur_record.status == PaymentStatus.COMPLETED:
                return OrderCaptureResponse(
                    order_id=cur_record.id,
                    capture_id=cur_record.capture_id,
                    proposal_id=cur_record.proposal_id,
                    mandate_id=cur_record.mandate_id,
                    status=PaymentStatus.COMPLETED,
                    amount_captured_usd=cur_record.usd_amount,
                    currency="USD",
                    captured_at=cur_record.captured_at,
                    message="Payment was already captured and completed.",
                )

            # Finalize mandate to CONSUMED and proposal to PAID
            self.policy_service.finalize_mandate_consumed(record.mandate_id, record.proposal_id, conn=conn)

            # Update order record to COMPLETED
            updated_record = record.model_copy(
                update={
                    "status": PaymentStatus.COMPLETED,
                    "capture_id": capture_id,
                    "capture_idempotency_key": capture_key,
                    "captured_at": now,
                }
            )
            self.repository.update_order(updated_record, conn=conn)

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

    async def reconcile_uncertain_capture(self, order_id: str) -> OrderCaptureResponse:
        """Reconcile an uncertain capture operation by verifying external state before retrying."""
        now = self.clock()
        record = self.repository.get_order(order_id)
        if not record:
            raise ValueError(f"Payment order '{order_id}' was not found.")

        # Query provider to check current external state
        details = await self.provider.get_order_details(record.id)
        current_status = details.get("status")

        if current_status == "COMPLETED":
            captures = details.get("purchase_units", [{}])[0].get("payments", {}).get("captures", [])
            cap_id = captures[0].get("id") if captures else (record.capture_id or f"CAP_{record.id}")
            cap_amount = Decimal(str(captures[0]["amount"]["value"])) if captures else record.usd_amount

            with self.repository.db.transaction() as conn:
                self.policy_service.finalize_mandate_consumed(record.mandate_id, record.proposal_id, conn=conn)
                updated = record.model_copy(
                    update={
                        "status": PaymentStatus.COMPLETED,
                        "capture_id": cap_id,
                        "captured_at": now,
                    }
                )
                self.repository.update_order(updated, conn=conn)

            return OrderCaptureResponse(
                order_id=updated.id,
                capture_id=cap_id,
                proposal_id=updated.proposal_id,
                mandate_id=updated.mandate_id,
                status=PaymentStatus.COMPLETED,
                amount_captured_usd=cap_amount,
                currency="USD",
                captured_at=now,
                message="PayPal capture verified and reconciled successfully. Mandate consumed.",
            )

        # Re-issue capture using the same idempotency key
        capture_key = record.capture_idempotency_key or str(uuid.uuid5(uuid.NAMESPACE_URL, f"capture_{record.id}"))
        status, capture_id, captured_amount = await self.provider.capture_order(
            paypal_order_id=record.id,
            expected_usd_amount=record.usd_amount,
            idempotency_key=capture_key,
        )

        with self.repository.db.transaction() as conn:
            self.policy_service.finalize_mandate_consumed(record.mandate_id, record.proposal_id, conn=conn)
            updated = record.model_copy(
                update={
                    "status": PaymentStatus.COMPLETED,
                    "capture_id": capture_id,
                    "capture_idempotency_key": capture_key,
                    "captured_at": now,
                }
            )
            self.repository.update_order(updated, conn=conn)

        return OrderCaptureResponse(
            order_id=updated.id,
            capture_id=capture_id,
            proposal_id=updated.proposal_id,
            mandate_id=updated.mandate_id,
            status=PaymentStatus.COMPLETED,
            amount_captured_usd=captured_amount,
            currency="USD",
            captured_at=now,
            message="PayPal capture executed and reconciled. Mandate consumed.",
        )

    def cancel_payment_order(self, order_id: str) -> OrderCancelResponse:
        """Cancel an order and release the mandate reservation back to ACTIVE in a single transaction."""
        now = self.clock()

        with self.repository.db.transaction() as conn:
            record = self.repository.get_order(order_id, conn=conn)
            if record is None:
                raise ValueError(f"Payment order '{order_id}' was not found.")

            if record.status == PaymentStatus.COMPLETED:
                raise ValueError("Cannot cancel an already completed payment order.")

            if record.status in (PaymentStatus.CAPTURE_UNKNOWN, PaymentStatus.CAPTURE_PENDING):
                raise ValueError(
                    f"Cannot cancel payment order in '{record.status}' state: capture is uncertain or in-flight; "
                    "mandate reservation must be retained until external payment state is resolved."
                )

            if record.status == PaymentStatus.FAILED:
                raise ValueError("Cannot cancel a failed payment order.")

            if record.status == PaymentStatus.CANCELLED:
                return OrderCancelResponse(
                    order_id=record.id,
                    proposal_id=record.proposal_id,
                    mandate_id=record.mandate_id,
                    status=PaymentStatus.CANCELLED,
                    message="Payment order was already cancelled.",
                )

            # Release mandate reservation
            self.policy_service.release_mandate_reservation(record.mandate_id, record.proposal_id, conn=conn)

            updated_record = record.model_copy(
                update={
                    "status": PaymentStatus.CANCELLED,
                    "cancelled_at": now,
                }
            )
            self.repository.update_order(updated_record, conn=conn)

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

    async def process_webhook_event(
        self, headers: dict[str, str], raw_body: bytes
    ) -> WebhookProcessResponse:
        """Ingest, verify, durably record, and idempotently process PayPal webhooks."""
        now = self.clock()

        # 1. Validate required transmission headers
        lower_headers = {k.lower(): v for k, v in headers.items()}
        required_headers = [
            "paypal-transmission-id",
            "paypal-transmission-time",
            "paypal-cert-url",
            "paypal-auth-algo",
            "paypal-transmission-sig",
        ]
        for h in required_headers:
            if not lower_headers.get(h):
                raise ValueError(f"Missing required PayPal transmission header: '{h}'.")

        # 2. Parse raw JSON body safely
        try:
            event_json = json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"Malformed webhook JSON payload: {exc}")

        event_id = event_json.get("id")
        event_type = event_json.get("event_type")
        if not event_id or not event_type:
            raise ValueError("Malformed webhook event: missing 'id' or 'event_type'.")

        # 3. Durable Idempotency Check
        existing_event = self.repository.get_webhook_event(event_id)
        if existing_event and existing_event.status == WebhookEventStatus.PROCESSED:
            return WebhookProcessResponse(
                status="duplicate",
                event_id=event_id,
                event_type=event_type,
                message="Duplicate webhook event already processed.",
            )

        # 4. Verify official webhook signature with provider
        is_valid = await self.provider.verify_webhook_signature(
            headers=headers,
            raw_body=raw_body,
        )
        if not is_valid:
            raise ValueError("Invalid PayPal webhook signature or verification failed.")

        # 5. Persist as RECEIVED durably before processing
        if not existing_event:
            event_record = WebhookEventRecord(
                id=event_id,
                event_type=event_type,
                order_id=None,
                capture_id=None,
                status=WebhookEventStatus.RECEIVED,
                payload=raw_body.decode("utf-8", errors="replace"),
                error_message=None,
                received_at=now,
            )
            self.repository.save_webhook_event(event_record)
        else:
            event_record = existing_event

        # 6. Event Dispatch & Safe State Transition
        try:
            if event_type == "CHECKOUT.ORDER.APPROVED":
                order_id, capture_id, msg = self._handle_order_approved(event_json, now)
                status_res = "processed"
            elif event_type == "CHECKOUT.PAYMENT-APPROVAL.REVERSED":
                order_id, capture_id, msg = self._handle_approval_reversed(event_json, now)
                status_res = "processed"
            elif event_type == "PAYMENT.CAPTURE.PENDING":
                order_id, capture_id, msg = self._handle_capture_pending(event_json, now)
                status_res = "processed"
            elif event_type == "PAYMENT.CAPTURE.COMPLETED":
                order_id, capture_id, msg = self._handle_capture_completed(event_json, now)
                status_res = "processed"
            elif event_type == "PAYMENT.CAPTURE.DENIED":
                order_id, capture_id, msg = self._handle_capture_denied(event_json, now)
                status_res = "processed"
            else:
                # Unknown event type: must NOT modify payment or mandate state
                order_id = None
                capture_id = None
                status_res = "ignored"
                msg = f"Unknown event type '{event_type}' ignored."

            # Update event record to PROCESSED or IGNORED
            updated_record = event_record.model_copy(
                update={
                    "status": (
                        WebhookEventStatus.IGNORED
                        if status_res == "ignored"
                        else WebhookEventStatus.PROCESSED
                    ),
                    "order_id": order_id,
                    "capture_id": capture_id,
                    "processed_at": now,
                    "error_message": None,
                }
            )
            self.repository.update_webhook_event(updated_record)

            return WebhookProcessResponse(
                status=status_res,
                event_id=event_id,
                event_type=event_type,
                message=msg,
            )

        except Exception as exc:
            # Record failure in webhook_events so it remains retryable
            logger.error("Error processing webhook event %s (%s): %s", event_id, event_type, exc)
            failed_record = event_record.model_copy(
                update={
                    "status": WebhookEventStatus.FAILED,
                    "error_message": str(exc),
                    "processed_at": now,
                }
            )
            self.repository.update_webhook_event(failed_record)
            raise ValueError(f"Webhook event processing failed: {exc}")

    def _handle_order_approved(self, event_json: dict[str, Any], now: datetime) -> tuple[str, Optional[str], str]:
        resource = event_json.get("resource", {})
        order_id = resource.get("id")
        if not order_id:
            raise ValueError("CHECKOUT.ORDER.APPROVED event missing resource.id.")

        order = self.repository.get_order(order_id)
        if not order:
            raise ValueError(f"Unrelated order: payment order '{order_id}' was not found.")

        # Guard against downgrading completed/terminal states
        if order.status in (PaymentStatus.COMPLETED, PaymentStatus.CANCELLED, PaymentStatus.FAILED):
            return order_id, None, f"Order already in {order.status} state; approval event not applied."

        with self.repository.db.transaction() as conn:
            cur = self.repository.get_order(order_id, conn=conn)
            if cur and cur.status in (
                PaymentStatus.CREATED,
                PaymentStatus.ORDER_CREATION_PENDING,
                PaymentStatus.ORDER_CREATION_UNKNOWN,
            ):
                self.repository.update_order(
                    cur.model_copy(update={"status": PaymentStatus.BUYER_APPROVED}),
                    conn=conn,
                )

        return order_id, None, "Buyer approval recorded successfully."

    def _handle_approval_reversed(self, event_json: dict[str, Any], now: datetime) -> tuple[str, Optional[str], str]:
        resource = event_json.get("resource", {})
        order_id = resource.get("id")
        if not order_id:
            raise ValueError("CHECKOUT.PAYMENT-APPROVAL.REVERSED event missing resource.id.")

        order = self.repository.get_order(order_id)
        if not order:
            raise ValueError(f"Unrelated order: payment order '{order_id}' was not found.")

        if order.status == PaymentStatus.COMPLETED:
            return order_id, None, "Completed payment cannot be reversed; mandate already consumed."
        if order.status in (PaymentStatus.CAPTURE_UNKNOWN, PaymentStatus.CAPTURE_PENDING):
            return order_id, None, f"Payment capture is uncertain/pending (status: '{order.status}'); approval reversal cannot release mandate."

        with self.repository.db.transaction() as conn:
            cur = self.repository.get_order(order_id, conn=conn)
            if cur and cur.status != PaymentStatus.CANCELLED:
                self.policy_service.release_mandate_reservation(cur.mandate_id, cur.proposal_id, conn=conn)
                self.repository.update_order(
                    cur.model_copy(update={"status": PaymentStatus.CANCELLED, "cancelled_at": now}),
                    conn=conn,
                )

        return order_id, None, "Payment approval reversed; mandate reservation released."

    def _handle_capture_pending(self, event_json: dict[str, Any], now: datetime) -> tuple[str, Optional[str], str]:
        resource = event_json.get("resource", {})
        capture_id = resource.get("id")
        order_id = (
            resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id")
            or resource.get("order_id")
        )
        if not order_id and capture_id:
            existing_ord = self.repository.get_order_by_capture_id(capture_id)
            if existing_ord:
                order_id = existing_ord.id

        if not order_id:
            raise ValueError("PAYMENT.CAPTURE.PENDING event missing order association.")

        order = self.repository.get_order(order_id)
        if not order:
            raise ValueError(f"Unrelated order: payment order '{order_id}' was not found.")

        if order.status == PaymentStatus.COMPLETED:
            return order_id, capture_id, "Order is already completed; capture pending not applied."

        with self.repository.db.transaction() as conn:
            cur = self.repository.get_order(order_id, conn=conn)
            if cur and cur.status not in (PaymentStatus.COMPLETED, PaymentStatus.CANCELLED, PaymentStatus.FAILED):
                self.repository.update_order(
                    cur.model_copy(
                        update={
                            "status": PaymentStatus.CAPTURE_PENDING,
                            "capture_id": capture_id or cur.capture_id,
                        }
                    ),
                    conn=conn,
                )

        return order_id, capture_id, "Capture pending recorded; mandate remains reserved."

    def _handle_capture_completed(self, event_json: dict[str, Any], now: datetime) -> tuple[str, Optional[str], str]:
        resource = event_json.get("resource", {})
        capture_id = resource.get("id")
        if not capture_id:
            raise ValueError("PAYMENT.CAPTURE.COMPLETED event missing resource.id.")

        capture_status = resource.get("status")
        if capture_status != "COMPLETED":
            raise ValueError(f"Capture status is '{capture_status}'; expected COMPLETED.")

        order_id = (
            resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id")
            or resource.get("order_id")
        )
        if not order_id:
            existing_ord = self.repository.get_order_by_capture_id(capture_id)
            if existing_ord:
                order_id = existing_ord.id

        if not order_id:
            raise ValueError("PAYMENT.CAPTURE.COMPLETED event missing associated order ID.")

        order = self.repository.get_order(order_id)
        if not order:
            raise ValueError(f"Unrelated order: payment order '{order_id}' was not found.")

        # Currency and Amount check
        amount_obj = resource.get("amount", {})
        currency = amount_obj.get("currency_code")
        if currency != "USD":
            raise ValueError(f"Captured currency mismatch: expected USD, received '{currency}'.")

        raw_val = amount_obj.get("value", "0.00")
        cap_amount = Decimal(str(raw_val))
        if cap_amount != order.usd_amount:
            raise ValueError(
                f"Captured amount mismatch: expected USD {order.usd_amount}, received USD {cap_amount}."
            )

        with self.repository.db.transaction() as conn:
            cur = self.repository.get_order(order_id, conn=conn)
            if cur and cur.status == PaymentStatus.COMPLETED:
                return order_id, capture_id, "Order was already completed."

            self.policy_service.finalize_mandate_consumed(order.mandate_id, order.proposal_id, conn=conn)
            updated_order = order.model_copy(
                update={
                    "status": PaymentStatus.COMPLETED,
                    "capture_id": capture_id,
                    "captured_at": now,
                }
            )
            self.repository.update_order(updated_order, conn=conn)

        return order_id, capture_id, "Capture completed verified; mandate consumed and proposal marked paid."

    def _handle_capture_denied(self, event_json: dict[str, Any], now: datetime) -> tuple[str, Optional[str], str]:
        resource = event_json.get("resource", {})
        capture_id = resource.get("id")
        order_id = (
            resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id")
            or resource.get("order_id")
        )
        if not order_id and capture_id:
            existing_ord = self.repository.get_order_by_capture_id(capture_id)
            if existing_ord:
                order_id = existing_ord.id

        if not order_id:
            raise ValueError("PAYMENT.CAPTURE.DENIED event missing associated order ID.")

        order = self.repository.get_order(order_id)
        if not order:
            raise ValueError(f"Unrelated order: payment order '{order_id}' was not found.")

        if order.status == PaymentStatus.COMPLETED:
            return order_id, capture_id, "Completed payment cannot be marked denied."

        with self.repository.db.transaction() as conn:
            cur = self.repository.get_order(order_id, conn=conn)
            if cur and cur.status not in (PaymentStatus.COMPLETED, PaymentStatus.FAILED, PaymentStatus.CANCELLED):
                self.policy_service.release_mandate_reservation(cur.mandate_id, cur.proposal_id, conn=conn)
                self.repository.update_order(
                    cur.model_copy(update={"status": PaymentStatus.FAILED}),
                    conn=conn,
                )

        return order_id, capture_id, "Capture denied; reservation released and order marked failed."


# Shared singleton payment service
payment_service = PaymentService()
