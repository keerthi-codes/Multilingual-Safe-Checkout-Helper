"""Main entrypoint for the FastAPI application."""

from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.catalogue.router import router as catalogue_router
from app.policy.router import router as policy_router
from app.ai.router import router as ai_router
from app.payment.router import router as payment_router

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Multilingual Safe-Checkout Helper backend service",
    version="0.1.0",
    debug=settings.DEBUG,
)

# Register product catalogue, spending policy, AI assistant, and PayPal payment routers
app.include_router(catalogue_router, prefix="/api/v1/products", tags=["Catalogue"])
app.include_router(policy_router, prefix="/api/v1/mandates", tags=["Spending Mandates"])
app.include_router(ai_router, prefix="/api/v1/ai", tags=["AI Assistant"])
app.include_router(payment_router, prefix="/api/v1/payments", tags=["PayPal Payments"])




@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint indicating service operational status."""
    return {
        "status": "ok",
        "app": settings.PROJECT_NAME,
        "environment": settings.ENVIRONMENT,
    }


# Mount minimal frontend static assets
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.exists():
    if (FRONTEND_DIR / "css").exists():
        app.mount("/css", StaticFiles(directory=FRONTEND_DIR / "css"), name="css")
    if (FRONTEND_DIR / "js").exists():
        app.mount("/js", StaticFiles(directory=FRONTEND_DIR / "js"), name="js")

    @app.get("/", include_in_schema=False)
    async def serve_index():
        """Serve the frontend index HTML page."""
        return FileResponse(FRONTEND_DIR / "index.html")
