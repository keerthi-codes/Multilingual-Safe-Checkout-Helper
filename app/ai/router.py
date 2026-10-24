"""FastAPI router exposing multilingual AI assistant chat endpoints."""

from fastapi import APIRouter, Depends, status

from app.ai.models import AIChatRequest, AIChatResponse
from app.ai.service import AIService, ai_service

router = APIRouter()


def get_service() -> AIService:
    """Dependency provider for AIService."""
    return ai_service


@router.post(
    "/chat",
    response_model=AIChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Chat with multilingual shopping assistant",
    description=(
        "Converse in English ('en'), Spanish ('es'), or French ('fr') with the AI shopping assistant. "
        "The assistant queries verified catalogue items and can propose purchases that are automatically "
        "evaluated against active spending mandates."
    ),
)
async def chat_with_assistant(
    request: AIChatRequest,
    service: AIService = Depends(get_service),
) -> AIChatResponse:
    """Process incoming chat query."""
    return await service.chat(request)
