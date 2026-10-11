---
name: apigee-proxy-builder
description: >
  Core, standalone skill for authoring, reviewing, debugging, validating, packaging and deploying Apigee X
  API proxies and shared flows - the foundation for API Management, AI Gateway (LLM) and MCP Tools Gateway
  proxies. Covers bundle anatomy, 60+ policy types (including LLMTokenQuota, PromptTokenLimit,
  SanitizeUserPrompt/SanitizeModelResponse, SemanticCacheLookup/Populate, ParsePayload,
  MonetizationLimitsCheck, DataCapture, TraceCapture), flows, conditions, endpoints, routing, fault handling,
  JavaScript callouts and shared flows. Start here for any Apigee proxy task; it routes to the specialised
  apigee-api-proxy, apigee-ai-gateway, apigee-mcp-gateway and apigee-proxy-lifecycle skills.
---

# Apigee X API Proxy Development

Comprehensive skill for building API proxies on Google Cloud's Apigee X platform. Covers the full proxy development lifecycle: bundle structure, endpoint configuration, flow design, policy implementation, fault handling, JavaScript extensibility, shared flows, and advanced patterns.

**This skill covers:** API proxy bundle authoring, policy configuration (60+ policy types, including the AI/LLM and MCP policies), flow execution design, conditional routing, fault handling, JavaScript callouts, shared flows, automated bundle validation, and advanced development patterns.

**This skill does NOT cover:** Apigee Hybrid-specific infrastructure provisioning, CI/CD pipeline server setup, or GCP IAM role creation. Focus is on proxy development artifacts and gateway execution logic.

---

## Skill Map: pick the right companion skill

All five skills live in this repo and share this skill's validator and references.

| Task | Skill | Fast path |
|---|---|---|
| New REST/HTTP API proxy (API key, OAuth, JWT, OAS validation, quota, caching, CORS) or a reusable shared flow | [`apigee-api-proxy`](../apigee-api-proxy/SKILL.md) | `scaffold_api_proxy.py`, `scaffold_shared_flow.py` |
| LLM / AI Gateway proxy (Gemini, Claude, model routing, Model Armor, token quota, semantic cache, cost) | [`apigee-ai-gateway`](../apigee-ai-gateway/SKILL.md) | `scaffold_ai_gateway.py` |
| MCP / Tools Gateway proxy (per-tool entitlement, tool-argument limits, keyless handshake) | [`apigee-mcp-gateway`](../apigee-mcp-gateway/SKILL.md) | `scaffold_mcp_proxy.py` |
| **Change an existing proxy** (export the deployed revision, edit, diff, redeploy, roll back), products, KVMs, target servers, flow hooks, trace | [`apigee-proxy-lifecycle`](../apigee-proxy-lifecycle/SKILL.md) | `apigee_ctl.py`, `bundle_edit.py` |
| Hand-authoring policies, flows, conditions or debugging any proxy | this skill | references below + `validate_bundle.py` |

**Golden rule for every task:** generate or edit, then `validate_bundle.py`, then (for an existing proxy) `apigee_ctl.py diff`,
then deploy. Never deploy a bundle that fails validation.

---

## When to Use This Skill

Use this skill when the user is:
- Building new API proxy bundles from scratch for API Management, AI Gateway, or Tools Gateway
- Governing LLM traffic: token quotas, prompt token limits, Model Armor, semantic cache, monetization
- Governing MCP tool calls: ParsePayload, per-tool API products, business-rule limits
- Writing or configuring Apigee policies (security, mediation, traffic management, caching, integration)
- Designing flow pipelines (PreFlow, conditional flows, PostFlow, PostClientFlow)
- Configuring ProxyEndpoints, TargetEndpoints, or RouteRules
- Implementing fault handling (FaultRules, DefaultFaultRule, RaiseFault)
- Writing JavaScript callout code for Apigee proxies
- Creating or consuming shared flows
- Debugging proxy execution or policy errors
- Reviewing existing proxy configurations for correctness or best practices
- Asking about Apigee X policy behavior, flow variables, or conditions

