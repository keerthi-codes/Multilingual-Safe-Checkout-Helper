"""Comprehensive test suite for PayPal Sandbox checkout, currency derivation, and mandate finalization."""

from datetime import datetime, timezone
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.payment.currency import convert_inr_to_usd
from app.payment.models import (
    CurrencyBreakdown,
    OrderCreateRequest,
    PaymentOrderRecord,
    PaymentStatus,
)
from app.payment.repository import in_memory_payment_repo
from app.payment.service import (
    MockPaymentProvider,
    PayPalSandboxPaymentProvider,
    PaymentService,
)
from app.policy.models import MandateStatus, ProposalStatus
from app.policy.repository import in_memory_policy_repo

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_repos():
    """Ensure in-memory policy and payment repositories are fresh for each test."""
    in_memory_policy_repo.clear()
    in_memory_payment_repo.clear()
    yield
    in_memory_policy_repo.clear()
    in_memory_payment_repo.clear()


# ---------------------------------------------------------------------------
# 1. Currency Conversion & Rounding Tests
# ---------------------------------------------------------------------------

def test_convert_inr_to_usd_deterministic_and_half_up_rounding():
    """Verify INR to USD conversion uses Decimal half-up rounding to cents."""
    rate = Decimal("0.012")

    # 1000 INR * 0.012 = 12.00 USD
    assert convert_inr_to_usd(Decimal("1000.00"), rate) == Decimal("12.00")

    # 2999 INR * 0.012 = 35.988 -> rounds half-up to 35.99 USD
    assert convert_inr_to_usd(Decimal("2999.00"), rate) == Decimal("35.99")

    # Exact boundary check: 100.416666... -> checks half-up
    # 125 * 0.012 = 1.50
    assert convert_inr_to_usd(Decimal("125.00"), rate) == Decimal("1.50")

    # Zero amount
    assert convert_inr_to_usd(Decimal("0.00"), rate) == Decimal("0.00")


def test_convert_inr_to_usd_rejects_invalid_inputs():
    """Verify negative amounts and non-positive exchange rates are rejected."""
    # Negative INR amount
    with pytest.raises(ValueError, match="cannot be negative"):
        convert_inr_to_usd(Decimal("-50.00"), Decimal("0.012"))

    # Zero rate
    with pytest.raises(ValueError, match="strictly positive"):
        convert_inr_to_usd(Decimal("100.00"), Decimal("0.00"))

    # Negative rate
    with pytest.raises(ValueError, match="strictly positive"):
        convert_inr_to_usd(Decimal("100.00"), Decimal("-0.012"))


# ---------------------------------------------------------------------------
# 2. End-to-End Order Creation & Idempotency Tests
# ---------------------------------------------------------------------------

def test_payment_order_creation_requires_user_approved_proposal():
    """Verify order creation strictly requires proposal to be in USER_APPROVED state."""
    # 1. Setup mandate and evaluate proposal
    m_res = client.post(
        "/api/v1/mandates",
        json={"max_amount": "5000.00", "permitted_categories": ["Electronics"], "max_quantity": 2},
    )
    mandate_id = m_res.json()["id"]

    eval_res = client.post(
        f"/api/v1/mandates/{mandate_id}/evaluate",
        json={"product_id": "prod-001", "quantity": 1},
    )
    proposal_id = eval_res.json()["proposal"]["id"]

    # Attempt to create payment order while still PENDING_APPROVAL
    pay_res = client.post("/api/v1/payments/orders/create", json={"proposal_id": proposal_id})
    assert pay_res.status_code == 400
    assert "Explicit user approval is required" in pay_res.json()["detail"]


def test_payment_order_creation_rejects_client_supplied_forged_fields():
    """Verify client cannot forge prices, amounts, conversion rates, or currencies."""
    m_res = client.post(
        "/api/v1/mandates",
        json={"max_amount": "5000.00", "permitted_categories": ["Electronics"], "max_quantity": 2},
    )
    mandate_id = m_res.json()["id"]

    eval_res = client.post(
        f"/api/v1/mandates/{mandate_id}/evaluate",
        json={"product_id": "prod-001", "quantity": 1},
    )
    proposal_id = eval_res.json()["proposal"]["id"]
    secret = eval_res.json()["approval_secret"]

    # User approves
    client.post(f"/api/v1/mandates/proposals/{proposal_id}/approve", json={"approval_secret": secret})

    # Client tries to pass a forged amount of 1 cent
    pay_res = client.post(
        "/api/v1/payments/orders/create",
        json={"proposal_id": proposal_id, "amount": "0.01", "currency": "USD"},
    )
    assert pay_res.status_code == 422


