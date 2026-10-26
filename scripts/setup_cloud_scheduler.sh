#!/usr/bin/env bash
# Provision (or preview with --dry-run) a Google Cloud Scheduler job that triggers
# the Apigee Release Notes Skill Sync Cloud Build pipeline every Monday at 9:00 AM.
#
# Usage:
#   ./scripts/setup_cloud_scheduler.sh --project PROJECT_ID [options]
#
# Options:
#   --project ID          Google Cloud project ID (default: $GOOGLE_CLOUD_PROJECT)
#   --region REGION       Cloud Scheduler & Cloud Build region (default: us-central1)
#   --schedule CRON       Cron schedule (default: "0 9 * * 1")
#   --time-zone TZ        IANA timezone (default: "Asia/Kolkata")
#   --job-name NAME       Cloud Scheduler job name (default: apigee-release-notes-sync)
#   --trigger-id ID       Existing Cloud Build trigger ID (if omitted, invokes builds:create on repo/branch)
#   --repo-uri URI        Git repo URI for Cloud Build (default: https://github.com/maloosatyam-google/apigee-skills.git)
#   --branch BRANCH       Git branch to check (default: main)
#   --sa-name NAME        Service account name (default: apigee-skills-sync)
#   --dry-run             Print gcloud commands without executing them
set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:-}"
REGION="us-central1"
SCHEDULE="0 9 * * 1"
TIME_ZONE="Asia/Kolkata"
JOB_NAME="apigee-release-notes-sync"
TRIGGER_ID=""
REPO_URI="https://github.com/maloosatyam-google/apigee-skills.git"
BRANCH="main"
SA_NAME="apigee-skills-sync"
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)    PROJECT="$2"; shift ;;
    --region)     REGION="$2"; shift ;;
    --schedule)   SCHEDULE="$2"; shift ;;
    --time-zone)  TIME_ZONE="$2"; shift ;;
    --job-name)   JOB_NAME="$2"; shift ;;
    --trigger-id) TRIGGER_ID="$2"; shift ;;
    --repo-uri)   REPO_URI="$2"; shift ;;
    --branch)     BRANCH="$2"; shift ;;
    --sa-name)    SA_NAME="$2"; shift ;;
    --dry-run)    DRY_RUN=true ;;
    -h|--help)    sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
  shift
done

if [[ -z "$PROJECT" ]]; then
  echo "Error: --project PROJECT_ID (or \$GOOGLE_CLOUD_PROJECT) is required." >&2
  exit 1
fi

SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"

run_cmd() {
  if $DRY_RUN; then
    printf '[dry-run] '
    printf '%q ' "$@"
    printf '\n'
  else
    "$@"
  fi
}

echo "==> 1. Enabling required Google Cloud APIs (Cloud Scheduler, Cloud Build, Vertex AI)..."
run_cmd gcloud services enable \
  cloudscheduler.googleapis.com \
  cloudbuild.googleapis.com \
  aiplatform.googleapis.com \
  --project "$PROJECT"

echo "==> 2. Ensuring service account ${SA_EMAIL} exists with least-privilege roles..."
if $DRY_RUN || ! gcloud iam service-accounts describe "$SA_EMAIL" --project "$PROJECT" >/dev/null 2>&1; then
  run_cmd gcloud iam service-accounts create "$SA_NAME" \
    --display-name "Apigee Skills Release Notes Sync" \
    --project "$PROJECT"
fi

for role in roles/cloudbuild.builds.editor roles/aiplatform.user roles/logging.logWriter; do
  run_cmd gcloud projects add-iam-policy-binding "$PROJECT" \
    --member "serviceAccount:${SA_EMAIL}" \
    --role "$role" \
    --condition=None \
    --quiet
done

if [[ -n "$TRIGGER_ID" ]]; then
  TARGET_URI="https://cloudbuild.googleapis.com/v1/projects/${PROJECT}/locations/${REGION}/triggers/${TRIGGER_ID}:run"
  BODY="{\"branchName\":\"${BRANCH}\"}"
else
  TARGET_URI="https://cloudbuild.googleapis.com/v1/projects/${PROJECT}/locations/${REGION}/builds"
  BODY=$(cat <<EOF
{
  "steps": [
    {
      "name": "python:3.11-slim",
      "entrypoint": "bash",
      "args": [
        "-ceu",
        "apt-get update && apt-get install -y --no-install-recommends git make zip && git clone --depth=1 -b ${BRANCH} ${REPO_URI} /workspace/apigee-skills && cd /workspace/apigee-skills && python3 scripts/sync_release_notes.py --apply --project ${PROJECT} --location ${REGION} && python3 tests/test_skills.py && make lint"
      ]
    }
  ],
  "options": { "logging": "CLOUD_LOGGING_ONLY" }
}
EOF
)
fi

echo "==> 3. Creating or updating Cloud Scheduler job '${JOB_NAME}' (${SCHEDULE} ${TIME_ZONE})..."
if ! $DRY_RUN && gcloud scheduler jobs describe "$JOB_NAME" --location "$REGION" --project "$PROJECT" >/dev/null 2>&1; then
  ACTION="update"
else
  ACTION="create"
fi

run_cmd gcloud scheduler jobs "$ACTION" http "$JOB_NAME" \
  --project "$PROJECT" \
  --location "$REGION" \
  --schedule "$SCHEDULE" \
  --time-zone "$TIME_ZONE" \
  --uri "$TARGET_URI" \
  --http-method POST \
  --headers "Content-Type=application/json" \
  --message-body "$BODY" \
  --oauth-service-account-email "$SA_EMAIL" \
  --oauth-token-scope "https://www.googleapis.com/auth/cloud-platform" \
  --description "Weekly check of Google Cloud Apigee release notes to sync updates into apigee-skills"

echo "Done. Trigger an immediate test run anytime with:"
echo "  gcloud scheduler jobs run ${JOB_NAME} --location ${REGION} --project ${PROJECT}"