---

## Consulting Official Documentation

When you need to look up specific policy syntax, verify behavior, or find recent changes:
- **Web search**: Use `site:cloud.google.com/apigee` to restrict results to official Apigee X docs
- **Policy reference**: Fetch `https://cloud.google.com/apigee/docs/api-platform/reference/policies/[policy-name]-policy`
- **Configuration reference**: Fetch `https://cloud.google.com/apigee/docs/api-platform/reference/api-proxy-configuration-reference`
- **Flow variables**: Fetch `https://cloud.google.com/apigee/docs/api-platform/reference/variables-reference`
- **Conditions**: Fetch `https://cloud.google.com/apigee/docs/api-platform/reference/conditions-reference`
- **Vetted examples**: Browse `https://github.com/GoogleCloudPlatform/apigee-samples` for production patterns

> [!IMPORTANT]
> Do NOT use `docs.apigee.com` — that is legacy Apigee Edge documentation. Always use `cloud.google.com/apigee` for Apigee X.

---

## API Proxy Bundle Quick Reference

```text
apiproxy/
├── <ProxyName>.xml                # Root proxy definition (name + revision)
├── proxies/                       # ProxyEndpoint definitions
│   └── default.xml
├── targets/                       # TargetEndpoint definitions
│   └── default.xml
├── policies/                      # All policy XML files
│   ├── AM-SetHeaders.xml
│   ├── EV-ExtractPath.xml
│   ├── VAK-VerifyApiKey.xml
│   └── ...
└── resources/                     # Custom code and resources
    ├── jsc/                       # JavaScript files
    ├── java/                      # Java JAR files
    └── xsl/                       # XSLT transformations
```

### Policy Naming Conventions

Follow the `[Abbreviation]-[Purpose].xml` pattern:

| Abbreviation | Policy Type | Example |
|---|---|---|
| **AM** | AssignMessage | `AM-SetHeaders.xml` |
| **EV** | ExtractVariables | `EV-ExtractTokenUsage.xml` |
| **SC** | ServiceCallout | `SC-CallBackend.xml` |
| **RF** | RaiseFault | `RF-InvalidInput.xml` |
| **FC** | FlowCallout | `FC-AuthSharedFlow.xml` |
| **SA** | SpikeArrest | `SA-SpikeArrest.xml` |
| **Q** | Quota | `Q-TokenQuota.xml` |
| **RC** | ResponseCache | `RC-ResponseCache.xml` |
| **LC** | LookupCache | `LC-GetCachedValue.xml` |
| **PC** | PopulateCache | `PC-StoreValue.xml` |
| **IC** | InvalidateCache | `IC-ClearCache.xml` |
| **KVM** | KeyValueMapOperations | `KVM-GetConfig.xml` |
| **JS** | JavaScript | `JS-RedactPII.xml` |
| **JWT** | JWT policies | `JWT-VerifyToken.xml` |
| **OAuth** | OAuthV2 | `OAuth-VerifyToken.xml` |
| **VA** / **VAK** | VerifyAPIKey | `VAK-VerifyApiKey.xml` |
| **ML** | MessageLogging | `ML-LogToCloud.xml` |
| **DC** | DataCapture | `DC-CaptureMetrics.xml` |
| **CORS** | CORS | `CORS-AllowOrigins.xml` |
| **JTP** | JSONThreatProtection | `JTP-ValidatePayload.xml` |
| **XTP** | XMLThreatProtection | `XTP-ValidateXML.xml` |
| **OAS** | OASValidation | `OAS-ValidateRequest.xml` |
| **QC** | Quota (cost/budget counter) | `QC-EnforceBudgetLimit.xml` |
| **AE** | AccessEntity | `AE-CallerKey.xml` |
| **DJWT** | DecodeJWT | `DJWT-ExtractUserIdentity.xml` |
| **LTQ** | LLMTokenQuota | `LTQ-TokenEnforce.xml` / `LTQ-TokenCount.xml` |
| **PTL** | PromptTokenLimit | `PTL-PromptTokenLimit.xml` |
| **SUP** | SanitizeUserPrompt (Model Armor) | `SUP-UserPrompt.xml` |
| **SMR** | SanitizeModelResponse (Model Armor) | `SMR-SanitizeModelResponse.xml` |
| **SCL** | SemanticCacheLookup | `SCL-Semantic-Cache-Lookup.xml` |
| **SCP** | SemanticCachePopulate | `SCP-Semantic-Cache-Populate.xml` |
| **MLC** | MonetizationLimitsCheck | `MLC-EnforceMonetizationLimits.xml` |
| **PP** | ParsePayload (MCP / JSON-RPC) | `PP-MCP.xml` |
| **TC** | TraceCapture | `TC-GatewayTrace.xml` |
| **PY** | PythonScript | `PY-Transform.xml` |

