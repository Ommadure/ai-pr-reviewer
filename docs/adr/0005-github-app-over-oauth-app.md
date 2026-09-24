# ADR 0005: Integrate as a GitHub App, not an OAuth App or a bot user

- **Status:** Accepted
- **Date:** 2026-09-24

## Context
ReviewPilot must receive PR events, read diffs, post reviews, and create check runs on repositories it's installed on.

## Decision
Use a **GitHub App**:
- **Fine-grained, least-privilege permissions**, chosen per resource: Pull requests (write), Contents (read), Checks (write), Issues (write), Metadata (read).
- **Repo owners choose which repositories** it can access at install time.
- **Short-lived credentials.** A 10-minute App JWT is signed with our private key and exchanged for a 1-hour installation token. That token is cached in Redis until 5 minutes before it expires. No long-lived user token acts on repositories.
- **Its own bot identity** (`<slug>[bot]`), so comments aren't attributed to a person. The webhook router ignores events from bots, which is how we avoid reacting to our own comments.
- **Check runs are only available to GitHub Apps.** We need them for the "ReviewPilot: reviewing…" status.
- **Webhooks are configured once on the App**, not per repository.

## Consequences
- Every API call is made on behalf of a specific installation (`installation_client(installation_id)`), which scopes access naturally.
- Dashboard login (Phase 5) uses the same App's user-authorization (OAuth) flow, so there's one registration to manage.
- We manage a private key. It lives in an environment variable (base64), is never logged, and is validated at startup.

## Alternatives considered
- **OAuth App:** it acts as a user, with broad scopes (e.g. `repo` means all of a user's repos). It can't create check runs and needs webhooks set up per repo.
- **Personal access token on a bot account:** long-lived, over-privileged, and tied to one account's rate limit. It's also against the spirit of GitHub's terms for a multi-user service.
