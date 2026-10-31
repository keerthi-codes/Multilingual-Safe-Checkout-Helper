"""AI Assistant service coordinating provider adapters, catalogue tools, and deterministic policy evaluation."""

from abc import ABC, abstractmethod
import json
import logging
import re
from typing import Any, Optional
import httpx

from app.ai.models import (
    AIChatRequest,
    AIChatResponse,
    AIProposalIntent,
    ChatMessage,
)
from app.ai.prompts import build_system_prompt
from app.ai.tools import (
    CatalogueToolManager,
    catalogue_tool_manager,
    MAX_TOOL_CALL_ROUNDS,
)
from app.catalogue.service import CatalogueService, catalogue_service
from app.core.config import settings
from app.policy.models import PolicyEvaluationResult
from app.policy.service import PolicyService, policy_service

logger = logging.getLogger(__name__)


class BaseAIProvider(ABC):
    """Abstract interface for AI model providers."""

    @abstractmethod
    async def generate_response(
        self,
        user_message: str,
        conversation_history: list[ChatMessage],
        language: str = "en",
    ) -> tuple[str, Optional[AIProposalIntent], list[str]]:
        """Generate conversational reply, optional proposal intent, and list of executed tool names."""
        pass


class MockAIProvider(BaseAIProvider):
    """Deterministic, zero-network mock provider for testing and offline development."""

    def __init__(self, tool_mgr: Optional[CatalogueToolManager] = None) -> None:
        self.tool_manager = tool_mgr or catalogue_tool_manager

    async def generate_response(
        self,
        user_message: str,
        conversation_history: list[ChatMessage],
        language: str = "en",
    ) -> tuple[str, Optional[AIProposalIntent], list[str]]:
        msg = user_message.lower().strip()
        executed_tools: list[str] = []

        # 1. Ambiguous query check
        ambiguous_triggers = [
            "recommend something", "suggest something", "buy something", "what to buy",
            "recomiéndame algo", "sugiere algo", "comprar algo", "¿qué compro?",
            "conseille-moi", "suggère quelque chose", "acheter quelque chose",
        ]
        if any(trig in msg for trig in ambiguous_triggers) or len(msg.split()) <= 2 and "buy" in msg:
            if language == "es":
                reply = (
                    "Con gusto te ayudo a elegir. Ofrecemos electrónica, libros, hogar y accesorios. "
                    "¿Qué tipo de producto buscas y cuál es tu presupuesto aproximado?"
                )
            elif language == "fr":
                reply = (
                    "Je serais ravi de vous conseiller. Notre catalogue propose de l'électronique, "
                    "des livres, des articles pour la maison et des accessoires. Quel type d'article recherchez-vous ?"
                )
            else:
                reply = (
                    "I would be glad to help! Our catalogue includes Electronics, Home, Books, and Accessories. "
                    "What category or price range do you have in mind?"
                )
            return reply, None, []

        # 2. Check for prompt-injection bypass attempts
        if any(term in msg for term in ["ignore all rules", "admin override", "self-approve", "price to 0", "bypass policy"]):
            if language == "es":
                reply = "No puedo eludir las reglas de gasto ni modificar precios. Toda compra debe cumplir con las políticas deterministas."
            elif language == "fr":
                reply = "Je ne peux pas contourner les règles budgétaires ni modifier les prix. Tout achat doit respecter les politiques déterministes."
            else:
                reply = "I cannot bypass spending rules or override product prices. All purchases must strictly conform to deterministic policy rules."
            return reply, None, []

        # 3. Product mapping keywords
        target_product_id: Optional[str] = None
        quantity = 1

        # Check for explicit quantity
        qty_match = re.search(r"\b(\d+)\s*(units?|items?|piezas?|exemplaires?)?\b", msg)
        if qty_match:
            try:
                parsed_qty = int(qty_match.group(1))
                if 1 <= parsed_qty <= 10:
                    quantity = parsed_qty
            except Exception:
                quantity = 1

        if any(w in msg for w in ["headphone", "auricular", "casque", "audio", "noise-cancelling"]):
            target_product_id = "prod-001"
        elif any(w in msg for w in ["keyboard", "teclado", "clavier", "gaming", "mechanical"]):
            target_product_id = "prod-002"
        elif any(w in msg for w in ["charger", "cargador", "chargeur", "gan", "usb-c"]):
            target_product_id = "prod-003"
        elif any(w in msg for w in ["pillow", "almohada", "oreiller", "cervical", "sleep"]):
            target_product_id = "prod-004"
        elif any(w in msg for w in ["bottle", "botella", "bouteille", "flask", "water"]):
            target_product_id = "prod-005"
        elif any(w in msg for w in ["mug", "taza", "tasse", "coffee", "café"]):
            target_product_id = "prod-006"
        elif any(w in msg for w in ["pragmatic", "pragmático", "pragmatique"]):
            target_product_id = "prod-007"
        elif any(w in msg for w in ["data-intensive", "distributed", "datos", "données"]):
            target_product_id = "prod-008"
        elif any(w in msg for w in ["sleeve", "funda", "housse", "laptop", "leather"]):
            target_product_id = "prod-009"
        elif any(w in msg for w in ["sunglasses", "gafas", "lunettes", "polarized", "uv"]):
            target_product_id = "prod-010"

        # Execute read-only tool
        if target_product_id:
            executed_tools.append("get_product_details")
            details = self.tool_manager.execute_tool("get_product_details", {"product_id": target_product_id})
            
            if details.get("available") is False:
                # Inform item unavailable
                if language == "es":
                    reply = (
                        f"El producto '{details['name']}' ({details['category']}) tiene un precio de INR {details['price']}, "
                        f"pero actualmente está fuera de stock (agotado). Te sugiero considerar una alternativa disponible."
                    )
                elif language == "fr":
                    reply = (
                        f"Le produit '{details['name']}' ({details['category']}) est au prix de INR {details['price']}, "
                        f"mais il est actuellement en rupture de stock. Je vous suggère une alternative disponible."
                    )
                else:
                    reply = (
                        f"The '{details['name']}' ({details['category']}) is priced at INR {details['price']}, "
                        f"but is currently out of stock. Would you like me to suggest an available alternative?"
                    )
                return reply, None, executed_tools

            # Item available: recommend proposal
            if language == "es":
                reply = (
                    f"Te recomiendo '{details['name']}' de la categoría {details['category']}. "
                    f"El precio unitario oficial es de INR {details['price']} y se encuentra disponible. "
                    f"He generado una propuesta de compra para {quantity} unidad(es) que queda pendiente de tu aprobación."
                )
                reason = f"Cumple con tus requisitos de {details['category']} al precio de INR {details['price']}."
            elif language == "fr":
                reply = (
                    f"Je vous recommande '{details['name']}' dans la catégorie {details['category']}. "
                    f"Le prix unitaire officiel est de INR {details['price']} et l'article est en stock. "
                    f"J'ai préparé une proposition d'achat pour {quantity} unité(s), en attente de votre validation."
                )
                reason = f"Correspond à votre recherche en {details['category']} au prix de INR {details['price']}."
            else:
                reply = (
                    f"I recommend the '{details['name']}' from our {details['category']} collection. "
                    f"The authoritative price is INR {details['price']} and it is currently in stock. "
                    f"I have created a purchase proposal for {quantity} unit(s), pending your explicit confirmation."
                )
                reason = f"Matches user inquiry in {details['category']} at INR {details['price']}."

            proposal_intent = AIProposalIntent(
                product_id=target_product_id,
                quantity=quantity,
                recommendation_reason=reason,
            )
            return reply, proposal_intent, executed_tools

        # General search fallback
        executed_tools.append("search_catalogue")
        search_results = self.tool_manager.execute_tool("search_catalogue", {"query": user_message[:50]})

        if search_results.get("count", 0) > 0:
            p_list = ", ".join([p["name"] for p in search_results["products"][:2]])
            if language == "es":
                reply = f"Encontré los siguientes productos relacionados en el catálogo: {p_list}. ¿Deseas más detalles sobre alguno?"
            elif language == "fr":
                reply = f"J'ai trouvé ces articles pertinents dans le catalogue : {p_list}. Souhaitez-vous des détails sur l'un d'eux ?"
            else:
                reply = f"I found the following matching items in our catalogue: {p_list}. Would you like more details on any of these?"
            return reply, None, executed_tools

        if language == "es":
            reply = "No encontré productos que coincidan con tu búsqueda en el catálogo demo. ¿Podrías intentar con otros términos como electrónica, libros u hogar?"
        elif language == "fr":
            reply = "Je n'ai trouvé aucun article correspondant dans le catalogue de démonstration. Pourriez-vous préciser avec des catégories comme l'électronique ou les livres ?"
        else:
            reply = "I couldn't find any items matching your search in the demo catalogue. Could you try searching for terms like electronics, books, home, or accessories?"
        return reply, None, executed_tools