See [proxy_bundle_anatomy.md](references/proxy_bundle_anatomy.md) for full details.

---

## Development Workflow

### Phase 1: Define Proxy Structure
1. Create the `apiproxy/` directory with root XML, `proxies/`, `targets/`, `policies/`.
2. Configure ProxyEndpoint: set BasePath, define HTTPProxyConnection.
3. Configure TargetEndpoint: set backend URL or LoadBalancer.
4. Set up RouteRules to connect proxy to target.

*Reference:* [proxy_bundle_anatomy.md](references/proxy_bundle_anatomy.md), [endpoints_and_routing.md](references/endpoints_and_routing.md)

### Phase 2: Design Flow Pipeline
1. Map API operations to conditional flows using verb + path conditions.
2. Place security policies in PreFlow (execute for every request).
3. Place business logic in conditional flows (execute per operation).
4. Place response headers and caching in PostFlow.
5. Place MessageLogging in PostClientFlow (guarantees execution even on fault).

*Reference:* [flows_and_execution.md](references/flows_and_execution.md), [flow_variables_and_conditions.md](references/flow_variables_and_conditions.md)

### Phase 3: Implement Policies
Follow this ordering within flows — security first, then mediation, then traffic:
1. **Security**: VerifyAPIKey, OAuthV2, VerifyJWT, AccessControl, threat protection
2. **Mediation**: ExtractVariables, AssignMessage, transformations
3. **Traffic Management**: SpikeArrest, Quota
4. **Caching**: ResponseCache (typically in PostFlow response)
5. **Logging**: MessageLogging (typically in PostClientFlow)

For **LLM proxies**, the order is: identity → VerifyAPIKey → cheap refusals → SanitizeUserPrompt →
PromptTokenLimit → MonetizationLimitsCheck / budget → SemanticCacheLookup → (on a miss) LLMTokenQuota
enforce + routing. In the response: cost → LLMTokenQuota count → DataCapture → SemanticCachePopulate →
SanitizeModelResponse. For **MCP proxies**: ParsePayload → method allowlist → VerifyAPIKey + Quota on
`tools/*` → argument limits.

*Reference:* [policies_security.md](references/policies_security.md), [policies_mediation.md](references/policies_mediation.md), [policies_traffic_management.md](references/policies_traffic_management.md), [policies_caching.md](references/policies_caching.md), [policies_integration.md](references/policies_integration.md), [policies_ai_gateway.md](references/policies_ai_gateway.md), [policies_mcp_tools.md](references/policies_mcp_tools.md)

### Phase 4: Add Fault Handling
1. Define FaultRules for known error types (auth failures, quota exceeded, backend errors).
2. Add DefaultFaultRule as catch-all with consistent error format.
3. Use RaiseFault for custom validation errors.
4. Set `continueOnError` only for policies where failure is acceptable.

*Reference:* [fault_handling.md](references/fault_handling.md)

### Phase 5: Optimize and Extend
1. Extract reusable policy sequences into shared flows.
2. Add response caching where appropriate.
3. Consider advanced patterns: proxy chaining, composite APIs, circuit breakers.
4. Add JavaScript callouts for complex transformations only when policies don't suffice.

