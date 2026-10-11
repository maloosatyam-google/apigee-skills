# AI Gateway Policies (the newer Apigee X policy types)

Policies for governing LLM traffic, with syntax checked against `cloud.google.com/apigee` and
against a production AI Gateway proxy. Every example works as written. Swap the
`{PROJECT}`, `{REGION}` and `{...}` placeholders for your own values.

| Policy | Prefix | Phase | Purpose |
|---|---|---|---|
| `SanitizeUserPrompt` | `SUP-` | Request | Model Armor screen of the prompt (injection, jailbreak, RAI, SDP, malicious URI) |
| `SanitizeModelResponse` | `SMR-` | Response | Model Armor screen of the model output |
| `PromptTokenLimit` | `PTL-` | Request | Spike-arrest on **prompt tokens** (short-term surge protection) |
| `LLMTokenQuota` | `LTQ-` | Req + Resp | Token quota over minutes to months. Uses an enforce/count pair |
| `SemanticCacheLookup` | `SCL-` | Request | Vector-similarity cache lookup. A hit short-circuits the target |
| `SemanticCachePopulate` | `SCP-` | Response | Upsert the prompt/response into the Vector Search index |
| `MonetizationLimitsCheck` | `MLC-` | Request | Block when the prepaid wallet or subscription limits are exhausted |
| `DataCapture` | `DC-` | Response / Fault | Custom analytics dimensions, plus monetization-scope variables |
| `ParsePayload` | `PP-` | Request | Parse MCP / JSON-RPC 2.0 bodies into variables. See `policies_mcp_tools.md` |
| `TraceCapture` (Preview) | `TC-` | Any | Add custom variables to distributed traces |
| `DecodeJWT` | `DJWT-` | Request | Read caller claims for **attribution** (no signature check) |
| `OASValidation` | `OAS-` | Request | Validate the request against OpenAPI, **including the body** |

> [!IMPORTANT]
> LLMTokenQuota, PromptTokenLimit, SanitizeUserPrompt, SanitizeModelResponse,
> SemanticCacheLookup/Populate and ParsePayload are **Extensible** policies, which can have
> licence or cost implications. LLMTokenQuota and PromptTokenLimit are available on Apigee X but
> **not on hybrid**. The Apigee runtime service account needs `roles/modelarmor.user` for
> the Sanitize policies and `roles/aiplatform.user` for the semantic cache policies.

---

## 1. SanitizeUserPrompt (Model Armor, request)

```xml
<SanitizeUserPrompt async="false" continueOnError="false" enabled="true" name="SUP-UserPrompt">
  <DisplayName>SUP-UserPrompt</DisplayName>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <ModelArmor>
    <TemplateName>projects/{PROJECT}/locations/{REGION}/templates/{PROMPT_TEMPLATE}</TemplateName>
  </ModelArmor>
  <!-- Message template. Point it at a pre-extracted variable (provider-agnostic) or a jsonPath. -->
  <UserPromptSource>{flow.userPrompt}</UserPromptSource>
  <!-- Optional: also screen tool results that are fed back to the model -->
  <!-- <FunctionResponseSource>{jsonPath('$.contents[-1].parts[-1].functionResponse.response',request.content,true)}</FunctionResponseSource> -->
</SanitizeUserPrompt>
```

- Default `UserPromptSource` (when omitted) is the Gemini shape: `{jsonPath('$.contents[-1].parts[-1].text',request.content,true)}`.
- For multi-provider gateways, extract the prompt **once** in JavaScript (Gemini `contents[].parts[].text`,
  Anthropic/OpenAI `messages[].content`, flat `prompt`) into `flow.userPrompt`. Reuse that variable
  in SUP, PTL and SCL.
- Guard the step: `<Condition>flow.userPrompt != null and flow.userPrompt != ""</Condition>`.
  An empty prompt otherwise raises `FailedToExtractUserPrompt`.
- `continueOnError="false"` blocks the request (fault `steps.sanitize.user.prompt.response.FilterMatched`).
- Variables: `SanitizeUserPrompt.{policy}.filterMatchState`, `.promptInjectionDetected`,
  `.promptInjectionConfidence`, `.raiMatchesFound`, `.maliciousURIsDetected`, `.csamFilterMatched`,
  `.invocationResult`, `.failed`.
