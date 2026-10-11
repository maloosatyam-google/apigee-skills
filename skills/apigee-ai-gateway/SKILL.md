---
name: apigee-ai-gateway
description: >-
  Design, scaffold, update and operate an Apigee X AI Gateway (LLM proxy) in front of Vertex AI Gemini and
  Anthropic Claude (and other LLM APIs). Use for model routing (direct, multi-provider with Gemini<->Claude
  shape conversion, and /auto classifier routing driven by API product attributes), Model Armor guardrails
  (SanitizeUserPrompt / SanitizeModelResponse), LLM token quotas (LLMTokenQuota enforce/count pair,
  PromptTokenLimit), semantic caching (SemanticCacheLookup/Populate), cost and budget governance (KVM rate
  card, Quota micro-dollar budgets, MonetizationLimitsCheck + DataCapture), per-user attribution, streaming,
  failover and gateway telemetry headers. Standalone: includes a scaffold that generates a deployable bundle
  and the matching llmOperationGroup API product.
---

# Apigee AI Gateway

Build a **model-agnostic AI Gateway** on Apigee X. Clients call one endpoint with one API key.
The gateway authenticates, screens, rate-limits, caches, routes, prices and logs every LLM call.
Shares the validator/deploy scripts and the full policy XML reference with
[`apigee-proxy-builder`](../apigee-proxy-builder/SKILL.md); changes to a deployed gateway go through
[`apigee-proxy-lifecycle`](../apigee-proxy-lifecycle/SKILL.md).

## When to use
- "Put Apigee in front of Gemini / Claude", "add Model Armor", "token quota per user/app/model"
- "semantic cache", "auto-route prompts to the cheapest suitable model", "budget / chargeback / prepaid wallet"
- Reviewing or debugging an LLM proxy (quota never trips, cache never hits, cost is wrong, 404 with an empty model)

## Prerequisites (check before deploying)
- Vertex AI API enabled; runtime service account with `roles/aiplatform.user`
  (+ `roles/modelarmor.user` for Model Armor, + `roles/logging.logWriter` for Cloud Logging)
- Claude: the Anthropic model enabled in Vertex AI Model Garden for the project
- Model Armor: templates created (`gcloud model-armor templates create ...`) in the chosen region
- Semantic cache: Vector Search index (streaming updates) deployed to a **public** endpoint, `text-embedding-005`

## Quick start

```bash
SK=<path-to>/skills
# 1. Generate a governed bundle + matching API product
python3 $SK/apigee-ai-gateway/scripts/scaffold_ai_gateway.py --out ./ai-gateway-v1 \
  --project $PROJECT --token-quota --prompt-token-limit 5000pm \
  --claude --auto-route \
  --armor-region $REGION                       # optional: Model Armor (create templates first)
  # --cache-region $REGION --index-endpoint-host H --index-endpoint-id E --index-id I   # optional semantic cache

# 2. Validate + deploy
python3 $SK/apigee-proxy-builder/scripts/validate_bundle.py ./ai-gateway-v1 --strict
bash    $SK/apigee-proxy-builder/scripts/deploy_bundle.sh ./ai-gateway-v1 --org $ORG --env $ENV --sa $RUNTIME_SA

# 3. Product + app, then call it
python3 $SK/apigee-proxy-lifecycle/scripts/apigee_ctl.py product-apply ./ai-gateway-v1/api_product.json --env $ENV
# create a developer + app with that product, then:
B='{"contents":[{"role":"user","parts":[{"text":"Write a Python function that reverses a list"}]}]}'
curl -si https://$HOST/ai/v1/models/gemini-3.6-flash:generateContent -H "x-apikey: $KEY" -H 'Content-Type: application/json' -d "$B" | grep -i x-gateway
curl -si https://$HOST/ai/v1/models/claude-haiku-5-5:generateContent -H "x-apikey: $KEY" -H 'Content-Type: application/json' -d "$B" | grep -i x-gateway
curl -si https://$HOST/ai/v1/models/auto:generateContent             -H "x-apikey: $KEY" -H 'Content-Type: application/json' -d "$B" | grep -i x-gateway-router
```

Demo ideas: jailbreak prompt → 403 from Model Armor. Loop calls → 429 from `LLMTokenQuota`. Same question twice
with `x-use-cache: true` → `x-gateway-cache-status: HIT` (skips the model, no tokens charged). A coding prompt to
`/models/auto` → `x-gateway-router-category: coding` and the product's coding model.

