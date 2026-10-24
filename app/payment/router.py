"""FastAPI router for PayPal payment creation, capture verification, and cancellation."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.payment.models import (
    OrderCaptureResponse,
    OrderCancelResponse,
    OrderCreateRequest,
    OrderCreateResponse,
    PaymentOrderRecord,
)
from app.payment.service import PaymentService, payment_service

router = APIRouter()


def get_payment_service() -> PaymentService:
    """Dependency provider for PaymentService."""
    return payment_service


@router.post(
    "/orders/create",
    response_model=OrderCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create PayPal Checkout Order",
    description=(
        "Initiate a PayPal Sandbox checkout for a user-approved proposal. Derives USD test amount "
        "deterministically on the server from the configured demo conversion rate."
    ),
)
async def create_payment_order(
    request: OrderCreateRequest,
    service: PaymentService = Depends(get_payment_service),
) -> OrderCreateResponse:
    """Create a PayPal order for an approved proposal."""
    try:
        return await service.create_payment_order(proposal_id=request.proposal_id)
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.post(
    "/orders/{order_id}/capture",
    response_model=OrderCaptureResponse,
    summary="Capture and Verify PayPal Payment",
    description=(
        "Verify order status and execute capture on PayPal Sandbox with idempotency headers. "
        "Upon verified completion, marks proposal PAID and finalizes mandate to CONSUMED."
    ),
)
async def capture_payment_order(
    order_id: str,
    service: PaymentService = Depends(get_payment_service),
) -> OrderCaptureResponse:
    """Capture payment and finalize mandate."""
    try:
        return await service.capture_payment_order(order_id=order_id)
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.post(
    "/orders/{order_id}/cancel",
    response_model=OrderCancelResponse,
    summary="Cancel PayPal Order and Release Mandate",
    description=(
        "Handle user cancellation of PayPal checkout. Releases the reserved mandate "
        "back to ACTIVE state."
    ),
)
def cancel_payment_order(
    order_id: str,
    service: PaymentService = Depends(get_payment_service),
) -> OrderCancelResponse:
    """Cancel payment order and release mandate hold."""
    try:
        return service.cancel_payment_order(order_id=order_id)
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)


@router.get(
    "/orders/{order_id}",
    response_model=PaymentOrderRecord,
    summary="Get Payment Order Status",
    description="Retrieve stored payment order details, transaction status, and capture IDs.",
)
def get_payment_order(
    order_id: str,
    service: PaymentService = Depends(get_payment_service),
) -> PaymentOrderRecord:
    """Retrieve stored payment order record."""
    try:
        return service.get_order_record(order_id=order_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
