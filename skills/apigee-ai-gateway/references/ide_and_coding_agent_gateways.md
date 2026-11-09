# IDE & Coding-Agent AI Gateways on Apigee X

Battle-tested patterns for fronting developer IDEs, CLI coding agents, and enterprise Copilots with Apigee X and Vertex AI — covering **Google Antigravity (`agy` & Desktop IDE)**, **Claude Code & Claude Desktop**, **GitHub Copilot (BYOK)**, and **Microsoft Copilot Studio / Azure OpenAI SDKs**.

---

## 1. Wire-Protocol Matrix & Gateway Topology

Coding assistants speak three distinct LLM wire protocols plus tool-specific discovery/health endpoints:

| Client / Assistant | Primary Wire Protocol | Discovery / Health / Telemetry Routes | Auth Header Sent by Client |
|---|---|---|---|
| **Google Antigravity** (`agy` CLI & Desktop IDE) | Vertex GenAI (`/v1beta/models/{m}:{generateContent\|streamGenerateContent}`) + `/v1internal:*` | `POST /v1internal:{recordCodeAssistMetrics,registerInteraction,recordInteraction}` | `x-goog-api-key` or `x-apikey` (+ optional `Authorization: Bearer <GCP JWT>`) |
| **Claude Code CLI & Claude Desktop** | Anthropic Messages (`POST /v1/messages`, `POST /v1/messages/count_tokens`) or Vertex Anthropic (`:{rawPredict\|streamRawPredict\|countTokens}`) | `HEAD /api/hello` (unauthenticated Bun startup probe), `GET /v1/models` | `x-api-key` or `Authorization: Bearer <key>` |
| **GitHub Copilot** (VS Code BYOK / JetBrains) | OpenAI Chat Completions (`POST /v1/chat/completions`, `/chat/completions`) | `GET /v1/models`, `GET /models` | `Authorization: Bearer <key>` or `api-key` |
| **Microsoft Copilot Studio / Azure OpenAI SDKs** | OpenAI (`/v1/chat/completions`) or Azure OpenAI (`/openai/deployments/{deployment}/chat/completions?api-version=...`) | `GET /v1/models`, `GET /openai/models` | `api-key` header, `?api-key=` query param, or `Authorization: Bearer <key>` |

### Dedicated Per-Tool Proxies vs. Unified Multi-Protocol Gateway

- **Unified Gateway (`/ide/v1`)**: One Apigee proxy accepts GenAI (`/v1beta/models/*`, `/v1internal:*`), Anthropic (`/v1/messages`), and OpenAI/Azure (`/v1/chat/completions`, `/openai/deployments/*`) on a single basepath, normalizing prompt extraction, token quotas, Model Armor, cost calculation, and analytics across all IDEs. Use `scaffold_ai_gateway.py --claude --openai --token-quota --analytics` to scaffold this topology.
- **Dedicated Per-Tool Gateways (`/antigravity`, `/claude`, `/copilot/github`, `/copilot/microsoft`)**: Separate basepaths, revision lifecycles, and per-tool API Products when teams govern IDE entitlements independently (for example, granting a developer GitHub Copilot access without Claude Code access).

---

## 2. Multi-Header API Key Extraction & GCP Identity Coexistence

IDE clients hardcode different credential header names, and enterprise developer workstations frequently send **both** an Apigee API key (app entitlement) and a Google Cloud identity JWT (per-engineer identity attribution).

In `ExtractVariables`, `<Header>` stanzas evaluate **top-to-bottom**, with later matches overwriting earlier ones. Place `Authorization` **first** and explicit API-key headers **after** it:

