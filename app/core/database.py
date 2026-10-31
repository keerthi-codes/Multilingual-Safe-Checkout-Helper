"""SQLite database management, schema initialization, and transactional connection handling."""

from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Generator, Optional, Union

from app.core.config import settings

logger = logging.getLogger(__name__)


SCHEMA_SQL = """
-- Spending Mandates table
CREATE TABLE IF NOT EXISTS spending_mandates (
    id TEXT PRIMARY KEY,
    max_amount TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    permitted_categories TEXT NOT NULL,
    max_quantity INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    requires_user_approval INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'RESERVED', 'CONSUMED', 'REVOKED', 'EXPIRED')),
    reserved_by_proposal_id TEXT DEFAULT NULL,
    consumed_by_proposal_id TEXT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_mandates_status ON spending_mandates(status);
CREATE INDEX IF NOT EXISTS idx_mandates_reserved_by ON spending_mandates(reserved_by_proposal_id);

-- Purchase Proposals table
CREATE TABLE IF NOT EXISTS purchase_proposals (
    id TEXT PRIMARY KEY,
    mandate_id TEXT NOT NULL REFERENCES spending_mandates(id) ON DELETE RESTRICT,
    product_id TEXT NOT NULL,
    product_name TEXT NOT NULL,
    product_category TEXT NOT NULL,
    unit_price TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    total_amount TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    status TEXT NOT NULL DEFAULT 'PENDING_APPROVAL' CHECK(status IN ('PENDING_APPROVAL', 'USER_APPROVED', 'PAID', 'CANCELLED', 'PAYMENT_FAILED', 'REJECTED')),
    requires_user_approval INTEGER NOT NULL DEFAULT 1,
    is_user_approved INTEGER NOT NULL DEFAULT 0,
    approval_secret_hash TEXT DEFAULT NULL,
    created_at TEXT NOT NULL,
    user_approved_at TEXT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_proposals_mandate_id ON purchase_proposals(mandate_id);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON purchase_proposals(status);

-- Payment Orders table
CREATE TABLE IF NOT EXISTS payment_orders (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL UNIQUE REFERENCES purchase_proposals(id) ON DELETE RESTRICT,
    mandate_id TEXT NOT NULL REFERENCES spending_mandates(id) ON DELETE RESTRICT,
    idempotency_key TEXT NOT NULL UNIQUE,
    capture_idempotency_key TEXT DEFAULT NULL,
    inr_total TEXT NOT NULL,
    usd_amount TEXT NOT NULL,
    exchange_rate TEXT NOT NULL,
    approve_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'CREATED' CHECK(status IN ('CREATED', 'BUYER_APPROVED', 'CAPTURE_PENDING', 'COMPLETED', 'FAILED', 'CANCELLED', 'ORDER_CREATION_PENDING', 'ORDER_CREATION_UNKNOWN', 'CAPTURE_UNKNOWN')),
    capture_id TEXT DEFAULT NULL,
    created_at TEXT NOT NULL,
    captured_at TEXT DEFAULT NULL,
    cancelled_at TEXT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_payment_orders_proposal_id ON payment_orders(proposal_id);
CREATE INDEX IF NOT EXISTS idx_payment_orders_mandate_id ON payment_orders(mandate_id);
CREATE INDEX IF NOT EXISTS idx_payment_orders_status ON payment_orders(status);
CREATE INDEX IF NOT EXISTS idx_payment_orders_idempotency ON payment_orders(idempotency_key);

-- Webhook Events table
CREATE TABLE IF NOT EXISTS webhook_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    order_id TEXT DEFAULT NULL,
    capture_id TEXT DEFAULT NULL,
    status TEXT NOT NULL DEFAULT 'RECEIVED' CHECK(status IN ('RECEIVED', 'PROCESSED', 'FAILED', 'IGNORED')),
    payload TEXT NOT NULL,
    error_message TEXT DEFAULT NULL,
    received_at TEXT NOT NULL,
    processed_at TEXT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_order_id ON webhook_events(order_id);
CREATE INDEX IF NOT EXISTS idx_webhook_events_status ON webhook_events(status);
CREATE INDEX IF NOT EXISTS idx_webhook_events_type ON webhook_events(event_type);
"""


class DatabaseManager:
    """Manages SQLite connection lifecycle, WAL configuration, and explicit transaction execution."""

    def __init__(self, db_path: Optional[Union[str, Path]] = None) -> None:
        self.db_path_str = str(db_path or settings.SQLITE_DB_PATH)
        self._ensure_parent_directory()
        self.init_db()

    def _ensure_parent_directory(self) -> None:
        if self.db_path_str != ":memory:" and not self.db_path_str.startswith("file:"):
            path = Path(self.db_path_str)
            path.parent.mkdir(parents=True, exist_ok=True)

    def create_connection(self) -> sqlite3.Connection:
        """Create and configure a single SQLite connection with safety pragmas."""
        conn = sqlite3.connect(self.db_path_str, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")

        # Enable WAL mode and normal synchronous for file-backed databases
        if self.db_path_str != ":memory:" and not self.db_path_str.startswith("file:"):
            try:
                conn.execute("PRAGMA journal_mode = WAL;")
                conn.execute("PRAGMA synchronous = NORMAL;")
            except sqlite3.OperationalError:
                pass

        return conn

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager yielding a read connection that closes on exit."""
        conn = self.create_connection()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(
        self, existing_conn: Optional[sqlite3.Connection] = None
    ) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for write transactions.
        
        Uses BEGIN IMMEDIATE to acquire a write lock immediately at transaction start,
        preventing concurrent upgrade deadlocks (SQLITE_BUSY).
        If existing_conn is provided, participates in that connection without managing commit/close.
        """
        if existing_conn is not None:
            yield existing_conn
            return

        conn = self.create_connection()
        # Set isolation_level to None so Python doesn't inject automatic BEGIN statements
        conn.isolation_level = None
        try:
            conn.execute("BEGIN IMMEDIATE;")
            yield conn
            conn.execute("COMMIT;")
        except Exception:
            try:
                conn.execute("ROLLBACK;")
            except Exception as rollback_err:
                logger.error("Failed to rollback transaction: %s", rollback_err)
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        """Idempotently create tables and indexes if they do not exist."""
        with self.get_connection() as conn:
            conn.executescript(SCHEMA_SQL)
        logger.info("SQLite database initialized at '%s'.", self.db_path_str)

    def clear_all(self) -> None:
        """Safely delete all stored data (used for test isolation)."""
        with self.transaction() as conn:
            conn.execute("DELETE FROM webhook_events;")
            conn.execute("DELETE FROM payment_orders;")
            conn.execute("DELETE FROM purchase_proposals;")
            conn.execute("DELETE FROM spending_mandates;")


# Global singleton instance
db_manager = DatabaseManager()
