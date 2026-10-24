"""API router exposing read-only product catalogue endpoints."""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from app.catalogue.models import Product
from app.catalogue.service import CatalogueService, catalogue_service

router = APIRouter()


def get_service() -> CatalogueService:
    """Dependency provider for CatalogueService."""
    return catalogue_service


@router.get(
    "/categories",
    response_model=list[str],
    summary="List unique product categories",
    description="Retrieve all unique product categories in deterministic, sorted order.",
)
def get_categories(
    service: CatalogueService = Depends(get_service),
) -> list[str]:
    """Return sorted unique categories."""
    return service.list_categories()


@router.get(
    "",
    response_model=list[Product],
    summary="List and filter catalogue products",
    description="Retrieve products with optional filtering by category, availability, and search terms.",
)
def get_products(
    category: Optional[str] = Query(
        default=None,
        description="Filter products by category name (case-insensitive).",
    ),
    available: Optional[bool] = Query(
        default=None,
        description="Filter products by availability boolean.",
    ),
    search: Optional[str] = Query(
        default=None,
        description="Case-insensitive keyword search in product name and description.",
    ),
    service: CatalogueService = Depends(get_service),
) -> list[Product]:
    """Return filtered product list."""
    return service.list_products(
        category=category,
        available=available,
        search=search,
    )


@router.get(
    "/{product_id}",
    response_model=Product,
    summary="Get product by ID",
    description="Retrieve an individual product by its stable identifier.",
)
def get_product_by_id(
    product_id: str,
    service: CatalogueService = Depends(get_service),
) -> Product:
    """Return a single product or HTTP 404."""
    product = service.get_product_by_id(product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with ID '{product_id}' was not found.",
        )
    return product
