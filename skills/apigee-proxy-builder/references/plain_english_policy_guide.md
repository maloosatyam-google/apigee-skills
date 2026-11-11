# Plain-English Policy Recommendation Guide & Glossary

Most developers, product owners, and platform users **do not know Apigee policy names** (`SpikeArrest`, `VerifyJWT`, `OASValidation`, `LLMTokenQuota`, `SanitizeUserPrompt`, `ParsePayload`, `FlowHook`, `TargetServer`, `KVM`). They describe **outcomes**: *"put my Cloud Run app behind a gateway"*, *"give our devs one key for Claude Code and Gemini without getting a \$10k bill"*, or *"let our support bot read orders, not delete them"*.

Always communicate in **simple, everyday English first**, recommend the protections they may not realize they need, and mention the Apigee policy name only in parentheses so they stay informed without needing Apigee expertise.

---

## 1. How the Agent Should Recommend Protections

Whenever a user asks to build or improve a gateway without naming specific Apigee policies:

1. **Ask 3–4 short, plain-English questions** (never ask *"Do you want SpikeArrest or OASValidation?"*):
   - **REST API**: *Who calls this service (partner/internal apps, logged-in end users, browsers, or public)? Where does your backend run (Cloud Run, GKE/VM, external URL)? Do you have an OpenAPI/Swagger file?*
   - **AI / Coding-Agent Gateway**: *Which AI models or coding tools will connect (Gemini, Claude / Claude Code, OpenAI / Copilot)? Should we enforce token budgets per user/team and screen prompts for safety/PII?*
   - **MCP Agent Tools Gateway**: *Where does your MCP server run, which agent roles exist, and do any tools take dollar/quantity inputs that need a hard cap?*
2. **Present a two-part Plain-English Recommendation** before generating or modifying files:
   - **Recommended Baseline (Included by Default)** — essential security, burst protection, and clean error handling explained in 1 simple sentence each.
   - **Optional Add-Ons to Consider** — extra capabilities (caching, contract validation, AI prompt screening, smart model routing, Cloud Logging) so the user discovers high-value protections they didn't know existed.
3. **Generate with [`recommend_policies.py`](../scripts/recommend_policies.py)** when helpful:
   ```bash
   python3 skills/apigee-proxy-builder/scripts/recommend_policies.py \
     --describe "React web app calling our Cloud Run orders service with an OpenAPI spec"
   ```

### Example User-Facing Recommendation Format
> Here is what I recommend putting in front of your service:
> 1. **Require an API key per caller app** — Blocks unknown traffic and lets you revoke or rate-limit individual callers *(Apigee: `VerifyAPIKey` + API Product)*.
> 2. **Smooth out sudden traffic spikes** — Stops a sudden burst or retry loop from crashing your backend *(Apigee: `SpikeArrest`)*.
> 3. **Block malformed or oversized JSON** — Rejects deeply nested or huge payloads at the edge *(Apigee: `JSONThreatProtection`)*.
> 4. **Keep your Cloud Run service private** — Removes caller keys and signs in to Cloud Run automatically with a Google identity token *(Apigee: `AssignMessage` + `GoogleIDToken`)*.
>
> **Optional add-ons — want any of these turned on?**
> - **Validate requests against an OpenAPI spec** — Automatically reject missing fields or unknown paths before hitting your code *(Apigee: `OASValidation`)*.
> - **Cache `GET` responses (e.g., 60s)** — Serve repeated reads from memory to cut latency *(Apigee: `ResponseCache`)*.

---

## 2. "What Do You Want to Do?" → Plain-English Capability Finder

### A. Controlling Who Can Call Your API
| What you want in plain English | When to recommend it | Apigee behind the scenes | Scaffold flag |
|---|---|---|---|
| **Give each partner, internal app, or IDE a revocable key** | Simplest, most common way to onboard apps and assign Free/Standard/Enterprise plans | `VerifyAPIKey` + API Product | `--auth apikey` |
| **Verify login tokens from Google, Okta, Auth0, or Entra ID** | Mobile or web apps where end users sign in with an identity provider | `VerifyJWT` (validates signature via JWKS URL) | `--auth jwt --jwks-url ... --issuer ...` |
| **Issue short-lived OAuth 2.0 tokens for machine-to-machine calls** | Backend services exchanging `client_id` + `client_secret` for temporary tokens | `OAuthV2` (`GenerateAccessToken` + `VerifyAccessToken`) | `--auth oauth --token-endpoint` |
| **Know *which human user* made a call behind a shared app key** | Team gateways or IDEs where an app key authorizes access and a Bearer token identifies the user | `DecodeJWT` (extracts email/sub for quotas & analytics) | See [policies_ai_gateway.md](policies_ai_gateway.md) |
| **Restrict internal/admin endpoints to specific office or VPN IPs** | Extra perimeter lock on non-public APIs (use Cloud Armor first for internet DDoS) | `AccessControl` (or Google Cloud Armor at the load balancer) | See [policies_security.md](policies_security.md) |

