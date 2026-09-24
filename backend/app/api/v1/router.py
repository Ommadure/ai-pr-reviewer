from fastapi import APIRouter

from app.api.v1.routes import health, webhooks

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(webhooks.router)
