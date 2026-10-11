---
trigger: glob
globs: "**/apiproxy/**,**/sharedflowbundle/**,**/api_product*.json,**/products/*.json"
description: "Conventions for creating and modifying Apigee X proxy bundles, shared flows and API products (API, AI Gateway, MCP)."
---

# Apigee X Proxy Development Standards

## 1. Workflow (always)
1. **New proxy**: start from a scaffold (`apigee-api-proxy`, `apigee-ai-gateway`, `apigee-mcp-gateway` skills), then customise.
2. **Existing proxy**: export the revision deployed in the target environment (`apigee_ctl.py export NAME --env ENV`);
   never edit a stale local copy or assume the latest revision is the deployed one.
3. Run `validate_bundle.py <dir> --strict` after every change; never deploy a failing bundle.
4. Show `apigee_ctl.py diff` before updating a shared or production environment; note the rollback revision.

## 2. Bundle structure
```text
apiproxy/                         sharedflowbundle/
├── <name>.xml   (manifest)       ├── <name>.xml
├── proxies/default.xml           ├── sharedflows/default.xml
├── targets/default.xml           ├── policies/*.xml
├── policies/*.xml                └── resources/<type>/*
└── resources/<jsc|oas|py|xsl|properties>/*
```
Every file in `policies/` is listed in the manifest `<Policies>` and attached by at least one `<Step>`; every
`jsc://`/`oas://`/... resource exists and is listed in `<Resources>`. `bundle_edit.py` keeps these in sync.

## 3. Policy naming: `<PREFIX>-<Purpose>`, file name == `name` attribute
`VA` VerifyAPIKey · `OA` OAuthV2 · `JWT` VerifyJWT/GenerateJWT · `DJWT` DecodeJWT · `SA` SpikeArrest · `Q`/`QC` Quota ·
`AM` AssignMessage · `EV` ExtractVariables · `JS` JavaScript · `PY` PythonScript · `KVM` KeyValueMapOperations ·
`SC` ServiceCallout · `FC` FlowCallout · `RF` RaiseFault · `RC` ResponseCache · `LC`/`PC`/`IC` cache ·
`ML` MessageLogging · `DC` DataCapture · `CORS` · `JTP`/`XTP` threat protection · `OAS` OASValidation ·
`AE` AccessEntity · `AC` AccessControl · `LTQ` LLMTokenQuota · `PTL` PromptTokenLimit ·
`SUP`/`SMR` Model Armor · `SCL`/`SCP` semantic cache · `MLC` MonetizationLimitsCheck · `PP` ParsePayload · `TC` TraceCapture

## 4. Flow design
- Security first in the PreFlow (auth → spike arrest → quota → threat protection/validation), then mediation.
- Only the first matching conditional flow runs: specific before generic, catch-all last.
- Parenthesize composite conditions: `(a) and (b)`.
- Set `target.copy.pathsuffix=false` whenever `target.url` is assigned dynamically.
- MessageLogging in PostClientFlow; one DefaultFaultRule producing `{"error":{"code","status","message","requestId"}}`.

## 5. AI Gateway conventions
- `LLMTokenQuota` as an EnforceOnly (request, cache-miss path) / CountOnly (response) pair with identical
  `Identifier`, `LLMModelSource`, `SharedName`; limits from the product `llmOperationGroup`. `PromptTokenLimit` for surges.
- Model Armor via `SanitizeUserPrompt` (after VerifyAPIKey) / `SanitizeModelResponse`; semantic cache via
  `SemanticCacheLookup`/`Populate` (not ResponseCache).
- Model names for `/auto` come from product attributes `routing.model.*`, never literals in the proxy.
- Normalise non-Gemini responses to `usageMetadata` so quota/cost/telemetry stay provider-agnostic.
- Telemetry headers `x-gateway-*` (Apigee strips `x-apigee-*`).

## 6. MCP / Tools Gateway conventions
- `ParsePayload` first, a JSON-RPC method allowlist (`-32601` otherwise), VerifyAPIKey + product Quota on `tools/*` only.
- Per-tool entitlement from product `payloadOperationGroup` operations `tools/call/<tool>`.
- Gateway-generated JSON-RPC responses echo the request `id` with its original JSON type; business limits return
  `result.isError=true` (HTTP 403) with a machine-readable code.

## 7. Official best practices (summary)
Full list: `skills/apigee-proxy-builder/references/best_practices_checklist.md`.
- Never call the Apigee management API from a proxy; prefer built-in policies over code (JavaScript > Java > Python).
- One condition per conditional flow rather than many conditional steps; exactly one default RouteRule, last.
- TargetServers for environment-specific hosts; bundles under 15 MB; remove unused policies/resources.
- Fault responses built with AssignMessage in FaultRules/DefaultFaultRule; human-readable messages.
- ResponseCache: lookup early (request PreFlow), populate late (response PostFlow), skip non-200/non-GET,
  no client_id in the key unless responses differ per client; never cache CORS responses.
- Scripts: try/catch, null checks, no HTTP calls (use ServiceCallout); ServiceCallout URL protocol is literal.
- Threat protection on every production proxy; IP-restrict test environments.

## 8. Change the right layer
Plans/paths/tools/models → API product. Per-env hosts → TargetServer. Secrets/tunables → encrypted KVM.
Org-wide policies → shared flow + flow hook. Only behaviour changes need a new proxy revision.
