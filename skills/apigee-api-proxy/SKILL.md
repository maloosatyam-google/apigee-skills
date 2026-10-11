---
name: apigee-api-proxy
description: >-
  Build new Apigee X REST/HTTP API proxies and reusable shared flows for classic API Management. Use for
  "put Apigee in front of my backend/Cloud Run service", "expose this OpenAPI spec through Apigee",
  API key / OAuth 2.0 client-credentials / JWT (JWKS) protection, spike arrest and product-driven quota,
  OpenAPI request validation with per-operation flows, response caching, CORS, consistent JSON error
  handling, target servers and load balancing, Google ID/access-token backend auth, API products and
  developer apps, and org-wide shared flows / flow hooks. Standalone: includes scaffold scripts that
  generate deployable, validated bundles plus matching API product JSON.
---

# Apigee API Proxy (REST) Builder

Turn a backend URL (and optionally an OpenAPI spec) into a governed, production-shaped Apigee X API proxy
in minutes, then iterate on it. Pairs with [`apigee-proxy-builder`](../apigee-proxy-builder/SKILL.md)
(policy references, validator, deploy script) and [`apigee-proxy-lifecycle`](../apigee-proxy-lifecycle/SKILL.md)
(updating deployed proxies, products, KVMs).

## When to use
- New API proxy for a REST/HTTP backend, with or without an OpenAPI spec
- Choosing and wiring client authentication (API key vs OAuth vs JWT) and quotas via API products
- A reusable security/logging chain for many proxies (shared flow + FlowCallout or environment flow hook)
- For **LLM** backends use [`apigee-ai-gateway`](../apigee-ai-gateway/SKILL.md); for **MCP** servers use
  [`apigee-mcp-gateway`](../apigee-mcp-gateway/SKILL.md).

## Step 0 - gather requirements (ask if unknown, never guess secrets)
| Question | Drives |
|---|---|
| Backend URL(s)? Cloud Run / Google API / external? Several hosts? | `--target` / `--target-server`, `--target-auth idtoken\|accesstoken` |
| Who calls it and how do they authenticate? | `--auth apikey\|oauth\|jwt\|none` |
| Is there an OpenAPI spec? | `--oas` (validation + flows + product operations) |
| Rate limits / plans? | `--spike-arrest`, `--quota` (limits live in the API product) |
| Browser clients? | `--cors` |
| Cacheable GETs? | `--cache-ttl` |
| Org-wide policies already in a shared flow? | `--shared-flow NAME` |
| Sensitive backend needing mTLS / private CA? | `--target-truststore ref://..`, `--target-mtls ref://keystore:alias` |
| XML or SOAP bodies? | `--xml-threat` (+ SOAPMessageValidation by hand) |
| Internet-facing? | Cloud Armor / WAAP in front ([security_architecture.md](../apigee-proxy-builder/references/security_architecture.md)) |
| Org / env / base path / runtime service account? | deploy flags |

## Quick start

```bash
SK=<path-to>/skills
# 1. Scaffold (examples/specs/orders-v1.json is a sample spec)
python3 $SK/apigee-api-proxy/scripts/scaffold_api_proxy.py --out ./orders-v1 \
  --name orders-v1 --basepath /orders/v1 --target https://mocktarget.apigee.net \
  --auth apikey --quota --spike-arrest 20ps --cors "*" --oas ./orders-v1.json --cache-ttl 60 --health

# 2. Validate, then deploy (add --sa when the target uses Google auth or Cloud Logging is on)
python3 $SK/apigee-proxy-builder/scripts/validate_bundle.py ./orders-v1 --strict
bash    $SK/apigee-proxy-builder/scripts/deploy_bundle.sh ./orders-v1 --org $ORG --env $ENV

# 3. API product + developer app -> key
python3 $SK/apigee-proxy-lifecycle/scripts/apigee_ctl.py product-apply ./orders-v1/api_product.json --env $ENV
# create a developer + app subscribed to "orders-v1-standard" (Apigee UI, apigeecli, or the Apigee API), then:
curl -i https://$HOST/orders/v1/ping                                   # 200 without a key (--health)
curl -i https://$HOST/orders/v1/orders -H "x-apikey: $KEY"             # 200
curl -i https://$HOST/orders/v1/nope   -H "x-apikey: $KEY"             # 404 JSON (OAS catch-all)
curl -i https://$HOST/orders/v1/orders                                 # 401 JSON (no key)
```

## What the scaffold generates