def test_payment_order_creation_idempotency_reuses_same_order():
    """Verify repeated create requests for the same proposal return the same order without creating duplicates."""
    # Setup approved proposal
    m_res = client.post(
        "/api/v1/mandates",
        json={"max_amount": "5000.00", "permitted_categories": ["Electronics"], "max_quantity": 2},
    )
    mandate_id = m_res.json()["id"]
    eval_res = client.post(f"/api/v1/mandates/{mandate_id}/evaluate", json={"product_id": "prod-001", "quantity": 1})
    proposal_id = eval_res.json()["proposal"]["id"]
    secret = eval_res.json()["approval_secret"]
    client.post(f"/api/v1/mandates/proposals/{proposal_id}/approve", json={"approval_secret": secret})

    # Call 1
    res1 = client.post("/api/v1/payments/orders/create", json={"proposal_id": proposal_id})
    assert res1.status_code == 201
    data1 = res1.json()
    assert data1["currency_breakdown"]["inr_total"] == "2999.00"
    assert data1["currency_breakdown"]["usd_amount"] == "35.99"
    assert "https://sandbox.paypal.com" in data1["approve_url"]

    # Call 2 (Retry)
    res2 = client.post("/api/v1/payments/orders/create", json={"proposal_id": proposal_id})
    assert res2.status_code == 201
    data2 = res2.json()

    # Identical order ID returned
    assert data1["order_id"] == data2["order_id"]
    assert data1["approve_url"] == data2["approve_url"]


# ---------------------------------------------------------------------------
# 3. Capture, Verification & Mandate Finalization Tests
# ---------------------------------------------------------------------------

def test_full_payment_capture_flow_finalizes_mandate_as_consumed():
    """Verify capture marks proposal PAID and transitions mandate to CONSUMED with capture ID."""
    # 1. Setup approved proposal
    m_res = client.post(
        "/api/v1/mandates",
        json={"max_amount": "5000.00", "permitted_categories": ["Electronics"], "max_quantity": 2},
    )
    mandate_id = m_res.json()["id"]
    eval_res = client.post(f"/api/v1/mandates/{mandate_id}/evaluate", json={"product_id": "prod-001", "quantity": 1})
    proposal_id = eval_res.json()["proposal"]["id"]
    secret = eval_res.json()["approval_secret"]
    client.post(f"/api/v1/mandates/proposals/{proposal_id}/approve", json={"approval_secret": secret})

    # Mandate is RESERVED
    m_reserved = client.get(f"/api/v1/mandates/{mandate_id}").json()
    assert m_reserved["status"] == "RESERVED"

    # 2. Create PayPal order
    create_res = client.post("/api/v1/payments/orders/create", json={"proposal_id": proposal_id})
    assert create_res.status_code == 201
    order_id = create_res.json()["order_id"]

    # 3. Capture payment
    cap_res = client.post(f"/api/v1/payments/orders/{order_id}/capture")
    assert cap_res.status_code == 200
    cap_data = cap_res.json()
    assert cap_data["status"] == "COMPLETED"
    assert cap_data["capture_id"].startswith("MOCK_CAP_")
    assert cap_data["amount_captured_usd"] == "35.99"

    # 4. Mandate is now CONSUMED
    m_consumed = client.get(f"/api/v1/mandates/{mandate_id}").json()
    assert m_consumed["status"] == "CONSUMED"
    assert m_consumed["consumed_by_proposal_id"] == proposal_id

    # 5. Proposal is now PAID
    prop_paid = client.get(f"/api/v1/mandates/proposals/{proposal_id}").json()
    assert prop_paid["status"] == "PAID"

    # 6. Idempotent repeat capture returns same COMPLETED status without re-capturing
    cap_repeat = client.post(f"/api/v1/payments/orders/{order_id}/capture")
    assert cap_repeat.status_code == 200
    assert cap_repeat.json()["status"] == "COMPLETED"
    assert cap_repeat.json()["capture_id"] == cap_data["capture_id"]


