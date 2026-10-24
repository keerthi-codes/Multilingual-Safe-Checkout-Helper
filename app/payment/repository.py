"""In-memory repository for PayPal payment orders and capture records."""

import copy
from typing import Optional

from app.payment.models import PaymentOrderRecord


class PaymentRepository:
    """Thread-safe in-memory store for payment orders and transaction associations."""

    def __init__(self) -> None:
        self._orders: dict[str, PaymentOrderRecord] = {}
        self._proposal_to_order: dict[str, str] = {}

    def save_order(self, record: PaymentOrderRecord) -> PaymentOrderRecord:
        """Persist a new payment order record."""
        if record.id in self._orders:
            raise ValueError(f"Payment order '{record.id}' already exists.")
        self._orders[record.id] = record.model_copy(deep=True)
        self._proposal_to_order[record.proposal_id] = record.id
        return record.model_copy(deep=True)

    def get_order(self, order_id: str) -> Optional[PaymentOrderRecord]:
        """Retrieve an order record by PayPal order ID."""
        rec = self._orders.get(order_id)
        if rec is None:
            return None
        return rec.model_copy(deep=True)

    def get_order_by_proposal_id(self, proposal_id: str) -> Optional[PaymentOrderRecord]:
        """Retrieve an order record by associated proposal ID."""
        order_id = self._proposal_to_order.get(proposal_id)
        if not order_id:
            return None
        return self.get_order(order_id)

    def update_order(self, record: PaymentOrderRecord) -> PaymentOrderRecord:
        """Update an existing payment order record."""
        if record.id not in self._orders:
            raise ValueError(f"Cannot update non-existent payment order '{record.id}'.")
        self._orders[record.id] = record.model_copy(deep=True)
        return record.model_copy(deep=True)

    def clear(self) -> None:
        """Clear all stored payment records (used for test isolation)."""
        self._orders.clear()
        self._proposal_to_order.clear()


# Shared repository singleton
in_memory_payment_repo = PaymentRepository()