```xml
<ExtractVariables continueOnError="true" enabled="true" name="EV-ExtractAPIKey">
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <!-- Evaluated top-to-bottom: explicit key headers override Authorization so callers can send
       Authorization: Bearer $(gcloud auth print-identity-token) alongside x-apikey / x-api-key -->
  <Header name="Authorization">
    <Pattern ignoreCase="true">Bearer {apiKey}</Pattern>
  </Header>
  <Header name="api-key"><Pattern>{apiKey}</Pattern></Header>
  <Header name="x-api-key"><Pattern>{apiKey}</Pattern></Header>
  <Header name="x-apikey"><Pattern>{apiKey}</Pattern></Header>
  <Header name="x-goog-api-key"><Pattern>{apiKey}</Pattern></Header>
  <QueryParam name="api-key"><Pattern>{apiKey}</Pattern></QueryParam>
  <Source clearPayload="false">request</Source>
  <VariablePrefix>flow</VariablePrefix>
</ExtractVariables>
```

- **Verify `<APIKey ref="flow.apiKey"/>`** in `VA-VerifyAPIKey`.
- **Attribute per-user identity**: If `Authorization: Bearer eyJ...` is a JWT (`request.header.authorization JavaRegex "^Bearer eyJ.*"`), run `DecodeJWT` (`continueOnError="true"`) to read `jwt.<policy>.claim.email` into `flow.userEmail`; otherwise fall back to `verifyapikey.VA-VerifyAPIKey.developer.email`.
- **Strip ALL client auth headers and query params before Vertex AI**: `AM-RemoveAuthorization` must remove `Authorization`, `api-key`, `x-api-key`, `x-apikey`, and `x-goog-api-key`, and every dynamic target `AssignMessage` must set `<AssignVariable><Name>target.copy.queryparams</Name><Value>false</Value></AssignVariable>` so `?api-key=...`, `?key=...`, or `?beta=true` never reach Vertex AI alongside `<GoogleAccessToken>`.

---

## 3. Client-Specific Quirks & Production Fixes

### 3.1 Claude Code CLI & Claude Desktop on Vertex AI

1. **Filter 1P `anthropic-beta` flags (prevents HTTP `400` retry storms)**:
   When Claude Code connects in 1P Anthropic API mode (`ANTHROPIC_BASE_URL=https://<APIGEE_HOST>/claude`, calling `POST /v1/messages?beta=true`), it sends 1P-only flags in `anthropic-beta` (such as `advisor-tool-2026-03-01`, `per-turn-control-2026-07-01`, `mid-conversation-tool-changes-2026-07-01`, `inline-tools-2026-09-15`). Forwarding that header untouched causes Vertex AI `:rawPredict` / `:streamRawPredict` to fail with **HTTP `400`** (`pinnacle-error-category: user`), triggering repeated ~120 KB prompt retries before the client strips beta flags.
   - Strip query parameters (`target.copy.queryparams = false`) so `?beta=true` is never sent upstream.
   - Filter `request.header.anthropic-beta` in JavaScript to Vertex-supported flags only:
     `claude-code-20250219`, `interleaved-thinking-2025-05-14`, `context-management-2025-06-27`, `thinking-token-count-2026-05-13` (omit on `*haiku*` models), `advanced-tool-use-2025-11-20`, `effort-2025-11-24`, `token-efficient-tools-2025-02-19`, `computer-use-2025-01-24`, `context-1m-2025-08-07`. Remove the header completely if no allowed flags remain.

2. **Short-circuit unauthenticated `HEAD /api/hello` startup probes**:
   Claude Code's Bun runtime sends two unauthenticated `HEAD /api/hello` requests on startup. Gate `VA-VerifyAPIKey` on `!((proxy.pathsuffix = "/api/hello") and (request.verb = "HEAD" or request.verb = "GET"))` and return `200 OK {"status":"ok"}` via a loopback `RouteRule` to avoid ~3.3 seconds of `401` retry delay.