*Reference:* [shared_flows.md](references/shared_flows.md), [javascript_development.md](references/javascript_development.md), [advanced_patterns.md](references/advanced_patterns.md), [anti_patterns_and_best_practices.md](references/anti_patterns_and_best_practices.md)

---

## Flow Execution Model

```text
CLIENT REQUEST
     │
     ▼
┌─────────────────────────────────────────────────┐
│  PROXY ENDPOINT                                 │
│  ┌──────────┐  ┌────────────────┐  ┌─────────┐ │
│  │ PreFlow  │→ │ Conditional    │→ │PostFlow │ │
│  │ (Request)│  │ Flows (Request)│  │(Request)│ │
│  └──────────┘  └────────────────┘  └─────────┘ │
│                                                 │
│  RouteRule evaluation → select TargetEndpoint   │
└─────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────┐
│  TARGET ENDPOINT                                │
│  ┌──────────┐  ┌────────────────┐  ┌─────────┐ │
│  │ PreFlow  │→ │ Conditional    │→ │PostFlow │ │
│  │ (Request)│  │ Flows (Request)│  │(Request)│ │
│  └──────────┘  └────────────────┘  └─────────┘ │
└─────────────────────────────────────────────────┘
     │
     ▼
  BACKEND SERVICE (request sent, response received)
     │
     ▼
┌─────────────────────────────────────────────────┐
│  TARGET ENDPOINT                                │
│  ┌──────────┐  ┌─────────────────┐ ┌─────────┐ │
│  │ PreFlow  │→ │ Conditional     │→│PostFlow │ │
│  │(Response)│  │ Flows (Response)│ │(Response│ │
│  └──────────┘  └─────────────────┘ └─────────┘ │
└─────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────┐
│  PROXY ENDPOINT                                 │
│  ┌──────────┐  ┌─────────────────┐ ┌─────────┐ │
│  │ PreFlow  │→ │ Conditional     │→│PostFlow │ │
│  │(Response)│  │ Flows (Response)│ │(Response│ │
│  └──────────┘  └─────────────────┘ └─────────┘ │
└─────────────────────────────────────────────────┘
     │
     ▼
CLIENT RESPONSE SENT
     │
     ▼
┌─────────────────────────────────────────────────┐
│  PostClientFlow (async — after response sent)   │
│  Only: MessageLogging, FlowCallout              │
└─────────────────────────────────────────────────┘
```

---

## Comprehensive Reference Index

| Document | Focus Area |
|---|---|
| [**`proxy_bundle_anatomy.md`**](references/proxy_bundle_anatomy.md) | Bundle directory structure, file types, naming conventions |
| [**`endpoints_and_routing.md`**](references/endpoints_and_routing.md) | ProxyEndpoint, TargetEndpoint, RouteRules, proxy chaining |
| [**`flows_and_execution.md`**](references/flows_and_execution.md) | Flow pipeline, PreFlow/PostFlow/conditional flows, execution order |
| [**`flow_variables_and_conditions.md`**](references/flow_variables_and_conditions.md) | Variable system, conditions syntax, message templates |
| [**`policies_traffic_management.md`**](references/policies_traffic_management.md) | SpikeArrest, Quota, ResetQuota |
| [**`policies_caching.md`**](references/policies_caching.md) | ResponseCache, cache-aside pattern, PopulateCache, LookupCache, InvalidateCache |
| [**`policies_security.md`**](references/policies_security.md) | API keys, OAuth 2.0, JWT, CORS, threat protection, IAM |
| [**`policies_mediation.md`**](references/policies_mediation.md) | AssignMessage, ExtractVariables, JSON/XML transforms |
| [**`policies_integration.md`**](references/policies_integration.md) | ServiceCallout, FlowCallout, KVM deep dive, PropertySets, logging, RaiseFault |
| [**`policies_ai_gateway.md`**](references/policies_ai_gateway.md) | **Newer AI policies**: LLMTokenQuota pair, PromptTokenLimit, Model Armor (SUP/SMR), semantic cache, MonetizationLimitsCheck, DataCapture, TraceCapture, budget Quota pair, LLM flow order |
| [**`policies_mcp_tools.md`**](references/policies_mcp_tools.md) | **MCP Tools Gateway**: ParsePayload, per-tool API products, method allowlist, JSON-RPC business-limit faults, AccessEntity |
| [**`policy_reference.md`**](references/policy_reference.md) | One-page cheat sheet of the most used policies |
| [**`load_balancing_and_routing.md`**](references/load_balancing_and_routing.md) | Target servers, load balancing algorithms, health monitors, advanced routing |
| [**`fault_handling.md`**](references/fault_handling.md) | FaultRules, DefaultFaultRule, error flows, error responses |
| [**`javascript_development.md`**](references/javascript_development.md) | JavaScript object model, patterns, best practices |
| [**`shared_flows.md`**](references/shared_flows.md) | Creating and consuming shared flows, flow hooks |
| [**`advanced_patterns.md`**](references/advanced_patterns.md) | Proxy chaining, composite APIs, circuit breaker, AI/LLM token policies |
| [**`anti_patterns_and_best_practices.md`**](references/anti_patterns_and_best_practices.md) | Common mistakes, production best practices |
| [**`debugging_and_performance.md`**](references/debugging_and_performance.md) | Debug sessions, trace methodology, performance optimization |
| [**`multi_tenant_patterns.md`**](references/multi_tenant_patterns.md) | Multi-tenant routing, isolation, per-tenant config and rate limiting |
| [**`websockets_and_streaming.md`**](references/websockets_and_streaming.md) | WebSocket proxying, SSE, HTTP streaming, timeout gotchas |
| [**`end_to_end_examples.md`**](references/end_to_end_examples.md) | 4 complete proxy bundle walkthroughs |

