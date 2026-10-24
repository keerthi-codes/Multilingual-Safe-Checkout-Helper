"""In-memory catalogue repository with seeded demo products."""

from decimal import Decimal
from typing import Optional
from app.catalogue.models import Product


class CatalogueRepository:
    """In-memory source of truth for the demo product catalogue."""

    def __init__(self, seed: bool = True) -> None:
        self._products: dict[str, Product] = {}
        if seed:
            self._seed_catalogue()

    def _seed_catalogue(self) -> None:
        """Seed 10 deterministic demo products with realistic INR pricing."""
        demo_items = [
            Product(
                id="prod-001",
                name="Noise-Cancelling Wireless Headphones",
                description="Over-ear Bluetooth headphones with active noise cancellation and 30-hour battery life.",
                category="Electronics",
                price=Decimal("2999.00"),
                currency="INR",
                available=True,
                supported_languages=["en", "es", "fr"],
            ),
            Product(
                id="prod-002",
                name="Mechanical RGB Gaming Keyboard",
                description="Compact tenkeyless mechanical keyboard with customizable RGB backlighting and tactile switches.",
                category="Electronics",
                price=Decimal("3499.00"),
                currency="INR",
                available=True,
                supported_languages=["en", "es", "fr"],
            ),
            Product(
                id="prod-003",
                name="Compact 65W GaN USB-C Charger",
                description="High-efficiency multi-port fast charger compatible with laptops, tablets, and smartphones.",
                category="Electronics",
                price=Decimal("1599.00"),
                currency="INR",
                available=False,
                supported_languages=["en", "es", "fr"],
            ),
            Product(
                id="prod-004",
                name="Ergonomic Memory Foam Pillow",
                description="Contoured cervical support pillow designed for spine alignment and cooling comfort.",
                category="Home",
                price=Decimal("1299.00"),
                currency="INR",
                available=True,
                supported_languages=["en", "es", "fr"],
            ),
            Product(
                id="prod-005",
                name="Insulated Stainless Steel Water Bottle",
                description="Double-wall vacuum insulated 750ml thermal flask keeping drinks cold for 24 hours.",
                category="Home",
                price=Decimal("799.00"),
                currency="INR",
                available=True,
                supported_languages=["en", "es", "fr"],
            ),
            Product(
                id="prod-006",
                name="Minimalist Ceramic Coffee Mug",
                description="Handcrafted matte finish ceramic mug, microwave and dishwasher safe (350ml).",
                category="Home",
                price=Decimal("499.00"),
                currency="INR",
                available=False,
                supported_languages=["en", "es", "fr"],
            ),
            Product(
                id="prod-007",
                name="The Pragmatic Programmer",
                description="20th Anniversary Edition classic textbook on software craftsmanship and career development.",
                category="Books",
                price=Decimal("899.00"),
                currency="INR",
                available=True,
                supported_languages=["en", "es", "fr"],
            ),
            Product(
                id="prod-008",
                name="Designing Data-Intensive Applications",
                description="In-depth guide to distributed systems, storage engines, transactions, and stream processing.",
                category="Books",
                price=Decimal("1499.00"),
                currency="INR",
                available=True,
                supported_languages=["en", "es", "fr"],
            ),
            Product(
                id="prod-009",
                name="Genuine Leather Laptop Sleeve",
                description="Slim protective case for 14-inch laptops crafted from full-grain leather with magnetic clasp.",
                category="Accessories",
                price=Decimal("1899.00"),
                currency="INR",
                available=True,
                supported_languages=["en", "es", "fr"],
            ),
            Product(
                id="prod-010",
                name="Polarized UV400 Sunglasses",
                description="Classic unisex aviator sunglasses with glare reduction and lightweight metal frame.",
                category="Accessories",
                price=Decimal("1199.00"),
                currency="INR",
                available=True,
                supported_languages=["en", "es", "fr"],
            ),
        ]
        for product in demo_items:
            self._products[product.id] = product

    def get_all(self) -> list[Product]:
        """Return safe copies of all catalogue products in deterministic order."""
        return [p.model_copy(deep=True) for p in self._products.values()]

    def get_by_id(self, product_id: str) -> Optional[Product]:
        """Retrieve a single product by ID, returning a safe copy or None."""
        product = self._products.get(product_id)
        if product is None:
            return None
        return product.model_copy(deep=True)

    def get_categories(self) -> list[str]:
        """Return unique category names in deterministic, sorted order."""
        return sorted({p.category for p in self._products.values()})


# Shared singleton repository instance for the application
in_memory_catalogue_repo = CatalogueRepository()
