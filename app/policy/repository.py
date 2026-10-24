"""In-memory repository for spending mandates and purchase proposals."""

from typing import Optional
from app.policy.models import PurchaseProposal, SpendingMandate


class PolicyRepository:
    """In-memory thread-safe store for spending mandates and purchase proposals.

    Note: Mandates and proposals are held in memory and do not persist across server restarts.
    """

    def __init__(self) -> None:
        self._mandates: dict[str, SpendingMandate] = {}
        self._proposals: dict[str, PurchaseProposal] = {}

    def save_mandate(self, mandate: SpendingMandate) -> SpendingMandate:
        """Store a spending mandate and return a safe copy."""
        self._mandates[mandate.id] = mandate
        return mandate.model_copy(deep=True)

    def get_mandate(self, mandate_id: str) -> Optional[SpendingMandate]:
        """Retrieve a spending mandate by ID, returning a safe copy or None."""
        mandate = self._mandates.get(mandate_id)
        if mandate is None:
            return None
        return mandate.model_copy(deep=True)

    def list_mandates(self) -> list[SpendingMandate]:
        """Return safe copies of all stored mandates."""
        return [m.model_copy(deep=True) for m in self._mandates.values()]

    def update_mandate(self, mandate: SpendingMandate) -> SpendingMandate:
        """Update an existing mandate."""
        self._mandates[mandate.id] = mandate
        return mandate.model_copy(deep=True)

    def save_proposal(self, proposal: PurchaseProposal) -> PurchaseProposal:
        """Store a purchase proposal and return a safe copy."""
        self._proposals[proposal.id] = proposal
        return proposal.model_copy(deep=True)

    def get_proposal(self, proposal_id: str) -> Optional[PurchaseProposal]:
        """Retrieve a purchase proposal by ID, returning a safe copy or None."""
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return None
        return proposal.model_copy(deep=True)

    def list_proposals(self) -> list[PurchaseProposal]:
        """Return safe copies of all stored proposals."""
        return [p.model_copy(deep=True) for p in self._proposals.values()]

    def update_proposal(self, proposal: PurchaseProposal) -> PurchaseProposal:
        """Update an existing proposal."""
        self._proposals[proposal.id] = proposal
        return proposal.model_copy(deep=True)

    def clear(self) -> None:
        """Clear all stored data (primarily used for test isolation)."""
        self._mandates.clear()
        self._proposals.clear()


# Shared singleton repository instance
in_memory_policy_repo = PolicyRepository()
