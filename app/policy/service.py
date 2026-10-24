"""Deterministic spending mandate policy engine and evaluation service."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import hmac
import secrets
from typing import Callable, Optional
import uuid

from app.catalogue.service import CatalogueService, catalogue_service
from app.policy.models import (
    MandateCreateRequest,
    MandateStatus,
    PolicyDecision,
    PolicyEvaluationResult,
    PolicyReasonCode,
    ProposalStatus,
    PurchaseProposal,
    SpendingMandate,
)
from app.policy.repository import PolicyRepository, in_memory_policy_repo


class PolicyService:
    """Service implementing deterministic spending mandates and purchase policy rules."""

    def __init__(
        self,
        catalogue: Optional[CatalogueService] = None,
        repository: Optional[PolicyRepository] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.catalogue_service = catalogue or catalogue_service
        self.repository = repository or in_memory_policy_repo
        self.clock: Callable[[], datetime] = clock or (lambda: datetime.now(timezone.utc))

    def create_mandate(self, req: MandateCreateRequest) -> SpendingMandate:
        """Create a new validated spending mandate with UTC timestamps."""
        created_at = self.clock()

        if req.expires_at is not None:
            if req.expires_at.tzinfo is None:
                raise ValueError("expires_at must be timezone-aware (UTC).")
            if req.expires_at <= created_at:
                raise ValueError("expires_at must be in the future relative to creation time.")
            expires_at = req.expires_at
        else:
            seconds = req.expires_in_seconds if req.expires_in_seconds is not None else 3600
            expires_at = created_at + timedelta(seconds=seconds)

        mandate = SpendingMandate(
            id=f"mandate_{uuid.uuid4().hex[:10]}",
            max_amount=req.max_amount,
            currency=req.currency,
            permitted_categories=req.permitted_categories,
            max_quantity=req.max_quantity,
            created_at=created_at,
            expires_at=expires_at,
            requires_user_approval=True,
            status=MandateStatus.ACTIVE,
        )
        return self.repository.save_mandate(mandate)

    def get_mandate(self, mandate_id: str) -> Optional[SpendingMandate]:
        """Retrieve a mandate and update status if it has expired."""
        mandate = self.repository.get_mandate(mandate_id)
        if mandate is None:
            return None

        now = self.clock()
        if mandate.status == MandateStatus.ACTIVE and mandate.expires_at <= now:
            expired_mandate = mandate.model_copy(update={"status": MandateStatus.EXPIRED})
            return self.repository.update_mandate(expired_mandate)

        return mandate

    def revoke_mandate(self, mandate_id: str) -> Optional[SpendingMandate]:
        """Revoke an active mandate to prevent any future evaluations or approvals."""
        mandate = self.get_mandate(mandate_id)
        if mandate is None:
            return None

        if mandate.status != MandateStatus.ACTIVE:
            raise ValueError(f"Cannot revoke mandate with status '{mandate.status}'.")

        revoked_mandate = mandate.model_copy(update={"status": MandateStatus.REVOKED})
        return self.repository.update_mandate(revoked_mandate)

    def evaluate_proposal(
        self,
        mandate_id: str,
        product_id: str,
        quantity: int,
    ) -> PolicyEvaluationResult:
        """Deterministically evaluate a purchase proposal against a mandate and catalogue.

        The evaluation performs pure validation:
        - It never captures money or initiates payments.
        - Evaluation alone does NOT consume the mandate.
        - Product details and prices are sourced exclusively from the trusted catalogue.
        """
        now = self.clock()

        # 1. Mandate exists check
        mandate = self.get_mandate(mandate_id)
        if mandate is None:
            return PolicyEvaluationResult(
                decision=PolicyDecision.BLOCKED,
                reason_code=PolicyReasonCode.MANDATE_NOT_FOUND,
                reason_message=f"Spending mandate '{mandate_id}' was not found.",
                mandate_id=mandate_id,
                product_id=product_id,
                quantity=quantity,
                evaluated_at=now,
            )

        # 2. Mandate expiration check
        if mandate.expires_at <= now or mandate.status == MandateStatus.EXPIRED:
            return PolicyEvaluationResult(
                decision=PolicyDecision.BLOCKED,
                reason_code=PolicyReasonCode.MANDATE_EXPIRED,
                reason_message="Spending mandate has expired.",
                mandate_id=mandate_id,
                product_id=product_id,
                quantity=quantity,
                evaluated_at=now,
            )

        # 3. Mandate lifecycle state (reserved, consumed or revoked)
        if mandate.status == MandateStatus.RESERVED:
            return PolicyEvaluationResult(
                decision=PolicyDecision.BLOCKED,
                reason_code=PolicyReasonCode.MANDATE_RESERVED,
                reason_message=f"Spending mandate has already been reserved by proposal '{mandate.reserved_by_proposal_id}' awaiting payment completion.",
                mandate_id=mandate_id,
                product_id=product_id,
                quantity=quantity,
                evaluated_at=now,
            )

        if mandate.status == MandateStatus.CONSUMED:
            return PolicyEvaluationResult(
                decision=PolicyDecision.BLOCKED,
                reason_code=PolicyReasonCode.MANDATE_ALREADY_CONSUMED,
                reason_message=f"Spending mandate has already been consumed by proposal '{mandate.consumed_by_proposal_id}'.",
                mandate_id=mandate_id,
                product_id=product_id,
                quantity=quantity,
                evaluated_at=now,
            )

        if mandate.status == MandateStatus.REVOKED:
            return PolicyEvaluationResult(
                decision=PolicyDecision.BLOCKED,
                reason_code=PolicyReasonCode.MANDATE_REVOKED,
                reason_message="Spending mandate has been revoked.",
                mandate_id=mandate_id,
                product_id=product_id,
                quantity=quantity,
                evaluated_at=now,
            )

        # 4. Product existence in authoritative catalogue
        product = self.catalogue_service.get_product_by_id(product_id)
        if product is None:
            return PolicyEvaluationResult(
                decision=PolicyDecision.BLOCKED,
                reason_code=PolicyReasonCode.PRODUCT_NOT_FOUND,
                reason_message=f"Product '{product_id}' was not found in authoritative catalogue.",
                mandate_id=mandate_id,
                product_id=product_id,
                quantity=quantity,
                evaluated_at=now,
            )

        # 5. Product availability check
        if not product.available:
            return PolicyEvaluationResult(
                decision=PolicyDecision.BLOCKED,
                reason_code=PolicyReasonCode.PRODUCT_UNAVAILABLE,
                reason_message=f"Product '{product.name}' is currently unavailable or out of stock.",
                mandate_id=mandate_id,
                product_id=product_id,
                quantity=quantity,
                evaluated_at=now,
            )

        # 6. Permitted category check (case-insensitive)
        permitted_categories = [c.lower() for c in mandate.permitted_categories]
        if product.category.lower() not in permitted_categories:
            return PolicyEvaluationResult(
                decision=PolicyDecision.BLOCKED,
                reason_code=PolicyReasonCode.CATEGORY_DISALLOWED,
                reason_message=(
                    f"Product category '{product.category}' is not allowed by this mandate. "
                    f"Permitted categories: {', '.join(mandate.permitted_categories)}."
                ),
                mandate_id=mandate_id,
                product_id=product_id,
                quantity=quantity,
                evaluated_at=now,
            )

        # 7. Positive integer quantity check
        if not isinstance(quantity, int) or quantity <= 0:
            return PolicyEvaluationResult(
                decision=PolicyDecision.BLOCKED,
                reason_code=PolicyReasonCode.INVALID_QUANTITY,
                reason_message=f"Quantity must be a positive integer >= 1, received {quantity}.",
                mandate_id=mandate_id,
                product_id=product_id,
                quantity=quantity,
                evaluated_at=now,
            )

        # 8. Maximum quantity limit check
        if quantity > mandate.max_quantity:
            return PolicyEvaluationResult(
                decision=PolicyDecision.BLOCKED,
                reason_code=PolicyReasonCode.QUANTITY_EXCEEDS_LIMIT,
                reason_message=(
                    f"Requested quantity {quantity} exceeds mandate limit of "
                    f"{mandate.max_quantity} items."
                ),
                mandate_id=mandate_id,
                product_id=product_id,
                quantity=quantity,
                evaluated_at=now,
            )

        # 9. Currency matching check
        if product.currency.upper() != mandate.currency.upper():
            return PolicyEvaluationResult(
                decision=PolicyDecision.BLOCKED,
                reason_code=PolicyReasonCode.CURRENCY_MISMATCH,
                reason_message=(
                    f"Currency mismatch: product is denominated in {product.currency}, "
                    f"while mandate permits {mandate.currency}."
                ),
                mandate_id=mandate_id,
                product_id=product_id,
                quantity=quantity,
                evaluated_at=now,
            )

        # 10. Accurate Decimal total amount calculation and ceiling check
        total_amount = (product.price * Decimal(quantity)).quantize(Decimal("0.01"))
        if total_amount > mandate.max_amount:
            return PolicyEvaluationResult(
                decision=PolicyDecision.BLOCKED,
                reason_code=PolicyReasonCode.AMOUNT_EXCEEDS_LIMIT,
                reason_message=(
                    f"Calculated purchase total of {mandate.currency} {total_amount} exceeds "
                    f"mandate ceiling of {mandate.currency} {mandate.max_amount}."
                ),
                mandate_id=mandate_id,
                product_id=product_id,
                quantity=quantity,
                total_amount=total_amount,
                currency=product.currency,
                evaluated_at=now,
            )

        # All policy checks successfully passed: generate authoritative proposal with secure secret
        raw_secret = f"sec_{secrets.token_urlsafe(32)}"
        secret_hash = hashlib.sha256(raw_secret.encode("utf-8")).hexdigest()

        proposal = PurchaseProposal(
            id=f"prop_{uuid.uuid4().hex[:10]}",
            mandate_id=mandate.id,
            product_id=product.id,
            product_name=product.name,
            product_category=product.category,
            unit_price=product.price,
            quantity=quantity,
            total_amount=total_amount,
            currency=product.currency,
            status=ProposalStatus.PENDING_APPROVAL,
            requires_user_approval=True,
            is_user_approved=False,
            approval_secret_hash=secret_hash,
            created_at=now,
        )
        saved_proposal = self.repository.save_proposal(proposal)

        return PolicyEvaluationResult(
            decision=PolicyDecision.ALLOW_PENDING_APPROVAL,
            reason_code=PolicyReasonCode.POLICY_PASSED_PENDING_USER_APPROVAL,
            reason_message="Purchase proposal conforms to spending mandate and is pending explicit user approval.",
            mandate_id=mandate.id,
            product_id=product.id,
            quantity=quantity,
            total_amount=total_amount,
            currency=product.currency,
            proposal=saved_proposal,
            approval_secret=raw_secret,
            evaluated_at=now,
        )

    def get_proposal(self, proposal_id: str) -> Optional[PurchaseProposal]:
        """Retrieve a purchase proposal by its unique ID."""
        return self.repository.get_proposal(proposal_id)

    def approve_proposal(self, proposal_id: str, approval_secret: str) -> PurchaseProposal:
        """Explicitly approve a pending proposal and consume the associated mandate.

        Enforces replay protection and secret validation:
        - Only a proposal in PENDING_APPROVAL status can be approved.
        - Validates the proposal-specific approval secret using constant-time comparison.
        - The associated mandate must still be ACTIVE and unexpired.
        - Approving immediately transitions the mandate to CONSUMED.
        """
        if not approval_secret or not isinstance(approval_secret, str):
            raise ValueError("Valid approval_secret must be provided to approve this proposal.")

        proposal = self.repository.get_proposal(proposal_id)
        if proposal is None:
            raise ValueError(f"Proposal '{proposal_id}' was not found.")

        # Ensure proposal is currently PENDING_APPROVAL
        if proposal.status != ProposalStatus.PENDING_APPROVAL:
            raise ValueError(f"Proposal cannot be approved in its current state '{proposal.status}'.")

        # Cryptographic approval secret verification (constant-time)
        computed_hash = hashlib.sha256(approval_secret.strip().encode("utf-8")).hexdigest()
        if not proposal.approval_secret_hash or not hmac.compare_digest(proposal.approval_secret_hash, computed_hash):
            raise ValueError("Invalid approval secret provided for proposal.")

        mandate = self.get_mandate(proposal.mandate_id)
        if mandate is None:
            raise ValueError(f"Mandate '{proposal.mandate_id}' associated with proposal not found.")

        if mandate.status != MandateStatus.ACTIVE:
            raise ValueError(f"Mandate cannot be consumed because its status is '{mandate.status}'.")

        now = self.clock()
        if mandate.expires_at <= now:
            raise ValueError("Mandate has expired and cannot be approved.")

        # Update proposal state
        approved_proposal = proposal.model_copy(
            update={
                "status": ProposalStatus.USER_APPROVED,
                "is_user_approved": True,
                "user_approved_at": now,
            }
        )
        self.repository.update_proposal(approved_proposal)

        # Reserve the mandate (held for payment) instead of consuming immediately
        reserved_mandate = mandate.model_copy(
            update={
                "status": MandateStatus.RESERVED,
                "reserved_by_proposal_id": approved_proposal.id,
            }
        )
        self.repository.update_mandate(reserved_mandate)

        return approved_proposal

    def finalize_mandate_consumed(self, mandate_id: str, proposal_id: str) -> tuple[SpendingMandate, PurchaseProposal]:
        """Finalize a reserved mandate to CONSUMED and proposal to PAID upon verified payment capture."""
        mandate = self.get_mandate(mandate_id)
        if mandate is None:
            raise ValueError(f"Mandate '{mandate_id}' not found.")

        proposal = self.get_proposal(proposal_id)
        if proposal is None:
            raise ValueError(f"Proposal '{proposal_id}' not found.")

        if mandate.status != MandateStatus.RESERVED:
            raise ValueError(f"Cannot finalize mandate with status '{mandate.status}'; expected RESERVED.")

        if proposal.status != ProposalStatus.USER_APPROVED:
            raise ValueError(f"Cannot finalize proposal with status '{proposal.status}'; expected USER_APPROVED.")

        consumed_mandate = mandate.model_copy(
            update={
                "status": MandateStatus.CONSUMED,
                "reserved_by_proposal_id": None,
                "consumed_by_proposal_id": proposal.id,
            }
        )
        self.repository.update_mandate(consumed_mandate)

        paid_proposal = proposal.model_copy(
            update={
                "status": ProposalStatus.PAID,
            }
        )
        self.repository.update_proposal(paid_proposal)
        return consumed_mandate, paid_proposal

    def release_mandate_reservation(self, mandate_id: str, proposal_id: str) -> tuple[SpendingMandate, PurchaseProposal]:
        """Release a reserved mandate back to ACTIVE if checkout is cancelled or definitively fails."""
        mandate = self.get_mandate(mandate_id)
        if mandate is None:
            raise ValueError(f"Mandate '{mandate_id}' not found.")

        proposal = self.get_proposal(proposal_id)
        if proposal is None:
            raise ValueError(f"Proposal '{proposal_id}' not found.")

        if mandate.status != MandateStatus.RESERVED:
            raise ValueError(f"Cannot release mandate with status '{mandate.status}'; expected RESERVED.")

        active_mandate = mandate.model_copy(
            update={
                "status": MandateStatus.ACTIVE,
                "reserved_by_proposal_id": None,
            }
        )
        self.repository.update_mandate(active_mandate)

        cancelled_proposal = proposal.model_copy(
            update={
                "status": ProposalStatus.CANCELLED,
            }
        )
        self.repository.update_proposal(cancelled_proposal)
        return active_mandate, cancelled_proposal


# Shared singleton service instance
policy_service = PolicyService()
