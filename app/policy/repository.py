"""SQLite-backed repository for spending mandates and purchase proposals."""

from datetime import datetime
from decimal import Decimal
import json
import sqlite3
from typing import Optional

from app.core.database import DatabaseManager, db_manager
from app.policy.models import (
    MandateStatus,
    ProposalStatus,
    PurchaseProposal,
    SpendingMandate,
)


class PolicyRepository:
    """Durable SQLite repository for spending mandates and purchase proposals."""

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db or db_manager

    def _row_to_mandate(self, row: sqlite3.Row) -> SpendingMandate:
        return SpendingMandate(
            id=row["id"],
            max_amount=Decimal(str(row["max_amount"])),
            currency=row["currency"],
            permitted_categories=json.loads(row["permitted_categories"]),
            max_quantity=int(row["max_quantity"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            requires_user_approval=bool(row["requires_user_approval"]),
            status=MandateStatus(row["status"]),
            reserved_by_proposal_id=row["reserved_by_proposal_id"],
            consumed_by_proposal_id=row["consumed_by_proposal_id"],
        )

    def _row_to_proposal(self, row: sqlite3.Row) -> PurchaseProposal:
        return PurchaseProposal(
            id=row["id"],
            mandate_id=row["mandate_id"],
            product_id=row["product_id"],
            product_name=row["product_name"],
            product_category=row["product_category"],
            unit_price=Decimal(str(row["unit_price"])),
            quantity=int(row["quantity"]),
            total_amount=Decimal(str(row["total_amount"])),
            currency=row["currency"],
            status=ProposalStatus(row["status"]),
            requires_user_approval=bool(row["requires_user_approval"]),
            is_user_approved=bool(row["is_user_approved"]),
            approval_secret_hash=row["approval_secret_hash"],
            created_at=datetime.fromisoformat(row["created_at"]),
            user_approved_at=(
                datetime.fromisoformat(row["user_approved_at"])
                if row["user_approved_at"]
                else None
            ),
        )

    def save_mandate(
        self, mandate: SpendingMandate, conn: Optional[sqlite3.Connection] = None
    ) -> SpendingMandate:
        """Store a spending mandate in SQLite and return a safe copy."""
        sql = """
        INSERT INTO spending_mandates (
            id, max_amount, currency, permitted_categories, max_quantity,
            created_at, expires_at, requires_user_approval, status,
            reserved_by_proposal_id, consumed_by_proposal_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            max_amount=excluded.max_amount,
            currency=excluded.currency,
            permitted_categories=excluded.permitted_categories,
            max_quantity=excluded.max_quantity,
            created_at=excluded.created_at,
            expires_at=excluded.expires_at,
            requires_user_approval=excluded.requires_user_approval,
            status=excluded.status,
            reserved_by_proposal_id=excluded.reserved_by_proposal_id,
            consumed_by_proposal_id=excluded.consumed_by_proposal_id;
        """
        params = (
            mandate.id,
            str(mandate.max_amount),
            mandate.currency,
            json.dumps(mandate.permitted_categories),
            mandate.max_quantity,
            mandate.created_at.isoformat(),
            mandate.expires_at.isoformat(),
            1 if mandate.requires_user_approval else 0,
            mandate.status.value,
            mandate.reserved_by_proposal_id,
            mandate.consumed_by_proposal_id,
        )

        with self.db.transaction(conn) as active_conn:
            active_conn.execute(sql, params)

        return mandate.model_copy(deep=True)

    def get_mandate(
        self, mandate_id: str, conn: Optional[sqlite3.Connection] = None
    ) -> Optional[SpendingMandate]:
        """Retrieve a spending mandate by ID, returning a model copy or None."""
        sql = "SELECT * FROM spending_mandates WHERE id = ?;"
        if conn is not None:
            cursor = conn.execute(sql, (mandate_id,))
            row = cursor.fetchone()
            return self._row_to_mandate(row) if row else None

        with self.db.get_connection() as c:
            cursor = c.execute(sql, (mandate_id,))
            row = cursor.fetchone()
            return self._row_to_mandate(row) if row else None

    def list_mandates(
        self, conn: Optional[sqlite3.Connection] = None
    ) -> list[SpendingMandate]:
        """Return copies of all stored mandates."""
        sql = "SELECT * FROM spending_mandates ORDER BY created_at DESC;"
        if conn is not None:
            cursor = conn.execute(sql)
            return [self._row_to_mandate(row) for row in cursor.fetchall()]

        with self.db.get_connection() as c:
            cursor = c.execute(sql)
            return [self._row_to_mandate(row) for row in cursor.fetchall()]

    def update_mandate(
        self, mandate: SpendingMandate, conn: Optional[sqlite3.Connection] = None
    ) -> SpendingMandate:
        """Update an existing mandate."""
        return self.save_mandate(mandate, conn=conn)

    def get_expired_reserved_mandates(
        self, now: datetime, conn: Optional[sqlite3.Connection] = None
    ) -> list[SpendingMandate]:
        """Retrieve all RESERVED mandates whose expires_at is less than or equal to now."""
        sql = "SELECT * FROM spending_mandates WHERE status = 'RESERVED' AND expires_at <= ? ORDER BY expires_at ASC;"
        if conn is not None:
            cursor = conn.execute(sql, (now.isoformat(),))
            return [self._row_to_mandate(row) for row in cursor.fetchall()]

        with self.db.get_connection() as c:
            cursor = c.execute(sql, (now.isoformat(),))
            return [self._row_to_mandate(row) for row in cursor.fetchall()]

    def save_proposal(
        self, proposal: PurchaseProposal, conn: Optional[sqlite3.Connection] = None
    ) -> PurchaseProposal:
        """Store a purchase proposal in SQLite and return a safe copy."""
        sql = """
        INSERT INTO purchase_proposals (
            id, mandate_id, product_id, product_name, product_category,
            unit_price, quantity, total_amount, currency, status,
            requires_user_approval, is_user_approved, approval_secret_hash,
            created_at, user_approved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            mandate_id=excluded.mandate_id,
            product_id=excluded.product_id,
            product_name=excluded.product_name,
            product_category=excluded.product_category,
            unit_price=excluded.unit_price,
            quantity=excluded.quantity,
            total_amount=excluded.total_amount,
            currency=excluded.currency,
            status=excluded.status,
            requires_user_approval=excluded.requires_user_approval,
            is_user_approved=excluded.is_user_approved,
            approval_secret_hash=excluded.approval_secret_hash,
            created_at=excluded.created_at,
            user_approved_at=excluded.user_approved_at;
        """
        params = (
            proposal.id,
            proposal.mandate_id,
            proposal.product_id,
            proposal.product_name,
            proposal.product_category,
            str(proposal.unit_price),
            proposal.quantity,
            str(proposal.total_amount),
            proposal.currency,
            proposal.status.value,
            1 if proposal.requires_user_approval else 0,
            1 if proposal.is_user_approved else 0,
            proposal.approval_secret_hash,
            proposal.created_at.isoformat(),
            proposal.user_approved_at.isoformat() if proposal.user_approved_at else None,
        )

        with self.db.transaction(conn) as active_conn:
            active_conn.execute(sql, params)

        return proposal.model_copy(deep=True)

    def get_proposal(
        self, proposal_id: str, conn: Optional[sqlite3.Connection] = None
    ) -> Optional[PurchaseProposal]:
        """Retrieve a purchase proposal by ID, returning a model copy or None."""
        sql = "SELECT * FROM purchase_proposals WHERE id = ?;"
        if conn is not None:
            cursor = conn.execute(sql, (proposal_id,))
            row = cursor.fetchone()
            return self._row_to_proposal(row) if row else None

        with self.db.get_connection() as c:
            cursor = c.execute(sql, (proposal_id,))
            row = cursor.fetchone()
            return self._row_to_proposal(row) if row else None

    def list_proposals(
        self, conn: Optional[sqlite3.Connection] = None
    ) -> list[PurchaseProposal]:
        """Return copies of all stored proposals."""
        sql = "SELECT * FROM purchase_proposals ORDER BY created_at DESC;"
        if conn is not None:
            cursor = conn.execute(sql)
            return [self._row_to_proposal(row) for row in cursor.fetchall()]

        with self.db.get_connection() as c:
            cursor = c.execute(sql)
            return [self._row_to_proposal(row) for row in cursor.fetchall()]

    def update_proposal(
        self, proposal: PurchaseProposal, conn: Optional[sqlite3.Connection] = None
    ) -> PurchaseProposal:
        """Update an existing proposal."""
        return self.save_proposal(proposal, conn=conn)

    def clear(self) -> None:
        """Clear all stored policy data (primarily used for test isolation)."""
        self.db.clear_all()


# Shared singleton repository instance for backward compatibility
in_memory_policy_repo = PolicyRepository()