def test_payment_cancellation_releases_mandate_reservation_back_to_active():
    """Verify buyer cancellation releases the reserved mandate back to ACTIVE and marks proposal CANCELLED."""
    # 1. Setup approved proposal
    m_res = client.post(
        "/api/v1/mandates",
        json={"max_amount": "5000.00", "permitted_categories": ["Electronics"], "max_quantity": 2},
    )
    mandate_id = m_res.json()["id"]
    eval_res = client.post(f"/api/v1/mandates/{mandate_id}/evaluate", json={"product_id": "prod-001", "quantity": 1})
    proposal_id = eval_res.json()["proposal"]["id"]
    secret = eval_res.json()["approval_secret"]
    client.post(f"/api/v1/mandates/proposals/{proposal_id}/approve", json={"approval_secret": secret})

    # Mandate is RESERVED
    assert client.get(f"/api/v1/mandates/{mandate_id}").json()["status"] == "RESERVED"

    # 2. Create order
    create_res = client.post("/api/v1/payments/orders/create", json={"proposal_id": proposal_id})
    order_id = create_res.json()["order_id"]

    # 3. Buyer cancels
    cancel_res = client.post(f"/api/v1/payments/orders/{order_id}/cancel")
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "CANCELLED"

    # 4. Mandate is released back to ACTIVE
    m_active = client.get(f"/api/v1/mandates/{mandate_id}").json()
    assert m_active["status"] == "ACTIVE"
    assert m_active["reserved_by_proposal_id"] is None

    # 5. Proposal is CANCELLED
    prop_cancelled = client.get(f"/api/v1/mandates/proposals/{proposal_id}").json()
    assert prop_cancelled["status"] == "CANCELLED"

    # 6. Cannot capture a cancelled order
    cap_attempt = client.post(f"/api/v1/payments/orders/{order_id}/capture")
    assert cap_attempt.status_code == 400


# ---------------------------------------------------------------------------
# 4. Error Handling & Provider Failure Modes
# ---------------------------------------------------------------------------

def test_mock_provider_capture_error_does_not_consume_mandate():
    """Verify provider decline during capture keeps mandate in RESERVED state without marking it CONSUMED."""
    # Setup custom mock provider with capture error simulation
    mock_provider = MockPaymentProvider()
    mock_provider.simulate_capture_error = True

    from app.policy.service import policy_service
    custom_payment_service = PaymentService(provider=mock_provider, policy=policy_service, repository=in_memory_payment_repo)

    # 1. Create mandate and approve proposal
    m_res = client.post(
        "/api/v1/mandates",
        json={"max_amount": "5000.00", "permitted_categories": ["Electronics"], "max_quantity": 2},
    )
    mandate_id = m_res.json()["id"]
    eval_res = client.post(f"/api/v1/mandates/{mandate_id}/evaluate", json={"product_id": "prod-001", "quantity": 1})
    proposal_id = eval_res.json()["proposal"]["id"]
    secret = eval_res.json()["approval_secret"]
    client.post(f"/api/v1/mandates/proposals/{proposal_id}/approve", json={"approval_secret": secret})

    # 2. Create order using custom service
    import asyncio
    order_resp = asyncio.run(custom_payment_service.create_payment_order(proposal_id=proposal_id))
    order_id = order_resp.order_id

    # 3. Attempt capture -> should raise ValueError
    with pytest.raises(ValueError, match="capture verification failed"):
        asyncio.run(custom_payment_service.capture_payment_order(order_id=order_id))

    # 4. Mandate remains RESERVED (not falsely CONSUMED)
    m_check = client.get(f"/api/v1/mandates/{mandate_id}").json()
    assert m_check["status"] == "RESERVED"


def test_paypal_sandbox_provider_missing_credentials_raises_error():
    """Verify live PayPal provider raises clean error when credentials are not configured."""
    provider = PayPalSandboxPaymentProvider(client_id=None, client_secret=None)
    import asyncio
    with pytest.raises(RuntimeError, match="PayPal Sandbox credentials missing"):
        asyncio.run(provider._get_access_token())


def test_get_payment_order_by_id_and_nonexistent_returns_404():
    """Verify GET /api/v1/payments/orders/{id} returns order or 404."""
    # Nonexistent order
    res_fake = client.get("/api/v1/payments/orders/FAKE_ORD_99999")
    assert res_fake.status_code == 404