### Scaffold options
| Flag | Adds |
|---|---|
| `--token-quota` | `LTQ-TokenEnforce` (conditional flow, cache-miss only) + `LTQ-TokenCount` (PostFlow), limits from product `llmTokenQuota` |
| `--prompt-token-limit 5000pm` | `PromptTokenLimit` surge protection per app |
| `--armor-region R` | `SanitizeUserPrompt` (blocks) + `SanitizeModelResponse` |
| `--cache-region R ...` | Opt-in semantic cache (`x-use-cache: true`) with HIT/MISS tracking |
| `--claude [--claude-model M]` | `claude-vertex-target`, `ClaudeRequestPrep.js` (Gemini → Anthropic Messages incl. tools, system, generationConfig) and `FormatClaudeResponse.js` (→ Gemini candidates + `usageMetadata`, so quota/cost/armor work unchanged) |
| `--auto-route [--router-model M]` | `/models/auto:generateContent`: classifier ServiceCallout (structured output) → `AutoRouting.js` reads `routing.model.<category>` from the caller's product |

## Architecture (request lifecycle)

```text
Client ── x-apikey (+ optional user JWT) ──▶ ProxyEndpoint /ai/v1
 PreFlow   identity (EV bearer → DecodeJWT → flow.emailId) → JS extract prompt/model → VerifyAPIKey
           → cheap refusals (unsupported path / streaming) → SanitizeUserPrompt → PromptTokenLimit
           → MonetizationLimitsCheck → budget Quota (EnforceOnly) + RaiseFault → strip auth
           → [opt-in] SemanticCacheLookup ─── HIT ──▶ response (skips target + conditional flows)
 Flows     (miss only, FIRST match wins) AutoRoutingFlow: LTQ enforce → classifier → JS route
           | LLMTokenQuotaFlow: LTQ enforce
 Target    AM sets target.url (Gemini :generateContent | Claude :rawPredict), target.copy.pathsuffix=false,
           GoogleAccessToken auth; Claude target converts request/response shapes
 PostFlow  EV usage → KVM rate card → JS cost → budget Quota (Weight) → LLMTokenQuota CountOnly
           → DataCapture (analytics + monetization) → SemanticCachePopulate → SanitizeModelResponse
           → x-gateway-* headers
 PostClientFlow MessageLogging · DefaultFaultRule(AlwaysEnforce) DataCapture for blocked calls
```

The scaffold implements the core of this lifecycle. Identity/DecodeJWT, cost, budget and monetization are added
by hand from [`policies_ai_gateway.md`](../apigee-proxy-builder/references/policies_ai_gateway.md)
(use `bundle_edit.py add-policy` / `attach` to keep the manifest consistent).

## 1. Model routing
- **Direct:** `POST /models/{model}:generateContent`. `EV-RequestDetails` sets `flow.model` from the path,
  and AM in the TargetEndpoint builds
  `https://aiplatform.googleapis.com/v1/projects/{p}/locations/global/publishers/google/models/{flow.model}:generateContent`.
- **Claude on Vertex:** `.../publishers/anthropic/models/{model}:rawPredict`. The body must use the Anthropic Messages
  shape with `"anthropic_version": "vertex-2023-10-16"` and no `model` field. Select it with a RouteRule on
  `flow.target_provider == "anthropic"`. Converting in the target keeps **one client contract**.
- **Auto routing:** a ServiceCallout classifies the prompt (coding / deep_reasoning / simple / general), and JS reads
  the target model from the **API product attributes** `routing.model.<category>`
  (`verifyapikey.VA-VerifyAPIKey.apiproduct.routing.model.coding`). Each persona product gets its own model
  map without any proxy change. Falls back to `routing.model.general`, then the default model.
