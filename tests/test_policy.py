"""Automated tests for deterministic spending mandates and policy engine."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.catalogue.models import Product
from app.catalogue.repository import CatalogueRepository
from app.catalogue.service import CatalogueService
from app.policy.models import (
    MandateCreateRequest,
    MandateStatus,
    PolicyDecision,
    PolicyReasonCode,
    ProposalStatus,
    SpendingMandate,
)
from app.policy.repository import PolicyRepository, in_memory_policy_repo
from app.policy.service import PolicyService

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_repositories():
    """Ensure in-memory repository is fresh for tests."""
    in_memory_policy_repo.clear()
    yield
    in_memory_policy_repo.clear()


def make_fixed_clock(start_time: datetime):
    """Factory creating an injectable deterministic clock."""
    current = [start_time]

    def clock() -> datetime:
        return current[0]

    def advance(delta: timedelta):
        current[0] += delta

    return clock, advance


# ---------------------------------------------------------------------------
# Model Validation Tests
# ---------------------------------------------------------------------------

def test_spending_mandate_model_validates_required_fields():
    """Verify SpendingMandate requires strictly positive Decimal amounts and UTC timestamps."""
    now = datetime.now(timezone.utc)
    future = now + timedelta(hours=1)

    mandate = SpendingMandate(
        id="mandate_100",
        max_amount=Decimal("5000.00"),
        currency="INR",
        permitted_categories=["Electronics", "Home"],
        max_quantity=3,
        created_at=now,
        expires_at=future,
        requires_user_approval=True,
    )
    assert mandate.id == "mandate_100"
    assert mandate.max_amount == Decimal("5000.00")
    assert mandate.requires_user_approval is True
    assert mandate.status == MandateStatus.ACTIVE


def test_spending_mandate_rejects_negative_or_zero_amounts():
    """Reject zero, negative, or binary float amounts."""
    now = datetime.now(timezone.utc)
    future = now + timedelta(hours=1)

    # Negative amount
    with pytest.raises(ValidationError):
        SpendingMandate(
            id="m_neg",
            max_amount=Decimal("-100.00"),
            permitted_categories=["Books"],
            max_quantity=1,
            created_at=now,
            expires_at=future,
        )

    # Zero amount
    with pytest.raises(ValidationError):
        SpendingMandate(
            id="m_zero",
            max_amount=Decimal("0.00"),
            permitted_categories=["Books"],
            max_quantity=1,
            created_at=now,
            expires_at=future,
        )

    # Binary float amount
    with pytest.raises(ValidationError):
        SpendingMandate(
            id="m_float",
            max_amount=49.99,  # type: ignore
            permitted_categories=["Books"],
            max_quantity=1,
            created_at=now,
            expires_at=future,
        )


def test_spending_mandate_rejects_empty_or_duplicate_categories():
    """Mandate must have at least one category and no duplicates."""
    now = datetime.now(timezone.utc)
    future = now + timedelta(hours=1)

    # Empty categories
    with pytest.raises(ValidationError):
        SpendingMandate(
            id="m_empty_cat",
            max_amount=Decimal("500.00"),
            permitted_categories=[],
            max_quantity=1,
            created_at=now,
            expires_at=future,
        )

    # Duplicate categories
    with pytest.raises(ValidationError):
        SpendingMandate(
            id="m_dup_cat",
            max_amount=Decimal("500.00"),
            permitted_categories=["Electronics", "electronics"],
            max_quantity=1,
            created_at=now,
            expires_at=future,
        )


def test_spending_mandate_rejects_past_or_equal_expiry():
    """Expiry must be strictly after creation time."""
    now = datetime.now(timezone.utc)

    # Expiry equal to created_at
    with pytest.raises(ValidationError):
        SpendingMandate(
            id="m_equal",
            max_amount=Decimal("500.00"),
            permitted_categories=["Electronics"],
            max_quantity=1,
            created_at=now,
            expires_at=now,
        )

    # Expiry before created_at
    with pytest.raises(ValidationError):
        SpendingMandate(
            id="m_past",
            max_amount=Decimal("500.00"),
            permitted_categories=["Electronics"],
            max_quantity=1,
            created_at=now,
            expires_at=now - timedelta(minutes=10),
        )


def test_spending_mandate_enforces_user_approval_requirement():
    """Client cannot disable user approval on spending mandates."""
    now = datetime.now(timezone.utc)
    future = now + timedelta(hours=1)

    with pytest.raises(ValidationError):
        SpendingMandate(
            id="m_no_apprv",
            max_amount=Decimal("500.00"),
            permitted_categories=["Electronics"],
            max_quantity=1,
            created_at=now,
            expires_at=future,
            requires_user_approval=False,  # type: ignore
        )


def test_spending_mandate_rejects_non_inr_currency():
    """Spending mandates currently only support INR."""
    now = datetime.now(timezone.utc)
    future = now + timedelta(hours=1)

    with pytest.raises(ValidationError):
        SpendingMandate(
            id="m_usd",
            max_amount=Decimal("500.00"),
            currency="USD",
            permitted_categories=["Electronics"],
            max_quantity=1,
            created_at=now,
            expires_at=future,
        )


# ---------------------------------------------------------------------------
# Policy Evaluation Unit Tests (Injectable Clock & Catalogue)
# ---------------------------------------------------------------------------

def test_pure_policy_evaluation_allows_valid_purchase():
    """Valid product purchase under budget returns ALLOW_PENDING_APPROVAL."""
    fixed_now = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
    clock, _ = make_fixed_clock(fixed_now)

    repo = PolicyRepository()
    service = PolicyService(repository=repo, clock=clock)

    mandate = service.create_mandate(
        MandateCreateRequest(
            max_amount=Decimal("4000.00"),
            permitted_categories=["Electronics"],
            max_quantity=2,
            expires_in_seconds=3600,
        )
    )

    # prod-001: Wireless Headphones (INR 2999.00, Electronics, Available)
    result = service.evaluate_proposal(
        mandate_id=mandate.id,
        product_id="prod-001",
        quantity=1,
    )

    assert result.decision == PolicyDecision.ALLOW_PENDING_APPROVAL
    assert result.reason_code == PolicyReasonCode.POLICY_PASSED_PENDING_USER_APPROVAL
    assert result.total_amount == Decimal("2999.00")
    assert result.currency == "INR"
    assert result.proposal is not None
    assert result.proposal.status == ProposalStatus.PENDING_APPROVAL
    assert result.proposal.is_user_approved is False
    assert result.proposal.product_name == "Noise-Cancelling Wireless Headphones"
    assert result.proposal.product_category == "Electronics"

    # Evaluation alone must NOT consume the mandate
    refreshed_mandate = service.get_mandate(mandate.id)
    assert refreshed_mandate.status == MandateStatus.ACTIVE


def test_policy_blocks_amount_exceeding_limit():
    """Total exceeding mandate maximum budget is deterministically BLOCKED."""
    fixed_now = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
    clock, _ = make_fixed_clock(fixed_now)

    repo = PolicyRepository()
    service = PolicyService(repository=repo, clock=clock)

    # Mandate max_amount is 3000.00
    mandate = service.create_mandate(
        MandateCreateRequest(
            max_amount=Decimal("3000.00"),
            permitted_categories=["Electronics"],
            max_quantity=2,
            expires_in_seconds=3600,
        )
    )

    # prod-002: Gaming Keyboard (3499.00 > 3000.00)
    result = service.evaluate_proposal(
        mandate_id=mandate.id,
        product_id="prod-002",
        quantity=1,
    )

    assert result.decision == PolicyDecision.BLOCKED
    assert result.reason_code == PolicyReasonCode.AMOUNT_EXCEEDS_LIMIT
    assert result.proposal is None
    assert result.total_amount == Decimal("3499.00")


def test_policy_blocks_quantity_exceeding_limit():
    """Quantity exceeding mandate limit is BLOCKED."""
    fixed_now = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
    clock, _ = make_fixed_clock(fixed_now)

    repo = PolicyRepository()
    service = PolicyService(repository=repo, clock=clock)

    mandate = service.create_mandate(
        MandateCreateRequest(
            max_amount=Decimal("10000.00"),
            permitted_categories=["Electronics"],
            max_quantity=1,  # Max 1 item
            expires_in_seconds=3600,
        )
    )

    result = service.evaluate_proposal(
        mandate_id=mandate.id,
        product_id="prod-001",
        quantity=2,  # Requests 2
    )

    assert result.decision == PolicyDecision.BLOCKED
    assert result.reason_code == PolicyReasonCode.QUANTITY_EXCEEDS_LIMIT
    assert result.proposal is None


def test_policy_blocks_disallowed_category():
    """Product whose category is not permitted by mandate is BLOCKED."""
    fixed_now = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
    clock, _ = make_fixed_clock(fixed_now)

    repo = PolicyRepository()
    service = PolicyService(repository=repo, clock=clock)

    # Mandate only allows "Books"
    mandate = service.create_mandate(
        MandateCreateRequest(
            max_amount=Decimal("5000.00"),
            permitted_categories=["Books"],
            max_quantity=2,
            expires_in_seconds=3600,
        )
    )

    # prod-001 is "Electronics"
    result = service.evaluate_proposal(
        mandate_id=mandate.id,
        product_id="prod-001",
        quantity=1,
    )

    assert result.decision == PolicyDecision.BLOCKED
    assert result.reason_code == PolicyReasonCode.CATEGORY_DISALLOWED


def test_policy_blocks_unavailable_product():
    """Product marked available=False in catalogue is BLOCKED."""
    fixed_now = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
    clock, _ = make_fixed_clock(fixed_now)

    repo = PolicyRepository()
    service = PolicyService(repository=repo, clock=clock)

    mandate = service.create_mandate(
        MandateCreateRequest(
            max_amount=Decimal("5000.00"),
            permitted_categories=["Electronics"],
            max_quantity=2,
            expires_in_seconds=3600,
        )
    )

    # prod-003 is out of stock (available=False)
    result = service.evaluate_proposal(
        mandate_id=mandate.id,
        product_id="prod-003",
        quantity=1,
    )

    assert result.decision == PolicyDecision.BLOCKED
    assert result.reason_code == PolicyReasonCode.PRODUCT_UNAVAILABLE


def test_policy_blocks_nonexistent_product():
    """Unknown product ID is BLOCKED."""
    fixed_now = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
    clock, _ = make_fixed_clock(fixed_now)

    repo = PolicyRepository()
    service = PolicyService(repository=repo, clock=clock)

    mandate = service.create_mandate(
        MandateCreateRequest(
            max_amount=Decimal("5000.00"),
            permitted_categories=["Electronics"],
            max_quantity=2,
            expires_in_seconds=3600,
        )
    )

    result = service.evaluate_proposal(
        mandate_id=mandate.id,
        product_id="nonexistent-prod-999",
        quantity=1,
    )

    assert result.decision == PolicyDecision.BLOCKED
    assert result.reason_code == PolicyReasonCode.PRODUCT_NOT_FOUND


def test_policy_blocks_nonexistent_mandate():
    """Unknown mandate ID is BLOCKED."""
    fixed_now = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
    clock, _ = make_fixed_clock(fixed_now)

    repo = PolicyRepository()
    service = PolicyService(repository=repo, clock=clock)

    result = service.evaluate_proposal(
        mandate_id="mandate_unknown_404",
        product_id="prod-001",
        quantity=1,
    )

    assert result.decision == PolicyDecision.BLOCKED
    assert result.reason_code == PolicyReasonCode.MANDATE_NOT_FOUND


def test_policy_blocks_expired_mandate_deterministically():
    """Expired mandate is deterministically BLOCKED using injectable clock."""
    fixed_now = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
    clock, advance = make_fixed_clock(fixed_now)

    repo = PolicyRepository()
    service = PolicyService(repository=repo, clock=clock)

    # Mandate valid for 60 seconds
    mandate = service.create_mandate(
        MandateCreateRequest(
            max_amount=Decimal("5000.00"),
            permitted_categories=["Electronics"],
            max_quantity=2,
            expires_in_seconds=60,
        )
    )

    # Advance clock by 61 seconds
    advance(timedelta(seconds=61))

    result = service.evaluate_proposal(
        mandate_id=mandate.id,
        product_id="prod-001",
        quantity=1,
    )

    assert result.decision == PolicyDecision.BLOCKED
    assert result.reason_code == PolicyReasonCode.MANDATE_EXPIRED


def test_policy_blocks_consumed_mandate_replay_protection():
    """Once a mandate is consumed by an approved proposal, further evaluations are BLOCKED."""
    fixed_now = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
    clock, _ = make_fixed_clock(fixed_now)

    repo = PolicyRepository()
    service = PolicyService(repository=repo, clock=clock)

    mandate = service.create_mandate(
        MandateCreateRequest(
            max_amount=Decimal("5000.00"),
            permitted_categories=["Electronics"],
            max_quantity=2,
            expires_in_seconds=3600,
        )
    )

    # 1. Evaluate proposal (succeeds)
    eval_res = service.evaluate_proposal(
        mandate_id=mandate.id,
        product_id="prod-001",
        quantity=1,
    )
    assert eval_res.decision == PolicyDecision.ALLOW_PENDING_APPROVAL
    proposal_id = eval_res.proposal.id
    assert eval_res.approval_secret is not None

    # 2. User explicitly approves the proposal with valid secret
    approved = service.approve_proposal(proposal_id, approval_secret=eval_res.approval_secret)
    assert approved.status == ProposalStatus.USER_APPROVED
    assert approved.is_user_approved is True

    # 3. Mandate is now RESERVED (held for payment)
    updated_mandate = service.get_mandate(mandate.id)
    assert updated_mandate.status == MandateStatus.RESERVED
    assert updated_mandate.reserved_by_proposal_id == proposal_id

    # 4. Attempting to evaluate another proposal on reserved mandate is BLOCKED
    eval_res_2 = service.evaluate_proposal(
        mandate_id=mandate.id,
        product_id="prod-001",
        quantity=1,
    )
    assert eval_res_2.decision == PolicyDecision.BLOCKED
    assert eval_res_2.reason_code == PolicyReasonCode.MANDATE_RESERVED

    # 5. Finalize payment capture: mandate transitions to CONSUMED
    service.finalize_mandate_consumed(mandate.id, proposal_id)
    assert service.get_mandate(mandate.id).status == MandateStatus.CONSUMED

    # 6. Evaluating on consumed mandate is BLOCKED with MANDATE_ALREADY_CONSUMED
    eval_res_3 = service.evaluate_proposal(
        mandate_id=mandate.id,
        product_id="prod-001",
        quantity=1,
    )
    assert eval_res_3.decision == PolicyDecision.BLOCKED
    assert eval_res_3.reason_code == PolicyReasonCode.MANDATE_ALREADY_CONSUMED


def test_policy_blocks_revoked_mandate():
    """Revoked mandate cannot be evaluated or approved."""
    fixed_now = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
    clock, _ = make_fixed_clock(fixed_now)

    repo = PolicyRepository()
    service = PolicyService(repository=repo, clock=clock)

    mandate = service.create_mandate(
        MandateCreateRequest(
            max_amount=Decimal("5000.00"),
            permitted_categories=["Electronics"],
            max_quantity=2,
            expires_in_seconds=3600,
        )
    )

    # Revoke mandate
    revoked = service.revoke_mandate(mandate.id)
    assert revoked.status == MandateStatus.REVOKED

    # Evaluation is BLOCKED
    result = service.evaluate_proposal(
        mandate_id=mandate.id,
        product_id="prod-001",
        quantity=1,
    )
    assert result.decision == PolicyDecision.BLOCKED
    assert result.reason_code == PolicyReasonCode.MANDATE_REVOKED


def test_policy_blocks_currency_mismatch():
    """Product with different currency than mandate is BLOCKED."""
    fixed_now = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
    clock, _ = make_fixed_clock(fixed_now)

    # Mock catalogue repository with a USD product
    cat_repo = CatalogueRepository(seed=False)
    # Add a product with USD currency (bypassing model validator for test purpose)
    usd_product = Product.model_construct(
        id="prod-usd-test",
        name="Imported Tech",
        description="US item",
        category="Electronics",
        price=Decimal("10.00"),
        currency="USD",
        available=True,
        supported_languages=["en"],
    )
    cat_repo._products[usd_product.id] = usd_product
    cat_service = CatalogueService(repository=cat_repo)

    policy_repo = PolicyRepository()
    service = PolicyService(catalogue=cat_service, repository=policy_repo, clock=clock)

    mandate = service.create_mandate(
        MandateCreateRequest(
            max_amount=Decimal("5000.00"),
            permitted_categories=["Electronics"],
            max_quantity=2,
            expires_in_seconds=3600,
        )
    )

    result = service.evaluate_proposal(
        mandate_id=mandate.id,
        product_id="prod-usd-test",
        quantity=1,
    )
    assert result.decision == PolicyDecision.BLOCKED
    assert result.reason_code == PolicyReasonCode.CURRENCY_MISMATCH


# ---------------------------------------------------------------------------
# API Integration Tests (FastAPI TestClient)
# ---------------------------------------------------------------------------

def test_api_create_and_get_mandate():
    """Test POST /api/v1/mandates and GET /api/v1/mandates/{id}."""
    payload = {
        "max_amount": "5000.00",
        "currency": "INR",
        "permitted_categories": ["Electronics", "Home"],
        "max_quantity": 2,
        "expires_in_seconds": 1800,
    }
    response = client.post("/api/v1/mandates", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["max_amount"] == "5000.00"
    assert data["currency"] == "INR"
    assert data["requires_user_approval"] is True
    assert data["status"] == "ACTIVE"

    mandate_id = data["id"]
    get_res = client.get(f"/api/v1/mandates/{mandate_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == mandate_id


def test_api_create_mandate_rejects_disabling_approval():
    """Client attempts to bypass user approval by sending requires_user_approval=false."""
    payload = {
        "max_amount": "5000.00",
        "currency": "INR",
        "permitted_categories": ["Electronics"],
        "max_quantity": 1,
        "requires_user_approval": False,
    }
    response = client.post("/api/v1/mandates", json=payload)
    assert response.status_code == 422


def test_api_evaluation_endpoint():
    """Test POST /api/v1/mandates/{id}/evaluate and verify calculation."""
    # Create mandate
    m_res = client.post(
        "/api/v1/mandates",
        json={
            "max_amount": "6000.00",
            "permitted_categories": ["Electronics"],
            "max_quantity": 3,
        },
    )
    mandate_id = m_res.json()["id"]

    # Evaluate proposal: 2 headphones @ 2999.00 = 5998.00 (under 6000.00 limit)
    eval_res = client.post(
        f"/api/v1/mandates/{mandate_id}/evaluate",
        json={"product_id": "prod-001", "quantity": 2},
    )
    assert eval_res.status_code == 200
    res_data = eval_res.json()
    assert res_data["decision"] == "ALLOW_PENDING_APPROVAL"
    assert res_data["reason_code"] == "POLICY_PASSED_PENDING_USER_APPROVAL"
    assert res_data["total_amount"] == "5998.00"
    assert res_data["proposal"]["total_amount"] == "5998.00"
    assert res_data["proposal"]["is_user_approved"] is False


def test_api_rejects_client_forged_fields():
    """Client attempts to forge unit_price or total_amount in evaluation request."""
    m_res = client.post(
        "/api/v1/mandates",
        json={
            "max_amount": "100.00",  # Low budget
            "permitted_categories": ["Electronics"],
            "max_quantity": 1,
        },
    )
    mandate_id = m_res.json()["id"]

    # Attacker passes forged unit_price and total to try to fool backend
    evil_payload = {
        "product_id": "prod-001",
        "quantity": 1,
        "unit_price": "10.00",
        "total_amount": "10.00",
        "product_category": "Electronics",
    }
    eval_res = client.post(
        f"/api/v1/mandates/{mandate_id}/evaluate",
        json=evil_payload,
    )
    # Pydantic extra='forbid' strictly rejects extra forged fields
    assert eval_res.status_code == 422


def test_api_explicit_user_approval_workflow():
    """Test evaluation followed by explicit user approval and verified mandate consumption."""
    # 1. Create mandate
    m_res = client.post(
        "/api/v1/mandates",
        json={
            "max_amount": "4000.00",
            "permitted_categories": ["Electronics"],
            "max_quantity": 1,
        },
    )
    mandate_id = m_res.json()["id"]

    # 2. Evaluate proposal
    eval_res = client.post(
        f"/api/v1/mandates/{mandate_id}/evaluate",
        json={"product_id": "prod-001", "quantity": 1},
    )
    eval_data = eval_res.json()
    proposal_id = eval_data["proposal"]["id"]
    approval_secret = eval_data["approval_secret"]
    assert approval_secret is not None
    assert approval_secret.startswith("sec_")

    # 3. Retrieve proposal before approval
    prop_res = client.get(f"/api/v1/mandates/proposals/{proposal_id}")
    assert prop_res.status_code == 200
    assert prop_res.json()["status"] == "PENDING_APPROVAL"
    assert prop_res.json()["is_user_approved"] is False

    # 4. Explicit user approval with secret
    apprv_res = client.post(
        f"/api/v1/mandates/proposals/{proposal_id}/approve",
        json={"approval_secret": approval_secret},
    )
    assert apprv_res.status_code == 200
    apprv_data = apprv_res.json()
    assert apprv_data["status"] == "USER_APPROVED"
    assert apprv_data["is_user_approved"] is True
    assert apprv_data["user_approved_at"] is not None

    # 5. Verify mandate is now RESERVED (held for payment)
    mandate_after = client.get(f"/api/v1/mandates/{mandate_id}").json()
    assert mandate_after["status"] == "RESERVED"
    assert mandate_after["reserved_by_proposal_id"] == proposal_id

    # 6. Attempting to approve again fails (one-time approval)
    repeat_res = client.post(
        f"/api/v1/mandates/proposals/{proposal_id}/approve",
        json={"approval_secret": approval_secret},
    )
    assert repeat_res.status_code == 400

    # 7. Complete payment flow: create order and capture payment
    create_pay_res = client.post(
        "/api/v1/payments/orders/create",
        json={"proposal_id": proposal_id},
    )
    assert create_pay_res.status_code == 201
    order_id = create_pay_res.json()["order_id"]

    cap_res = client.post(f"/api/v1/payments/orders/{order_id}/capture")
    assert cap_res.status_code == 200
    assert cap_res.json()["status"] == "COMPLETED"

    # 8. Mandate is now CONSUMED and proposal is PAID
    mandate_final = client.get(f"/api/v1/mandates/{mandate_id}").json()
    assert mandate_final["status"] == "CONSUMED"
    assert mandate_final["consumed_by_proposal_id"] == proposal_id

    prop_final = client.get(f"/api/v1/mandates/proposals/{proposal_id}").json()
    assert prop_final["status"] == "PAID"


def test_api_approval_rejected_without_or_with_invalid_secret():
    """Verify approval endpoint strictly rejects missing, invalid, or forged secrets."""
    # 1. Create mandate and evaluate proposal
    m_res = client.post(
        "/api/v1/mandates",
        json={
            "max_amount": "4000.00",
            "permitted_categories": ["Electronics"],
            "max_quantity": 1,
        },
    )
    mandate_id = m_res.json()["id"]
    eval_res = client.post(
        f"/api/v1/mandates/{mandate_id}/evaluate",
        json={"product_id": "prod-001", "quantity": 1},
    )
    proposal_id = eval_res.json()["proposal"]["id"]
    valid_secret = eval_res.json()["approval_secret"]

    # Rejection: Missing secret body
    res_no_body = client.post(f"/api/v1/mandates/proposals/{proposal_id}/approve")
    assert res_no_body.status_code == 422

    # Rejection: Incorrect secret
    res_wrong = client.post(
        f"/api/v1/mandates/proposals/{proposal_id}/approve",
        json={"approval_secret": "sec_this_is_wrong_secret_12345"},
    )
    assert res_wrong.status_code == 403
    assert "Invalid approval secret" in res_wrong.json()["detail"]

    # Rejection: Secret from another proposal cannot approve this one
    eval_res_2 = client.post(
        f"/api/v1/mandates/{mandate_id}/evaluate",
        json={"product_id": "prod-002", "quantity": 1},
    )
    other_proposal_id = eval_res_2.json()["proposal"]["id"]
    other_secret = eval_res_2.json()["approval_secret"]

    res_cross = client.post(
        f"/api/v1/mandates/proposals/{proposal_id}/approve",
        json={"approval_secret": other_secret},
    )
    assert res_cross.status_code == 403

    # Verification: Stored proposal never exposes approval_secret_hash to client
    stored_prop = client.get(f"/api/v1/mandates/proposals/{proposal_id}").json()
    assert "approval_secret_hash" not in stored_prop
    assert "approval_secret" not in stored_prop

    # Valid secret succeeds
    success = client.post(
        f"/api/v1/mandates/proposals/{proposal_id}/approve",
        json={"approval_secret": valid_secret},
    )
    assert success.status_code == 200
    assert success.json()["status"] == "USER_APPROVED"


def test_approval_secret_hash_never_leaks_in_repr_or_serialization():
    """Verify approval_secret_hash is excluded from dict/json dump and repr string representation."""
    # Create mandate and evaluate proposal
    m_res = client.post(
        "/api/v1/mandates",
        json={
            "max_amount": "5000.00",
            "permitted_categories": ["Electronics"],
            "max_quantity": 2,
        },
    )
    mandate_id = m_res.json()["id"]
    eval_res = client.post(
        f"/api/v1/mandates/{mandate_id}/evaluate",
        json={"product_id": "prod-001", "quantity": 1},
    )
    proposal_id = eval_res.json()["proposal"]["id"]
    stored_proposal = in_memory_policy_repo.get_proposal(proposal_id)
    assert stored_proposal is not None
    assert stored_proposal.approval_secret_hash is not None

    # Secret hash is excluded from model_dump()
    dumped = stored_proposal.model_dump()
    assert "approval_secret_hash" not in dumped

    # Secret hash is excluded from repr() and str() to prevent log leaks
    proposal_repr = repr(stored_proposal)
    assert stored_proposal.approval_secret_hash not in proposal_repr
    assert "approval_secret_hash" not in proposal_repr

    proposal_str = str(stored_proposal)
    assert stored_proposal.approval_secret_hash not in proposal_str


def test_approve_nonexistent_or_blocked_proposal_rejected():
    """Verify non-existent proposals and blocked proposals cannot be approved."""
    # 1. Non-existent proposal returns 404
    res_fake = client.post(
        "/api/v1/mandates/proposals/prop_fake99999/approve",
        json={"approval_secret": "sec_dummy_secret_for_testing_123"},
    )
    assert res_fake.status_code == 404
    assert "was not found" in res_fake.json()["detail"]

    # 2. Blocked proposal creates no record in repository
    m_res = client.post(
        "/api/v1/mandates",
        json={
            "max_amount": "100.00",
            "permitted_categories": ["Electronics"],
            "max_quantity": 1,
        },
    )
    mandate_id = m_res.json()["id"]

    # Product costs 2999 > 100 -> BLOCKED
    eval_res = client.post(
        f"/api/v1/mandates/{mandate_id}/evaluate",
        json={"product_id": "prod-001", "quantity": 1},
    )
    assert eval_res.json()["decision"] == "BLOCKED"
    assert eval_res.json()["proposal"] is None
    assert eval_res.json()["approval_secret"] is None


def test_short_approval_secret_rejected_with_validation_error():
    """Verify approval secret shorter than 16 chars is rejected with HTTP 422."""
    res_short = client.post(
        "/api/v1/mandates/proposals/prop_any/approve",
        json={"approval_secret": "short_secret"},
    )
    assert res_short.status_code == 422