- Faults: `steps.sanitize.modelarmor.{AuthenticationFailure|ModelArmorAPIFailed|ServiceUnavailable|ModelArmorTemplateNameExtractionFailed}`,
  `steps.sanitize.user.prompt.{FailedToExtractUserPrompt|SanitizationResponseParsingFailed|InternalError}`.

**Placement:** after authentication (VerifyAPIKey), so unauthenticated callers get a 401 and you
are not charged for a Model Armor call. Also put it after any cheap refusals (unsupported path,
streaming not supported), for the same reason.

## 2. SanitizeModelResponse (Model Armor, response)

```xml
<SanitizeModelResponse async="false" continueOnError="true" enabled="true" name="SMR-SanitizeModelResponse">
  <DisplayName>SMR-SanitizeModelResponse</DisplayName>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <ModelArmor>
    <TemplateName>projects/{PROJECT}/locations/{REGION}/templates/{RESPONSE_TEMPLATE}</TemplateName>
  </ModelArmor>
  <!-- Docs name this <LLMResponseSource>; ModelResponseSource is also accepted by the runtime -->
  <LLMResponseSource>{jsonPath('$.candidates[-1].content.parts',response.content,true)}</LLMResponseSource>
  <!-- Optional --> <!-- <UserPromptSource>{flow.userPrompt}</UserPromptSource> -->
  <!-- Optional --> <!-- <FunctionCallSource>{jsonPath('$.candidates[-1].content.parts[-1].functionCall.args',response.content,true)}</FunctionCallSource> -->
</SanitizeModelResponse>
```

- Run in the ProxyEndpoint PostFlow Response with `response.status.code = 200`.
- The default extraction is Gemini-shaped. If a target returns another shape (for example Anthropic
  `content[]`), either normalise the response to Gemini first or skip the step for that provider.
- Faults: `steps.sanitize.model.response.{FilterMatched|FailedToExtractLLMResponse|ModelArmorAPIFailed|...}`.
- `continueOnError="true"` gives monitor mode. Set it to `false` to block.

## 3. PromptTokenLimit (token spike arrest)

```xml
<PromptTokenLimit continueOnError="false" enabled="true" name="PTL-PromptTokenLimit">
  <DisplayName>PTL-PromptTokenLimit</DisplayName>
  <Properties/>
  <UserPromptSource>{flow.userPrompt}</UserPromptSource>
  <Identifier ref="verifyapikey.VA-VerifyAPIKey.client_id"/>
  <Rate>5000pm</Rate>              <!-- prompt tokens per minute (pm) or second (ps) -->
  <UseEffectiveCount>true</UseEffectiveCount>
  <IgnoreUnresolvedVariables>false</IgnoreUnresolvedVariables>
</PromptTokenLimit>
```

- **PromptTokenLimit compared with LLMTokenQuota:** PTL works like SpikeArrest. It counts the
  prompt tokens before the call and smooths surges. LTQ is a business quota on actual
  consumption (prompt + output) over a longer window.
- `<Rate ref="...">` can read a rate from a product attribute.
- Variables: `ratelimit.{policy}.failed`, `.userPromptTokenCount`, `.resolvedUserPrompt`.
- Faults: `policies.prompttokenlimit.PromptTokenLimitViolation` (429),
  `FailedToExtractUserPrompt`, `FailedToCalculateUserPromptTokens`.

## 4. LLMTokenQuota (token quota, enforce/count pair)

The supported pattern uses **two policies that share a counter**:
- An **EnforceOnly** policy in the request checks the counter and does not increment it.
- A **CountOnly** policy in the response adds the real token usage.

