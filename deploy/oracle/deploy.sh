#!/usr/bin/env bash
# Build and (re)start ReviewPilot on the VM: pull the latest main, rebuild the image,
# restart what changed. Migrations run when the API starts (docs/deploy.md).
#
#   ~/ai-pr-reviewer/deploy/oracle/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/../.."

ENV_FILE=deploy/oracle/.env.production
COMPOSE=(docker compose --env-file "$ENV_FILE" -f deploy/oracle/compose.yaml)

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE: copy deploy/oracle/.env.production.example and fill it in." >&2
  exit 1
fi
chmod 600 "$ENV_FILE" # secrets: readable by this user only
# Compose reads deploy/oracle/.env by itself, so plain `docker compose -f … ps/logs`
# can resolve API_DOMAIN too. A symlink, so there's still only one secrets file.
ln -sfn .env.production deploy/oracle/.env

git pull --ff-only
"${COMPOSE[@]}" up -d --build --remove-orphans
docker image prune -f >/dev/null # old image layers would slowly fill the disk

echo
"${COMPOSE[@]}" ps
echo
echo "Logs:   docker compose -f deploy/oracle/compose.yaml logs -f api worker"
echo "Health: curl -s https://\$(grep ^API_DOMAIN= $ENV_FILE | cut -d= -f2)/api/v1/ready"
