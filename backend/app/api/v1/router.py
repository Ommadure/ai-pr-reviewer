from fastapi import APIRouter

from app.api.v1.routes import auth, dashboard, health, webhooks

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(webhooks.router)
api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
