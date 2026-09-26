"""Security checklist: the OAuth-state cookie is HttpOnly, SameSite=Lax, and Secure
whenever the dashboard is served over HTTPS (production)."""

import httpx
from fastapi import FastAPI

from app.core.config import Settings, get_settings


async def login_cookie(app: FastAPI, settings: Settings) -> str:
    app.dependency_overrides[get_settings] = lambda: settings
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/auth/github/login")
    assert response.status_code == 302
    return response.headers["set-cookie"].lower()


async def test_state_cookie_is_secure_over_https(app: FastAPI, settings: Settings) -> None:
    https = settings.model_copy(
        update={
            "app_base_url": "https://reviewpilot.example",
            "frontend_url": "https://reviewpilot.example",
        }
    )
    cookie = await login_cookie(app, https)
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" in cookie
    assert "path=/api/v1/auth" in cookie  # never sent anywhere else


async def test_production_always_sets_secure(app: FastAPI, settings: Settings) -> None:
    production = settings.model_copy(update={"app_env": "production"})
    assert production.secure_cookies
    assert "secure" in await login_cookie(app, production)


async def test_plain_http_localhost_can_still_sign_in(app: FastAPI, settings: Settings) -> None:
    # Browsers drop Secure cookies on http://localhost, so dev must not set it.
    assert "secure" not in await login_cookie(app, settings)