```xml
<!-- Request: after VerifyAPIKey -->
<LLMTokenQuota continueOnError="false" enabled="true" name="LTQ-TokenEnforce" type="rollingwindow">
  <DisplayName>LTQ-TokenEnforce</DisplayName>
  <Allow count="1000" countRef="verifyapikey.VA-VerifyAPIKey.apiproduct.developer.llmQuota.limit"/>
  <Interval ref="verifyapikey.VA-VerifyAPIKey.apiproduct.developer.llmQuota.interval">1</Interval>
  <TimeUnit ref="verifyapikey.VA-VerifyAPIKey.apiproduct.developer.llmQuota.timeunit">minute</TimeUnit>
  <Distributed>true</Distributed>
  <Synchronous>true</Synchronous>
  <Identifier ref="flow.emailId"/>                                   <!-- MUST match the count policy -->
  <LLMModelSource>{firstnonnull(flow.quota_model,flow.model)}</LLMModelSource>  <!-- MUST match -->
  <EnforceOnly>true</EnforceOnly>
  <SharedName>common-counter</SharedName>                            <!-- MUST match -->
</LLMTokenQuota>
```

```xml
<!-- Response: PostFlow, only on 200 and NOT on cache hits -->
<LLMTokenQuota continueOnError="true" enabled="true" name="LTQ-TokenCount" type="rollingwindow">
  <DisplayName>LTQ-TokenCount</DisplayName>
  <Allow count="1000" countRef="verifyapikey.VA-VerifyAPIKey.apiproduct.developer.llmQuota.limit"/>
  <Interval ref="verifyapikey.VA-VerifyAPIKey.apiproduct.developer.llmQuota.interval">1</Interval>
  <TimeUnit ref="verifyapikey.VA-VerifyAPIKey.apiproduct.developer.llmQuota.timeunit">minute</TimeUnit>
  <Distributed>true</Distributed>
  <Synchronous>true</Synchronous>
  <Identifier ref="flow.emailId"/>
  <LLMTokenUsageSource>{jsonPath('$.usageMetadata.totalTokenCount',response.content,true)}</LLMTokenUsageSource>
  <LLMModelSource>{firstnonnull(flow.quota_model,flow.model)}</LLMModelSource>
  <CountOnly>true</CountOnly>
  <SharedName>common-counter</SharedName>
</LLMTokenQuota>
```

**How the limit is resolved.** VerifyAPIKey matches the key to an API product that has an
`llmOperationGroup`. The model string from `LLMModelSource` selects the operationConfig that
supplies `...developer.llmQuota.limit/interval/timeunit`. The literal `count`/`Interval`/`TimeUnit`
values are only a fallback.

```json
"llmOperationGroup": {
  "operationConfigs": [
    { "apiSource": "ai-gateway-v1",
      "llmOperations": [ { "resource": "/models/gemini-3.6-flash:*", "methods": ["POST"], "model": "gemini-3.6-flash" } ],
      "llmTokenQuota": { "limit": "10000", "interval": "1", "timeUnit": "minute" } },
    { "apiSource": "ai-gateway-v1",
      "llmOperations": [ { "resource": "/auto", "methods": ["POST"], "model": "auto" } ],
      "llmTokenQuota": { "limit": "50000", "interval": "1", "timeUnit": "minute" } }
  ]
}
```

**Defects found in production (avoid these):**
1. **Mismatched `Identifier`, `SharedName` or `LLMModelSource` between the pair.** The enforcer
   then reads a counter that nothing writes to, and the quota admits everything.
2. **The model string must match an operationConfig `model` exactly.** If anything overwrites
   `flow.model` mid-flow (for example an ExtractVariables of `$.modelVersion` into a variable named
   `model`, or an auto-router writing the routed model), the lookup fails with
   `keymanagement.service.InvalidAPICallAsNoApiProductMatchFound`. With `continueOnError="true"`
   the failure is silent. Vertex reports Claude as `claude-x-y-YYYYMMDD` while the request used
   `claude-x-y@YYYYMMDD`. Keep a separate `flow.quota_model` (for example `auto`) that never changes.
3. **Key per user, not per app,** when many people share one app credential. Otherwise one
   person's usage triggers 429s for everyone (`<Identifier ref="flow.emailId"/>`).
4. **Skip the count on semantic-cache hits** (`flow.cached != "true"`). A cached answer should not
   use up quota.
5. **`ratelimit.{CountOnly}.allowed.count` is `Long.MAX_VALUE`.** For "x% used" warnings, read
   the limit from `verifyapikey.{VA}.apiproduct.developer.llmQuota.limit` instead.
