"""Comprehensive test suite for the Product Catalogue."""

from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.catalogue.models import Product
from app.catalogue.repository import in_memory_catalogue_repo

client = TestClient(app)


# ---------------------------------------------------------------------------
# API Endpoint Tests
# ---------------------------------------------------------------------------

def test_list_products_returns_seeded_catalogue():
    """Verify that GET /api/v1/products returns all 10 seeded demo products."""
    response = client.get("/api/v1/products")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 10


def test_every_product_has_valid_fields_and_inr_currency():
    """Verify every product in the catalogue has all required fields and uses INR."""
    response = client.get("/api/v1/products")
    assert response.status_code == 200
    products = response.json()

    for item in products:
        assert isinstance(item["id"], str) and len(item["id"].strip()) > 0
        assert isinstance(item["name"], str) and len(item["name"].strip()) > 0
        assert isinstance(item["description"], str) and len(item["description"].strip()) > 0
        assert isinstance(item["category"], str) and len(item["category"].strip()) > 0
        assert item["currency"] == "INR"
        assert isinstance(item["available"], bool)
        assert isinstance(item["supported_languages"], list)
        assert set(["en", "es", "fr"]).issubset(set(item["supported_languages"]))
        # Verify price representation is accurate
        price = Decimal(str(item["price"]))
        assert price >= Decimal("0.00")


def test_product_ids_are_unique():
    """Verify that all product IDs in the catalogue are unique."""
    response = client.get("/api/v1/products")
    assert response.status_code == 200
    products = response.json()
    ids = [p["id"] for p in products]
    assert len(ids) == len(set(ids))


def test_get_product_by_valid_id():
    """Verify that a valid product ID returns HTTP 200 with matching attributes."""
    response = client.get("/api/v1/products/prod-001")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "prod-001"
    assert data["name"] == "Noise-Cancelling Wireless Headphones"
    assert data["category"] == "Electronics"
    assert data["currency"] == "INR"
    assert Decimal(str(data["price"])) == Decimal("2999.00")


def test_get_product_by_unknown_id_returns_404():
    """Verify that a non-existent product ID returns HTTP 404 without leaking internals."""
    response = client.get("/api/v1/products/nonexistent-id-999")
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data
    assert "nonexistent-id-999" in data["detail"]


def test_categories_endpoint_unique_and_sorted():
    """Verify GET /api/v1/products/categories returns unique sorted category names."""
    response = client.get("/api/v1/products/categories")
    assert response.status_code == 200
    categories = response.json()
    assert isinstance(categories, list)
    assert len(categories) == len(set(categories))
    assert categories == sorted(categories)
    assert "Electronics" in categories
    assert "Home" in categories
    assert "Books" in categories
    assert "Accessories" in categories


def test_category_filtering():
    """Verify category filtering works and is case-insensitive."""
    # Exact case
    response = client.get("/api/v1/products?category=Books")
    assert response.status_code == 200
    books = response.json()
    assert len(books) == 2
    assert all(p["category"] == "Books" for p in books)

    # Lowercase
    response_lower = client.get("/api/v1/products?category=books")
    assert response_lower.status_code == 200
    assert response_lower.json() == books


def test_availability_filtering_true_and_false():
    """Verify availability filtering works for both available=true and available=false."""
    # Available items
    res_avail = client.get("/api/v1/products?available=true")
    assert res_avail.status_code == 200
    avail_items = res_avail.json()
    assert len(avail_items) == 8
    assert all(p["available"] is True for p in avail_items)

    # Unavailable items
    res_unavail = client.get("/api/v1/products?available=false")
    assert res_unavail.status_code == 200
    unavail_items = res_unavail.json()
    assert len(unavail_items) == 2
    assert all(p["available"] is False for p in unavail_items)
    unavail_ids = {p["id"] for p in unavail_items}
    assert unavail_ids == {"prod-003", "prod-006"}