class GeminiAIProvider(BaseAIProvider):
    """Live Google Gemini API provider using the documented REST API with httpx."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        tool_mgr: Optional[CatalogueToolManager] = None,
    ) -> None:
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.model = model or settings.GEMINI_MODEL
        self.tool_manager = tool_mgr or catalogue_tool_manager

    async def generate_response(
        self,
        user_message: str,
        conversation_history: list[ChatMessage],
        language: str = "en",
    ) -> tuple[str, Optional[AIProposalIntent], list[str]]:
        if not self.api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not configured. Either provide an API key in your environment "
                "or set AI_PROVIDER=mock for offline development."
            )

        executed_tools: list[str] = []
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"

        system_instruction = build_system_prompt(language)
        tool_declarations = self.tool_manager.get_tool_definitions()

        # Build contents array
        contents: list[dict[str, Any]] = []
        for msg in conversation_history[-10:]:
            contents.append({
                "role": "model" if msg.role == "assistant" else "user",
                "parts": [{"text": msg.content}],
            })
        contents.append({"role": "user", "parts": [{"text": user_message}]})

        # Request payload
        payload: dict[str, Any] = {
            "system_instruction": {
                "parts": [{"text": system_instruction}]
            },
            "contents": contents,
            "tools": [{"function_declarations": tool_declarations}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 800,
            },
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            for _ in range(MAX_TOOL_CALL_ROUNDS):
                try:
                    resp = await client.post(endpoint, json=payload)
                except httpx.TimeoutException:
                    raise RuntimeError("Gemini API request timed out after 15 seconds.")
                except Exception as exc:
                    raise RuntimeError(f"Network error calling Gemini API: {str(exc)}")

                if resp.status_code == 429:
                    raise RuntimeError("Gemini API rate limit exceeded (HTTP 429). Please retry shortly.")
                if resp.status_code != 200:
                    logger.error("Gemini API error HTTP %d: %s", resp.status_code, resp.text)
                    raise RuntimeError(f"Gemini API returned error HTTP {resp.status_code}.")

                data = resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    return "No response candidate was returned by the AI provider.", None, executed_tools

                model_parts = candidates[0].get("content", {}).get("parts", [])
                function_call = None
                for part in model_parts:
                    if "functionCall" in part:
                        function_call = part["functionCall"]
                        break

                if not function_call:
                    # Final text response reached
                    text_parts = [p.get("text", "") for p in model_parts if "text" in p]
                    final_text = "\n".join(text_parts).strip()
                    proposal_intent = self._extract_proposal_intent(final_text)
                    return final_text, proposal_intent, executed_tools

                # Execute tool
                tool_name = function_call.get("name", "")
                args = function_call.get("args", {})
                executed_tools.append(tool_name)
                tool_result = self.tool_manager.execute_tool(tool_name, args)

                # Append assistant function call and tool response to conversation
                contents.append({
                    "role": "model",
                    "parts": [{"functionCall": function_call}]
                })
                contents.append({
                    "role": "function",
                    "parts": [{
                        "functionResponse": {
                            "name": tool_name,
                            "response": tool_result,
                        }
                    }]
                })
                payload["contents"] = contents

        return "Tool execution exceeded maximum permitted rounds.", None, executed_tools

    def _extract_proposal_intent(self, text: str) -> Optional[AIProposalIntent]:
        """Attempt to parse structured proposal intent from model completion if present."""
        try:
            # Look for JSON block in markdown
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            raw_json = match.group(1) if match else None
            if not raw_json and "product_id" in text:
                # Try finding bare JSON object
                json_match = re.search(r"(\{.*\"product_id\".*?\})", text, re.DOTALL)
                if json_match:
                    raw_json = json_match.group(1)

            if raw_json:
                data = json.loads(raw_json)
                if "product_id" in data:
                    return AIProposalIntent(
                        product_id=data["product_id"],
                        quantity=int(data.get("quantity", 1)),
                        recommendation_reason=data.get("recommendation_reason", "Recommended by assistant."),
                    )
        except Exception:
            pass
        return None


class AIService:
    """Orchestrator for multilingual AI assistance, catalogue tools, and policy checks."""

    def __init__(
        self,
        provider: Optional[BaseAIProvider] = None,
        catalogue: Optional[CatalogueService] = None,
        policy: Optional[PolicyService] = None,
    ) -> None:
        self.catalogue_service = catalogue or catalogue_service
        self.policy_service = policy or policy_service
        self.provider = provider or self._resolve_provider()

    def _resolve_provider(self) -> BaseAIProvider:
        prov_type = settings.AI_PROVIDER.lower()
        if prov_type == "gemini":
            return GeminiAIProvider()
        return MockAIProvider()

    async def chat(self, request: AIChatRequest) -> AIChatResponse:
        """Process chat request, invoke provider, and evaluate policy if proposal is recommended."""
        try:
            reply_text, proposal_intent, tools_used = await self.provider.generate_response(
                user_message=request.message,
                conversation_history=request.conversation_history,
                language=request.language,
            )
        except RuntimeError as exc:
            logger.error("AI Provider error: %s", exc)
            return AIChatResponse(
                reply_text=f"AI Assistant Error: {str(exc)}",
                language=request.language,
                recommended_proposal=None,
                evaluated_policy_result=None,
                tool_calls_executed=[],
            )

        policy_result: Optional[PolicyEvaluationResult] = None

        # If a proposal is recommended and a mandate ID is provided, evaluate policy
        if proposal_intent and request.mandate_id:
            # Independent backend policy evaluation strictly uses authoritative catalogue data
            policy_result = self.policy_service.evaluate_proposal(
                mandate_id=request.mandate_id,
                product_id=proposal_intent.product_id,
                quantity=proposal_intent.quantity,
            )

        return AIChatResponse(
            reply_text=reply_text,
            language=request.language,
            recommended_proposal=proposal_intent,
            evaluated_policy_result=policy_result,
            tool_calls_executed=tools_used,
        )


# Shared singleton AI service
ai_service = AIService()
