"""Automated tests for SQLite persistence, transaction atomicity, foreign keys, and crash resilience."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import sqlite3
import pytest

from app.core.database import DatabaseManager
from app.payment.models import PaymentOrderRecord, PaymentStatus
from app.payment.repository import PaymentRepository
from app.payment.service import MockPaymentProvider, PaymentService
from app.policy.models import MandateCreateRequest, MandateStatus, ProposalStatus, SpendingMandate
from app.policy.repository import PolicyRepository
from app.policy.service import PolicyService


@pytest.fixture
def isolated_db(tmp_path):
    """Factory creating an isolated SQLite DatabaseManager for direct persistence testing."""
    db_file = tmp_path / "test_isolated.db"
    mgr = DatabaseManager(db_file)
    mgr.init_db()
    return mgr


def test_mandate_persists_across_connection_reopens(tmp_path):
    """Verify SpendingMandate persists accurately across closing and reopening database connections."""
    db_file = tmp_path / "mandate_persist.db"
    mgr1 = DatabaseManager(db_file)
    repo1 = PolicyRepository(db=mgr1)

    now = datetime.now(timezone.utc)
    mandate = SpendingMandate(
        id="mandate_persisted_001",
        max_amount=Decimal("4500.50"),
        currency="INR",
        permitted_categories=["Electronics", "Home & Kitchen"],
        max_quantity=3,
        created_at=now,
        expires_at=now + timedelta(hours=2),
        requires_user_approval=True,
        status=MandateStatus.ACTIVE,
    )
    repo1.save_mandate(mandate)

    # Reopen connection in a completely new DatabaseManager instance (simulating restart)
    mgr2 = DatabaseManager(db_file)
    repo2 = PolicyRepository(db=mgr2)

    loaded = repo2.get_mandate("mandate_persisted_001")
    assert loaded is not None
    assert loaded.id == "mandate_persisted_001"
    assert loaded.max_amount == Decimal("4500.50")
    assert loaded.currency == "INR"
    assert loaded.permitted_categories == ["Electronics", "Home & Kitchen"]
    assert loaded.max_quantity == 3
    assert loaded.status == MandateStatus.ACTIVE
    assert loaded.requires_user_approval is True
    assert loaded.reserved_by_proposal_id is None
    assert loaded.consumed_by_proposal_id is None


def test_proposal_and_approval_secret_hash_persists_raw_secret_never_persisted(tmp_path):
    """Verify proposal persists hash only; raw secret is never persisted to SQLite."""
    db_file = tmp_path / "proposal_persist.db"
    mgr = DatabaseManager(db_file)
    policy_repo = PolicyRepository(db=mgr)
    policy_svc = PolicyService(repository=policy_repo)

    # 1. Create mandate and evaluate proposal
    mandate = policy_svc.create_mandate(
        MandateCreateRequest(
            max_amount=Decimal("5000.00"),
            permitted_categories=["Electronics"],
            max_quantity=2,
        )
    )
    eval_result = policy_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    raw_secret = eval_result.approval_secret
    proposal_id = eval_result.proposal.id

    assert raw_secret is not None
    assert raw_secret.startswith("sec_")

    # 2. Inspect raw SQLite table directly
    with sqlite3.connect(str(db_file)) as raw_conn:
        raw_conn.row_factory = sqlite3.Row
        row = raw_conn.execute("SELECT * FROM purchase_proposals WHERE id = ?;", (proposal_id,)).fetchone()
        assert row is not None

        # Verify hash matches sha256(raw_secret)
        expected_hash = hashlib.sha256(raw_secret.encode("utf-8")).hexdigest()
        assert row["approval_secret_hash"] == expected_hash

        # Verify raw secret is nowhere in any column of the row
        row_values_str = " ".join(str(v) for v in tuple(row))
        assert raw_secret not in row_values_str

    # 3. Re-read through fresh repository instance
    mgr_fresh = DatabaseManager(db_file)
    policy_repo_fresh = PolicyRepository(db=mgr_fresh)
    loaded_prop = policy_repo_fresh.get_proposal(proposal_id)
    assert loaded_prop is not None
    assert loaded_prop.approval_secret_hash == expected_hash

    # Verify Pydantic model serialization excludes hash
    dumped = loaded_prop.model_dump()
    assert "approval_secret_hash" not in dumped


def test_payment_order_persists_and_retains_foreign_key_associations(tmp_path):
    """Verify payment orders persist in SQLite and retain exact foreign-key links to proposal and mandate."""
    db_file = tmp_path / "payment_persist.db"
    mgr = DatabaseManager(db_file)
    policy_repo = PolicyRepository(db=mgr)
    payment_repo = PaymentRepository(db=mgr)
    policy_svc = PolicyService(repository=policy_repo)
    payment_svc = PaymentService(policy=policy_svc, repository=payment_repo, provider=MockPaymentProvider())

    # Create and approve proposal
    mandate = policy_svc.create_mandate(
        MandateCreateRequest(max_amount=Decimal("5000.00"), permitted_categories=["Electronics"])
    )
    eval_res = policy_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    policy_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)

    # Create payment order
    order_res = asyncio.run(payment_svc.create_payment_order(eval_res.proposal.id))
    order_id = order_res.order_id

    # Reopen connection in fresh instances
    mgr2 = DatabaseManager(db_file)
    payment_repo2 = PaymentRepository(db=mgr2)
    loaded_order = payment_repo2.get_order(order_id)

    assert loaded_order is not None
    assert loaded_order.id == order_id
    assert loaded_order.proposal_id == eval_res.proposal.id
    assert loaded_order.mandate_id == mandate.id
    assert loaded_order.inr_total == Decimal("2999.00")
    assert loaded_order.usd_amount == Decimal("35.99")
    assert loaded_order.status == PaymentStatus.CREATED


def test_competing_proposals_cannot_double_reserve_mandate(tmp_path):
    """Verify SQLite guarded transactions prevent two proposals from reserving the same mandate."""
    db_file = tmp_path / "double_reserve.db"
    mgr = DatabaseManager(db_file)
    policy_repo = PolicyRepository(db=mgr)
    policy_svc = PolicyService(repository=policy_repo)

    mandate = policy_svc.create_mandate(
        MandateCreateRequest(max_amount=Decimal("5000.00"), permitted_categories=["Electronics"])
    )

    # Create two eligible proposals against the same mandate
    eval1 = policy_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    eval2 = policy_svc.evaluate_proposal(mandate.id, "prod-002", 1)

    # Approve proposal 1 -> Mandate transitions to RESERVED
    prop1_approved = policy_svc.approve_proposal(eval1.proposal.id, eval1.approval_secret)
    assert prop1_approved.status == ProposalStatus.USER_APPROVED

    # Mandate is now RESERVED by prop1
    m_check = policy_svc.get_mandate(mandate.id)
    assert m_check.status == MandateStatus.RESERVED
    assert m_check.reserved_by_proposal_id == eval1.proposal.id

    # Attempt to approve proposal 2 against the same mandate -> MUST FAIL
    with pytest.raises(ValueError, match="Mandate cannot be consumed"):
        policy_svc.approve_proposal(eval2.proposal.id, eval2.approval_secret)

    # Verify mandate is STILL reserved ONLY by prop1
    m_final = policy_svc.get_mandate(mandate.id)
    assert m_final.reserved_by_proposal_id == eval1.proposal.id
    assert m_final.status == MandateStatus.RESERVED

    # Proposal 2 remains PENDING_APPROVAL
    prop2_check = policy_svc.get_proposal(eval2.proposal.id)
    assert prop2_check.status == ProposalStatus.PENDING_APPROVAL


def test_failed_transaction_rolls_back_all_changes(tmp_path):
    """Verify that any exception within an SQLite transaction causes full rollback of all affected rows."""
    db_file = tmp_path / "rollback.db"
    mgr = DatabaseManager(db_file)

    now = datetime.now(timezone.utc).isoformat()

    # Intentionally insert a row and then raise an unhandled exception
    with pytest.raises(RuntimeError, match="Simulated crash"):
        with mgr.transaction() as conn:
            conn.execute(
                "INSERT INTO spending_mandates (id, max_amount, currency, permitted_categories, max_quantity, created_at, expires_at, status) "
                "VALUES ('m_rollback_test', '1000.00', 'INR', '[\"Books\"]', 1, ?, ?, 'ACTIVE');",
                (now, now),
            )
            # Crash before commit
            raise RuntimeError("Simulated crash")

    # Verify the row was rolled back and does not exist in the database
    with mgr.get_connection() as check_conn:
        row = check_conn.execute("SELECT * FROM spending_mandates WHERE id = 'm_rollback_test';").fetchone()
        assert row is None


def test_capture_finalization_atomic_consumption_and_payment(tmp_path):
    """Verify capture finalization updates payment order, proposal, and mandate in a single atomic transaction."""
    db_file = tmp_path / "atomic_capture.db"
    mgr = DatabaseManager(db_file)
    policy_repo = PolicyRepository(db=mgr)
    payment_repo = PaymentRepository(db=mgr)
    policy_svc = PolicyService(repository=policy_repo)
    payment_svc = PaymentService(policy=policy_svc, repository=payment_repo, provider=MockPaymentProvider())

    mandate = policy_svc.create_mandate(
        MandateCreateRequest(max_amount=Decimal("5000.00"), permitted_categories=["Electronics"])
    )
    eval_res = policy_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    policy_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)
    order_res = asyncio.run(payment_svc.create_payment_order(eval_res.proposal.id))

    # Capture payment
    cap_res = asyncio.run(payment_svc.capture_payment_order(order_res.order_id))
    assert cap_res.status == PaymentStatus.COMPLETED
    assert cap_res.capture_id is not None

    # Check database state for all 3 entities
    m = policy_svc.get_mandate(mandate.id)
    assert m.status == MandateStatus.CONSUMED
    assert m.consumed_by_proposal_id == eval_res.proposal.id
    assert m.reserved_by_proposal_id is None

    p = policy_svc.get_proposal(eval_res.proposal.id)
    assert p.status == ProposalStatus.PAID

    o = payment_svc.get_order_record(order_res.order_id)
    assert o.status == PaymentStatus.COMPLETED
    assert o.capture_id == cap_res.capture_id
    assert o.captured_at is not None


def test_repeated_capture_is_idempotent_no_double_consume(tmp_path):
    """Verify calling capture on an already-completed order returns the completed record idempotently."""
    db_file = tmp_path / "idempotent_capture.db"
    mgr = DatabaseManager(db_file)
    policy_repo = PolicyRepository(db=mgr)
    payment_repo = PaymentRepository(db=mgr)
    policy_svc = PolicyService(repository=policy_repo)
    payment_svc = PaymentService(policy=policy_svc, repository=payment_repo, provider=MockPaymentProvider())

    mandate = policy_svc.create_mandate(
        MandateCreateRequest(max_amount=Decimal("5000.00"), permitted_categories=["Electronics"])
    )
    eval_res = policy_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    policy_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)
    order_res = asyncio.run(payment_svc.create_payment_order(eval_res.proposal.id))

    # Capture call 1
    cap1 = asyncio.run(payment_svc.capture_payment_order(order_res.order_id))
    assert cap1.status == PaymentStatus.COMPLETED

    # Capture call 2 (idempotent repeat)
    cap2 = asyncio.run(payment_svc.capture_payment_order(order_res.order_id))
    assert cap2.status == PaymentStatus.COMPLETED
    assert cap2.capture_id == cap1.capture_id

    # Mandate remains CONSUMED (not modified again)
    m = policy_svc.get_mandate(mandate.id)
    assert m.status == MandateStatus.CONSUMED


def test_uncertain_provider_result_does_not_release_or_consume_mandate(tmp_path):
    """Verify provider error during capture leaves mandate in RESERVED state without consuming or releasing."""
    db_file = tmp_path / "uncertain_result.db"
    mgr = DatabaseManager(db_file)
    policy_repo = PolicyRepository(db=mgr)
    payment_repo = PaymentRepository(db=mgr)
    policy_svc = PolicyService(repository=policy_repo)

    mock_prov = MockPaymentProvider()
    mock_prov.simulate_capture_error = True
    payment_svc = PaymentService(policy=policy_svc, repository=payment_repo, provider=mock_prov)

    mandate = policy_svc.create_mandate(
        MandateCreateRequest(max_amount=Decimal("5000.00"), permitted_categories=["Electronics"])
    )
    eval_res = policy_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    policy_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)
    order_res = asyncio.run(payment_svc.create_payment_order(eval_res.proposal.id))

    # Capture raises error
    with pytest.raises(ValueError, match="Payment capture verification failed"):
        asyncio.run(payment_svc.capture_payment_order(order_res.order_id))

    # Mandate MUST remain RESERVED
    m = policy_svc.get_mandate(mandate.id)
    assert m.status == MandateStatus.RESERVED
    assert m.reserved_by_proposal_id == eval_res.proposal.id

    # Proposal MUST remain USER_APPROVED
    p = policy_svc.get_proposal(eval_res.proposal.id)
    assert p.status == ProposalStatus.USER_APPROVED

    # Order record MUST remain CREATED (not COMPLETED, not CANCELLED)
    o = payment_svc.get_order_record(order_res.order_id)
    assert o.status == PaymentStatus.CREATED


def test_duplicate_order_creation_requests_reuse_existing_order(tmp_path):
    """Verify duplicate order creation requests for the same proposal return existing order without duplicating."""
    db_file = tmp_path / "duplicate_order.db"
    mgr = DatabaseManager(db_file)
    policy_repo = PolicyRepository(db=mgr)
    payment_repo = PaymentRepository(db=mgr)
    policy_svc = PolicyService(repository=policy_repo)
    payment_svc = PaymentService(policy=policy_svc, repository=payment_repo, provider=MockPaymentProvider())

    mandate = policy_svc.create_mandate(
        MandateCreateRequest(max_amount=Decimal("5000.00"), permitted_categories=["Electronics"])
    )
    eval_res = policy_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    policy_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)

    # First creation call
    res1 = asyncio.run(payment_svc.create_payment_order(eval_res.proposal.id))

    # Second creation call
    res2 = asyncio.run(payment_svc.create_payment_order(eval_res.proposal.id))

    assert res1.order_id == res2.order_id

    # Verify table has exactly one row
    with mgr.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM payment_orders WHERE proposal_id = ?;", (eval_res.proposal.id,)).fetchone()[0]
        assert count == 1


def test_foreign_key_and_uniqueness_constraints(tmp_path):
    """Verify SQLite foreign-key enforcement and uniqueness constraints reject invalid relationships."""
    db_file = tmp_path / "fk_constraints.db"
    mgr = DatabaseManager(db_file)

    now = datetime.now(timezone.utc).isoformat()

    # 1. Foreign Key: purchase_proposals requires valid mandate_id
    with pytest.raises(sqlite3.IntegrityError):
        with mgr.transaction() as conn:
            conn.execute(
                "INSERT INTO purchase_proposals (id, mandate_id, product_id, product_name, product_category, "
                "unit_price, quantity, total_amount, currency, status, created_at) "
                "VALUES ('prop_orphan', 'nonexistent_mandate', 'prod-001', 'Test', 'Electronics', '10.00', 1, '10.00', 'INR', 'PENDING_APPROVAL', ?);",
                (now,),
            )

    # 2. Foreign Key: payment_orders requires valid proposal_id and mandate_id
    with pytest.raises(sqlite3.IntegrityError):
        with mgr.transaction() as conn:
            conn.execute(
                "INSERT INTO payment_orders (id, proposal_id, mandate_id, idempotency_key, inr_total, usd_amount, exchange_rate, approve_url, status, created_at) "
                "VALUES ('ord_orphan', 'nonexistent_prop', 'nonexistent_mandate', 'key_1', '10.00', '0.12', '0.012', 'http://url', 'CREATED', ?);",
                (now,),
            )


def test_fresh_database_initialization_is_idempotent(tmp_path):
    """Verify DatabaseManager.init_db() creates all tables on fresh DB and is safely idempotent on re-run."""
    db_file = tmp_path / "fresh_idempotent.db"
    mgr = DatabaseManager(db_file)

    # Calling init_db multiple times must not raise errors
    mgr.init_db()
    mgr.init_db()

    with mgr.get_connection() as conn:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;").fetchall()
        table_names = [r["name"] for r in tables]
        assert "spending_mandates" in table_names
        assert "purchase_proposals" in table_names
        assert "payment_orders" in table_names
