# Security review

This is the checklist from the spec (section 18), reviewed in Phase 7 on 2026-09-26. Each item names the code that enforces it and the test that proves it, so a claim here can be checked, not just trusted.

## Threat model in one paragraph
- **PR content is attacker-controlled.** Anyone who can open a pull request on an installed repository controls its diff, title, description and code comments.
- **GitHub is trusted only through signatures.** Webhooks are authenticated by HMAC, and user access is decided by GitHub itself (ADR 0012).
- **Protected assets:**
  - the App's private key and its installation tokens (write access to customers' PRs);
  - dashboard users' GitHub tokens;
  - customers' private source code;
  - the LLM budget.

## Checklist

| # | Requirement | Where it's enforced | Proof |
| --- | --- | --- | --- |
| 1 | Webhook HMAC verified in constant time, on the raw body | `app/github/signatures.py` (`hmac.compare_digest`); `routes/webhooks.py` verifies before parsing JSON; the body is capped at 25 MB | `tests/unit/test_signatures.py` (GitHub's documented example, tampered body, empty secret never validates); `test_webhooks.py::test_unsigned_or_badly_signed_requests_are_rejected` |
| 2 | Least-privilege App; never approves, requests changes or merges | Permissions in `docs/github-app-setup.md`; every review is posted with `event: "COMMENT"` (`app/github/client.py`, ADR 0006); no merge endpoint is ever called | Code search: `APPROVE`, `REQUEST_CHANGES` and `/merge` appear in `app/` only in the comment that rules them out (`client.py`) |
| 3 | Secrets only in env, never logged | `pydantic.SecretStr` for every secret (`app/core/config.py`); the app fails fast if they're missing; log calls carry ids, never tokens or bodies; the Gemini key goes in a header, never a URL; Sentry strips headers, cookies, bodies, query strings and locals (`app/core/observability.py`) | `test_config.py::test_secrets_are_masked_when_printed`; `test_observability.py::test_sentry_events_are_scrubbed_and_tagged`; `test_llm.py` (key not in URL); Phase 7 grep of every log call |
| 4 | User OAuth tokens encrypted at rest | Fernet (`TokenCipher`, `ENCRYPTION_KEY`), applied to access and refresh tokens | `test_dashboard.py::test_full_oauth_login_flow` (the stored value isn't the token; it decrypts back) |
| 5 | OAuth `state` validated; session cookie HttpOnly, Secure, SameSite | A signed, 10-minute state cookie scoped to `/api/v1/auth`, compared in constant time; session is an HS256 JWT in an `HttpOnly; SameSite=Lax` cookie, `Secure` in production or over HTTPS | `test_dashboard.py::test_callback_rejections`, `::test_session_is_required_and_verified`; `tests/unit/test_cookie_security.py`; checked by hand on the production image |
| 6 | Tenancy enforced in the repository layer, and tested | Every dashboard query takes the caller's allowed installation ids (`app/repositories/dashboard.py`); an out-of-scope id returns 404, never 403; fails closed if GitHub can't be asked | `test_dashboard.py::test_every_endpoint_hides_other_tenants`, `::test_users_only_see_their_own_installations`, `::test_analytics_only_counts_the_callers_data` |
| 7 | Secrets redacted before any LLM call; findings never echo the value | `app/review/redaction.py` runs before filtering, chunking or prompting; the secret-scanner comment names the *kind* of secret only | `tests/unit/review/test_redaction.py`; `test_engine.py::test_secrets_are_redacted_and_flagged_without_the_llm` |
| 8 | Prompt-injection defences | Untrusted content goes inside `<pr_metadata>`/`<diff>` delimiters that PR text can't close (`neutralize`); the system prompt forbids following instructions inside them; output is schema-restricted JSON; the validator drops comments on lines outside the diff; the sanitizer removes @mentions, images, HTML and non-GitHub links; the bot only ever comments | `test_prompt_and_chunker.py::test_pr_content_cannot_close_our_delimiters`, `::test_template_loading_rejects_path_tricks`; `test_validator.py`; `test_fingerprint_sanitizer_pricing.py::test_sanitize_markdown` |
| 9 | Config read from the default branch only; `yaml.safe_load`; size limits | `app/services/config_loader.py` reads the default branch at a pinned commit (ADR 0009); 20 KB file cap, 20 rules × 300 characters, invalid config falls back to defaults | `tests/unit/review/test_repo_config.py` |
| 10 | Bot-sender events ignored (no loops) | `webhook_router._sent_by_bot`: sender type `Bot` or our own bot login | `test_webhooks.py::test_events_from_bots_are_ignored` |
| 11 | Slash commands permission-checked and rate limited | Role check via the collaborator permission API; Redis fixed window, 5 manual reviews per PR per hour | `test_commands.py::test_privileged_commands_need_write_access`, `::test_manual_reviews_are_rate_limited`; `test_dashboard.py::test_rereview_queues_a_run_and_is_rate_limited` |
| 12 | Per-run token and cost caps | Input budget (`REVIEW_MAX_INPUT_TOKENS`, `REVIEW_MAX_CHUNK_TOKENS`, `REVIEW_MAX_FILES`); **new in Phase 7:** a per-call output ceiling (`REVIEW_MAX_OUTPUT_TOKENS`, sent as Gemini `maxOutputTokens`) and a per-run cost cap (`REVIEW_MAX_COST_USD`). The cost guard reserves each call's worst-case cost first, so concurrent calls can't cross the cap | `tests/unit/review/test_cost_cap.py`; `test_llm.py::test_gemini_caps_output_tokens_when_asked`; `test_prompt_and_chunker.py` (budget limits) |
| 13 | Dependencies audited in CI | `pip-audit` on the hash-pinned production lock, and `npm audit --omit=dev --audit-level=high`, on every push and weekly; Dependabot for uv, npm and Actions | `.github/workflows/ci.yml`, `.github/dependabot.yml`; clean on 2026-09-26 |

## Found and fixed during Phase 7
- **No output-token ceiling.** A runaway or adversarially long response could bill up to the model's own limit. Fixed by row 12.
- **No cost cap.** Only the input side was bounded. Fixed by row 12.
- **Log context didn't reach nested code.** GitHub-client and LLM retry logs had no `run_id`, `repo` or `delivery_id`, which hampered incident tracing. Fixed with per-request and per-task structlog contextvars.
- **Handled errors never reached Sentry.** Exceptions the webhook handler catches (to return a clean 500 or 503) were invisible to it. `log.exception` / `log.error` now report to Sentry when it's configured.

(Phase 6's eval also found and fixed a crash on any diff containing `{{`, a robustness bug that affected React code.)

## Production hardening (ADR 0014, ADR 0015)
- **TLS everywhere:**
  - Neon `ssl=require`;
  - Upstash `rediss://` with `CERT_REQUIRED`;
  - Caddy on the VM (automatic Let's Encrypt, HTTP→HTTPS redirect, HSTS) and Vercel terminate HTTPS, and uvicorn trusts the proxy headers so cookies are `Secure`.
- **Dashboard headers** (`frontend/vercel.json`):
  - a strict CSP (scripts: `self` plus the SHA-256 of the one inline theme script, kept in sync by `frontend/deploy.test.ts`);
  - `frame-ancestors 'none'`, HSTS, `nosniff`, `Referrer-Policy` and `Permissions-Policy`.
- **API:**
  - no interactive docs in production (`/docs` returns 404);
  - no CORS (same-origin through the Vercel rewrite);
  - the request id is taken from a header only if it's safe (1–64 characters from `[A-Za-z0-9._-]`), so it can't be used for log injection.
- **VM** (`deploy/oracle/`):
  - the API container isn't published, so only Caddy can reach it;
  - only ports 22, 80 and 443 are open, in both the cloud Security List and the VM's iptables;
  - SSH is key-only, and OS security updates install automatically;
  - containers run as a non-root user, with memory caps and rotated logs;
  - the repo is cloned with a read-only deploy key, and the secrets file is `chmod 600` and git-ignored.

## Known limitations
- **Any member can toggle repositories.** Anyone who can see an installation can turn automatic reviews on or off for its repositories (ADR 0012). Restricting that to admins needs a per-repository permission check.
- **The free Gemini tier may use prompts for product improvement.** Only point the free tier at repositories whose code you can share (ADR 0003). A paid tier or self-hosted model avoids this.
- **The cost cap is only as good as `LLM_PRICING`.** An unpriced model counts as $0 and relies on the token caps alone.