6. For SSE, place the count policy in an `<EventFlow content-type="text/event-stream"><Response>`. It
   counts only on the event that carries usage metadata.

Variables: `ratelimit.{policy}.used.count`, `.allowed.count`, `.exceed.count`, `.expiry.time`,
`.failed`. Faults: `policies.llmtokenquota.LLMTokenQuotaViolation` (429),
`FailedToResolveModelName` (400), `FailedToResolveTokenUsageCount` (500),
`MessageTemplateExtractionFailed`, `InvalidConfiguration`.

## 5. SemanticCacheLookup / SemanticCachePopulate

```xml
<SemanticCacheLookup async="false" continueOnError="true" enabled="true" name="SCL-Semantic-Cache-Lookup">
  <DisplayName>SCL-Semantic-Cache-Lookup</DisplayName>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <UserPromptSource>{flow.userPrompt}</UserPromptSource>
  <Embeddings>
    <VertexAI>
      <URL>https://{REGION}-aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/publishers/google/models/text-embedding-005:predict</URL>
    </VertexAI>
  </Embeddings>
  <SimilaritySearch>
    <VertexAI>
      <URL>https://{PUBLIC_DOMAIN}/v1/projects/{PROJECT}/locations/{REGION}/indexEndpoints/{INDEX_ENDPOINT_ID}:findNeighbors</URL>
      <DeployedIndexID>{DEPLOYED_INDEX_ID}</DeployedIndexID>
      <Threshold>0.95</Threshold>
      <!-- optional: <DistanceMeasureType>DOT_PRODUCT_DISTANCE</DistanceMeasureType> -->
    </VertexAI>
  </SimilaritySearch>
</SemanticCacheLookup>
```

```xml
<SemanticCachePopulate async="false" continueOnError="true" enabled="true" name="SCP-Semantic-Cache-Populate">
  <DisplayName>SCP-Semantic-Cache-Populate</DisplayName>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <SimilaritySearch>
    <VertexAI>
      <URL>https://{REGION}-aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/indexes/{INDEX_ID}:upsertDatapoints</URL>
    </VertexAI>
  </SimilaritySearch>
  <TTLInSeconds>180</TTLInSeconds>
</SemanticCachePopulate>
```

- Prerequisites: a Vector Search index with **streaming updates**, deployed to a **public**
  index endpoint (or PSC, using `<PrivateServiceConnect><GrpcEndpoint>`), and the embeddings model.
- Lookup runs in the ProxyEndpoint PreFlow. **A hit returns the cached response and skips the target and
  the target flows.** Put anything that must run on a miss only (router callouts, quota
  enforcement) in later conditional flows or in the TargetEndpoint.
- Populate runs in the response with `response.status.code = 200 and flow.cached != "true"`.
- **Tracking hits and misses:** the policy sets no explicit hit variable that you can rely on. A
  reliable pattern:
  `AM-SetCacheHitExpected` (flow.cached=true, HIT) before SCL → `AM-SetCacheMiss` (false, MISS) in the
  **TargetEndpoint PreFlow**, which only runs on a miss. Do **not** guard ProxyEndpoint conditional
  flows on `flow.cached`, because it still reads "true" there on a miss.
- The cache key is the prompt only. The model is **not** part of the key, so do not route the
  request (for example with a classifier callout) before the lookup.
- Make it opt-in, for example with header `x-use-cache: true`. Use `continueOnError="true"` so a
  Vector Search outage degrades to a miss.
- Faults: `steps.semanticcache.lookup.{EmbeddingsAPIFailed|VectorSearchAPIFailed|FailedToExtractUserPrompt|...}`,
  `steps.semanticcache.populate.{VectorSearchUpsertAPIFailed|...}`.

## 6. MonetizationLimitsCheck (prepaid wallet / subscription)