---

## Validation, Packaging & Deployment (standalone)

The skill ships its own scripts (Python 3.9+ stdlib, bash, curl, zip, gcloud), so it works in any repository or none:

```bash
SKILL=<path-to>/skills/apigee-proxy-builder

# Validate structure + security + AI/MCP pitfalls.
# Accepts a proxy dir, a shared flow dir, an apiproxy/ or sharedflowbundle/ dir, or a .zip
python3 $SKILL/scripts/validate_bundle.py ./my-proxy          # --strict fails on warnings, --json for tooling

# Package only (zip lands in a temp dir unless --out is given)
bash $SKILL/scripts/deploy_bundle.sh ./my-proxy --package-only --out ./my-proxy.zip

# Validate + import + deploy + wait for READY (works for apiproxy/ and sharedflowbundle/)
bash $SKILL/scripts/deploy_bundle.sh ./my-proxy --org $APIGEE_ORG --env $APIGEE_ENV \
  --sa apigee-runtime@$PROJECT.iam.gserviceaccount.com        # needed for Google auth targets, Model Armor, Cloud Logging
```

For **existing** proxies (export the deployed revision, diff, update, roll back) use
[`apigee-proxy-lifecycle`](../apigee-proxy-lifecycle/SKILL.md) (`apigee_ctl.py update` = validate + import + deploy + wait).

What `validate_bundle.py` catches, beyond XML and reference checks:
- Policies/resources missing, unattached or not listed in the manifest; RouteRules to missing targets; duplicate BasePaths
- No authentication policy attached anywhere (warning), hardcoded secrets (Google API keys, bearer tokens,
  private keys, literal passwords), KVM `<InitialEntries>` seeded from the bundle
- An LLMTokenQuota or Quota pair that shares a `SharedName` but disagrees on Identifier, model source or limits (silent no-op quota)
- An EnforceOnly limit with `continueOnError="true"` and no RaiseFault checking `ratelimit.<name>.failed` (never enforced)
- `DataCapture` `default="{var}"`, which is recorded literally
- Legacy or invalid AI syntax (`<CacheConfig>`, `<Source>` in the Sanitize policies, missing `<ModelArmor><TemplateName>`)
- Conditions that reference `parsepayload.<name>.*` without a matching ParsePayload policy

> [!TIP]
> If a repository provides its own wrappers or CI checks, run those too for repository conventions; the skill
> scripts provide the portable baseline.