```text
PreFlow (request)  CORS → [FC-<shared flow>] → auth → SpikeArrest → Quota (product) → JSONThreatProtection
                   → ResponseCache lookup (GET) → strip client credentials
Flows              Health (/ping, no target) · Token (/token, OAuth) · one flow per OAS operation (OASValidation)
                   · NotFound catch-all (RaiseFault 404)            ← only the FIRST matching flow runs
PostFlow (resp)    ResponseCache populate · security headers (HSTS, nosniff, x-request-id; strip Server)
PostClientFlow     MessageLogging → Cloud Logging (--cloud-logging)
DefaultFaultRule   AM-FaultResponse: {"error":{"code","status","message","requestId"}} (skips RaiseFault)
Target             URL or LoadBalancer(TargetServers) · success.codes 1xx-4xx (backend 4xx pass through,
                   5xx sanitized) · timeouts · GoogleIDToken / GoogleAccessToken
api_product.json   operationGroup with per-path operations + quota (apikey/oauth)
```

Unprotected requests (CORS preflight, `/ping`, `/token`) are excluded from auth/quota by one explicit
condition, so auth is never accidentally skipped for real traffic.

## Choosing client authentication
| Option | Use when | Identity variable for quota/analytics |
|---|---|---|
| `apikey` | Partner/internal apps, simplest, product-driven entitlement | `verifyapikey.VA-VerifyAPIKey.client_id` |
| `oauth` (+`--token-endpoint`) | App-to-app with short-lived tokens, scopes | `client_id` |
| `jwt` | Tokens from an IdP (Google, Okta, Entra ID, Auth0) via JWKS | `jwt.JWT-VerifyToken.decoded.claim.sub` |
| `none` | Truly public APIs only (validator warns) | - |

Production note: run `/token` in a dedicated identity proxy shared by many APIs, not in every proxy.

## Shared flows (reuse across API, AI and MCP proxies)

```bash
python3 $SK/apigee-api-proxy/scripts/scaffold_shared_flow.py --out ./sf-security --name sf-security \
  --features apikey,spikearrest,threat,strip-auth
bash $SK/apigee-proxy-builder/scripts/deploy_bundle.sh ./sf-security --org $ORG --env $ENV
# consume per proxy:   scaffold_api_proxy.py ... --shared-flow sf-security --auth none
# or for every proxy:  apigee_ctl.py flowhook-attach PreProxyFlowHook sf-security --env $ENV
```
Deploy the shared flow **before** any proxy that calls it. Flow hooks run for every proxy in the environment.
Avoid double authentication when a proxy also has its own VerifyAPIKey.

## Extending the generated proxy
Edit the bundle directly or use `bundle_edit.py` from the lifecycle skill (keeps the manifest in sync):

```bash
E=$SK/apigee-proxy-lifecycle/scripts/bundle_edit.py
python3 $E add-policy ./orders-v1 ./KVM-GetConfig.xml
python3 $E attach ./orders-v1 KVM-GetConfig --flow PreFlow --phase Request --after VA-VerifyAPIKey
python3 $E outline ./orders-v1
```
Policy syntax: [policies_security.md](../apigee-proxy-builder/references/policies_security.md),
[policies_mediation.md](../apigee-proxy-builder/references/policies_mediation.md),
[policies_traffic_management.md](../apigee-proxy-builder/references/policies_traffic_management.md),
[policies_caching.md](../apigee-proxy-builder/references/policies_caching.md),
[fault_handling.md](../apigee-proxy-builder/references/fault_handling.md),
[load_balancing_and_routing.md](../apigee-proxy-builder/references/load_balancing_and_routing.md).
Design guidance and the test matrix: [references/api_proxy_patterns.md](references/api_proxy_patterns.md).
Official best-practice review list: [best_practices_checklist.md](../apigee-proxy-builder/references/best_practices_checklist.md).
Layered security (Cloud Armor, WAAP, mTLS, data masking): [security_architecture.md](../apigee-proxy-builder/references/security_architecture.md).

## Troubleshooting
| Symptom | Likely cause |
|---|---|
| `InvalidApiKeyForGivenResource` / 401 with a valid key | Product has no operation matching the path/verb, product not in this env, or app not approved |
| `steps.oasvalidation.Failed` for a valid path | Spec `servers` URL path vs proxy base path mismatch, or path params not declared |
| Every response is 404 | Path not covered by any OAS operation (catch-all `NotFound` flow) |
| Quota never applies | Product operation has no `quota`; or Quota runs before VerifyAPIKey |
| 500 `GoogleTokenGenerationFailure` | Deployed without `--sa`, or the SA lacks `roles/run.invoker` / target role |
| Backend errors look generic | 5xx is sanitized by design; backend 4xx pass through (`success.codes`) |
| CORS fails in browser | Origin not in `--cors` list, or auth header not in `AllowHeaders` |
