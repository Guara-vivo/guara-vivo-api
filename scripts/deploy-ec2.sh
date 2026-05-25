#!/usr/bin/env bash

set -Eeuo pipefail

DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/guara-vivo}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
API_REPO="${API_REPO:-guara-vivo-api}"
IDENTIFIER_REPO="${IDENTIFIER_REPO:-guara-vivo-identifier}"
WORKER_REPO="${WORKER_REPO:-guara-vivo-worker}"
ENV_FILE="${ENV_FILE:-${DEPLOY_ROOT}/${API_REPO}/.env.docker-compose}"
HEALTHCHECK_URL="${DEPLOY_HEALTHCHECK_URL:-http://127.0.0.1:8001/health}"

update_repo() {
  local repo_dir="$1"
  local repo_path="${DEPLOY_ROOT}/${repo_dir}"

  if [ ! -d "${repo_path}/.git" ]; then
    echo "Missing Git repository: ${repo_path}" >&2
    exit 1
  fi

  echo "Updating ${repo_dir}"
  git -C "${repo_path}" fetch --prune origin "${DEPLOY_BRANCH}"
  git -C "${repo_path}" reset --hard "origin/${DEPLOY_BRANCH}"
}

if [ ! -f "${ENV_FILE}" ]; then
  echo "Missing env file: ${ENV_FILE}" >&2
  exit 1
fi

update_repo "${API_REPO}"
update_repo "${IDENTIFIER_REPO}"
update_repo "${WORKER_REPO}"

cd "${DEPLOY_ROOT}/${API_REPO}"

chmod 600 "${ENV_FILE}"

docker compose --env-file "${ENV_FILE}" config --quiet
docker compose --env-file "${ENV_FILE}" up -d --build --remove-orphans
docker image prune -f
docker compose --env-file "${ENV_FILE}" ps

curl --fail --silent --show-error \
  --retry 12 \
  --retry-all-errors \
  --retry-delay 5 \
  "${HEALTHCHECK_URL}"

echo "Deploy completed"
