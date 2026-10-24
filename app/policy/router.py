"""FastAPI router for spending mandates and policy evaluation."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.policy.models import (
    MandateCreateRequest,
    PolicyEvaluationResult,
    ProposalApprovalRequest,
    ProposalEvaluationRequest,
    PurchaseProposal,
    SpendingMandate,
)
from app.policy.service import PolicyService, policy_service

router = APIRouter()


def get_service() -> PolicyService:
    """Dependency provider for PolicyService."""
    return policy_service


@router.post(
    "",
    response_model=SpendingMandate,
    status_code=status.HTTP_201_CREATED,
    summary="Create spending mandate",
    description="Establish a new spending mandate with validated budgetary and category constraints.",
)
def create_mandate(
    request: MandateCreateRequest,
    service: PolicyService = Depends(get_service),
) -> SpendingMandate:
    """Create a new spending mandate."""
    try:
        return service.create_mandate(request)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.get(
    "/{mandate_id}",
    response_model=SpendingMandate,
    summary="Get spending mandate",
    description="Retrieve spending mandate by ID, reflecting its latest lifecycle state.",
)
def get_mandate(
    mandate_id: str,
    service: PolicyService = Depends(get_service),
) -> SpendingMandate:
    """Retrieve mandate or return HTTP 404."""
    mandate = service.get_mandate(mandate_id)
    if mandate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Spending mandate '{mandate_id}' was not found.",
        )
    return mandate


@router.post(
    "/{mandate_id}/evaluate",
    response_model=PolicyEvaluationResult,
    summary="Evaluate purchase proposal against mandate",
    description="Deterministically check proposed product purchase against spending mandate constraints.",
)
def evaluate_mandate_proposal(
    mandate_id: str,
    request: ProposalEvaluationRequest,
    service: PolicyService = Depends(get_service),
) -> PolicyEvaluationResult:
    """Evaluate proposal using mandate ID from URL path."""
    return service.evaluate_proposal(
        mandate_id=mandate_id,
        product_id=request.product_id,
        quantity=request.quantity,
    )


@router.post(
    "/evaluate",
    response_model=PolicyEvaluationResult,
    summary="Evaluate purchase proposal",
    description="Evaluate purchase proposal when mandate_id is provided in the request body.",
)
def evaluate_proposal(
    request: ProposalEvaluationRequest,
    service: PolicyService = Depends(get_service),
) -> PolicyEvaluationResult:
    """Evaluate proposal using mandate_id from payload."""
    if not request.mandate_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Field 'mandate_id' is required when evaluating via /api/v1/mandates/evaluate.",
        )
    return service.evaluate_proposal(
        mandate_id=request.mandate_id,
        product_id=request.product_id,
        quantity=request.quantity,
    )


@router.get(
    "/proposals/{proposal_id}",
    response_model=PurchaseProposal,
    summary="Get purchase proposal",
    description="Retrieve evaluated proposal details by proposal ID.",
)
def get_proposal(
    proposal_id: str,
    service: PolicyService = Depends(get_service),
) -> PurchaseProposal:
    """Retrieve proposal or return HTTP 404."""
    proposal = service.get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Proposal '{proposal_id}' was not found.",
        )
    return proposal


@router.post(
    "/proposals/{proposal_id}/approve",
    response_model=PurchaseProposal,
    summary="Explicitly user-approve a purchase proposal",
    description=(
        "Transition a proposal from PENDING_APPROVAL to USER_APPROVED and consume "
        "the associated mandate using a verified cryptographic approval secret. Does NOT process any payment."
    ),
)
def approve_proposal(
    proposal_id: str,
    request: ProposalApprovalRequest,
    service: PolicyService = Depends(get_service),
) -> PurchaseProposal:
    """Execute explicit user approval with required approval_secret."""
    try:
        return service.approve_proposal(
            proposal_id=proposal_id,
            approval_secret=request.approval_secret,
        )
    except ValueError as exc:
        msg = str(exc)
        if "Invalid approval secret" in msg or "Valid approval_secret" in msg:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=msg,
            )
        if "was not found" in msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=msg,
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=msg,
        )


@router.post(
    "/{mandate_id}/revoke",
    response_model=SpendingMandate,
    summary="Revoke spending mandate",
    description="Revoke an active mandate so no further proposals can be evaluated or approved.",
)
def revoke_mandate(
    mandate_id: str,
    service: PolicyService = Depends(get_service),
) -> SpendingMandate:
    """Revoke a spending mandate."""
    try:
        mandate = service.revoke_mandate(mandate_id)
        if mandate is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Mandate '{mandate_id}' was not found.",
            )
        return mandate
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