### B. Stopping Overload, Abuse & Runaway Bills
| What you want in plain English | When to recommend it | Apigee behind the scenes | Scaffold flag |
|---|---|---|---|
| **Protect your backend from sudden traffic bursts or retry storms** | **Always recommend** on every REST proxy | `SpikeArrest` (smooths bursts, returns `429`) | `--spike-arrest 50ps` (on by default) |
| **Cap how many API calls each customer/tier gets per minute, day, or month** | Any multi-caller API or tiered Free/Pro/Partner product | `Quota` reading limits from the API Product | `--quota` (or `--jwt-quota 60`) |
| **Cap how many AI tokens each developer, app, or model can consume** | **Always recommend** on AI gateways to prevent denial-of-wallet bills | `LLMTokenQuota` (`EnforceOnly` + buffered/SSE `CountOnly`) | `--token-quota` (`scaffold_ai_gateway.py`) |
| **Stop a single giant prompt or runaway loop from burning thousands of tokens at once** | AI gateways exposed to chat apps, scripts, or coding agents | `PromptTokenLimit` | `--prompt-token-limit 5000pm` |
| **Enforce a hard dollar budget or prepaid wallet per developer** | Commercial APIs or internal chargeback/showback AI platforms | `Quota` (micro-dollar weight) or `MonetizationLimitsCheck` + `DataCapture` | See [policies_ai_gateway.md](policies_ai_gateway.md) |

### C. Blocking Bad Inputs, Attacks & Unsafe AI / Agent Actions
| What you want in plain English | When to recommend it | Apigee behind the scenes | Scaffold flag |
|---|---|---|---|
| **Block huge or deeply nested JSON payloads that crash parsers** | **Always recommend** on JSON APIs | `JSONThreatProtection` | Built-in default (`scaffold_api_proxy.py`) |
| **Block malicious XML / XXE entity-expansion attacks** | Any endpoint accepting `application/xml` or SOAP | `XMLThreatProtection` | `--xml-threat` |
| **Automatically reject requests that don't match your OpenAPI / Swagger spec** | Whenever the team already has an `openapi.yaml` or `.json` file | `OASValidation` + per-operation conditional flows + `404` catch-all | `--oas ./openapi.yaml` |
| **Screen AI prompts & streaming answers for prompt injection, jailbreaks, toxicity, and PII** | **Strongly recommend** whenever user text or external files go to an LLM | Google Cloud **Model Armor** (`SanitizeUserPrompt` + `SanitizeModelResponse` w/ SSE `ContinuousScan`) | `--armor-region us-central1` |
| **Control which AI agent can call which MCP tool** | Any MCP tool server with read vs write/admin tools | `ParsePayload` + `VerifyAPIKey` + Persona API Products (`payloadOperationGroup`) | `--tools ... --persona "Role:t1,t2"` |
| **Block high-risk tool inputs (e.g., refunds over \$50) and tell the agent to ask a human** | Agent tools that move money, change inventory, or modify records | `ExtractVariables` + conditional `RaiseFault` (`result.isError=true`) | `--limit "tool:arg:max:Role:CODE:msg"` |

### D. Speeding Up Responses & Lowering Backend / AI Costs
| What you want in plain English | When to recommend it | Apigee behind the scenes | Scaffold flag |
|---|---|---|---|
| **Cache repeated `GET` API responses for a few seconds or minutes** | Product catalogs, reference lookups, or slow read-heavy backends | `ResponseCache` (skips non-200 and non-GET automatically) | `--cache-ttl 60` |
| **Reuse AI answers when users ask the same or very similar questions** | Customer support bots, FAQ assistants, and search/RAG summaries | `SemanticCacheLookup` + `SemanticCachePopulate` (Vertex Vector Search) | `--cache-region ... --index-id ...` |
| **Automatically send simple prompts to cheap/fast AI models and hard ones to frontier models** | Teams wanting frontier quality on coding/reasoning without paying frontier prices on "hi" or summaries | Classifier `ServiceCallout` + `AutoRouting.js` + Product `routing.model.*` attributes | `--auto-route` |
| **Stream AI answers live word-by-word without 30-second timeouts** | Chat UIs, Claude Code, Antigravity, GitHub Copilot, and agent CLIs | Dedicated `*-stream-target` (`response.streaming.enabled=true`, 300s timeout) + `<EventFlow>` | Built-in default (`--sse`) |