```xml
<MonetizationLimitsCheck continueOnError="false" enabled="true" name="MLC-EnforceMonetizationLimits">
  <DisplayName>MLC-EnforceMonetizationLimits</DisplayName>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <FaultResponse>
    <Set>
      <Payload contentType="application/json">{"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "Monetization limit exceeded or prepaid balance exhausted: {mint.limitscheck.status_message}"}}</Payload>
      <StatusCode>403</StatusCode>
      <ReasonPhrase>Forbidden</ReasonPhrase>
    </Set>
  </FaultResponse>
</MonetizationLimitsCheck>
```

- Must run **after** VerifyAPIKey or VerifyAccessToken.
- Variables: `mint.limitscheck.is_request_blocked`, `.is_subscription_found`, `.status_message`,
  `.purchased_product_name`, `.prepaid_developer_balance`, `.prepaid_developer_currency`,
  `mint.subscription_start_time_ms` / `end_time_ms`.
- Status reasons: `mint.service.developer_usage_exceeds_balance`,
  `subscription_not_found_for_developer`, `wallet_not_found_for_developer`, `wallet_blocked_due_to_inactivity`.
- Charging is done by **DataCapture monetization-scope collectors** in the response (see below).
  For cost-based charging on a rate plan with a $0.001 base fee, set
  `perUnitPriceMultiplier = costUSD * 1000`.

## 7. DataCapture (analytics and monetization)

```xml
<DataCapture name="DC-ModelAnalytics" continueOnError="true" enabled="true">
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <Capture><Collect ref="flow.emailId" default=""/><DataCollector>dc_user_email</DataCollector></Capture>
  <Capture><Collect ref="flow.target_model" default=""/><DataCollector>dc_model_name</DataCollector></Capture>
  <Capture><Collect ref="flow.totalTokenCount" default="0"/><DataCollector>dc_total_token_count</DataCollector></Capture>
  <Capture><Collect ref="flow.cacheStatus" default=""/><DataCollector>dc_cache_status</DataCollector></Capture>
  <!-- Monetization scope: what the rating engine charges -->
  <Capture><Collect ref="perUnitPriceMultiplier" default="1.0"/><DataCollector scope="monetization">perUnitPriceMultiplier</DataCollector></Capture>
  <Capture><Collect ref="currency" default="USD"/><DataCollector scope="monetization">currency</DataCollector></Capture>
  <Capture><Collect ref="transactionSuccess" default="true"/><DataCollector scope="monetization">transactionSuccess</DataCollector></Capture>
</DataCapture>
```

- Create the Data Collectors (`dc_*`) in the org first.
- **`default` is a string literal, not a message template.** `default="{flow.model}"` records the text
  `{flow.model}`. Use `""` instead.
- On a cache hit or an unpriced call, **set** `transactionSuccess=false` and
  `perUnitPriceMultiplier=0` explicitly. If you leave them unset, the defaults bill every request.
- Faults skip the PostFlow. Add a fault-path twin (identity + model only, **no monetization
  collectors**) in `<DefaultFaultRule><AlwaysEnforce>true</AlwaysEnforce>`. This attributes
  blocked requests to a user without charging them.

## 8. TraceCapture (Preview)

```xml
<TraceCapture continueOnError="true" enabled="true" name="TC-GatewayTrace">
  <Variables>
    <Variable name="gateway.model" ref="flow.target_model">unknown</Variable>
    <Variable name="gateway.cache" ref="flow.cacheStatus">DISABLED</Variable>
  </Variables>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</TraceCapture>
```
Requires distributed tracing to be enabled on the environment.

## 9. Identity, validation and cost (supporting policies)

**DecodeJWT for attribution only.** Use it when an upstream proxy (such as IAP) authenticates the
user and the gateway only needs the email for per-user quota, logs and billing. It does **not**
verify the signature, so never base authorization on its claims. Use `VerifyJWT` against the
issuer's JWKS for that.
```xml
<DecodeJWT continueOnError="true" enabled="true" name="DJWT-ExtractUserIdentity">
  <Source>flow.rawToken</Source>   <!-- set by EV on "Authorization: Bearer {rawToken}" -->
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</DecodeJWT>
```
Claims are available as `jwt.DJWT-ExtractUserIdentity.decoded.claim.email` (also `.claim.email`).

