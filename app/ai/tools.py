"""Narrowly scoped read-only catalogue inspection tools for the AI assistant."""

from typing import Any, Optional
from app.catalogue.service import CatalogueService, catalogue_service

# Whitelist of allowed tool names
ALLOWED_TOOL_NAMES = frozenset(["search_catalogue", "get_product_details"])
MAX_TOOL_CALL_ROUNDS = 3


class CatalogueToolManager:
    """Manages secure, validated invocation of catalogue inspection tools."""

    def __init__(self, service: Optional[CatalogueService] = None) -> None:
        self.catalogue_service = service or catalogue_service

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return OpenAPI-compatible tool schema definitions for LLMs."""
        return [
            {
                "name": "search_catalogue",
                "description": (
                    "Search demo catalogue products by keyword or category. "
                    "Returns matching items with ID, name, category, INR price, and availability."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Keyword to search in product title or description.",
                        },
                        "category": {
                            "type": "string",
                            "description": "Optional category filter (e.g. 'Electronics', 'Books', 'Home', 'Accessories').",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "get_product_details",
                "description": (
                    "Look up full details of a specific product by its ID. "
                    "Returns name, description, price, currency, availability, and supported languages."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "product_id": {
                            "type": "string",
                            "description": "Unique product ID (e.g. 'prod-001').",
                        },
                    },
                    "required": ["product_id"],
                },
            },
        ]

    def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a validated read-only tool function."""
        if tool_name not in ALLOWED_TOOL_NAMES:
            return {"error": f"Tool '{tool_name}' is not permitted or does not exist."}

        if tool_name == "search_catalogue":
            query = arguments.get("query", "")
            if not isinstance(query, str) or len(query.strip()) == 0:
                return {"error": "Parameter 'query' must be a non-empty string."}
            # Limit length to avoid abuse
            safe_query = query.strip()[:100]
            category = arguments.get("category")
            safe_category = category.strip()[:50] if isinstance(category, str) and category.strip() else None

            products = self.catalogue_service.list_products(
                search=safe_query,
                category=safe_category,
            )
            return {
                "count": len(products),
                "products": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "category": p.category,
                        "price": str(p.price),
                        "currency": p.currency,
                        "available": p.available,
                    }
                    for p in products
                ],
            }

        elif tool_name == "get_product_details":
            product_id = arguments.get("product_id", "")
            if not isinstance(product_id, str) or not product_id.strip():
                return {"error": "Parameter 'product_id' must be a non-empty string."}
            safe_id = product_id.strip()[:50]
            product = self.catalogue_service.get_product_by_id(safe_id)
            if product is None:
                return {"error": f"Product with ID '{safe_id}' was not found in catalogue."}
            return {
                "id": product.id,
                "name": product.name,
                "description": product.description,
                "category": product.category,
                "price": str(product.price),
                "currency": product.currency,
                "available": product.available,
                "supported_languages": product.supported_languages,
            }

        return {"error": "Unhandled tool execution."}


# Shared singleton tool manager
catalogue_tool_manager = CatalogueToolManager()
