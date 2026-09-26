# ADR 0015: Run the backend on an Oracle Cloud Always Free VM

- **Status:** Accepted. It supersedes the Render compute part of [ADR 0014](0014-deployment-topology.md); Neon, Upstash, Vercel and the Redis budget stay.
- **Date:** 2026-09-26

## Context
- **Render isn't free for us.** It has no free background workers, and the smallest paid one is about $7 a month. The goal is $0.
- **Oracle Always Free gives a whole VM at no charge:**
  - **Ampere A1 (arm64):** 2 OCPUs and 12 GB of RAM in total, *or*
  - **AMD micro:** two VMs with 1/8 OCPU and 1 GB each;
  - 200 GB of disk and 10 TB of egress a month;
  - all only in the account's **home region**, which is chosen once, at signup.
- **Oracle may reclaim idle free VMs:** 95th-percentile CPU, network and (on A1) memory all under 20% for 7 days.

([Oracle: Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), checked 2026-09-26.)

## Decision
- **One VM, home region Singapore (`ap-singapore-1`)**, next to Neon and Upstash (ADR 0014's latency reasoning).
  - Preferred shape: **A1.Flex, 1 OCPU / 2 GB**, which leaves the rest of the free quota unused.
  - Fallback if A1 has no capacity: **E2.1.Micro** plus 2 GB of swap. The whole stack measured about **290 MB** (API 89, worker 184, Caddy 18), so it fits either shape.
- **Docker Compose on the VM** (`deploy/oracle/compose.yaml`):
  - **`api`**, not published: it's only reachable by Caddy on the compose network. It migrates on start.
  - **`worker`**, with **the one embedded beat**: a single container, so there's still exactly one beat by construction. It starts only after the API is healthy, so the schema is migrated first.
  - **`caddy`**, which terminates **HTTPS with automatic Let's Encrypt** certificates. It redirects HTTP to HTTPS, sets HSTS, allows bodies up to 26 MB (webhooks) and strips the `Server` header.
  - Every service has memory caps, log rotation and `restart: unless-stopped`.
- **A free hostname from DuckDNS** (`<name>.duckdns.org` → the VM's IP). Let's Encrypt needs a domain; a raw IP can't get a certificate.
- **Neon and Upstash stay** (managed, backed up, TLS). The VM holds no data, so it can be rebuilt from scratch.
- **The repository stays private:** the VM clones with a **read-only deploy key**.
- **Updates:** `deploy/oracle/deploy.sh` does `git pull --ff-only`, `compose up -d --build` and an image prune.
- **Idle-reclaim protection:** upgrade the account to Pay As You Go, where Always Free resources stay free, and set a $1 budget alert. A small A1 allocation also keeps memory use above 20%.

## Consequences
- **Cost:** $0. Oracle needs a card for identity verification, but Always Free resources are never charged.
- **We operate a server now**, which a platform used to handle:
  - OS security updates are automatic (`unattended-upgrades`);
  - the firewall is two layers (the cloud Security List and the VM's iptables), and `bootstrap.sh` sets the VM layer;
  - SSH is key-only, which is Oracle's default.
- **More headroom than Render's free API:** no sleeping (so no keep-warm), and no 512 MB cap shared with a worker.
- **Single point of failure:** if the VM goes down, reviews pause. GitHub keeps failed deliveries for redelivery, and dedupe makes redelivery safe.
- **Deploys are manual** (`deploy.sh` over SSH). A GitHub Actions deploy over SSH can come later.
- **Render stays a documented alternative:** `render.yaml` still describes it, with a paid worker.
