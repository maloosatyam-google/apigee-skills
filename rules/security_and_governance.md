---
trigger: glob
globs: "**/apiproxy/**,**/sharedflowbundle/**,**/*.sh,**/products/*.json,**/api_product*.json"
paths:
  - "**/apiproxy/**"
  - "**/sharedflowbundle/**"
  - "**/*.sh"
  - "**/products/*.json"
  - "**/api_product*.json"
applyTo: "**/apiproxy/**,**/sharedflowbundle/**,**/*.sh,**/products/*.json,**/api_product*.json"
description: "Mandatory security and governance rules for Apigee proxies, shared flows, products and deployment scripts."
---

# Apigee Security & Governance Rules

## 1. Secrets
- Never put a credential (API key, bearer token, client secret, password, private key, service account key) in
  policy XML, JavaScript, scripts, product JSON or any committed file, not even as a default or example value.
- Gateway-owned secrets live in an **encrypted environment KVM**, written out of band
  (`apigee_ctl.py kvm-put MAP KEY - --env ENV`, reading the value from stdin), read by a `KVM-*` policy into a
  `private.*` variable. Do not seed KVMs from the bundle (`<InitialEntries>`).
- Call Google backends with `GoogleIDToken` / `GoogleAccessToken` and a deployment service account, not keys.
- Scripts read org/env/tokens from the environment and fail when they are missing.

## 2. Authentication & authorization
- Every externally reachable path authenticates in the PreFlow (VerifyAPIKey, OAuthV2 VerifyAccessToken, VerifyJWT
  or VerifyIAM). Unauthenticated paths (CORS preflight, health, token, MCP handshake) are excluded explicitly and narrowly.
- Entitlement lives in API products (operations, `llmOperationGroup`, `payloadOperationGroup`), least privilege per persona.
- Strip client credentials before the target.

## 3. Abuse protection
- SpikeArrest on every proxy; Quota (product-driven) for business limits; `PromptTokenLimit` + `LLMTokenQuota` for LLM
  traffic (denial-of-wallet); threat protection / OAS body validation for request payloads.
- AI traffic is screened with Model Armor (`SanitizeUserPrompt` / `SanitizeModelResponse`) rather than regex redaction.

## 4. Layered defence (Architecture Center guidance)
- Internet-facing APIs sit behind an external Application Load Balancer with **Cloud Armor** (OWASP rules, DDoS,
  per-IP rate-based ban, IP/geo rules); add reCAPTCHA (WAAP) for web/mobile callers. Prefer Cloud Armor over the
  AccessControl policy for IP/geo filtering.
- Backends are private (peering / PSC / Interconnect) or HTTPS with certificate validation; mTLS (keystore +
  truststore **references**) for sensitive backends; never `IgnoreValidationErrors=true`.
- API products scope access per client type (path + verb + quota).
- Configure debug masks in every environment (`apigee_ctl.py debugmask-apply`); least-privilege IAM; keep audit logs.
- Details: `skills/apigee-proxy-builder/references/security_architecture.md`.

## 5. Error hygiene
- Never leak backend hostnames, stack traces, project ids or raw backend error bodies (sanitize 5xx).
- Return one JSON error shape with a request id; use RaiseFault for deliberate refusals.

## 6. Change safety
- Do not deploy to production, undeploy, or delete proxies/products/KVM entries without explicit user approval.
- Always keep a rollback path (`apigee_ctl.py rollback`) and verify after deploying.
