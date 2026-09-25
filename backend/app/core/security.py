"""Session cookies, OAuth state, and encryption of stored GitHub tokens.

- Session: a signed JWT (HS256, SESSION_SECRET) in an HttpOnly cookie. It only
  carries our user id; GitHub tokens never reach the browser.
- OAuth state: a random value, sent to GitHub and also kept in a short-lived
  signed cookie. The callback must present both, proving the login was started
  by this browser (CSRF protection for the login flow).
- Stored GitHub user tokens are Fernet-encrypted: a database leak alone doesn't
  hand out working GitHub credentials.
"""

import hmac
import secrets
import time
from dataclasses import dataclass

import jwt
from cryptography.fernet import Fernet, InvalidToken

SESSION_COOKIE = "rp_session"
STATE_COOKIE = "rp_oauth_state"
STATE_TTL_SECONDS = 10 * 60
ALGORITHM = "HS256"


@dataclass(frozen=True)
class SessionTokens:
    secret: str
    ttl_seconds: int

    def issue(self, user_id: int, *, now: float | None = None) -> str:
        issued = int(now if now is not None else time.time())
        claims = {
            "sub": str(user_id),
            "typ": "session",
            "iat": issued,
            "exp": issued + self.ttl_seconds,
        }
        return jwt.encode(claims, self.secret, algorithm=ALGORITHM)

    def user_id(self, token: str | None) -> int | None:
        """The user id from a valid session token, or None (missing, forged, expired)."""
        claims = _decode(token, self.secret, "session")
        subject = str(claims.get("sub", "")) if claims else ""
        return int(subject) if subject.isdigit() else None


@dataclass(frozen=True)
class OAuthState:
    secret: str

    def issue(self) -> tuple[str, str]:
        """(state for the GitHub URL, signed value for the state cookie)."""
        state = secrets.token_urlsafe(32)
        now = int(time.time())
        cookie = jwt.encode(
            {"state": state, "typ": "oauth_state", "iat": now, "exp": now + STATE_TTL_SECONDS},
            self.secret,
            algorithm=ALGORITHM,
        )
        return state, cookie

    def matches(self, state: str | None, cookie: str | None) -> bool:
        claims = _decode(cookie, self.secret, "oauth_state")
        if not claims or not state:
            return False
        return hmac.compare_digest(str(claims.get("state", "")), state)


def _decode(token: str | None, secret: str, expected_type: str) -> dict[str, object] | None:
    if not token:
        return None
    try:
        claims: dict[str, object] = jwt.decode(token, secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    # A state token must never work as a session token (and vice versa).
    return claims if claims.get("typ") == expected_type else None


class TokenCipher:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str | None:
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except InvalidToken:  # wrong key (rotated?) or tampered data
            return None