- **Other providers (OpenAI-compatible, self-hosted):** add a TargetEndpoint + RouteRule; normalise the response to the
  Gemini `usageMetadata` shape (or point `LLMTokenUsageSource` at the provider's usage field).
- Details, failover and streaming: [references/model_routing_playbook.md](references/model_routing_playbook.md).

## 2. Guardrails (Model Armor)
- `SanitizeUserPrompt` goes **after** VerifyAPIKey, so you do not pay for screening unauthenticated traffic. Use
  `continueOnError="false"` to block.
- `SanitizeModelResponse` goes in the PostFlow on 200. Its default extraction expects the Gemini response shape (Claude
  responses are converted first, so it covers both).
- Feed both from a single provider-agnostic `flow.userPrompt` extracted in JS.
- Use Model Armor instead of hand-written regex redaction. It covers prompt injection, jailbreak, RAI, SDP/PII, malicious URLs and CSAM.

## 3. Token governance
| Need | Policy |
|---|---|
| Stop sudden surges in prompt size | `PromptTokenLimit` (Rate, for example `5000pm`) |
| Per-minute to per-month token allowance per model | `LLMTokenQuota` EnforceOnly (request) + CountOnly (response) |
| Limits per persona / model | API product `llmOperationGroup.operationConfigs[].llmTokenQuota` |
| Warn before 429 | JS: `ratelimit.LTQ-TokenCount.used.count` ÷ `...apiproduct.developer.llmQuota.limit` |

Rules learned the hard way:
1. Both LTQ policies must use **identical** `Identifier`, `LLMModelSource` and `SharedName` (the validator checks this).
2. Key the quota per **user** (`flow.emailId`) when many users share one app.
3. `LLMModelSource` must equal an operationConfig `model`, and must **not** be overwritten mid-flow. Keep
   `flow.quota_model` (for example `auto` for `/models/auto`) separate from the routed `flow.model`.
4. Do not count semantic-cache hits.
5. The CountOnly policy reads `usageMetadata.totalTokenCount`. A Claude response must be normalised to that shape
   (the scaffold's `FormatClaudeResponse.js` does) or it is never counted.

## 4. Semantic cache
Lookup goes at the end of the PreFlow, populate in the PostFlow. Make it opt-in (`x-use-cache: true`) and use
`continueOnError="true"`. Track HIT/MISS by setting HIT before the lookup and MISS in the **TargetEndpoint** PreFlow,
which only runs on a miss. The cache key is the prompt only, so do not classify or route before the lookup.

## 5. Cost, budget, monetization
- Keep the rate card in an environment **KVM** as JSON (`{"gemini-3.6-flash":{"input":0.5,"output":3.0},...}`, USD per 1M tokens):
  `apigee_ctl.py kvm-put ai-gateway-config rate-card - --env $ENV < rate_card.json`.
  `JS-CalculateCost` computes `flow.tx_cost_usd` and `flow.tx_cost_micros`. Count thinking tokens at the output rate.
- **Budget:** a Quota pair on `SharedName developer-budget-counter`, with limits from product attributes `developer.budget.*`.
  The read side is `EnforceOnly` with `continueOnError="true"`, followed by an explicit RaiseFault 429 on `ratelimit.X.failed`.
- **Prepaid wallet:** `MonetizationLimitsCheck` in the request. In the response, `DataCapture` with monetization-scope
  `perUnitPriceMultiplier`, `currency` and `transactionSuccess`. Set `transactionSuccess=false` on cache hits and on unpriced calls.

## 6. Identity and telemetry
- Authorization uses the **API key** (product = persona = entitlement). User identity for attribution comes from a JWT via
  `DecodeJWT`. Use `VerifyJWT` if you need to trust the token itself.
- Response headers (`AM-SetResponseHeaders`): `x-gateway-model`, `-provider`, `-prompt-tokens`, `-completion-tokens`,
  `-total-tokens`, `-cached`, `-cache-status`, `-router-category`, plus `-cost-usd`, `-token-quota-*`, `-budget-*` when added.
  Avoid `x-apigee-*`, which Apigee strips.
- `MessageLogging` in PostClientFlow. `DataCapture` dimensions `dc_user_email`, `dc_model_name`, `dc_*_token_count`,
  `dc_cache_status`, plus a fault-path twin in `DefaultFaultRule` with `AlwaysEnforce`.

## 7. Updating a deployed AI gateway
```bash
C=$SK/apigee-proxy-lifecycle/scripts/apigee_ctl.py
python3 $C export ai-gateway-v1 --env $ENV --out ./ai-gw          # what is actually running
# edit ./ai-gw (or bundle_edit.py), then:
python3 $C diff ./ai-gw --env $ENV && python3 $C update ./ai-gw --env $ENV --sa $RUNTIME_SA
```
Model or quota changes usually belong in the **API product** (`llmOperationGroup`, `routing.model.*`), not the proxy:
`apigee_ctl.py product-get "<name>"` → edit → `product-apply`.

## Troubleshooting
| Symptom | Likely cause |
|---|---|
| Token quota never trips | LTQ pair mismatch, `flow.model` overwritten, or CountOnly fault swallowed (`continueOnError`). Check `ratelimit.LTQ-TokenCount.failed` in trace |
| `InvalidAPICallAsNoApiProductMatchFound` | Model string not in the product's `llmOperations` (for example `@` and `-` revision separators differ) |
| 404 from Vertex with an empty model in the URL | Routing skipped. Do not guard routing flows on `flow.cached` |
| Claude 400 `messages: roles must alternate` / `max_tokens` | Body not converted: `JS-ClaudeRequestPrep` missing from the Claude target, or the request was neither Gemini nor Messages shape |
| `/models/auto` always uses the general model | Classifier call failed (SA lacks `aiplatform.user`, router model not available) or product lacks `routing.model.*` attributes |
| Budget never enforced | EnforceOnly + continueOnError without a RaiseFault checking `ratelimit.*.failed` |
| Wallet charged on cache hits | DataCapture defaults are used. Set `transactionSuccess=false` and the multiplier to 0 |
| Analytics shows `{flow.model}` | `DataCapture default` is a literal string |
| Body never validated | `OASValidation` needs `<ValidateMessageBody>true</ValidateMessageBody>` |