def test_search_case_insensitive_name_and_description():
    """Verify search matches keywords in both name and description regardless of case."""
    # Match in name (uppercase)
    res_name = client.get("/api/v1/products?search=HEADPHONES")
    assert res_name.status_code == 200
    results_name = res_name.json()
    assert len(results_name) == 1
    assert results_name[0]["id"] == "prod-001"

    # Match in description (lowercase)
    res_desc = client.get("/api/v1/products?search=distributed")
    assert res_desc.status_code == 200
    results_desc = res_desc.json()
    assert len(results_desc) == 1
    assert results_desc[0]["id"] == "prod-008"


def test_search_no_match_returns_empty_list():
    """Verify that a search yielding no matches returns an empty JSON list."""
    response = client.get("/api/v1/products?search=thisdoesnotmatchanyproduct12345")
    assert response.status_code == 200
    data = response.json()
    assert data == []


def test_multiple_filters_combine_correctly():
    """Verify that multiple query filters combine with logical AND."""
    # Category + Availability
    response = client.get("/api/v1/products?category=Electronics&available=false")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["id"] == "prod-003"
    assert items[0]["category"] == "Electronics"
    assert items[0]["available"] is False

    # Category + Availability + Search
    response_comb = client.get(
        "/api/v1/products?category=Electronics&available=true&search=keyboard"
    )
    assert response_comb.status_code == 200
    comb_items = response_comb.json()
    assert len(comb_items) == 1
    assert comb_items[0]["id"] == "prod-002"


def test_decimal_prices_serialize_correctly_in_api_response():
    """Verify that Decimal prices serialize accurately as decimal-preserving strings/numbers."""
    response = client.get("/api/v1/products/prod-001")
    assert response.status_code == 200
    data = response.json()
    price_str = str(data["price"])
    assert price_str == "2999.00"
    assert Decimal(price_str) == Decimal("2999.00")


# ---------------------------------------------------------------------------
# Unit Validation Tests on Product Model
# ---------------------------------------------------------------------------

def test_product_model_validation_rejects_invalid_data():
    """Verify Pydantic model rejects invalid prices, empty fields, and bad currencies."""
    # Reject negative price
    with pytest.raises(ValidationError):
        Product(
            id="test-1",
            name="Valid Name",
            description="Valid Description",
            category="Valid Category",
            price=Decimal("-10.00"),
        )

    # Reject binary float price
    with pytest.raises(ValidationError):
        Product(
            id="test-2",
            name="Valid Name",
            description="Valid Description",
            category="Valid Category",
            price=19.99,  # type: ignore
        )

    # Reject empty or whitespace-only name
    with pytest.raises(ValidationError):
        Product(
            id="test-3",
            name="   ",
            description="Valid Description",
            category="Valid Category",
            price=Decimal("10.00"),
        )

    # Reject empty description
    with pytest.raises(ValidationError):
        Product(
            id="test-4",
            name="Valid Name",
            description="",
            category="Valid Category",
            price=Decimal("10.00"),
        )

    # Reject non-INR initial currency
    with pytest.raises(ValidationError):
        Product(
            id="test-5",
            name="Valid Name",
            description="Valid Description",
            category="Valid Category",
            price=Decimal("10.00"),
            currency="USD",
        )

    # Reject duplicate language codes
    with pytest.raises(ValidationError):
        Product(
            id="test-6",
            name="Valid Name",
            description="Valid Description",
            category="Valid Category",
            price=Decimal("10.00"),
            supported_languages=["en", "es", "en"],
        )

    # Reject empty language list
    with pytest.raises(ValidationError):
        Product(
            id="test-7",
            name="Valid Name",
            description="Valid Description",
            category="Valid Category",
            price=Decimal("10.00"),
            supported_languages=[],
        )


def test_repository_returns_safe_copies():
    """Verify repository prevents in-place mutation of internal storage."""
    all_products = in_memory_catalogue_repo.get_all()
    original_len = len(all_products)

    # Mutating returned list does not affect repository
    all_products.pop()
    assert len(in_memory_catalogue_repo.get_all()) == original_len

    # Products are frozen models
    prod = in_memory_catalogue_repo.get_by_id("prod-001")
    assert prod is not None
    with pytest.raises(ValidationError):
        prod.name = "Mutated Name"  # type: ignore


def test_frontend_index_route_serves_html():
    """Verify that GET / returns the HTML frontend."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Multilingual Safe-Checkout Helper" in response.text

