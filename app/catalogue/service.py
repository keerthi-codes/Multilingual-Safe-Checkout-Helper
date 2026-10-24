"""Catalogue business logic and filtering service."""

from typing import Optional
from app.catalogue.models import Product
from app.catalogue.repository import CatalogueRepository, in_memory_catalogue_repo


class CatalogueService:
    """Service encapsulating deterministic product querying, filtering, and search logic."""

    def __init__(self, repository: Optional[CatalogueRepository] = None) -> None:
        self.repository = repository or in_memory_catalogue_repo

    def list_products(
        self,
        category: Optional[str] = None,
        available: Optional[bool] = None,
        search: Optional[str] = None,
    ) -> list[Product]:
        """List products with optional multi-attribute deterministic filtering.

        - category: Case-insensitive exact category match.
        - available: Strict boolean availability check.
        - search: Case-insensitive substring match in product name or description.
        All supplied filters are combined with logical AND.
        """
        products = self.repository.get_all()

        if category is not None and category.strip():
            cat_lower = category.strip().lower()
            products = [p for p in products if p.category.lower() == cat_lower]

        if available is not None:
            products = [p for p in products if p.available is available]

        if search is not None and search.strip():
            search_term = search.strip().lower()
            products = [
                p
                for p in products
                if search_term in p.name.lower() or search_term in p.description.lower()
            ]

        return products

    def get_product_by_id(self, product_id: str) -> Optional[Product]:
        """Retrieve a single product by its unique ID. Returns None if not found."""
        clean_id = product_id.strip()
        if not clean_id:
            return None
        return self.repository.get_by_id(clean_id)

    def list_categories(self) -> list[str]:
        """Return unique category names in deterministic, sorted order."""
        return self.repository.get_categories()


# Shared service instance
catalogue_service = CatalogueService()
