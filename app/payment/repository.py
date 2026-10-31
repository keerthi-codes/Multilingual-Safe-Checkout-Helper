"""SQLite-backed repository for PayPal payment orders and capture records."""

from datetime import datetime
from decimal import Decimal
import sqlite3
from typing import Optional

from app.core.database import DatabaseManager, db_manager
from app.payment.models import (
    PaymentOrderRecord,
    PaymentStatus,
    WebhookEventRecord,
    WebhookEventStatus,
)


class PaymentRepository:
    """Durable SQLite store for payment orders and transaction associations."""

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db or db_manager

    def _row_to_record(self, row: sqlite3.Row) -> PaymentOrderRecord:
        return PaymentOrderRecord(
            id=row["id"],
            proposal_id=row["proposal_id"],
            mandate_id=row["mandate_id"],
            idempotency_key=row["idempotency_key"],
            capture_idempotency_key=row["capture_idempotency_key"],
            inr_total=Decimal(str(row["inr_total"])),
            usd_amount=Decimal(str(row["usd_amount"])),
            exchange_rate=Decimal(str(row["exchange_rate"])),
            approve_url=row["approve_url"],
            status=PaymentStatus(row["status"]),
            capture_id=row["capture_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            captured_at=(
                datetime.fromisoformat(row["captured_at"])
                if row["captured_at"]
                else None
            ),
            cancelled_at=(
                datetime.fromisoformat(row["cancelled_at"])
                if row["cancelled_at"]
                else None
            ),
        )

    def save_order(
        self, record: PaymentOrderRecord, conn: Optional[sqlite3.Connection] = None
    ) -> PaymentOrderRecord:
        """Persist a new payment order record."""
        sql = """
        INSERT INTO payment_orders (
            id, proposal_id, mandate_id, idempotency_key, capture_idempotency_key,
            inr_total, usd_amount, exchange_rate, approve_url, status,
            capture_id, created_at, captured_at, cancelled_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = (
            record.id,
            record.proposal_id,
            record.mandate_id,
            record.idempotency_key,
            record.capture_idempotency_key,
            str(record.inr_total),
            str(record.usd_amount),
            str(record.exchange_rate),
            record.approve_url,
            record.status.value,
            record.capture_id,
            record.created_at.isoformat(),
            record.captured_at.isoformat() if record.captured_at else None,
            record.cancelled_at.isoformat() if record.cancelled_at else None,
        )

        try:
            with self.db.transaction(conn) as active_conn:
                active_conn.execute(sql, params)
        except sqlite3.IntegrityError as err:
            err_msg = str(err).lower()
            if "unique" in err_msg or "primary key" in err_msg:
                raise ValueError(f"Payment order '{record.id}' already exists.") from err
            raise

        return record.model_copy(deep=True)

    def get_order(
        self, order_id: str, conn: Optional[sqlite3.Connection] = None
    ) -> Optional[PaymentOrderRecord]:
        """Retrieve an order record by PayPal order ID."""
        sql = "SELECT * FROM payment_orders WHERE id = ?;"
        if conn is not None:
            cursor = conn.execute(sql, (order_id,))
            row = cursor.fetchone()
            return self._row_to_record(row) if row else None

        with self.db.get_connection() as c:
            cursor = c.execute(sql, (order_id,))
            row = cursor.fetchone()
            return self._row_to_record(row) if row else None

    def get_order_by_proposal_id(
        self, proposal_id: str, conn: Optional[sqlite3.Connection] = None
    ) -> Optional[PaymentOrderRecord]:
        """Retrieve an order record by associated proposal ID."""
        sql = "SELECT * FROM payment_orders WHERE proposal_id = ?;"
        if conn is not None:
            cursor = conn.execute(sql, (proposal_id,))
            row = cursor.fetchone()
            return self._row_to_record(row) if row else None

        with self.db.get_connection() as c:
            cursor = c.execute(sql, (proposal_id,))
            row = cursor.fetchone()
            return self._row_to_record(row) if row else None

    def update_order(
        self, record: PaymentOrderRecord, conn: Optional[sqlite3.Connection] = None
    ) -> PaymentOrderRecord:
        """Update an existing payment order record."""
        sql = """
        UPDATE payment_orders SET
            proposal_id = ?,
            mandate_id = ?,
            idempotency_key = ?,
            capture_idempotency_key = ?,
            inr_total = ?,
            usd_amount = ?,
            exchange_rate = ?,
            approve_url = ?,
            status = ?,
            capture_id = ?,
            created_at = ?,
            captured_at = ?,
            cancelled_at = ?
        WHERE id = ?;
        """
        params = (
            record.proposal_id,
            record.mandate_id,
            record.idempotency_key,
            record.capture_idempotency_key,
            str(record.inr_total),
            str(record.usd_amount),
            str(record.exchange_rate),
            record.approve_url,
            record.status.value,
            record.capture_id,
            record.created_at.isoformat(),
            record.captured_at.isoformat() if record.captured_at else None,
            record.cancelled_at.isoformat() if record.cancelled_at else None,
            record.id,
        )

        with self.db.transaction(conn) as active_conn:
            cursor = active_conn.execute(sql, params)
            if cursor.rowcount == 0:
                raise ValueError(f"Cannot update non-existent payment order '{record.id}'.")

        return record.model_copy(deep=True)

    def get_order_by_capture_id(
        self, capture_id: str, conn: Optional[sqlite3.Connection] = None
    ) -> Optional[PaymentOrderRecord]:
        """Retrieve an order record by associated capture ID."""
        sql = "SELECT * FROM payment_orders WHERE capture_id = ?;"
        if conn is not None:
            cursor = conn.execute(sql, (capture_id,))
            row = cursor.fetchone()
            return self._row_to_record(row) if row else None

        with self.db.get_connection() as c:
            cursor = c.execute(sql, (capture_id,))
            row = cursor.fetchone()
            return self._row_to_record(row) if row else None

    def get_orders_by_status(
        self, status: PaymentStatus, conn: Optional[sqlite3.Connection] = None
    ) -> list[PaymentOrderRecord]:
        """Retrieve all payment order records matching a particular status."""
        sql = "SELECT * FROM payment_orders WHERE status = ? ORDER BY created_at ASC;"
        if conn is not None:
            cursor = conn.execute(sql, (status.value,))
            return [self._row_to_record(r) for r in cursor.fetchall()]

        with self.db.get_connection() as c:
            cursor = c.execute(sql, (status.value,))
            return [self._row_to_record(r) for r in cursor.fetchall()]

    def _row_to_webhook_record(self, row: sqlite3.Row) -> WebhookEventRecord:
        return WebhookEventRecord(
            id=row["id"],
            event_type=row["event_type"],
            order_id=row["order_id"],
            capture_id=row["capture_id"],
            status=WebhookEventStatus(row["status"]),
            payload=row["payload"],
            error_message=row["error_message"],
            received_at=datetime.fromisoformat(row["received_at"]),
            processed_at=(
                datetime.fromisoformat(row["processed_at"])
                if row["processed_at"]
                else None
            ),
        )

    def save_webhook_event(
        self, record: WebhookEventRecord, conn: Optional[sqlite3.Connection] = None
    ) -> WebhookEventRecord:
        """Persist a new webhook event record."""
        sql = """
        INSERT INTO webhook_events (
            id, event_type, order_id, capture_id, status, payload,
            error_message, received_at, processed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = (
            record.id,
            record.event_type,
            record.order_id,
            record.capture_id,
            record.status.value,
            record.payload,
            record.error_message,
            record.received_at.isoformat(),
            record.processed_at.isoformat() if record.processed_at else None,
        )

        try:
            with self.db.transaction(conn) as active_conn:
                active_conn.execute(sql, params)
        except sqlite3.IntegrityError as err:
            err_msg = str(err).lower()
            if "unique" in err_msg or "primary key" in err_msg:
                raise ValueError(f"Webhook event '{record.id}' already exists.") from err
            raise

        return record.model_copy(deep=True)

    def get_webhook_event(
        self, event_id: str, conn: Optional[sqlite3.Connection] = None
    ) -> Optional[WebhookEventRecord]:
        """Retrieve a webhook event record by event ID."""
        sql = "SELECT * FROM webhook_events WHERE id = ?;"
        if conn is not None:
            cursor = conn.execute(sql, (event_id,))
            row = cursor.fetchone()
            return self._row_to_webhook_record(row) if row else None

        with self.db.get_connection() as c:
            cursor = c.execute(sql, (event_id,))
            row = cursor.fetchone()
            return self._row_to_webhook_record(row) if row else None

    def update_webhook_event(
        self, record: WebhookEventRecord, conn: Optional[sqlite3.Connection] = None
    ) -> WebhookEventRecord:
        """Update a webhook event record."""
        sql = """
        UPDATE webhook_events SET
            event_type = ?,
            order_id = ?,
            capture_id = ?,
            status = ?,
            payload = ?,
            error_message = ?,
            received_at = ?,
            processed_at = ?
        WHERE id = ?;
        """
        params = (
            record.event_type,
            record.order_id,
            record.capture_id,
            record.status.value,
            record.payload,
            record.error_message,
            record.received_at.isoformat(),
            record.processed_at.isoformat() if record.processed_at else None,
            record.id,
        )

        with self.db.transaction(conn) as active_conn:
            cursor = active_conn.execute(sql, params)
            if cursor.rowcount == 0:
                raise ValueError(f"Cannot update non-existent webhook event '{record.id}'.")

        return record.model_copy(deep=True)

    def clear(self) -> None:
        """Clear all stored payment and webhook records (used for test isolation)."""
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM webhook_events;")
            conn.execute("DELETE FROM payment_orders;")


# Shared repository singleton for backward compatibility
in_memory_payment_repo = PaymentRepository()