**OASValidation including the body.** `ValidateMessageBody` defaults to **false**, which
validates only the path and parameters.
```xml
<OASValidation continueOnError="false" enabled="true" name="OAS-ValidateRequest">
  <OASResource>oas://openapi.yaml</OASResource>
  <Options>
    <ValidateMessageBody>true</ValidateMessageBody>
    <AllowUnspecifiedParameters>
      <Header>true</Header><Query>false</Query><Cookie>true</Cookie>
    </AllowUnspecifiedParameters>
  </Options>
  <Source>request</Source>
</OASValidation>
```

**Dollar budgets with Quota (read/write pair).** Count micro-dollars instead of requests.
```xml
<!-- PreFlow: check only. Without EnforceOnly it adds weight=1 on every call. -->
<Quota continueOnError="true" enabled="true" name="QC-EnforceBudgetLimit">
  <Identifier ref="verifyapikey.VA-VerifyAPIKey.developer.id"/>
  <Allow count="100000000" countRef="verifyapikey.VA-VerifyAPIKey.apiproduct.developer.budget.limit"/>
  <Interval ref="verifyapikey.VA-VerifyAPIKey.apiproduct.developer.budget.interval">1</Interval>
  <TimeUnit ref="verifyapikey.VA-VerifyAPIKey.apiproduct.developer.budget.timeunit">month</TimeUnit>
  <EnforceOnly>true</EnforceOnly>
  <Distributed>true</Distributed><Synchronous>true</Synchronous>
  <SharedName>developer-budget-counter</SharedName>
</Quota>
<!-- PostFlow: spend. Weight = cost in micro-dollars computed by JS from a KVM rate card. -->
<Quota continueOnError="true" enabled="true" name="QC-DeductBudget">
  ...same Identifier/Allow/Interval/TimeUnit/SharedName...
  <Weight ref="flow.tx_cost_micros"/>
</Quota>
```
With `continueOnError="true"` on the enforcer, the violation is swallowed. You **must** follow it
with `RF-BudgetExceeded` conditioned on `ratelimit.QC-EnforceBudgetLimit.failed = true and
ratelimit.QC-EnforceBudgetLimit.exceed.count > 0`. Without that step the budget is never enforced.

---

## Recommended order in an AI Gateway ProxyEndpoint

```text
PreFlow Request:
  CORS → OAS-ValidateRequest → EV-RequestDetails (model from path) → EV-ExtractBearerToken
  → DJWT-ExtractUserIdentity → AM-SetUserIdentity → RF-MissingUserEmail
  → JS-ExtractPromptAndModel (flow.userPrompt, flow.quota_model) → VA-VerifyAPIKey
  → RF-StreamingNotSupported (if no SSE) → SUP-UserPrompt → [PTL-PromptTokenLimit]
  → MLC-EnforceMonetizationLimits → QC-EnforceBudgetLimit → RF-BudgetExceeded
  → AM-RemoveAuthorization → AM-InitCacheStatus → AM-Prep<Provider>Direct
  → AM-SetCacheHitExpected + SCL-Semantic-Cache-Lookup   (opt-in header)
Conditional flows (only on a cache miss):
  LLMTokenLimitFlow / AutoRoutingFlow: LTQ-TokenEnforce → KVM creds → AM router req → SC-ModelRouter → JS-AutoRouting
TargetEndpoint PreFlow: AM-SetCacheMiss → AM-Route<Provider>Target (target.url, target.copy.pathsuffix=false)
PostFlow Response (status 200):
  EV-ModelResponse → KVM-GetModelRates → JS-CalculateCost → QC-DeductBudget (not cached)
  → JS-AuditBudgetAccounting → LTQ-TokenCount (not cached) → JS-TokenQuotaThreshold
  → DC-ModelAnalytics → SCP-Semantic-Cache-Populate (not cached) → SMR-SanitizeModelResponse
  → AM-SetResponseHeaders (x-gateway-*)
PostClientFlow: ML-CloudLogging
DefaultFaultRule (AlwaysEnforce): DC-FaultAnalytics
```

See also: [ai-gateway-policy-manager skill](../../ai-gateway-policy-manager/SKILL.md) |
[policies_mcp_tools.md](policies_mcp_tools.md) | [policies_traffic_management.md](policies_traffic_management.md)
