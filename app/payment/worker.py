"""Background worker for safe abandoned-checkout and expired mandate reconciliation."""

import asyncio
from datetime import datetime
import logging
from typing import Optional

from app.core.config import settings
from app.payment.models import PaymentStatus
from app.payment.service import PaymentService, payment_service
from app.policy.models import ProposalStatus
from app.policy.service import PolicyService, policy_service

logger = logging.getLogger(__name__)


class ExpiryReconciliationWorker:
    """Lightweight lifecycle-managed background worker for reconciling expired checkout reservations.

    Critical Reservation Rule:
    A mandate's expiry timestamp alone is NOT sufficient evidence that its payment reservation can be released.
    External provider state must be verified to guarantee no successful or unresolved capture exists.
    """

    def __init__(
        self,
        pay_service: Optional[PaymentService] = None,
        pol_service: Optional[PolicyService] = None,
        interval_seconds: Optional[int] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        self.payment_service = pay_service or payment_service
        self.policy_service = pol_service or policy_service
        self.interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else settings.RECONCILIATION_INTERVAL_SECONDS
        )
        self.enabled = (
            enabled
            if enabled is not None
            else settings.EXPIRY_RECONCILIATION_ENABLED
        )
        self._task: Optional[asyncio.Task] = None
        self._running: bool = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the background worker loop if enabled and not already running."""
        async with self._lock:
            if not self.enabled:
                logger.info("Expiry reconciliation worker is disabled.")
                return

            if self._running or (self._task and not self._task.done()):
                logger.warning("Expiry reconciliation worker is already running; skipping duplicate start.")
                return

            self._running = True
            self._task = asyncio.create_task(self._run_loop(), name="expiry_reconciliation_worker")
            logger.info(
                "Expiry reconciliation worker started with %ds polling interval.",
                self.interval_seconds,
            )

    async def stop(self) -> None:
        """Stop the background worker cleanly during application shutdown."""
        async with self._lock:
            if not self._running and not self._task:
                return

            self._running = False
            if self._task:
                self._task.cancel()
                try:
                    await asyncio.gather(self._task, return_exceptions=True)
                except Exception as exc:
                    logger.warning("Exception during worker task cancellation: %s", exc)
                self._task = None
            logger.info("Expiry reconciliation worker stopped cleanly.")

    async def _run_loop(self) -> None:
        """Continuous execution loop invoking reconciliation at configured intervals."""
        while self._running:
            try:
                await self.reconcile_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Unhandled error during expiry reconciliation cycle: %s", exc)

            try:
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break

    async def reconcile_once(self) -> dict[str, int]:
        """Perform a single reconciliation sweep of expired reserved spending mandates.
        
        Returns summary statistics of actions taken.
        """
        now = self.payment_service.clock()
        repo = self.policy_service.repository
        expired_mandates = repo.get_expired_reserved_mandates(now)

        stats = {
            "scanned": len(expired_mandates),
            "finalized": 0,
            "retained": 0,
            "released": 0,
            "skipped": 0,
        }

        for mandate in expired_mandates:
            try:
                outcome = await self._reconcile_single_mandate(mandate, now)
                if outcome in stats:
                    stats[outcome] += 1
            except Exception as exc:
                logger.error("Error reconciling mandate %s: %s", mandate.id, exc)
                stats["retained"] += 1

        return stats

    async def _reconcile_single_mandate(self, mandate, now: datetime) -> str:
        """Reconcile a single expired reserved mandate adhering to the Critical Reservation Rule."""
        proposal_id = mandate.reserved_by_proposal_id
        if not proposal_id:
            self.policy_service.expire_mandate_reservation(mandate.id, None)
            return "released"

        proposal = self.policy_service.get_proposal(proposal_id)
        if not proposal:
            self.policy_service.expire_mandate_reservation(mandate.id, None)
            return "released"

        # Find associated payment order
        order = self.payment_service.repository.get_order_by_proposal_id(proposal.id)

        # Case 1: No PayPal order exists
        if not order:
            # Verify no order creation operation is unresolved before releasing
            if proposal.status != ProposalStatus.USER_APPROVED:
                self.policy_service.expire_mandate_reservation(mandate.id, proposal.id)
                return "released"
            # Proposal is USER_APPROVED but no order was ever created: safe to expire
            self.policy_service.expire_mandate_reservation(mandate.id, proposal.id)
            return "released"

        # Case 2: Order exists in SQLite
        # If order is already COMPLETED: finalize purchase if mandate still RESERVED
        if order.status == PaymentStatus.COMPLETED:
            with self.payment_service.repository.db.transaction() as conn:
                self.policy_service.finalize_mandate_consumed(mandate.id, proposal.id, conn=conn)
            return "finalized"

        # If order is already definitively CANCELLED or FAILED: safe to release
        if order.status in (PaymentStatus.CANCELLED, PaymentStatus.FAILED):
            self.policy_service.expire_mandate_reservation(mandate.id, proposal.id)
            return "released"

        # If order is in uncertain operation state: retain reservation and retry later
        if order.status in (
            PaymentStatus.ORDER_CREATION_PENDING,
            PaymentStatus.ORDER_CREATION_UNKNOWN,
            PaymentStatus.CAPTURE_UNKNOWN,
        ):
            logger.info("Order %s in uncertain operation state %s; retaining reservation.", order.id, order.status)
            return "retained"

        # Case 3: Order is CREATED, BUYER_APPROVED, or CAPTURE_PENDING:
        # Retrieve external state from PayPal OUTSIDE of SQLite transaction
        try:
            order_details = await self.payment_service.provider.get_order_details(order.id)
        except Exception as exc:
            # Section 6: "If the provider is unreachable or the outcome is uncertain, retain the reservation and retry later."
            logger.warning(
                "PayPal provider unreachable when reconciling order %s: %s. Retaining reservation.",
                order.id,
                exc,
            )
            return "retained"

        paypal_status = order_details.get("status")

        # 4. If capture is completed, verify it and finalize purchase
        if paypal_status == "COMPLETED":
            captures = order_details.get("purchase_units", [{}])[0].get("payments", {}).get("captures", [])
            cap_id = captures[0].get("id") if captures else (order.capture_id or f"CAP_{order.id}")
            with self.payment_service.repository.db.transaction() as conn:
                self.policy_service.finalize_mandate_consumed(mandate.id, proposal.id, conn=conn)
                updated_order = order.model_copy(
                    update={
                        "status": PaymentStatus.COMPLETED,
                        "capture_id": cap_id,
                        "captured_at": now,
                    }
                )
                self.payment_service.repository.update_order(updated_order, conn=conn)
            return "finalized"

        # 5. If capture is pending, keep the mandate RESERVED
        if paypal_status == "PENDING" or order.status == PaymentStatus.CAPTURE_PENDING:
            logger.info("Order %s capture is pending; keeping mandate %s RESERVED.", order.id, mandate.id)
            return "retained"

        # 6. If order is approved but not captured, do not release mandate. Reconcile capture lifecycle.
        if paypal_status == "APPROVED":
            logger.info("Order %s is approved by buyer; keeping mandate %s RESERVED.", order.id, mandate.id)
            return "retained"

        # 7. Active / payer action required states on PayPal: retain reservation
        if paypal_status in ("CREATED", "SAVED", "PAYER_ACTION_REQUIRED"):
            logger.info("Order %s active on PayPal (status=%s); keeping mandate %s RESERVED.", order.id, paypal_status, mandate.id)
            return "retained"

        # 8. Release reservation only after a definitive safe terminal outcome establishes that no successful or unresolved capture exists
        if paypal_status in ("VOIDED", "EXPIRED"):
            logger.info("Order %s has terminal outcome %s; safely releasing reservation.", order.id, paypal_status)
            with self.payment_service.repository.db.transaction() as conn:
                self.policy_service.expire_mandate_reservation(mandate.id, proposal.id, conn=conn)
                updated_order = order.model_copy(
                    update={
                        "status": PaymentStatus.CANCELLED,
                        "cancelled_at": now,
                    }
                )
                self.payment_service.repository.update_order(updated_order, conn=conn)
            return "released"

        # Fallback for any unknown PayPal status: retain
        return "retained"


# Shared worker singleton
expiry_worker = ExpiryReconciliationWorker()