### E. Connecting Privately, Supporting Browsers & Operating Cleanly
| What you want in plain English | When to recommend it | Apigee behind the scenes | Scaffold flag |
|---|---|---|---|
| **Let a React / Vue / Angular web app call the API from a browser** | Any browser frontend calling the gateway on another domain | `CORS` policy (handles `OPTIONS` preflight before auth) | `--cors "https://app.example.com"` |
| **Keep Cloud Run / Cloud Functions private (no public unauthenticated URL)** | Any backend hosted on Cloud Run or behind Identity-Aware Proxy | `GoogleIDToken` on `TargetEndpoint` (uses runtime Service Account) | `--target-auth idtoken` |
| **Call Google Cloud APIs (Vertex AI, Storage, BigQuery) without API keys** | Gateways fronting Google Cloud services | `GoogleAccessToken` on `TargetEndpoint` | `--target-auth accesstoken` |
| **Verify mutual TLS (two-way certificates) with an on-prem or regulated backend** | Banking, healthcare, or zero-trust private backends | `<SSLInfo>` `ClientAuthEnabled` + Keystore & Truststore `ref://` references | `--target-mtls ref://ks:alias --target-truststore ref://ts` |
| **Hide caller keys from backends & replace ugly 500 stack traces with clean JSON errors** | **Always recommend** on every gateway | `AssignMessage` (`Remove` auth headers) + `DefaultFaultRule` | Built-in default |
| **Use different backend URLs in dev, staging, and prod without changing proxy code** | Any API promoted across multiple environments | Environment `TargetServer` (`apigee_ctl.py targetserver-apply`) | `--target-server backend-srv` |
| **Store third-party backend API keys, model rate cards, or thresholds safely** | Whenever the gateway itself needs a secret or tunable setting | Encrypted **KVM** (`apigee_ctl.py kvm-put` + `KeyValueMapOperations` into `private.*`) | See [api_proxy_patterns.md](api_proxy_patterns.md) |
| **Send an audit log of every call to Cloud Logging with zero latency impact** | Production observability, security auditing, and SIEM export | `MessageLogging` inside `PostClientFlow` (runs after client gets response) | `--cloud-logging` |

---

## 3. One-Sentence Apigee Jargon Translator

When a user sees an Apigee term in the Google Cloud Console or generated files, explain it in one sentence:

| Apigee Term | Plain-English Meaning |
|---|---|
| **API Proxy** | The protective doorway Apigee puts in front of your backend URL to check callers, enforce limits, and route traffic. |
| **ProxyEndpoint vs TargetEndpoint** | `ProxyEndpoint` is the front door facing your callers; `TargetEndpoint` is the back door connecting to your backend service or AI model. |
| **PreFlow / Conditional Flow / PostFlow** | `PreFlow` runs on *every* request first (login & burst checks); `Conditional Flows` run only on specific URLs/verbs; `PostFlow` runs right before returning the response. |
| **PostClientFlow** | A background step that runs *after* the caller already received their answer — ideal for audit logging with zero added latency. |
| **EventFlow** | A real-time stream filter for Server-Sent Events (SSE) that scans AI chunks for safety and counts tokens on the final chunk. |
| **API Product** | An **Access Plan** (e.g., *Free*, *Partner*, *Support Agent*, *Engineering AI*) defining which paths, AI models, or MCP tools a key can use and its quota — editable live without redeploying the proxy. |
| **Developer & Developer App** | The registered owner and application record that holds a caller's revocable API key (`client_id`). |
| **SpikeArrest vs Quota** | `SpikeArrest` smooths sudden millisecond/second bursts to keep servers alive; `Quota` counts hourly/daily/monthly business allowances per customer plan. |
| **Shared Flow & Flow Hook** | A `Shared Flow` is a reusable pack of policies; a `Flow Hook` automatically runs it on *every* API in an environment so no team can skip company security rules. |
| **TargetServer** | An environment-specific backend hostname setting (so `dev` hits `dev.internal` and `prod` hits `prod.internal` using the exact same proxy revision). |
| **KVM (Key Value Map)** | Apigee's encrypted settings/secrets store per environment — where backend API keys, pricing tables, and thresholds live out of source control. |
| **Model Armor (`SanitizeUserPrompt` / `SanitizeModelResponse`)** | Google Cloud's AI safety shield that blocks prompt injection, jailbreaks, toxic content, and sensitive data (PII) in prompts and AI replies. |