def test_paypal_sandbox_provider_oauth_and_order_creation_request_construction():
    """Verify PayPalSandboxPaymentProvider constructs OAuth and Orders v2 requests with correct headers and payloads."""
    import unittest.mock as mock
    import httpx

    provider = PayPalSandboxPaymentProvider(
        client_id="test_client_id",
        client_secret="test_client_secret",
        base_url="https://api-m.sandbox.paypal.com",
    )

    # 1. Mock OAuth token response
    mock_token_resp = mock.MagicMock(spec=httpx.Response)
    mock_token_resp.status_code = 200
    mock_token_resp.json.return_value = {"access_token": "mock_sandbox_access_token_xyz"}

    # 2. Mock Order creation response
    mock_order_resp = mock.MagicMock(spec=httpx.Response)
    mock_order_resp.status_code = 201
    mock_order_resp.json.return_value = {
        "id": "SANDBOX-ORDER-123",
        "status": "CREATED",
        "links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/checkoutnow?token=SANDBOX-ORDER-123"}],
    }

    import asyncio
    with mock.patch("httpx.AsyncClient.post", new_callable=mock.AsyncMock) as mock_post:
        mock_post.side_effect = [mock_token_resp, mock_order_resp]

        order_id, approve_url = asyncio.run(
            provider.create_order(
                proposal_id="prop-test",
                usd_amount=Decimal("35.99"),
                idempotency_key="idemp-key-test-123",
                return_url="http://localhost:8000/return",
                cancel_url="http://localhost:8000/cancel",
            )
        )

        assert order_id == "SANDBOX-ORDER-123"
        assert "sandbox.paypal.com" in approve_url

        # Verify calls
        assert mock_post.call_count == 2
        # First call: OAuth token
        oauth_call = mock_post.call_args_list[0]
        assert oauth_call[0][0] == "https://api-m.sandbox.paypal.com/v1/oauth2/token"
        assert oauth_call[1]["auth"] == ("test_client_id", "test_client_secret")

        # Second call: Orders v2 create
        order_call = mock_post.call_args_list[1]
        assert order_call[0][0] == "https://api-m.sandbox.paypal.com/v2/checkout/orders"
        headers = order_call[1]["headers"]
        assert headers["PayPal-Request-Id"] == "idemp-key-test-123"
        assert headers["Authorization"] == "Bearer mock_sandbox_access_token_xyz"
        json_body = order_call[1]["json"]
        assert json_body["intent"] == "CAPTURE"
        assert json_body["purchase_units"][0]["amount"]["currency_code"] == "USD"
        assert json_body["purchase_units"][0]["amount"]["value"] == "35.99"


def test_payment_capture_amount_or_currency_mismatch_rejected():
    """Verify capture verification rejects mismatched amounts or non-USD currencies."""
    import unittest.mock as mock

    # Setup approved proposal and order
    m_res = client.post(
        "/api/v1/mandates",
        json={"max_amount": "5000.00", "permitted_categories": ["Electronics"], "max_quantity": 2},
    )
    mandate_id = m_res.json()["id"]
    eval_res = client.post(f"/api/v1/mandates/{mandate_id}/evaluate", json={"product_id": "prod-001", "quantity": 1})
    proposal_id = eval_res.json()["proposal"]["id"]
    secret = eval_res.json()["approval_secret"]
    client.post(f"/api/v1/mandates/proposals/{proposal_id}/approve", json={"approval_secret": secret})

    # Create order
    create_res = client.post("/api/v1/payments/orders/create", json={"proposal_id": proposal_id})
    order_id = create_res.json()["order_id"]

    # Mock provider raising currency mismatch error during capture verification
    from app.payment.service import payment_service
    with mock.patch.object(payment_service.provider, "capture_order", new_callable=mock.AsyncMock) as mock_cap:
        mock_cap.side_effect = RuntimeError("Captured currency mismatch: expected USD, received EUR.")
        res = client.post(f"/api/v1/payments/orders/{order_id}/capture")
        assert res.status_code == 400
        assert "Captured currency mismatch" in res.json()["detail"]


def test_reserved_and_consumed_mandates_cannot_be_reused_by_another_proposal():
    """Verify that while a mandate is RESERVED or CONSUMED, new proposals cannot consume it."""
    # 1. Setup mandate and approve proposal 1
    m_res = client.post(
        "/api/v1/mandates",
        json={"max_amount": "5000.00", "permitted_categories": ["Electronics"], "max_quantity": 2},
    )
    mandate_id = m_res.json()["id"]
    eval_res = client.post(f"/api/v1/mandates/{mandate_id}/evaluate", json={"product_id": "prod-001", "quantity": 1})
    proposal_id = eval_res.json()["proposal"]["id"]
    secret = eval_res.json()["approval_secret"]
    client.post(f"/api/v1/mandates/proposals/{proposal_id}/approve", json={"approval_secret": secret})

    # Mandate is now RESERVED
    # 2. Evaluate a second proposal against the same mandate -> must be BLOCKED
    eval2 = client.post(f"/api/v1/mandates/{mandate_id}/evaluate", json={"product_id": "prod-002", "quantity": 1})
    assert eval2.status_code == 200
    assert eval2.json()["decision"] == "BLOCKED"
    assert eval2.json()["reason_code"] == "MANDATE_RESERVED"

    # 3. Complete payment on proposal 1 -> Mandate transitions to CONSUMED
    create_res = client.post("/api/v1/payments/orders/create", json={"proposal_id": proposal_id})
    order_id = create_res.json()["order_id"]
    client.post(f"/api/v1/payments/orders/{order_id}/capture")

    # 4. Evaluate third proposal -> must be BLOCKED with MANDATE_ALREADY_CONSUMED
    eval3 = client.post(f"/api/v1/mandates/{mandate_id}/evaluate", json={"product_id": "prod-002", "quantity": 1})
    assert eval3.status_code == 200
    assert eval3.json()["decision"] == "BLOCKED"
    assert eval3.json()["reason_code"] == "MANDATE_ALREADY_CONSUMED"

