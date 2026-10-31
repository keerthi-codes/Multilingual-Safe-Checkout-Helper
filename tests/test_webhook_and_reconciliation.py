"""Deterministic automated tests for PayPal webhook verification, durable event processing,
safe abandoned-checkout expiry worker, and external-operation recovery.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import httpx
import pytest

from app.core.config import settings
from app.core.database import DatabaseManager
from app.main import app
from app.payment.models import (
    PaymentOrderRecord,
    PaymentStatus,
    WebhookEventRecord,
    WebhookEventStatus,
)
from app.payment.repository import PaymentRepository
from app.payment.service import (
    MockPaymentProvider,
    PayPalSandboxPaymentProvider,
    PaymentService,
)
from app.payment.worker import ExpiryReconciliationWorker
from app.policy.models import (
    MandateCreateRequest,
    MandateStatus,
    ProposalStatus,
    SpendingMandate,
)
from app.policy.repository import PolicyRepository
from app.policy.service import PolicyService


def _valid_headers(sig: str = "valid_mock_signature_123") -> dict[str, str]:
    return {
        "paypal-transmission-id": "trans-test-12345",
        "paypal-transmission-time": "2026-10-31T12:00:00Z",
        "paypal-cert-url": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-123",
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-transmission-sig": sig,
    }


def _setup_service(tmp_path, simulated_clock=None):
    db_file = tmp_path / "webhook_test.db"
    mgr = DatabaseManager(db_file)
    pol_repo = PolicyRepository(db=mgr)
    pay_repo = PaymentRepository(db=mgr)
    clock = simulated_clock or (lambda: datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc))
    pol_svc = PolicyService(repository=pol_repo, clock=clock)
    mock_prov = MockPaymentProvider()
    pay_svc = PaymentService(
        policy=pol_svc, repository=pay_repo, provider=mock_prov, clock=clock
    )
    return mgr, pol_svc, pay_svc, mock_prov, clock


def _create_approved_order(pol_svc, pay_svc):
    mandate = pol_svc.create_mandate(
        MandateCreateRequest(
            max_amount=Decimal("5000.00"),
            permitted_categories=["Electronics", "Audio"],
        )
    )
    eval_res = pol_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    pol_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)
    order_res = asyncio.run(pay_svc.create_payment_order(eval_res.proposal.id))
    return mandate, eval_res.proposal, order_res


# ---------------------------------------------------------------------------
# Test 2: Valid webhook signature verification
# ---------------------------------------------------------------------------
def test_valid_webhook_signature_verification(tmp_path):
    mgr, pol_svc, pay_svc, mock_prov, clock = _setup_service(tmp_path)
    mandate, proposal, order_res = _create_approved_order(pol_svc, pay_svc)

    event_payload = {
        "id": "WH-EVT-001",
        "event_type": "CHECKOUT.ORDER.APPROVED",
        "resource": {"id": order_res.order_id, "status": "APPROVED"},
    }
    raw_body = json.dumps(event_payload).encode("utf-8")
    headers = _valid_headers()

    res = asyncio.run(pay_svc.process_webhook_event(headers, raw_body))
    assert res.status == "processed"
    assert res.event_id == "WH-EVT-001"

    # Also test PayPalSandboxPaymentProvider REST verification with mocked HTTP
    sandbox_prov = PayPalSandboxPaymentProvider(
        client_id="fake_id", client_secret="fake_secret", webhook_id="WH_ID_123"
    )

    async def mock_post(url, *args, **kwargs):
        if "oauth2/token" in str(url):
            return httpx.Response(200, json={"access_token": "token_123", "expires_in": 3600})
        if "verify-webhook-signature" in str(url):
            return httpx.Response(200, json={"verification_status": "SUCCESS"})
        return httpx.Response(404)

    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        valid = asyncio.run(sandbox_prov.verify_webhook_signature(headers, raw_body, "WH_ID_123"))
        assert valid is True


# ---------------------------------------------------------------------------
# Test 3: Invalid signature rejection
# ---------------------------------------------------------------------------
def test_invalid_signature_rejection(tmp_path):
    mgr, pol_svc, pay_svc, mock_prov, clock = _setup_service(tmp_path)
    mandate, proposal, order_res = _create_approved_order(pol_svc, pay_svc)

    event_payload = {
        "id": "WH-EVT-002",
        "event_type": "CHECKOUT.ORDER.APPROVED",
        "resource": {"id": order_res.order_id},
    }
    raw_body = json.dumps(event_payload).encode("utf-8")
    headers = _valid_headers(sig="INVALID_SIGNATURE")

    with pytest.raises(ValueError, match="Invalid PayPal webhook signature"):
        asyncio.run(pay_svc.process_webhook_event(headers, raw_body))

    # In sandbox provider with mocked failure
    sandbox_prov = PayPalSandboxPaymentProvider(
        client_id="fake_id", client_secret="fake_secret", webhook_id="WH_ID_123"
    )

    async def mock_post(url, *args, **kwargs):
        if "oauth2/token" in str(url):
            return httpx.Response(200, json={"access_token": "token_123", "expires_in": 3600})
        if "verify-webhook-signature" in str(url):
            return httpx.Response(200, json={"verification_status": "FAILURE"})
        return httpx.Response(404)

    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        valid = asyncio.run(sandbox_prov.verify_webhook_signature(_valid_headers(), raw_body, "WH_ID_123"))
        assert valid is False


# ---------------------------------------------------------------------------
# Test 4: Missing or incorrect webhook ID handling
# ---------------------------------------------------------------------------
def test_missing_or_incorrect_webhook_id_handling():
    sandbox_prov = PayPalSandboxPaymentProvider(
        client_id="fake_id", client_secret="fake_secret", webhook_id=None
    )
    with patch.object(settings, "PAYPAL_WEBHOOK_ID", None):
        with pytest.raises(ValueError, match="PAYPAL_WEBHOOK_ID is not configured"):
            asyncio.run(
                sandbox_prov.verify_webhook_signature(
                    _valid_headers(), b'{"id":"1"}', webhook_id=None
                )
            )


# ---------------------------------------------------------------------------
# Test 5: Malformed webhook rejection
# ---------------------------------------------------------------------------
def test_malformed_webhook_rejection(tmp_path):
    mgr, pol_svc, pay_svc, mock_prov, clock = _setup_service(tmp_path)
    headers = _valid_headers()

    # Case A: Non-JSON body
    with pytest.raises(ValueError, match="Malformed webhook JSON payload"):
        asyncio.run(pay_svc.process_webhook_event(headers, b"NOT_JSON_DATA"))

    # Case B: JSON missing required 'id'
    with pytest.raises(ValueError, match="missing 'id' or 'event_type'"):
        asyncio.run(pay_svc.process_webhook_event(headers, b'{"event_type":"TEST"}'))

    # Case C: Missing transmission headers
    with pytest.raises(ValueError, match="Missing required PayPal transmission header"):
        asyncio.run(pay_svc.process_webhook_event({}, b'{"id":"1","event_type":"TEST"}'))


# ---------------------------------------------------------------------------
# Test 6: Duplicate webhook event delivery
# ---------------------------------------------------------------------------
def test_duplicate_webhook_event_delivery(tmp_path):
    mgr, pol_svc, pay_svc, mock_prov, clock = _setup_service(tmp_path)
    mandate, proposal, order_res = _create_approved_order(pol_svc, pay_svc)

    event_payload = {
        "id": "WH-EVT-DUP-01",
        "event_type": "CHECKOUT.ORDER.APPROVED",
        "resource": {"id": order_res.order_id},
    }
    raw_body = json.dumps(event_payload).encode("utf-8")
    headers = _valid_headers()

    res1 = asyncio.run(pay_svc.process_webhook_event(headers, raw_body))
    assert res1.status == "processed"

    # Second delivery
    res2 = asyncio.run(pay_svc.process_webhook_event(headers, raw_body))
    assert res2.status == "duplicate"
    assert "already processed" in res2.message

    # Confirm only one event row exists
    evt = pay_svc.repository.get_webhook_event("WH-EVT-DUP-01")
    assert evt is not None
    assert evt.status == WebhookEventStatus.PROCESSED


# ---------------------------------------------------------------------------
# Test 7: Out-of-order webhook delivery
# ---------------------------------------------------------------------------
def test_out_of_order_webhook_delivery(tmp_path):
    mgr, pol_svc, pay_svc, mock_prov, clock = _setup_service(tmp_path)
    mandate, proposal, order_res = _create_approved_order(pol_svc, pay_svc)

    # First, order is already captured and completed
    asyncio.run(pay_svc.capture_payment_order(order_res.order_id))
    order = pay_svc.repository.get_order(order_res.order_id)
    assert order.status == PaymentStatus.COMPLETED

    # An older out-of-order event arrives: CHECKOUT.ORDER.APPROVED
    old_evt = {
        "id": "WH-EVT-OLD-01",
        "event_type": "CHECKOUT.ORDER.APPROVED",
        "resource": {"id": order_res.order_id},
    }
    res = asyncio.run(pay_svc.process_webhook_event(_valid_headers(), json.dumps(old_evt).encode("utf-8")))
    assert res.status == "processed"

    # Status MUST remain COMPLETED (no state downgrade!)
    order_after = pay_svc.repository.get_order(order_res.order_id)
    assert order_after.status == PaymentStatus.COMPLETED


# ---------------------------------------------------------------------------
# Test 8: Unknown event types
# ---------------------------------------------------------------------------
def test_unknown_event_types(tmp_path):
    mgr, pol_svc, pay_svc, mock_prov, clock = _setup_service(tmp_path)
    mandate, proposal, order_res = _create_approved_order(pol_svc, pay_svc)

    unknown_evt = {
        "id": "WH-EVT-UNK-01",
        "event_type": "CUSTOMER.DISPUTE.CREATED",
        "resource": {"id": "DISPUTE-123"},
    }
    res = asyncio.run(pay_svc.process_webhook_event(_valid_headers(), json.dumps(unknown_evt).encode("utf-8")))
    assert res.status == "ignored"

    # Stored as IGNORED
    stored = pay_svc.repository.get_webhook_event("WH-EVT-UNK-01")
    assert stored.status == WebhookEventStatus.IGNORED

    # Order and mandate states untouched
    order = pay_svc.repository.get_order(order_res.order_id)
    assert order.status == PaymentStatus.CREATED
    m = pol_svc.get_mandate(mandate.id)
    assert m.status == MandateStatus.RESERVED


# ---------------------------------------------------------------------------
# Test 9: Unrelated or mismatched order/capture association
# ---------------------------------------------------------------------------
def test_unrelated_or_mismatched_order_capture_association(tmp_path):
    mgr, pol_svc, pay_svc, mock_prov, clock = _setup_service(tmp_path)

    unrelated_evt = {
        "id": "WH-EVT-UNRELATED-01",
        "event_type": "CHECKOUT.ORDER.APPROVED",
        "resource": {"id": "NON_EXISTENT_ORDER_ID_999"},
    }
    with pytest.raises(ValueError, match="Unrelated order"):
        asyncio.run(pay_svc.process_webhook_event(_valid_headers(), json.dumps(unrelated_evt).encode("utf-8")))

    # Event stored as FAILED
    stored = pay_svc.repository.get_webhook_event("WH-EVT-UNRELATED-01")
    assert stored.status == WebhookEventStatus.FAILED


# ---------------------------------------------------------------------------
# Test 10: Currency and amount mismatch
# ---------------------------------------------------------------------------
def test_currency_and_amount_mismatch(tmp_path):
    mgr, pol_svc, pay_svc, mock_prov, clock = _setup_service(tmp_path)
    mandate, proposal, order_res = _create_approved_order(pol_svc, pay_svc)

    # Currency mismatch
    curr_mismatch_evt = {
        "id": "WH-EVT-CURR-ERR",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "id": "CAP-001",
            "status": "COMPLETED",
            "amount": {"value": str(order_res.currency_breakdown.usd_amount), "currency_code": "EUR"},
            "supplementary_data": {"related_ids": {"order_id": order_res.order_id}},
        },
    }
    with pytest.raises(ValueError, match="Captured currency mismatch"):
        asyncio.run(pay_svc.process_webhook_event(_valid_headers(), json.dumps(curr_mismatch_evt).encode("utf-8")))

    # Amount mismatch
    amt_mismatch_evt = {
        "id": "WH-EVT-AMT-ERR",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "id": "CAP-002",
            "status": "COMPLETED",
            "amount": {"value": "999.99", "currency_code": "USD"},
            "supplementary_data": {"related_ids": {"order_id": order_res.order_id}},
        },
    }
    with pytest.raises(ValueError, match="Captured amount mismatch"):
        asyncio.run(pay_svc.process_webhook_event(_valid_headers(), json.dumps(amt_mismatch_evt).encode("utf-8")))

    # Mandate is NOT consumed!
    m = pol_svc.get_mandate(mandate.id)
    assert m.status == MandateStatus.RESERVED


# ---------------------------------------------------------------------------
# Test 11: CHECKOUT.ORDER.APPROVED does not mark a payment PAID by itself
# ---------------------------------------------------------------------------
def test_checkout_order_approved_does_not_mark_paid(tmp_path):
    mgr, pol_svc, pay_svc, mock_prov, clock = _setup_service(tmp_path)
    mandate, proposal, order_res = _create_approved_order(pol_svc, pay_svc)

    evt = {
        "id": "WH-EVT-APPROVE-ONLY",
        "event_type": "CHECKOUT.ORDER.APPROVED",
        "resource": {"id": order_res.order_id},
    }
    res = asyncio.run(pay_svc.process_webhook_event(_valid_headers(), json.dumps(evt).encode("utf-8")))
    assert res.status == "processed"

    order = pay_svc.repository.get_order(order_res.order_id)
    assert order.status == PaymentStatus.BUYER_APPROVED

    # Critical: proposal remains USER_APPROVED, mandate remains RESERVED
    p = pol_svc.get_proposal(proposal.id)
    assert p.status == ProposalStatus.USER_APPROVED
    m = pol_svc.get_mandate(mandate.id)
    assert m.status == MandateStatus.RESERVED


# ---------------------------------------------------------------------------
# Test 12: PAYMENT.CAPTURE.PENDING retains the mandate reservation
# ---------------------------------------------------------------------------
def test_payment_capture_pending_retains_mandate_reservation(tmp_path):
    mgr, pol_svc, pay_svc, mock_prov, clock = _setup_service(tmp_path)
    mandate, proposal, order_res = _create_approved_order(pol_svc, pay_svc)

    evt = {
        "id": "WH-EVT-PENDING-01",
        "event_type": "PAYMENT.CAPTURE.PENDING",
        "resource": {
            "id": "CAP-PEND-123",
            "status": "PENDING",
            "supplementary_data": {"related_ids": {"order_id": order_res.order_id}},
        },
    }
    res = asyncio.run(pay_svc.process_webhook_event(_valid_headers(), json.dumps(evt).encode("utf-8")))
    assert res.status == "processed"

    order = pay_svc.repository.get_order(order_res.order_id)
    assert order.status == PaymentStatus.CAPTURE_PENDING

    m = pol_svc.get_mandate(mandate.id)
    assert m.status == MandateStatus.RESERVED


# ---------------------------------------------------------------------------
# Test 13: PAYMENT.CAPTURE.COMPLETED finalizes only after verification
# ---------------------------------------------------------------------------
def test_payment_capture_completed_finalizes_after_verification(tmp_path):
    mgr, pol_svc, pay_svc, mock_prov, clock = _setup_service(tmp_path)
    mandate, proposal, order_res = _create_approved_order(pol_svc, pay_svc)

    evt = {
        "id": "WH-EVT-CAP-DONE",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "id": "CAP-VERIFIED-777",
            "status": "COMPLETED",
            "amount": {
                "value": str(order_res.currency_breakdown.usd_amount),
                "currency_code": "USD",
            },
            "supplementary_data": {"related_ids": {"order_id": order_res.order_id}},
        },
    }
    res = asyncio.run(pay_svc.process_webhook_event(_valid_headers(), json.dumps(evt).encode("utf-8")))
    assert res.status == "processed"

    # Order COMPLETED
    order = pay_svc.repository.get_order(order_res.order_id)
    assert order.status == PaymentStatus.COMPLETED
    assert order.capture_id == "CAP-VERIFIED-777"

    # Proposal PAID
    p = pol_svc.get_proposal(proposal.id)
    assert p.status == ProposalStatus.PAID

    # Mandate CONSUMED
    m = pol_svc.get_mandate(mandate.id)
    assert m.status == MandateStatus.CONSUMED
    assert m.consumed_by_proposal_id == proposal.id


# ---------------------------------------------------------------------------
# Test 14: PAYMENT.CAPTURE.DENIED follows safe failure rules
# ---------------------------------------------------------------------------
def test_payment_capture_denied_follows_safe_failure_rules(tmp_path):
    mgr, pol_svc, pay_svc, mock_prov, clock = _setup_service(tmp_path)
    mandate, proposal, order_res = _create_approved_order(pol_svc, pay_svc)

    evt = {
        "id": "WH-EVT-DENIED-01",
        "event_type": "PAYMENT.CAPTURE.DENIED",
        "resource": {
            "id": "CAP-DENIED-999",
            "status": "DENIED",
            "supplementary_data": {"related_ids": {"order_id": order_res.order_id}},
        },
    }
    res = asyncio.run(pay_svc.process_webhook_event(_valid_headers(), json.dumps(evt).encode("utf-8")))
    assert res.status == "processed"

    order = pay_svc.repository.get_order(order_res.order_id)
    assert order.status == PaymentStatus.FAILED

    m = pol_svc.get_mandate(mandate.id)
    assert m.status == MandateStatus.ACTIVE
    assert m.reserved_by_proposal_id is None


# ---------------------------------------------------------------------------
# Test 15: Reversed approval does not incorrectly undo a completed capture
# ---------------------------------------------------------------------------
def test_reversed_approval_does_not_undo_completed_capture(tmp_path):
    mgr, pol_svc, pay_svc, mock_prov, clock = _setup_service(tmp_path)
    mandate, proposal, order_res = _create_approved_order(pol_svc, pay_svc)

    # First, complete the capture
    asyncio.run(pay_svc.capture_payment_order(order_res.order_id))

    # Next, approval reversed event arrives
    evt = {
        "id": "WH-EVT-REV-01",
        "event_type": "CHECKOUT.PAYMENT-APPROVAL.REVERSED",
        "resource": {"id": order_res.order_id},
    }
    res = asyncio.run(pay_svc.process_webhook_event(_valid_headers(), json.dumps(evt).encode("utf-8")))
    assert res.status == "processed"
    assert "Completed payment cannot be reversed" in res.message

    # Order must remain COMPLETED and mandate CONSUMED
    order = pay_svc.repository.get_order(order_res.order_id)
    assert order.status == PaymentStatus.COMPLETED
    m = pol_svc.get_mandate(mandate.id)
    assert m.status == MandateStatus.CONSUMED


# ---------------------------------------------------------------------------
# Test 16: Expired reservation with no order and no unresolved operation
# ---------------------------------------------------------------------------
def test_expired_reservation_with_no_order(tmp_path):
    base_time = datetime(2026, 10, 31, 10, 0, 0, tzinfo=timezone.utc)
    current_time = base_time

    def test_clock():
        return current_time

    mgr, pol_svc, pay_svc, mock_prov, _ = _setup_service(tmp_path, simulated_clock=test_clock)

    # Create mandate that expires in 10 minutes
    mandate = pol_svc.create_mandate(
        MandateCreateRequest(
            max_amount=Decimal("5000.00"),
            permitted_categories=["Electronics"],
            expires_at=base_time + timedelta(minutes=10),
        )
    )
    # Evaluate and approve proposal (reserves mandate) but do not create an order
    eval_res = pol_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    pol_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)
    m = pol_svc.get_mandate(mandate.id)
    assert m.status == MandateStatus.RESERVED

    # Advance clock past expiry
    current_time = base_time + timedelta(minutes=15)

    worker = ExpiryReconciliationWorker(pay_service=pay_svc, pol_service=pol_svc)
    stats = asyncio.run(worker.reconcile_once())
    assert stats["released"] == 1

    # Mandate is now EXPIRED
    m_after = pol_svc.get_mandate(mandate.id)
    assert m_after.status == MandateStatus.EXPIRED


# ---------------------------------------------------------------------------
# Test 17: Expired reservation with an active PayPal order
# ---------------------------------------------------------------------------
def test_expired_reservation_with_active_paypal_order(tmp_path):
    base_time = datetime(2026, 10, 31, 10, 0, 0, tzinfo=timezone.utc)
    current_time = base_time

    def test_clock():
        return current_time

    mgr, pol_svc, pay_svc, mock_prov, _ = _setup_service(tmp_path, simulated_clock=test_clock)

    mandate = pol_svc.create_mandate(
        MandateCreateRequest(
            max_amount=Decimal("5000.00"),
            permitted_categories=["Electronics"],
            expires_at=base_time + timedelta(minutes=10),
        )
    )
    eval_res = pol_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    pol_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)
    order_res = asyncio.run(pay_svc.create_payment_order(eval_res.proposal.id))

    # Advance clock past expiry
    current_time = base_time + timedelta(minutes=15)

    # Mock PayPal reports order is still active (APPROVED)
    worker = ExpiryReconciliationWorker(pay_service=pay_svc, pol_service=pol_svc)
    stats = asyncio.run(worker.reconcile_once())
    assert stats["retained"] == 1
    assert stats["released"] == 0

    # Critical reservation rule: mandate is NOT released!
    m = pol_svc.get_mandate(mandate.id)
    assert m.status == MandateStatus.RESERVED


# ---------------------------------------------------------------------------
# Test 18: Expired reservation with pending capture
# ---------------------------------------------------------------------------
def test_expired_reservation_with_pending_capture(tmp_path):
    base_time = datetime(2026, 10, 31, 10, 0, 0, tzinfo=timezone.utc)
    current_time = base_time

    def test_clock():
        return current_time

    mgr, pol_svc, pay_svc, mock_prov, _ = _setup_service(tmp_path, simulated_clock=test_clock)

    mandate = pol_svc.create_mandate(
        MandateCreateRequest(
            max_amount=Decimal("5000.00"),
            permitted_categories=["Electronics"],
            expires_at=base_time + timedelta(minutes=10),
        )
    )
    eval_res = pol_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    pol_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)
    order_res = asyncio.run(pay_svc.create_payment_order(eval_res.proposal.id))

    # Update order to CAPTURE_PENDING
    pay_svc.repository.update_order(
        pay_svc.repository.get_order(order_res.order_id).model_copy(
            update={"status": PaymentStatus.CAPTURE_PENDING}
        )
    )

    current_time = base_time + timedelta(minutes=15)
    worker = ExpiryReconciliationWorker(pay_service=pay_svc, pol_service=pol_svc)
    stats = asyncio.run(worker.reconcile_once())
    assert stats["retained"] == 1

    m = pol_svc.get_mandate(mandate.id)
    assert m.status == MandateStatus.RESERVED


# ---------------------------------------------------------------------------
# Test 19: Expired reservation when PayPal is unreachable
# ---------------------------------------------------------------------------
def test_expired_reservation_when_paypal_unreachable(tmp_path):
    base_time = datetime(2026, 10, 31, 10, 0, 0, tzinfo=timezone.utc)
    current_time = base_time

    def test_clock():
        return current_time

    mgr, pol_svc, pay_svc, mock_prov, _ = _setup_service(tmp_path, simulated_clock=test_clock)

    mandate = pol_svc.create_mandate(
        MandateCreateRequest(
            max_amount=Decimal("5000.00"),
            permitted_categories=["Electronics"],
            expires_at=base_time + timedelta(minutes=10),
        )
    )
    eval_res = pol_svc.evaluate_proposal(mandate.id, "prod-001", 1)
    pol_svc.approve_proposal(eval_res.proposal.id, eval_res.approval_secret)
    order_res = asyncio.run(pay_svc.create_payment_order(eval_res.proposal.id))

    # Advance clock
    current_time = base_time + timedelta(minutes=15)

    # Provider network failure
    mock_prov.simulate_provider_down = True

    worker = ExpiryReconciliationWorker(pay_service=pay_svc, pol_service=pol_svc)
    stats = asyncio.run(worker.reconcile_once())
    assert stats["retained"] == 1
    assert stats["released"] == 0

    # Mandate is still RESERVED
    m = pol_svc.get_mandate(mandate.id)
    assert m.status == MandateStatus.RESERVED


# ---------------------------------------------------------------------------
# Test 20: Duplicate capture prevention
# ---------------------------------------------------------------------------
def test_duplicate_capture_prevention(tmp_path):
    mgr, pol_svc, pay_svc, mock_prov, clock = _setup_service(tmp_path)
    mandate, proposal, order_res = _create_approved_order(pol_svc, pay_svc)

    cap1 = asyncio.run(pay_svc.capture_payment_order(order_res.order_id))
    assert cap1.status == PaymentStatus.COMPLETED

    cap2 = asyncio.run(pay_svc.capture_payment_order(order_res.order_id))
    assert cap2.status == PaymentStatus.COMPLETED
    assert "already captured" in cap2.message


# ---------------------------------------------------------------------------
# Test 21: Worker starts and shuts down cleanly
# ---------------------------------------------------------------------------
def test_worker_lifecycle_start_and_shutdown():
    worker = ExpiryReconciliationWorker(interval_seconds=10, enabled=True)
    assert worker._running is False

    asyncio.run(worker.start())
    assert worker._running is True
    assert worker._task is not None

    # Duplicate start does not spawn second task
    initial_task = worker._task
    asyncio.run(worker.start())
    assert worker._task is initial_task

    asyncio.run(worker.stop())
    assert worker._running is False
    assert worker._task is None


# ---------------------------------------------------------------------------
# Test 22: SQLite transactions roll back correctly after failures
# ---------------------------------------------------------------------------
def test_sqlite_transactions_rollback_after_failures(tmp_path):
    mgr = DatabaseManager(tmp_path / "rollback_test.db")
    with pytest.raises(RuntimeError):
        with mgr.transaction() as conn:
            conn.execute(
                "INSERT INTO spending_mandates (id, max_amount, permitted_categories, created_at, expires_at) "
                "VALUES ('m-fail', '100', '[\"Electronics\"]', '2026-10-31T00:00:00Z', '2026-10-31T01:00:00Z');"
            )
            raise RuntimeError("Simulated transaction crash!")

    with mgr.get_connection() as conn:
        row = conn.execute("SELECT * FROM spending_mandates WHERE id = 'm-fail';").fetchone()
        assert row is None


# ---------------------------------------------------------------------------
# Test 23: Mock mode makes zero external network calls
# ---------------------------------------------------------------------------
def test_mock_mode_makes_zero_external_network_calls(tmp_path):
    mgr, pol_svc, pay_svc, mock_prov, clock = _setup_service(tmp_path)
    mandate, proposal, order_res = _create_approved_order(pol_svc, pay_svc)

    # Disallow httpx network calls
    async def no_network(*args, **kwargs):
        raise AssertionError("Network call attempted while running in mock mode!")

    with patch("httpx.AsyncClient.post", side_effect=no_network), \
         patch("httpx.AsyncClient.get", side_effect=no_network):
        # 1. Capture in mock mode
        cap_res = asyncio.run(pay_svc.capture_payment_order(order_res.order_id))
        assert cap_res.status == PaymentStatus.COMPLETED

        # 2. Webhook verification in mock mode
        webhook_evt = {
            "id": "WH-ZERO-NET-01",
            "event_type": "CHECKOUT.ORDER.APPROVED",
            "resource": {"id": order_res.order_id},
        }
        wh_res = asyncio.run(
            pay_svc.process_webhook_event(
                _valid_headers(), json.dumps(webhook_evt).encode("utf-8")
            )
        )
        assert wh_res.status == "processed"

        # 3. Reconciliation in mock mode
        worker = ExpiryReconciliationWorker(pay_service=pay_svc, pol_service=pol_svc)
        asyncio.run(worker.reconcile_once())


# ---------------------------------------------------------------------------
# API Integration Test: Webhook endpoint via TestClient
# ---------------------------------------------------------------------------
def test_api_webhook_endpoint(tmp_path):
    client = TestClient(app)

    # Create an order via API
    m_resp = client.post(
        "/api/v1/mandates",
        json={"max_amount": "5000.00", "permitted_categories": ["Electronics"]},
    )
    assert m_resp.status_code == 201
    m_id = m_resp.json()["id"]

    eval_resp = client.post(
        f"/api/v1/mandates/{m_id}/evaluate",
        json={"product_id": "prod-001", "quantity": 1},
    )
    assert eval_resp.status_code == 200
    p_id = eval_resp.json()["proposal"]["id"]
    secret = eval_resp.json()["approval_secret"]

    appr_resp = client.post(
        f"/api/v1/mandates/proposals/{p_id}/approve",
        json={"approval_secret": secret},
    )
    assert appr_resp.status_code == 200

    order_resp = client.post(
        "/api/v1/payments/orders/create",
        json={"proposal_id": p_id},
    )
    assert order_resp.status_code == 201
    order_id = order_resp.json()["order_id"]

    # Post webhook event to /api/v1/payments/webhooks
    wh_payload = {
        "id": "WH-API-TEST-01",
        "event_type": "CHECKOUT.ORDER.APPROVED",
        "resource": {"id": order_id},
    }
    wh_resp = client.post(
        "/api/v1/payments/webhooks",
        headers=_valid_headers(),
        json=wh_payload,
    )
    assert wh_resp.status_code == 200
    data = wh_resp.json()
    assert data["status"] == "processed"
    assert data["event_id"] == "WH-API-TEST-01"

    # Post duplicate
    dup_resp = client.post(
        "/api/v1/payments/webhooks",
        headers=_valid_headers(),
        json=wh_payload,
    )
    assert dup_resp.status_code == 200
    assert dup_resp.json()["status"] == "duplicate"
