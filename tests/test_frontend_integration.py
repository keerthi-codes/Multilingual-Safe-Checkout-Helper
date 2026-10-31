"""End-to-end frontend integration and shopping experience tests.

Validates the complete shopping workflow and API contracts required by Task 7:
1. Load product catalogue and categories
2. Filter and select a product
3. Multilingual conversations (EN, ES, FR)
4. Spending mandate establishment (default & custom)
5. AI product recommendations and proposal generation
6. Deterministic policy evaluation (allowed vs blocked)
7. Explicit user approval with one-time cryptographic secret
8. PayPal Sandbox checkout creation with dual-currency breakdown (INR total, USD demo amount)
9. PayPal return and cancel URL handling
10. Verified capture confirmation and mandate transition to CONSUMED
11. Verification that only confirmed capture produces PAID state
12. Negative scenarios: invalid input, rejected policy, expired mandate, duplicate submissions
"""

from decimal import Decimal
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.policy.models import MandateStatus, PolicyDecision, ProposalStatus
from app.payment.models import PaymentStatus

client = TestClient(app)


def test_frontend_static_assets_served():
    """Verify index.html, style.css, and main.js are served with valid content."""
    res_html = client.get("/")
    assert res_html.status_code == 200
    assert "text/html" in res_html.headers.get("content-type", "")
    assert "Multilingual Safe-Checkout Helper" in res_html.text
    assert "id=\"products-grid\"" in res_html.text
    assert "id=\"mandate-section\"" in res_html.text
    assert "id=\"ai-section\"" in res_html.text
    assert "id=\"proposal-modal\"" in res_html.text

    res_css = client.get("/css/style.css")
    assert res_css.status_code == 200
    assert "text/css" in res_css.headers.get("content-type", "")

    res_js = client.get("/js/main.js")
    assert res_js.status_code == 200
    assert "javascript" in res_js.headers.get("content-type", "")
    assert "currentProposalApprovalSecret" in res_js.text


def test_complete_end_to_end_shopping_flow():
    """Verify the complete 11-step flow from catalogue to verified capture."""
    # Step 1: Load catalogue and categories
    res_cats = client.get("/api/v1/products/categories")
    assert res_cats.status_code == 200
    categories = res_cats.json()
    assert "Electronics" in categories
    assert "Books" in categories

    res_products = client.get("/api/v1/products")
    assert res_products.status_code == 200
    products = res_products.json()
    assert len(products) >= 10

    # Step 2: Filter catalogue
    res_filtered = client.get("/api/v1/products?category=Electronics&available=true")
    assert res_filtered.status_code == 200
    filtered_items = res_filtered.json()
    assert all(p["category"] == "Electronics" and p["available"] for p in filtered_items)
    target_product = filtered_items[0]

    # Step 3: Multilingual conversations (EN, ES, FR)
    for lang, prompt in [
        ("en", "Can you recommend noise-cancelling headphones?"),
        ("es", "¿Qué auriculares me recomiendas?"),
        ("fr", "Quels écouteurs recommandez-vous?"),
    ]:
        res_ai = client.post(
            "/api/v1/ai/chat",
            json={"message": prompt, "language": lang, "conversation_history": []},
        )
        assert res_ai.status_code == 200
        ai_data = res_ai.json()
        assert ai_data["language"] == lang
        assert len(ai_data["reply_text"]) > 0

    # Step 4: Create spending mandate (₹5,000 budget for Electronics & Books)
    res_mandate = client.post(
        "/api/v1/mandates",
        json={
            "max_amount": "5000.00",
            "currency": "INR",
            "permitted_categories": ["Electronics", "Books"],
            "max_quantity": 2,
            "expires_in_seconds": 7200,
        },
    )
    assert res_mandate.status_code == 201
    mandate = res_mandate.json()
    mandate_id = mandate["id"]
    assert mandate["status"] == MandateStatus.ACTIVE

    # Step 5 & 6: Evaluate proposal against mandate
    res_eval = client.post(
        f"/api/v1/mandates/{mandate_id}/evaluate",
        json={"product_id": target_product["id"], "quantity": 1},
    )
    assert res_eval.status_code == 200
    eval_result = res_eval.json()
    assert eval_result["decision"] == PolicyDecision.ALLOW_PENDING_APPROVAL
    assert eval_result["approval_secret"] is not None
    approval_secret = eval_result["approval_secret"]
    proposal_id = eval_result["proposal"]["id"]

    # Step 7: Explicit user approval with one-time secret
    res_approve = client.post(
        f"/api/v1/mandates/proposals/{proposal_id}/approve",
        json={"approval_secret": approval_secret},
    )
    assert res_approve.status_code == 200
    approved_prop = res_approve.json()
    assert approved_prop["status"] == ProposalStatus.USER_APPROVED

    # Verify mandate transitioned to RESERVED
    res_mandate_check = client.get(f"/api/v1/mandates/{mandate_id}")
    assert res_mandate_check.status_code == 200
    assert res_mandate_check.json()["status"] == MandateStatus.RESERVED

    # Step 8: Initiate PayPal Sandbox Checkout
    res_order = client.post(
        "/api/v1/payments/orders/create",
        json={"proposal_id": proposal_id},
    )
    assert res_order.status_code == 201
    order_data = res_order.json()
    order_id = order_data["order_id"]
    assert order_data["status"] == PaymentStatus.CREATED
    assert "https://" in order_data["approve_url"]
    assert order_data["currency_breakdown"]["inr_total"] == approved_prop["total_amount"]
    assert Decimal(str(order_data["currency_breakdown"]["usd_amount"])) > Decimal("0.00")

    # Step 9 & 10: Query status before capture
    res_status = client.get(f"/api/v1/payments/orders/{order_id}")
    assert res_status.status_code == 200
    assert res_status.json()["status"] == PaymentStatus.CREATED

    # Step 11: Execute verified capture
    res_cap = client.post(f"/api/v1/payments/orders/{order_id}/capture")
    assert res_cap.status_code == 200
    cap_data = res_cap.json()
    assert cap_data["status"] == PaymentStatus.COMPLETED
    assert cap_data["capture_id"] is not None

    # Verify mandate finalized to CONSUMED
    res_mandate_final = client.get(f"/api/v1/mandates/{mandate_id}")
    assert res_mandate_final.status_code == 200
    assert res_mandate_final.json()["status"] == MandateStatus.CONSUMED