3. **Background Haiku calls, `-YYYYMMDD` -> `@YYYYMMDD` model IDs & `:countTokens`**:
   - Claude Code fires parallel background calls to a small fast Haiku model (`ANTHROPIC_SMALL_FAST_MODEL`) to summarize/title conversations and calls `POST /v1/messages/count_tokens` (`:countTokens`). Enable the Haiku model in Vertex Model Garden **and** list it in `llmOperationGroup`.
   - Normalize Anthropic 1P date suffixes (`claude-sonnet-4-5-20250929` -> `claude-sonnet-4-5@20250929`) into `flow.target_model` for the Vertex URL, while keeping `flow.quota_model` stripped of `@YYYYMMDD` (`model.split("@")[0]`) so `LLMTokenQuota` matches the base model name in `llmOperationGroup`.
   - On `:countTokens` / `/v1/messages/count_tokens`, delete `anthropic_version`, `max_tokens`, and `stream` from the body before calling Vertex `:countTokens`, and skip `LLMTokenQuota` enforce/count steps.

4. **Client configuration options**:
   ```bash
   # Option A: 1P Anthropic Messages contract (/v1/messages -> mediated by Apigee to Vertex)
   export ANTHROPIC_BASE_URL="https://<APIGEE_HOST>/claude"
   export ANTHROPIC_AUTH_TOKEN="<APIGEE_API_KEY>"

   # Option B: Native Vertex Anthropic contract (/:rawPredict & /:streamRawPredict)
   export CLAUDE_CODE_USE_VERTEX=1
   export CLOUD_ML_REGION=global
   export ANTHROPIC_VERTEX_PROJECT_ID="<PROJECT>"
   export ANTHROPIC_VERTEX_BASE_URL="https://<APIGEE_HOST>/claude/v1"
   export ANTHROPIC_CUSTOM_HEADERS="x-apikey: <APIGEE_API_KEY>"
   ```

### 3.2 Google Antigravity (`agy` CLI & Desktop IDE)

1. **`/v1internal` envelope unwrapping & telemetry loopback**:
   - Antigravity sends internal Code Assist calls to `/v1internal:generateContent`, `/v1internal:streamGenerateContent`, and `/v1internal:countTokens` wrapped in `{ "model": "...", "request": { "contents": [...], "session_id": "..." } }`. Unwrap `p.request`, delete `session_id`, and forward the inner GenAI payload to Vertex `:generateContent` / `:streamGenerateContent?alt=sse`.
   - Short-circuit client telemetry/session RPCs (`:recordCodeAssistMetrics`, `:registerInteraction`, `:recordInteraction`) to a `200 OK {}` loopback `RouteRule` without invoking Vertex AI, `LLMTokenQuota`, or `DataCapture`.

2. **`admin_settings.json` top-level schema & Windows UTF-8-without-BOM requirement**:
   - `"models"` (`defaultModelId`, `list`, `tiers`) **must** be a top-level sibling of `"gateway"` in `admin_settings.json`, never nested inside `"gateway"`.
   - **Windows encoding trap**: PowerShell `>` / `Set-Content` / `Out-File` defaults to UTF-16 LE with a BOM on Windows PowerShell 5.1, which causes Antigravity reading `%ProgramData%\Antigravity\admin_settings.json` to silently fail JSON parsing and fall back to cached Google login credentials. Always write UTF-8 **without** BOM:
     ```powershell
     $path = "$env:ProgramData\Antigravity\admin_settings.json"
     [System.IO.File]::WriteAllText($path, $json, (New-Object System.Text.UTF8Encoding($false)))
     ```

3. **Gemini 4 thinking parameters**:
   `gemini-4-*` models reject `generationConfig.thinkingConfig.thinkingBudget` (even `0`) with HTTP `400`, requiring `thinkingLevel` (`MINIMAL` / `LOW` / `MEDIUM` / `HIGH`). When supporting older IDE clients that inject `thinkingBudget: 0`, delete `thinkingBudget` in JavaScript when `flow.model` matches `^gemini-4`.

### 3.3 GitHub Copilot (BYOK) & Microsoft Copilot / Azure OpenAI

1. **Vertex AI OpenAI endpoint contract**:
   - Target URL: `https://{region}-aiplatform.googleapis.com/v1beta1/projects/{project}/locations/{location}/endpoints/openapi/chat/completions` (`aiplatform.googleapis.com` when `location` is `global`).
   - Prefix `body.model` with `"google/"` (for example `"google/gemini-3.6-flash"`). For Azure OpenAI routes (`/openai/deployments/{model}/chat/completions`), copy `{flow.model}` from the URL path into `body.model` when omitted from the JSON body.
   - Inject `"stream_options": { "include_usage": true }` whenever `stream === true`; without it, Vertex AI's OpenAI SSE stream omits the final `usage` chunk before `data: [DONE]`.

