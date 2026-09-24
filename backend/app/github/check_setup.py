"""Verify the GitHub App configuration in backend/.env before starting the stack.

    cd backend && uv run python -m app.github.check_setup

Authenticates as the App (JWT) and checks, against GitHub itself:
the key works, the slug matches GITHUB_APP_SLUG (needed to ignore our own
events), the permissions and webhook events are the ones ReviewPilot needs,
and the App is installed somewhere. Prints no secrets.
"""

import asyncio
import sys
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.core.config import Settings
from app.github.app_auth import GitHubAppAuth
from app.github.client import GitHubClient, GitHubError, create_http_client

REQUIRED_PERMISSIONS = {
    "pull_requests": "write",
    "contents": "read",
    "checks": "write",
    "issues": "write",
    "metadata": "read",
}
REQUIRED_EVENTS = {"pull_request", "issue_comment"}
ACCESS_RANK = {"read": 1, "write": 2, "admin": 3}
MIN_WEBHOOK_SECRET_LENGTH = 20


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    failed: bool = False

    def ok(self, message: str) -> None:
        self.lines.append(f"  ✓ {message}")

    def fail(self, message: str) -> None:
        self.lines.append(f"  ✗ {message}")
        self.failed = True

    def warn(self, message: str) -> None:
        self.lines.append(f"  ! {message}")


async def check(settings: Settings, client: GitHubClient) -> Report:
    report = Report()
    if len(settings.github_webhook_secret.get_secret_value()) >= MIN_WEBHOOK_SECRET_LENGTH:
        report.ok("webhook secret is set")
    else:
        report.fail(f"GITHUB_WEBHOOK_SECRET is shorter than {MIN_WEBHOOK_SECRET_LENGTH} characters")

    try:
        app: dict[str, Any] = (await client.request("GET", "/app")).json()
    except GitHubError as exc:
        report.fail(
            f"GitHub rejected the App credentials ({exc.status_code}: {exc.message}). Check "
            "GITHUB_APP_CLIENT_ID / GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY_B64."
        )
        return report
    report.ok(f"authenticated as the GitHub App '{app.get('name')}' (id {app.get('id')})")

    slug = app.get("slug")
    if slug == settings.github_app_slug:
        report.ok(f"GITHUB_APP_SLUG matches ({slug}); bot login is {settings.bot_login}")
    else:
        report.fail(
            f"GITHUB_APP_SLUG is '{settings.github_app_slug}' but the App's slug is '{slug}'. "
            "Fix it, or ReviewPilot won't recognise (and ignore) its own comments."
        )

    granted: dict[str, str] = app.get("permissions") or {}
    for name, needed in REQUIRED_PERMISSIONS.items():
        have = granted.get(name)
        if have and ACCESS_RANK.get(have, 0) >= ACCESS_RANK[needed]:
            report.ok(f"permission {name}: {have}")
        else:
            report.fail(f"permission {name} is '{have or 'none'}', needs '{needed}'")
    extra = sorted(set(granted) - set(REQUIRED_PERMISSIONS))
    if extra:
        report.warn(f"extra permissions not needed (least privilege): {', '.join(extra)}")

    events = set(app.get("events") or [])
    missing_events = REQUIRED_EVENTS - events
    if missing_events:
        report.fail(f"not subscribed to events: {', '.join(sorted(missing_events))}")
    else:
        report.ok(f"subscribed to events: {', '.join(sorted(REQUIRED_EVENTS))}")

    installations: list[dict[str, Any]] = await client.paginate("/app/installations")
    if installations:
        accounts = ", ".join(
            f"{(i.get('account') or {}).get('login')} (installation {i.get('id')})"
            for i in installations
        )
        report.ok(f"installed on: {accounts}")
    else:
        report.fail("the App isn't installed anywhere yet: install it on your test repository")
    return report


async def _main() -> int:
    try:
        settings = Settings()
    except ValidationError as exc:
        print("backend/.env is incomplete:")
        for error in exc.errors():
            print(f"  ✗ {error['msg'].removeprefix('Value error, ')}")
        return 1

    async with create_http_client() as http:
        auth = GitHubAppAuth(
            issuer=settings.github_app_jwt_issuer,
            private_key_pem=settings.github_app_private_key_pem,
            http=http,
            cache=_NoCache(),
        )

        async def app_jwt() -> str:
            return auth.create_jwt()

        report = await check(settings, GitHubClient(http, app_jwt))

    print("GitHub App setup check")
    print("\n".join(report.lines))
    print(
        "\nAll good: start the stack and open a PR." if not report.failed else "\nFix the ✗ items."
    )
    return 1 if report.failed else 0


class _NoCache:
    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        return None

    async def delete(self, key: str) -> None:
        return None


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
