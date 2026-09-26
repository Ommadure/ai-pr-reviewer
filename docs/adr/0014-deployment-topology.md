# ADR 0014: Production deployment on Render, Neon, Upstash and Vercel

- **Status:** Accepted. The Render compute part is superseded by [ADR 0015](0015-oracle-always-free-vm.md) (Oracle Always Free VM); Neon, Upstash, Vercel and the Redis budget still apply.
- **Date:** 2026-09-26

## Context
Production needs:
- a webhook API that answers within GitHub's **10-second** timeout;
- a worker that runs reviews, plus **exactly one** beat scheduler;
- Postgres, Redis and the static dashboard;
- all of it for close to $0, because this is a portfolio project.

What the hosts offered (checked 2026-09-26):
- **Render.** Free web services sleep after 15 idle minutes and take about a minute to wake. Workers have no free plan. Free Postgres expires after 30 days.
- **Neon.** Free, serverless Postgres that suspends when idle and wakes in about a second.
- **Upstash Redis.** Free up to **500K commands a month** (about 11 a minute).
- **Vercel.** Free static hosting, with rewrites.

## Decision
- **API: a Render web service on the free plan** (`render.yaml`).
  - It runs `alembic upgrade head` and then uvicorn. With a single instance, migrations can't race.
  - uvicorn has `--proxy-headers` on, because Render's proxy terminates TLS.
- **Worker: a Render background worker on the smallest paid plan** (about $7 a month).
  - It runs **celery beat embedded** (`-B`) with `numInstances: 1`, so there is exactly one beat by construction.
  - The worker is the only always-on process, so it also runs **keep-warm**: every 10 minutes it GETs the API's `/health` (`KEEP_WARM_URL`), and the free API never sleeps through a webhook. 24/7 is about 744 hours, inside Render's 750 free hours a month.
- **Postgres: Neon.**
  - `DATABASE_URL` accepts Neon's string as shown. Settings normalise `postgresql://…?sslmode=require&channel_binding=require` to asyncpg's `postgresql+asyncpg://…?ssl=require`.
  - `pool_pre_ping` discards connections that Neon closed while suspended.
- **Redis: Upstash over TLS** (`rediss://`). Celery gets `ssl_cert_reqs=CERT_REQUIRED`: the certificate is always verified.
- **A measured Redis budget.** An idle worker with Celery defaults sent **~115 commands a minute** (5M a month, 10× the free tier):
  - one `BRPOP` a second, the queue poll;
  - about 31 `PUBLISH` a minute, task events that only Flower uses.

  With `CELERY_POLL_SECONDS=30`, `CELERY_TASK_EVENTS=false`, `task_ignore_result`, health checks scaled to 120 s, and `--without-heartbeat/gossip/mingle`, it's **~5 a minute** (~230K a month idle). A longer poll adds **no latency**, because `BRPOP` returns as soon as a task arrives. Local development keeps the defaults for Flower.
- **Dashboard: Vercel.**
  - `vercel.json` rewrites `/api/*` to the Render API. The browser therefore only ever talks to one origin, and the session and OAuth-state cookies stay first-party (ADR 0012).
  - The GitHub App's OAuth callback goes through the Vercel domain. The **webhook URL goes straight to Render**, one hop fewer, and GitHub signs it anyway.
  - It sets a strict CSP (scripts: `self` plus the hash of the one inline theme script, checked by a test), `frame-ancestors 'none'`, HSTS and `nosniff`.
- **Deploys** use `autoDeployTrigger: checksPass`: Render ships only commits whose CI passed.

## Consequences
- **Cost:** about $7 a month, for the worker. Everything else is on free tiers.
- **Spare capacity:** keep-warm uses most of Render's free hours, so a second free web service in the same workspace would run out of hours.
- **Upstash headroom:** the idle budget leaves about half of Upstash's free quota for real traffic. A busy install should move to Upstash pay-as-you-go (~$0.20 per 100K commands) or Render Key Value (not billed per command, not persistent on free).
- **Broker restarts:** Redis is only a broker and a cache. Run state lives in Postgres, so a broker restart loses at most queued tasks, and `mark_stuck_runs` fails those after 30 minutes.
- **Scaling the worker:** set `-B` off, run a separate single `celery beat` service, and scale workers freely. The rule of one beat still holds.
