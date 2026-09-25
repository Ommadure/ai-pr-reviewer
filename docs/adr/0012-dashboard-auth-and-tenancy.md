# ADR 0012: Dashboard login, sessions, and tenant isolation

- **Status:** Accepted
- **Date:** 2026-09-25

## Context
The dashboard shows review history for every installation of the App. One deployment can serve many GitHub accounts and organisations (tenants). A user must only ever see the installations *they* have access to on GitHub. We also want to hold as few long-lived credentials as possible, and none of them in the browser.

## Decision
**Login uses the GitHub App's own user-authorization flow**, not a separate OAuth App:
- `/auth/github/login` puts a random `state` in the GitHub URL, and also in a signed, 10-minute, `HttpOnly` cookie scoped to `/api/v1/auth`. The callback requires both to match (constant-time comparison), which blocks login CSRF.
- The code is exchanged for a user access token plus a refresh token. GitHub App user tokens expire after about 8 hours, and we refresh them 5 minutes before expiry. Both tokens are stored **Fernet-encrypted** (`ENCRYPTION_KEY`); a database dump alone doesn't hand out working GitHub credentials.
- The browser gets a **session cookie**: an HS256 JWT holding only our user id, `HttpOnly` (JavaScript can't read it), `SameSite=Lax` (not sent on cross-site POSTs), `Secure` whenever we serve HTTPS, 7-day expiry. GitHub tokens never reach the browser.

**Tenancy** means *GitHub decides who sees what*:
- A user may see an installation's data only if `GET /user/installations`, called **with that user's token**, lists it.
- The answer is cached for 5 minutes in `user_installations`, and cleared on login.
- **Every** dashboard query takes the allowed installation ids and filters through `Repository.installation_id`, in one place (`app/repositories/dashboard.py`). No endpoint queries without a scope.
- Anything outside the scope returns **404, never 403**, so an outsider can't even confirm that an id exists.
- If GitHub can't be asked, the request **fails closed** with a 503. If the token can't be refreshed, the user is signed out (401).

**Same origin:** the SPA calls relative `/api/*` paths. In development Vite proxies them to FastAPI; in production a Vercel rewrite does the same. The session cookie is therefore first-party, which avoids third-party cookie blocking and any need for CORS.

## Consequences
- **Removing someone's access on GitHub removes it here** within 5 minutes, with no user management of our own.
- **Tenancy is a single, testable rule.** Tests prove user A gets 404 from every endpoint for user B's installation, repository, PR and run. They also cover mutations (repository toggle, re-review), which change nothing.
- **Cost of the design:** each dashboard session calls GitHub at most once every 5 minutes, and dashboard access depends on GitHub being up (fail-closed by design).
- **Any member can toggle repositories.** Anyone who can see an installation can turn reviews on or off for its repositories. Restricting that to repository admins would need a per-repo permission check, which we defer.
