# Deploying ReviewPilot

This sets up the production layout from [ADR 0015](adr/0015-oracle-always-free-vm.md), with Neon and Vercel from [ADR 0014](adr/0014-deployment-topology.md). Postgres is also the job queue, so there is no Redis ([ADR 0016](adr/0016-postgres-job-queue-in-process-worker.md)). Everything is free. It takes about an hour the first time.

| Piece | Where | Cost |
| --- | --- | --- |
| API with its in-process worker, HTTPS (Caddy) | one Oracle Cloud Always Free VM | $0 |
| Postgres (data and job queue) | Neon, free | $0 |
| Dashboard | Vercel, free | $0 |
| HTTPS hostname for the VM | DuckDNS, free | $0 |
| Errors (optional) | Sentry, free developer plan | $0 |

Sign in to each service with GitHub. Pick **Singapore / `ap-southeast-1`** wherever you're asked for a region, so every piece sits in the same place (lowest latency from India). Secrets go into each provider's dashboard only, never into the repo or a chat.

---

## 1. Neon (Postgres)
1. [console.neon.tech](https://console.neon.tech): **New project**. Name it `reviewpilot`, choose Postgres 16 and region **AWS Asia Pacific (Singapore)**.
2. On the dashboard, click **Connect**. Turn **Connection pooling off**, because we want the *direct* connection string, then copy it. It looks like `postgresql://…@ep-….ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require`.
3. Paste it as-is later. ReviewPilot converts it to asyncpg's format by itself.

## 2. Vercel (dashboard)
1. [vercel.com/new](https://vercel.com/new): **Import** the `ai-pr-reviewer` repository.
2. Set **Root Directory** to `frontend`. Vercel reads `frontend/vercel.json`, so framework, build command and output are already set.
3. Deploy, then open **Settings → Domains** and note the **production domain**, for example `https://reviewpilot-xxxx.vercel.app`. It's the dashboard's public address, so it goes into `APP_BASE_URL` and `FRONTEND_URL` below, and into the GitHub App in step 5.
   - **Don't use a deployment link** (`reviewpilot-<hash>-<team>.vercel.app`, the one GitHub's "View deployment" and Vercel's emails give you). Those sit behind Vercel's login. If you're signed in as another account, they say "This doesn't exist, or it belongs to an account you can't access".
   - If `<project>.vercel.app` is already taken by someone else, Vercel adds a suffix. Copy the domain from the dashboard; don't guess it.
4. **Settings → Deployment Protection:** set **Vercel Authentication** to *Only Preview Deployments* (or off). The dashboard must be public, because visitors and GitHub's sign-in redirect can't log in to your Vercel account.

   The dashboard can't sign anyone in until the API exists; that's expected at this step.

## 3. An encryption key (on your machine)
```bash
cd ~/Desktop/ai-pull-request-reviewer/backend && uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
Keep the output for step 4. Don't reuse your local `.env` key: production gets its own secrets.

## 4. Oracle Cloud VM (API and its worker)

### 4a. Account (one-time)
1. Sign up at [signup.cloud.oracle.com](https://signup.cloud.oracle.com). **Choose home region `Singapore (ap-singapore-1)`.** You can't change it later, and free resources only work in the home region. Singapore puts the VM next to Neon.
2. Oracle asks for a card **to verify your identity only**; Always Free resources are never charged.
3. **Recommended:** once the account is active, upgrade it to **Pay As You Go** (Billing → Upgrade), which is still $0 for Always Free resources. Then add a **$1 budget alert** (Billing → Budgets). Oracle can reclaim *idle* free VMs on accounts that aren't upgraded, and a small app like ours looks idle.

### 4b. The VM
1. **Compute → Instances → Create instance**, named `reviewpilot`.
2. **Image:** Canonical **Ubuntu 24.04**.
3. **Shape:** **Ampere → VM.Standard.A1.Flex, 1 OCPU, 2 GB memory** ("Always Free-eligible"). If it says *Out of capacity*, try again later or pick **AMD → VM.Standard.E2.1.Micro**; the stack fits in 1 GB (the bootstrap adds swap).
4. **Networking:** keep "Assign a public IPv4 address" on.
5. **SSH keys:** "Generate a key pair for me" → **save the private key**. On your laptop:
   ```bash
   mv ~/Downloads/ssh-key-*.key ~/.ssh/reviewpilot-vm.key && chmod 600 ~/.ssh/reviewpilot-vm.key
   ```
6. Create the instance, then copy its **Public IP address**.
7. **Open ports 80 and 443 in the cloud firewall:** on the instance page, click its **Subnet → Security List → Add Ingress Rules** and add two rules:
   - source `0.0.0.0/0`, TCP, destination port `80`;
   - source `0.0.0.0/0`, TCP, destination port `443`.

### 4c. A hostname: DuckDNS
Let's Encrypt needs a domain name for HTTPS; a bare IP can't get a certificate.
1. [duckdns.org](https://www.duckdns.org): sign in with GitHub and add a subdomain, e.g. `reviewpilot-om`.
2. Set its IP to the VM's public IP and click **update ip**.
3. Your API's address is now `https://reviewpilot-om.duckdns.org`. It's the `API_DOMAIN` below.

### 4d. Set up the VM
From your laptop, in the repository folder:
```bash
scp -i ~/.ssh/reviewpilot-vm.key deploy/oracle/bootstrap.sh ubuntu@<vm-ip>:
```
```bash
ssh -i ~/.ssh/reviewpilot-vm.key ubuntu@<vm-ip> 'bash bootstrap.sh'
```
It installs Docker, opens 80/443 in the VM's own firewall, turns on automatic security updates, adds swap on small VMs, and prints a **deploy key**.
- **Add the deploy key on GitHub:** repo **Settings → Deploy keys → Add deploy key**, titled `oracle-vm`. Paste the key and leave **Allow write access unchecked**. The VM can then clone this one private repo, read-only.

Then log in (this also picks up the docker group) and clone:
```bash
ssh -i ~/.ssh/reviewpilot-vm.key ubuntu@<vm-ip>
```
```bash
git clone github-reviewpilot:Ommadure/ai-pr-reviewer.git ~/ai-pr-reviewer && cd ~/ai-pr-reviewer && cp deploy/oracle/.env.production.example deploy/oracle/.env.production && nano deploy/oracle/.env.production
```

Fill in the file (it's git-ignored, and `deploy.sh` makes it readable by you only):

| Variable | Value |
| --- | --- |
| `API_DOMAIN` | your DuckDNS name, e.g. `reviewpilot-om.duckdns.org` |
| `APP_BASE_URL`, `FRONTEND_URL` | your Vercel URL, e.g. `https://reviewpilot-om.vercel.app` |
| `DATABASE_URL` | the Neon string (step 1) |
| `GITHUB_APP_*`, `GITHUB_WEBHOOK_SECRET` | the same values as your local `backend/.env`. A new webhook secret is better; if you make one, also set it on the App in step 5 |
| `SESSION_SECRET` | run `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` on the VM |
| `ENCRYPTION_KEY` | the key from step 3 |
| `GEMINI_API_KEY` | your Gemini key |

### 4e. Start it
```bash
~/ai-pr-reviewer/deploy/oracle/deploy.sh
```
The first build takes a few minutes. Caddy then fetches the HTTPS certificate by itself. Check it:
```bash
curl -s https://reviewpilot-om.duckdns.org/api/v1/ready
```
You should see `{"status":"ok","checks":{"database":"ok"}}`. After a quiet spell, the first check takes a few seconds, because Neon's free tier suspends the database after 5 idle minutes and the check waits for it to wake.

If a check says `error`, the API log says which one and why (`ready.check_failed`, with the error type):
```bash
docker compose -f ~/ai-pr-reviewer/deploy/oracle/compose.yaml logs api | grep ready.check_failed
```

## 5. Point the dashboard and the GitHub App at the VM
**Vercel.** In `frontend/vercel.json`, change the `/api` rewrite's `destination` to your API:
```json
"destination": "https://reviewpilot-om.duckdns.org/api/:path*"
```
Commit and push. Vercel redeploys the dashboard by itself.

**GitHub App.** On github.com, go to **Settings → Developer settings → GitHub Apps → your app → General**:
- **Webhook URL:** `https://reviewpilot-om.duckdns.org/api/v1/webhooks/github`. This goes straight to the VM, not through Vercel.
- **Callback URL:** add `https://<your-vercel-url>/api/v1/auth/github/callback`. Keep the localhost one too; a GitHub App can have several.
- **Homepage URL:** your Vercel URL.

This moves your App's webhooks from smee (local) to production. To develop locally again, point the Webhook URL back to smee, or create a second "dev" App for local work. A separate dev App is the tidier long-term setup.

## 6. Check it works end to end
1. Open the Vercel URL and click **Sign in with GitHub**. You should land on the Overview with your installation.
2. Open (or push to) a pull request on your test repository. On the VM, watch the API (the worker logs there too) with `docker compose -f ~/ai-pr-reviewer/deploy/oracle/compose.yaml logs -f api`. You should see JSON lines with `task: review`, `job_id`, `run_id`, `repo` and `pr`. On the PR, the "ReviewPilot" check should run, followed by the review.
3. On GitHub, open **App settings → Advanced → Recent deliveries**. Each delivery should show a 202 response in well under a second.

## 7. Sentry (optional)
[sentry.io](https://sentry.io): create a project on the **Python / FastAPI** platform, put its DSN in `SENTRY_DSN` in `deploy/oracle/.env.production`, and run `deploy.sh` again.

Every event is tagged with `component` (api), `run_id`, `repo`, `pr` and `delivery_id`. Request bodies, cookies, auth headers, query strings and local variables are stripped before sending (`app/core/observability.py`).

## Operating it
- **Deploying an update:** merge to `main`, then on the VM run `~/ai-pr-reviewer/deploy/oracle/deploy.sh`. It pulls, rebuilds and restarts only what changed. Migrations run when the API starts, so write additive migrations.
- **Logs:**
  ```bash
  docker compose -f ~/ai-pr-reviewer/deploy/oracle/compose.yaml logs -f api
  ```
  Logs are JSON; search by `run_id`, `repo` or `delivery_id`. They rotate at 3 × 10 MB per service.
- **Status and restart:** `docker compose -f ~/ai-pr-reviewer/deploy/oracle/compose.yaml ps` shows status; `restart api` restarts the API and its worker. Every service restarts itself after a crash or a VM reboot.
- **Scaling:** run one API container. More would keep the queue correct (`SKIP LOCKED`), but each would also run the maintenance timers ([ADR 0016](adr/0016-postgres-job-queue-in-process-worker.md), Consequences).
- **Stuck or failed jobs:** the `jobs` table shows each job's `status`, `attempts` and `last_error`. A job left `running` by a restart is requeued within about 16 minutes.
- **Rotating a secret:** edit `.env.production`, then run `deploy.sh`. For the GitHub private key, generate a new one on the App page first, then delete the old one.
- **If the VM is lost:** it holds no data (Neon does). Create a new VM, repeat steps 4b–4e, and update the IP in DuckDNS.
- **Security:** SSH is key-only (Oracle's default), OS updates install automatically, and only ports 22, 80 and 443 are open.