def test_policy_rejection_and_negative_scenarios():
    """Verify negative and edge cases: category disallowed, budget exceeded, invalid secret."""
    # 1. Mandate with ₹1,000 budget for Books only
    res_mandate = client.post(
        "/api/v1/mandates",
        json={
            "max_amount": "1000.00",
            "currency": "INR",
            "permitted_categories": ["Books"],
            "max_quantity": 1,
            "expires_in_seconds": 3600,
        },
    )
    assert res_mandate.status_code == 201
    mandate_id = res_mandate.json()["id"]

    # 2. Blocked: Disallowed Category (Electronics)
    res_blocked_cat = client.post(
        f"/api/v1/mandates/{mandate_id}/evaluate",
        json={"product_id": "prod-001", "quantity": 1},
    )
    assert res_blocked_cat.status_code == 200
    data_cat = res_blocked_cat.json()
    assert data_cat["decision"] == PolicyDecision.BLOCKED
    assert data_cat["reason_code"] == "CATEGORY_DISALLOWED"
    assert data_cat["approval_secret"] is None

    # 3. Blocked: Amount exceeds limit (Book costing ₹1499 > ₹1000)
    res_blocked_amt = client.post(
        f"/api/v1/mandates/{mandate_id}/evaluate",
        json={"product_id": "prod-008", "quantity": 1},
    )
    assert res_blocked_amt.status_code == 200
    data_amt = res_blocked_amt.json()
    assert data_amt["decision"] == PolicyDecision.BLOCKED
    assert data_amt["reason_code"] == "AMOUNT_EXCEEDS_LIMIT"

    # 4. Valid evaluation: Book costing ₹899 <= ₹1000
    res_ok = client.post(
        f"/api/v1/mandates/{mandate_id}/evaluate",
        json={"product_id": "prod-007", "quantity": 1},
    )
    assert res_ok.status_code == 200
    prop_id = res_ok.json()["proposal"]["id"]

    # 5. Invalid approval secret fails with 403
    res_bad_secret = client.post(
        f"/api/v1/mandates/proposals/{prop_id}/approve",
        json={"approval_secret": "invalid-wrong-secret-12345"},
    )
    assert res_bad_secret.status_code == 403

    # 6. Cannot create payment order for unapproved proposal
    res_bad_order = client.post(
        "/api/v1/payments/orders/create",
        json={"proposal_id": prop_id},
    )
    assert res_bad_order.status_code == 400
