# GitHub App setup

These steps are manual and happen once. They take about 15 minutes. At the end you'll have:

- a GitHub App,
- a smee.io channel that forwards webhooks to your laptop,
- a test repository, and
- a filled-in `backend/.env`.

> GitHub's UI changes occasionally. If a field name below doesn't match what you see, the official guide is authoritative: <https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app>

## 1. Create a smee.io channel (local webhook forwarding)

GitHub can't reach `localhost`, so smee gives you a public URL and relays every webhook to your machine.

1. Open <https://smee.io> and click **Start a new channel**.
2. Copy the channel URL (for example `https://smee.io/AbC123xyz`). You'll use it as the webhook URL below.
3. When the backend is running, start the relay:
   ```bash
   npx smee-client --url https://smee.io/AbC123xyz --target http://localhost:8000/api/v1/webhooks/github
   ```
   The `/webhooks/github` endpoint is built in Phase 1. Until then smee will show 404s, which is expected.

## 2. Generate a webhook secret

GitHub signs every webhook with this secret. We verify the signature so nobody can fake events.

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```
Keep the output. It goes in the App form and in `GITHUB_WEBHOOK_SECRET`.

## 3. Register the GitHub App

Go to GitHub → your avatar → **Settings** → **Developer settings** → **GitHub Apps** → **New GitHub App**.

| Field | Value |
|---|---|
| GitHub App name | `reviewpilot-om` (must be globally unique; if taken, pick another and use it as `GITHUB_APP_SLUG`) |
| Homepage URL | your GitHub profile or the repo URL for now |
| Callback URL | `http://localhost:8000/api/v1/auth/github/callback` (dashboard login, used in Phase 5) |
| Expire user authorization tokens | ✅ keep checked |
| Request user authorization (OAuth) during installation | leave unchecked |
| Webhook → Active | ✅ |
| Webhook URL | your smee channel URL |
| Webhook secret | the value from step 2 |

### Repository permissions (least privilege)

Set only these. Everything else stays **No access**.

| Permission | Access | Why |
|---|---|---|
| Pull requests | Read & write | read diffs, post reviews and review comments |
| Contents | Read-only | read `.reviewpilot.yml`, compare commits |
| Checks | Read & write | create and update the "ReviewPilot" check run |
| Issues | Read & write | reply to slash commands, add 👀 reactions on PR conversation comments |
| Metadata | Read-only | mandatory; GitHub selects it automatically |

### Subscribe to events

- ✅ **Pull request**
- ✅ **Issue comment** (this checkbox only appears after you grant the Issues permission)

You don't subscribe to `installation` or `installation_repositories`. GitHub always delivers those to the App.

### Where can this GitHub App be installed?

**Only on this account**. That's enough for a portfolio project, and you can switch it later.

Click **Create GitHub App**.

## 4. Collect credentials

On the App's **General** page:

1. Copy the **App ID** into `GITHUB_APP_ID`.
2. Copy the **Client ID** into `GITHUB_APP_CLIENT_ID`.
3. Click **Generate a new client secret** and copy it into `GITHUB_APP_CLIENT_SECRET`. GitHub shows it only once.
4. Scroll to **Private keys**, click **Generate a private key**, and a `.pem` file downloads. Base64-encode it onto one line so it fits in an env var:
   ```bash
   base64 -w0 ~/Downloads/reviewpilot-om.*.private-key.pem
   ```
   Put the output in `GITHUB_APP_PRIVATE_KEY_B64`, then move the `.pem` somewhere safe outside the repo. `*.pem` is gitignored as a safety net, but don't rely on it.

## 5. Create a test repository and install the App

1. Create a repository such as `reviewpilot-playground`. Add a little Python and TypeScript code so reviews have something to look at.
2. On the App's page, go to **Install App** → your account → **Only select repositories** → pick the test repo → **Install**.

## 6. Fill in `backend/.env`

```bash
cp backend/.env.example backend/.env
```
Fill in the GitHub values above, plus `GEMINI_API_KEY` from <https://aistudio.google.com/apikey>.

**Never commit `backend/.env`.** It's gitignored.

## Production later (Phase 7)

When the backend is deployed:

- Change the **Webhook URL** to `https://<your-api>/api/v1/webhooks/github`.
- Change the **Callback URL** to `https://<your-frontend>/api/v1/auth/github/callback`. This is the frontend domain, because Vercel proxies `/api/*` to the backend.
- Rotate the webhook secret and the client secret you used locally.
