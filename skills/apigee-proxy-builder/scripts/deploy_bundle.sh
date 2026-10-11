#!/usr/bin/env bash
# Standalone: validate, package and (optionally) deploy an Apigee X API proxy OR shared flow bundle.
# Requires: zip, curl, python3, gcloud (authenticated). No repo dependencies.
#
#   deploy_bundle.sh <bundle-dir> [--org ORG] [--env ENV] [--sa SERVICE_ACCOUNT_EMAIL]
#                    [--package-only] [--no-validate] [--out ZIP_PATH]
#
# <bundle-dir> contains apiproxy/ (API proxy) or sharedflowbundle/ (shared flow).
# The bundle name is the root manifest's name attribute.
# --sa is required when targets use GoogleAccessToken / GoogleIDToken authentication
#      (or when Model Armor / semantic cache / Vertex AI policies are used).
# Env fallbacks: APIGEE_ORG, APIGEE_ENV, APIGEE_DEPLOY_SA, APIGEE_TOKEN (skips gcloud).
set -euo pipefail

usage="usage: $0 <bundle-dir> [--org ORG] [--env ENV] [--sa SA] [--package-only] [--no-validate] [--out ZIP]"
DIR="${1:?$usage}"; shift
ORG="${APIGEE_ORG:-}"; ENV="${APIGEE_ENV:-}"; SA="${APIGEE_DEPLOY_SA:-}"
PACKAGE_ONLY=false; VALIDATE=true; OUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --org) ORG="$2"; shift ;;
    --env) ENV="$2"; shift ;;
    --sa) SA="$2"; shift ;;
    --out) OUT="$2"; shift ;;
    --package-only) PACKAGE_ONLY=true ;;
    --no-validate) VALIDATE=false ;;
    -h|--help) echo "$usage"; exit 0 ;;
    *) echo "unknown arg $1"; echo "$usage"; exit 1 ;;
  esac; shift
done

if [[ -d "$DIR/apiproxy" ]]; then
  KIND=apiproxy; COLLECTION=apis; ROOT_TAG=APIProxy
elif [[ -d "$DIR/sharedflowbundle" ]]; then
  KIND=sharedflowbundle; COLLECTION=sharedflows; ROOT_TAG=SharedFlowBundle
else
  echo "no apiproxy/ or sharedflowbundle/ under $DIR"; exit 1
fi
MANIFEST=$(ls "$DIR/$KIND"/*.xml | head -1)
NAME=$(sed -n "s/.*<${ROOT_TAG}[^>]*name=\"\\([^\"]*\\)\".*/\\1/p" "$MANIFEST" | head -1)
[[ -n "$NAME" ]] || NAME=$(basename "$MANIFEST" .xml)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
$VALIDATE && python3 "$SCRIPT_DIR/validate_bundle.py" "$DIR"

ZIP="${OUT:-$(mktemp -d)/${NAME}.zip}"
rm -f "$ZIP"
(cd "$DIR" && zip -qr "$ZIP" "$KIND" -x "*.DS_Store*" -x "*/node_modules/*" -x "*.swp")
echo "Packaged $ZIP"
$PACKAGE_ONLY && exit 0

[[ -n "$ORG" && -n "$ENV" ]] || { echo "--org and --env (or APIGEE_ORG/APIGEE_ENV) required"; exit 1; }
TOKEN="${APIGEE_TOKEN:-$(gcloud auth print-access-token)}"
API="https://apigee.googleapis.com/v1/organizations/${ORG}"

# call METHOD URL [curl args...] -> prints body; exits with the API error message on HTTP >= 400
call() {
  local method="$1" url="$2"; shift 2
  local tmp; tmp=$(mktemp)
  local code
  code=$(curl -s -o "$tmp" -w '%{http_code}' -X "$method" -H "Authorization: Bearer $TOKEN" "$@" "$url")
  if [[ "$code" -ge 400 ]]; then
    echo "HTTP $code from $method $url" >&2
    python3 -c 'import sys,json
try: print(json.load(open(sys.argv[1]))["error"]["message"])
except Exception: print(open(sys.argv[1]).read())' "$tmp" >&2
    rm -f "$tmp"; exit 1
  fi
  cat "$tmp"; rm -f "$tmp"
}

REV=$(call POST "${API}/${COLLECTION}?name=${NAME}&action=import" -F "file=@${ZIP}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["revision"])')
echo "Imported ${COLLECTION}/${NAME} revision ${REV}"

Q="override=true"; [[ -n "$SA" ]] && Q="${Q}&serviceAccount=${SA}"
call POST "${API}/environments/${ENV}/${COLLECTION}/${NAME}/revisions/${REV}/deployments?${Q}" >/dev/null
echo "Deploying revision ${REV} to ${ENV}..."

STATE=""
for _ in $(seq 1 60); do
  BODY=$(call GET "${API}/environments/${ENV}/${COLLECTION}/${NAME}/revisions/${REV}/deployments")
  STATE=$(echo "$BODY" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("state",""))')
  if [[ "$STATE" == "READY" ]]; then echo "READY: ${NAME} r${REV} in ${ENV}"; exit 0; fi
  if [[ "$STATE" == "ERROR" ]]; then
    echo "Deployment ERROR:"; echo "$BODY" | python3 -c 'import sys,json
d=json.load(sys.stdin)
for e in d.get("errors",[]): print(" -", e.get("message"))' ; exit 1
  fi
  sleep 5
done
echo "Timed out waiting for READY (last state: ${STATE})"; exit 1