2. **Serving Claude models behind OpenAI / Azure OpenAI clients**:
   Vertex AI's `/endpoints/openapi/chat/completions` endpoint **only supports `google/*` models** — sending `"anthropic/claude-..."` returns HTTP `400`/`404`. To expose Claude models to GitHub Copilot or Azure OpenAI SDKs:
   - Detect `^claude-` on `flow.model` and route to Anthropic `:rawPredict`.
   - Convert OpenAI `messages` (`system` / `developer` roles -> top-level `system` string; `user` / `assistant` messages -> Anthropic `messages`) and `max_completion_tokens` / `max_tokens` -> `max_tokens`.
   - Transform the Anthropic response back into `object: "chat.completion"` (`choices[0].message.content`, `usage.{prompt_tokens,completion_tokens,total_tokens}`). If the client requested `stream: true`, format the response as `chat.completion.chunk` SSE events ending with a `usage` chunk and `data: [DONE]`.

3. **Model discovery (`GET /v1/models`)**:
   VS Code Copilot BYOK, Claude Desktop, and OpenAI SDKs call `GET /v1/models` (or `/models`) when validating a custom base URL. Serve an OpenAI-schema `{"object": "list", "data": [...]}` loopback response and include `"GET"` in your API Product's `operationGroup`.

---

## 4. SSE Streaming Execution Order, Duplicate-Firing Fixes & Analytics

When a `TargetEndpoint` enables `<Property name="response.streaming.enabled">true</Property>`, Apigee X executes flows in this exact order:

1. **`ProxyEndpoint` `PostFlow.Response`** runs as soon as the `200 OK` headers arrive from Vertex AI — **before** any SSE chunks are streamed (`response.content` is empty and token variables are unset).
   - **Safe here**: `KVM-GetModelRates` (`assignTo="flow.model_rates_json"`, reading `model_rates` once per stream instead of on every SSE chunk; use `flow.*` rather than `private.*` if JavaScript reads the variable directly).
   - **Must guard against streaming**: `EV-ModelResponse`, `LTQ-TokenCount`, `SMR-SanitizeModelResponse`, `DC-ModelAnalytics`, and `SCP-Semantic-Cache-Populate` **must** carry `flow.is_streaming != "true"` in their `<Condition>`.
2. **`TargetEndpoint` `<EventFlow content-type="text/event-stream"><Response>`** runs **once per SSE event** (maximum **4 policies**, only from the 7-policy allowlist). Recommended 4-step pipeline:
   1. `JS-StreamMetrics` (unconditioned — runs on every chunk)
   2. `SMR-SanitizeModelResponseSSE` (`<ContinuousScan>true</ContinuousScan>`, placed **before** `LLMTokenQuota` and `DataCapture`)
   3. `LTQ-TokenCountSSE` (`<Condition>flow.stream_done = "true"</Condition>`)
   4. `DC-ModelAnalytics` (`<Condition>flow.stream_done = "true"</Condition>`)
3. **`ProxyEndpoint` `PostClientFlow.Response`** runs **once after** the SSE stream closes (`MessageLogging` only). Log reassembled `flow.responseText` (accumulated from non-thought `!part.thought` text deltas up to 4,096 chars), token counts, and `flow.tx_cost_usd`.

### Three SSE Bugs to Avoid in Production

