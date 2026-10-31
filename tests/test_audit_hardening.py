"""Comprehensive End-to-End Audit Scenario Test Suite (Task 8).

Validates all 18 security and reliability audit scenarios specified in Task 8:
1. Successful mock-mode shopping flow.
2. Product unavailable.
3. Spending limit exceeded.
4. Category restriction.
5. Expired mandate.
6. Duplicate approval request.
7. Duplicate payment-order creation.
8. Duplicate capture request.
9. Capture failure.
10. Capture timeout and uncertain result.
11. Pending PayPal capture.
12. Verified completed capture.
13. Invalid webhook signature.
14. Duplicate webhook event.
15. Out-of-order webhook delivery.
16. Browser refresh during checkout.
17. User cancellation.
18. Provider unreachable during expiry reconciliation.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import pytest
from starlette.testclient import TestClient

from app.core.database import DatabaseManager
from app.main import app
from app.payment.models import PaymentStatus, WebhookEventStatus
from app.payment.repository import PaymentRepository
from app.payment.service import MockPaymentProvider, PaymentService, payment_service
from app.payment.worker import ExpiryReconciliationWorker
from app.policy.models import (
    MandateCreateRequest,
    MandateStatus,
    PolicyDecision,
    PolicyReasonCode,
    ProposalStatus,
)
from app.policy.repository import PolicyRepository
from app.policy.service import PolicyService

client = TestClient(app)


def _valid_headers(sig: str = "valid_mock_signature_123") -> dict[str, str]:
    return {
        "paypal-transmission-id": "trans-audit-12345",
        "paypal-transmission-time": "2026-10-31T12:00:00Z",
        "paypal-cert-url": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-123",
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-transmission-sig": sig,
    }


def _setup_isolated_services(tmp_path):
    mgr = DatabaseManager(tmp_path / "audit_test.db")
    pol_repo = PolicyRepository(db=mgr)
    pay_repo = PaymentRepository(db=mgr)
    clock = lambda: datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    pol_svc = PolicyService(repository=pol_repo, clock=clock)
    mock_prov = MockPaymentProvider()
    pay_svc = PaymentService(policy=pol_svc, repository=pay_repo, provider=mock_prov, clock=clock)
    return mgr, pol_svc, pay_svc, mock_prov, clock


# ---------------------------------------------------------------------------
# Scenario 1: Successful mock-mode shopping flow
# ---------------------------------------------------------------------------
def test_scenario_01_successful_mock_mode_shopping_flow(tmp_path):
    mgr, pol_svc, pay_svc, mock_prov, clock = _setup_isolated_services(tmp_path)
    mandate = pol_svc.create_mandate(
        MandateCreateRequest(
            max_amount=Decimal("5000.00"),
            permitted_categories=["Electronics", "Books"],
            max_quantity=2,
        )
    )
    eval_res = pol_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    assert eval_res.decision == PolicyDecision.ALLOW_PENDING_APPROVAL
    assert eval_res.approval_secret is not None

    approved = pol_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)
    assert approved.status == ProposalStatus.USER_APPROVED
    assert pol_svc.get_mandate(mandate.id).status == MandateStatus.RESERVED

    order_res = asyncio.run(pay_svc.create_payment_order(approved.id))
    assert order_res.status == PaymentStatus.CREATED
    assert order_res.currency_breakdown.inr_total == Decimal("2999.00")
    assert order_res.currency_breakdown.usd_amount == Decimal("35.99")

    cap_res = asyncio.run(pay_svc.capture_payment_order(order_res.order_id))
    assert cap_res.status == PaymentStatus.COMPLETED
    assert pol_svc.get_mandate(mandate.id).status == MandateStatus.CONSUMED
    assert pol_svc.get_proposal(approved.id).status == ProposalStatus.PAID


# ---------------------------------------------------------------------------
# Scenario 2: Product unavailable
# ---------------------------------------------------------------------------
def test_scenario_02_product_unavailable(tmp_path):
    mgr, pol_svc, pay_svc, _, _ = _setup_isolated_services(tmp_path)
    mandate = pol_svc.create_mandate(
        MandateCreateRequest(max_amount=Decimal("5000.00"), permitted_categories=["Electronics"])
    )
    # prod-003 is Compact 65W GaN USB-C Charger (available=False in seed)
    eval_res = pol_svc.evaluate_proposal(mandate.id, "prod-003", 1)
    assert eval_res.decision == PolicyDecision.BLOCKED
    assert eval_res.reason_code == PolicyReasonCode.PRODUCT_UNAVAILABLE
    assert eval_res.approval_secret is None


# ---------------------------------------------------------------------------
# Scenario 3: Spending limit exceeded
# ---------------------------------------------------------------------------
def test_scenario_03_spending_limit_exceeded(tmp_path):
    mgr, pol_svc, pay_svc, _, _ = _setup_isolated_services(tmp_path)
    mandate = pol_svc.create_mandate(
        MandateCreateRequest(max_amount=Decimal("2000.00"), permitted_categories=["Electronics"])
    )
    # prod-001 costs 2999.00 > 2000.00
    eval_res = pol_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    assert eval_res.decision == PolicyDecision.BLOCKED
    assert eval_res.reason_code == PolicyReasonCode.AMOUNT_EXCEEDS_LIMIT
    assert eval_res.approval_secret is None


# ---------------------------------------------------------------------------
# Scenario 4: Category restriction
# ---------------------------------------------------------------------------
def test_scenario_04_category_restriction(tmp_path):
    mgr, pol_svc, pay_svc, _, _ = _setup_isolated_services(tmp_path)
    mandate = pol_svc.create_mandate(
        MandateCreateRequest(max_amount=Decimal("5000.00"), permitted_categories=["Books"])
    )
    # prod-001 is Electronics, not permitted
    eval_res = pol_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    assert eval_res.decision == PolicyDecision.BLOCKED
    assert eval_res.reason_code == PolicyReasonCode.CATEGORY_DISALLOWED


# ---------------------------------------------------------------------------
# Scenario 5: Expired mandate
# ---------------------------------------------------------------------------
def test_scenario_05_expired_mandate(tmp_path):
    base_time = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    current_time = base_time

    def clock():
        return current_time

    mgr = DatabaseManager(tmp_path / "scenario_05.db")
    pol_svc = PolicyService(repository=PolicyRepository(db=mgr), clock=clock)
    mandate = pol_svc.create_mandate(
        MandateCreateRequest(
            max_amount=Decimal("5000.00"),
            permitted_categories=["Electronics"],
            expires_in_seconds=3600,
        )
    )
    # Advance time past expiration
    current_time = base_time + timedelta(seconds=7200)
    eval_res = pol_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    assert eval_res.decision == PolicyDecision.BLOCKED
    assert eval_res.reason_code == PolicyReasonCode.MANDATE_EXPIRED


# ---------------------------------------------------------------------------
# Scenario 6: Duplicate approval request
# ---------------------------------------------------------------------------
def test_scenario_06_duplicate_approval_request(tmp_path):
    mgr, pol_svc, pay_svc, _, _ = _setup_isolated_services(tmp_path)
    mandate = pol_svc.create_mandate(
        MandateCreateRequest(max_amount=Decimal("5000.00"), permitted_categories=["Electronics"])
    )
    eval_res = pol_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    pol_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)

    # Second approval attempt for same proposal must fail
    with pytest.raises(ValueError, match="Proposal cannot be approved in its current state"):
        pol_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)


# ---------------------------------------------------------------------------
# Scenario 7: Duplicate payment-order creation
# ---------------------------------------------------------------------------
def test_scenario_07_duplicate_payment_order_creation(tmp_path):
    mgr, pol_svc, pay_svc, _, _ = _setup_isolated_services(tmp_path)
    mandate = pol_svc.create_mandate(
        MandateCreateRequest(max_amount=Decimal("5000.00"), permitted_categories=["Electronics"])
    )
    eval_res = pol_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    pol_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)

    ord1 = asyncio.run(pay_svc.create_payment_order(eval_res.proposal.id))
    ord2 = asyncio.run(pay_svc.create_payment_order(eval_res.proposal.id))
    assert ord1.order_id == ord2.order_id
    assert ord1.approve_url == ord2.approve_url


# ---------------------------------------------------------------------------
# Scenario 8: Duplicate capture request
# ---------------------------------------------------------------------------
def test_scenario_08_duplicate_capture_request(tmp_path):
    mgr, pol_svc, pay_svc, _, _ = _setup_isolated_services(tmp_path)
    mandate = pol_svc.create_mandate(
        MandateCreateRequest(max_amount=Decimal("5000.00"), permitted_categories=["Electronics"])
    )
    eval_res = pol_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    pol_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)
    order_res = asyncio.run(pay_svc.create_payment_order(eval_res.proposal.id))

    cap1 = asyncio.run(pay_svc.capture_payment_order(order_res.order_id))
    assert cap1.status == PaymentStatus.COMPLETED

    cap2 = asyncio.run(pay_svc.capture_payment_order(order_res.order_id))
    assert cap2.status == PaymentStatus.COMPLETED
    assert cap2.capture_id == cap1.capture_id
    assert "already captured" in cap2.message.lower()


# ---------------------------------------------------------------------------
# Scenario 9: Capture failure
# ---------------------------------------------------------------------------
def test_scenario_09_capture_failure(tmp_path):
    mgr, pol_svc, pay_svc, mock_prov, _ = _setup_isolated_services(tmp_path)
    mandate = pol_svc.create_mandate(
        MandateCreateRequest(max_amount=Decimal("5000.00"), permitted_categories=["Electronics"])
    )
    eval_res = pol_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    pol_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)
    order_res = asyncio.run(pay_svc.create_payment_order(eval_res.proposal.id))

    # Configure provider to simulate capture error
    mock_prov.simulate_capture_error = True
    with pytest.raises(ValueError, match="Payment capture verification failed"):
        asyncio.run(pay_svc.capture_payment_order(order_res.order_id))

    # Mandate remains RESERVED (not consumed!)
    assert pol_svc.get_mandate(mandate.id).status == MandateStatus.RESERVED


# ---------------------------------------------------------------------------
# Scenario 10: Capture timeout and uncertain result
# ---------------------------------------------------------------------------
def test_scenario_10_capture_timeout_and_uncertain_result(tmp_path):
    mgr, pol_svc, pay_svc, mock_prov, _ = _setup_isolated_services(tmp_path)
    mandate = pol_svc.create_mandate(
        MandateCreateRequest(max_amount=Decimal("5000.00"), permitted_categories=["Electronics"])
    )
    eval_res = pol_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    pol_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)
    order_res = asyncio.run(pay_svc.create_payment_order(eval_res.proposal.id))

    # Simulate capture timeout
    mock_prov.simulate_capture_timeout = True
    with pytest.raises(ValueError, match="timeout"):
        asyncio.run(pay_svc.capture_payment_order(order_res.order_id))

    # Order enters CAPTURE_UNKNOWN, mandate reservation is preserved
    order_rec = pay_svc.repository.get_order(order_res.order_id)
    assert order_rec.status == PaymentStatus.CAPTURE_UNKNOWN
    assert pol_svc.get_mandate(mandate.id).status == MandateStatus.RESERVED


# ---------------------------------------------------------------------------
# Scenario 11: Pending PayPal capture
# ---------------------------------------------------------------------------
def test_scenario_11_pending_paypal_capture(tmp_path):
    mgr, pol_svc, pay_svc, _, clock = _setup_isolated_services(tmp_path)
    mandate = pol_svc.create_mandate(
        MandateCreateRequest(max_amount=Decimal("5000.00"), permitted_categories=["Electronics"])
    )
    eval_res = pol_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    pol_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)
    order_res = asyncio.run(pay_svc.create_payment_order(eval_res.proposal.id))

    # Ingest PAYMENT.CAPTURE.PENDING webhook event
    evt_payload = {
        "id": "WH-PENDING-001",
        "event_type": "PAYMENT.CAPTURE.PENDING",
        "resource": {"id": "CAP-PENDING-123", "order_id": order_res.order_id},
    }
    res = asyncio.run(pay_svc.process_webhook_event(_valid_headers(), json.dumps(evt_payload).encode()))
    assert res.status == "processed"

    # Order is CAPTURE_PENDING, mandate remains RESERVED
    assert pay_svc.repository.get_order(order_res.order_id).status == PaymentStatus.CAPTURE_PENDING
    assert pol_svc.get_mandate(mandate.id).status == MandateStatus.RESERVED


# ---------------------------------------------------------------------------
# Scenario 12: Verified completed capture
# ---------------------------------------------------------------------------
def test_scenario_12_verified_completed_capture(tmp_path):
    mgr, pol_svc, pay_svc, _, _ = _setup_isolated_services(tmp_path)
    mandate = pol_svc.create_mandate(
        MandateCreateRequest(max_amount=Decimal("5000.00"), permitted_categories=["Electronics"])
    )
    eval_res = pol_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    pol_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)
    order_res = asyncio.run(pay_svc.create_payment_order(eval_res.proposal.id))

    # Webhook PAYMENT.CAPTURE.COMPLETED with verified matching USD amount
    evt_payload = {
        "id": "WH-COMP-001",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "id": "CAP-VERIFIED-123",
            "status": "COMPLETED",
            "order_id": order_res.order_id,
            "amount": {"currency_code": "USD", "value": "35.99"},
        },
    }
    res = asyncio.run(pay_svc.process_webhook_event(_valid_headers(), json.dumps(evt_payload).encode()))
    assert res.status == "processed"
    assert pay_svc.repository.get_order(order_res.order_id).status == PaymentStatus.COMPLETED
    assert pol_svc.get_mandate(mandate.id).status == MandateStatus.CONSUMED
    assert pol_svc.get_proposal(eval_res.proposal.id).status == ProposalStatus.PAID


# ---------------------------------------------------------------------------
# Scenario 13: Invalid webhook signature
# ---------------------------------------------------------------------------
def test_scenario_13_invalid_webhook_signature(tmp_path):
    mgr, pol_svc, pay_svc, mock_prov, _ = _setup_isolated_services(tmp_path)
    mock_prov.simulate_invalid_signature = True

    evt_payload = {"id": "WH-INVALID-001", "event_type": "CHECKOUT.ORDER.APPROVED", "resource": {}}
    headers = _valid_headers(sig="bad_forged_sig")

    with pytest.raises(ValueError, match="Invalid PayPal webhook signature"):
        asyncio.run(pay_svc.process_webhook_event(headers, json.dumps(evt_payload).encode()))


# ---------------------------------------------------------------------------
# Scenario 14: Duplicate webhook event
# ---------------------------------------------------------------------------
def test_scenario_14_duplicate_webhook_event(tmp_path):
    mgr, pol_svc, pay_svc, _, _ = _setup_isolated_services(tmp_path)
    mandate = pol_svc.create_mandate(
        MandateCreateRequest(max_amount=Decimal("5000.00"), permitted_categories=["Electronics"])
    )
    eval_res = pol_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    pol_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)
    order_res = asyncio.run(pay_svc.create_payment_order(eval_res.proposal.id))

    evt_payload = {
        "id": "WH-DUP-001",
        "event_type": "CHECKOUT.ORDER.APPROVED",
        "resource": {"id": order_res.order_id, "status": "APPROVED"},
    }
    raw_body = json.dumps(evt_payload).encode()
    headers = _valid_headers()

    res1 = asyncio.run(pay_svc.process_webhook_event(headers, raw_body))
    assert res1.status == "processed"

    res2 = asyncio.run(pay_svc.process_webhook_event(headers, raw_body))
    assert res2.status == "duplicate"


# ---------------------------------------------------------------------------
# Scenario 15: Out-of-order webhook delivery
# ---------------------------------------------------------------------------
def test_scenario_15_out_of_order_webhook_delivery(tmp_path):
    mgr, pol_svc, pay_svc, _, _ = _setup_isolated_services(tmp_path)
    mandate = pol_svc.create_mandate(
        MandateCreateRequest(max_amount=Decimal("5000.00"), permitted_categories=["Electronics"])
    )
    eval_res = pol_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    pol_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)
    order_res = asyncio.run(pay_svc.create_payment_order(eval_res.proposal.id))

    # 1. Capture completed arrives first
    comp_payload = {
        "id": "WH-ORDER-001",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "id": "CAP-EARLY-123",
            "status": "COMPLETED",
            "order_id": order_res.order_id,
            "amount": {"currency_code": "USD", "value": "35.99"},
        },
    }
    res_comp = asyncio.run(pay_svc.process_webhook_event(_valid_headers(), json.dumps(comp_payload).encode()))
    assert res_comp.status == "processed"
    assert pay_svc.repository.get_order(order_res.order_id).status == PaymentStatus.COMPLETED

    # 2. Delayed CHECKOUT.ORDER.APPROVED arrives later: must NOT downgrade COMPLETED state
    late_payload = {
        "id": "WH-ORDER-002",
        "event_type": "CHECKOUT.ORDER.APPROVED",
        "resource": {"id": order_res.order_id, "status": "APPROVED"},
    }
    res_late = asyncio.run(pay_svc.process_webhook_event(_valid_headers(), json.dumps(late_payload).encode()))
    assert "completed" in res_late.message.lower()
    assert pay_svc.repository.get_order(order_res.order_id).status == PaymentStatus.COMPLETED


# ---------------------------------------------------------------------------
# Scenario 16: Browser refresh during checkout
# ---------------------------------------------------------------------------
def test_scenario_16_browser_refresh_during_checkout(tmp_path):
    """Verify that browser reloading during checkout safely retains reservation in SQLite."""
    mgr, pol_svc, pay_svc, _, _ = _setup_isolated_services(tmp_path)
    mandate = pol_svc.create_mandate(
        MandateCreateRequest(max_amount=Decimal("5000.00"), permitted_categories=["Electronics"])
    )
    eval_res = pol_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    pol_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)
    order_res = asyncio.run(pay_svc.create_payment_order(eval_res.proposal.id))

    # Simulate reload by querying new connection from fresh instance on same DB
    fresh_mgr = DatabaseManager(mgr.db_path_str)
    fresh_pol_svc = PolicyService(repository=PolicyRepository(db=fresh_mgr))
    fresh_pay_svc = PaymentService(policy=fresh_pol_svc, repository=PaymentRepository(db=fresh_mgr))

    reloaded_order = fresh_pay_svc.get_order_record(order_res.order_id)
    assert reloaded_order.status == PaymentStatus.CREATED
    assert fresh_pol_svc.get_mandate(mandate.id).status == MandateStatus.RESERVED


# ---------------------------------------------------------------------------
# Scenario 17: User cancellation & hardened uncertain capture protection
# ---------------------------------------------------------------------------
def test_scenario_17_user_cancellation_and_uncertain_protection(tmp_path):
    mgr, pol_svc, pay_svc, _, _ = _setup_isolated_services(tmp_path)
    mandate = pol_svc.create_mandate(
        MandateCreateRequest(max_amount=Decimal("5000.00"), permitted_categories=["Electronics"])
    )
    eval_res = pol_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    pol_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)
    order_res = asyncio.run(pay_svc.create_payment_order(eval_res.proposal.id))

    # A: Cancellation blocked when capture is CAPTURE_UNKNOWN
    uncertain_order = pay_svc.repository.get_order(order_res.order_id).model_copy(
        update={"status": PaymentStatus.CAPTURE_UNKNOWN}
    )
    pay_svc.repository.update_order(uncertain_order)
    with pytest.raises(ValueError, match="capture is uncertain"):
        pay_svc.cancel_payment_order(order_res.order_id)
    assert pol_svc.get_mandate(mandate.id).status == MandateStatus.RESERVED

    # B: Safe cancellation when order is in normal CREATED state releases mandate
    pay_svc.repository.update_order(uncertain_order.model_copy(update={"status": PaymentStatus.CREATED}))
    cancel_res = pay_svc.cancel_payment_order(order_res.order_id)
    assert cancel_res.status == PaymentStatus.CANCELLED
    assert pol_svc.get_mandate(mandate.id).status == MandateStatus.ACTIVE

    # C: Repeated cancellation is idempotent
    repeat_cancel = pay_svc.cancel_payment_order(order_res.order_id)
    assert repeat_cancel.status == PaymentStatus.CANCELLED


# ---------------------------------------------------------------------------
# Scenario 18: Provider unreachable during expiry reconciliation
# ---------------------------------------------------------------------------
def test_scenario_18_provider_unreachable_during_expiry_reconciliation(tmp_path):
    base_time = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    current_time = base_time

    def clock():
        return current_time

    mgr = DatabaseManager(tmp_path / "scenario_18.db")
    pol_svc = PolicyService(repository=PolicyRepository(db=mgr), clock=clock)
    mock_prov = MockPaymentProvider()
    pay_svc = PaymentService(policy=pol_svc, repository=PaymentRepository(db=mgr), provider=mock_prov, clock=clock)

    mandate = pol_svc.create_mandate(
        MandateCreateRequest(
            max_amount=Decimal("5000.00"),
            permitted_categories=["Electronics"],
            expires_in_seconds=3600,
        )
    )
    eval_res = pol_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    pol_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)
    order_res = asyncio.run(pay_svc.create_payment_order(eval_res.proposal.id))

    # Advance time past expiration
    current_time = base_time + timedelta(seconds=7200)

    # Provider network failure during reconciliation
    mock_prov.simulate_provider_down = True
    worker = ExpiryReconciliationWorker(pol_service=pol_svc, pay_service=pay_svc)
    stats = asyncio.run(worker.reconcile_once())

    # Reservation MUST be retained (not released!) because provider state could not be confirmed
    assert stats["retained"] == 1
    assert stats["released"] == 0
    assert pol_svc.get_mandate(mandate.id).status == MandateStatus.RESERVED