- **Never put backslash-escaped quotes `\"` inside `<Condition>`**: Apigee X's condition parser rejects `JavaRegex "(?s).*(\"finishReason\").*"` at deploy time with `expressions.parser.RegexRequired`. Parse JSON inside `<EventFlow>` JavaScript and condition downstream steps on `flow.stream_done = "true"`.
- **Always reset `flow.stream_done = "false"` on line 1 of `<EventFlow>` JS**: On OpenAI SSE, the `usage` chunk arrives second-to-last, followed by `data: [DONE]`; on Anthropic SSE, `message_delta` is followed by `message_stop`. Without resetting `flow.stream_done` to `"false"` at the top of the script on every chunk, `flow.stream_done` stays `"true"` onto the trailing event and fires `LTQ-TokenCountSSE` and `DC-ModelAnalytics` **twice**.
- **Never key Gemini SSE completion on `candidatesTokenCount`**: Intermediate Gemini SSE chunks can include partial `usageMetadata.candidatesTokenCount`. Only `candidates[0].finishReason` marks the single final Gemini chunk. For Gemini thinking models (`gemini-2.5-*`, `gemini-3-*`, `gemini-4-*`), `totalTokenCount = promptTokenCount + candidatesTokenCount + thoughtsTokenCount`, so compute billable output tokens as `(totalTok > promptTok) ? (totalTok - promptTok) : (candTok + thoughtTok)`.

### 8-Collector `DataCapture` Standard

Provision these 8 custom Data Collectors in your Apigee org (`apigee_ctl.py` / Management API `/datacollectors`) and populate them in `DC-ModelAnalytics` (plus `dc_user_email` and `dc_model_name` in `DC-FaultAnalytics` inside `<DefaultFaultRule>` with `<AlwaysEnforce>true</AlwaysEnforce>`):

| Data Collector | Type | Source Variable | Default | Purpose |
|---|---|---|---|---|
| `dc_user_email` | `STRING` | `flow.userEmail` | `anonymous` | Per-developer attribution across shared IDE keys |
| `dc_model_name` | `STRING` | `flow.model` | `unknown` | Model breakdown in custom dashboards |
| `dc_prompt_token_count` | `INTEGER` | `flow.promptTokenCount` | `0` | Input tokens (custom reports) |
| `dc_candidates_token_count` | `INTEGER` | `flow.candidatesTokenCount` | `0` | Output tokens (custom reports) |
| `dc_total_token_count` | `INTEGER` | `flow.totalTokenCount` | `0` | Total tokens billed against quota |
| `dc_llm_input_token` | `INTEGER` | `flow.promptTokenCount` | `0` | Populates Apigee Vertex AI Consumption dashboard |
| `dc_llm_output_token` | `INTEGER` | `flow.candidatesTokenCount` | `0` | Populates Apigee Vertex AI Consumption dashboard |
| `dc_cache_status` | `STRING` | `flow.cacheStatus` | `DISABLED` | `HIT` / `MISS` / `DISABLED` cache efficiency |

---

## 5. API Product Rules for IDE Gateways

1. **Exactly 1 entity per `llmOperations` array**: Apigee X Management API rejects `POST/PUT /apiproducts` with `HTTP 400: Operations must contain exactly one entity but found N entities` if a single `llmOperationGroup.operationConfigs[]` entry lists multiple items in `llmOperations`. Create **one `operationConfigs[]` object per model**, each with a 1-element `llmOperations` list.
2. **Keep `operationGroup` and `llmOperationGroup` `apiSource` sets identical**: If an API Product defines both `operationGroup` and `llmOperationGroup`, both groups must reference the exact same proxy name(s). If `operationGroup` includes an extra proxy `P` that has no `llmOperationGroup` entry, `VA-VerifyAPIKey` can match that product on calls to `P` (even when the Developer App also holds `P`'s dedicated product!) and `LTQ-TokenEnforce` then fails with **HTTP `500` `keymanagement.service.InvalidAPICallAsNoApiProductMatchFound`**.
3. **Allow `GET`, `POST`, and `OPTIONS` on `/**` in `operationGroup`**: Desktop IDEs probe `GET /v1/models` and browser extensions send `OPTIONS` preflights; restricting `operationGroup` to `POST /models/*` causes `401 InvalidApiKeyForGivenResource` during IDE onboarding. Keep `llmOperationGroup` scoped to `methods: ["POST"]` and `resource: "/**"`.
