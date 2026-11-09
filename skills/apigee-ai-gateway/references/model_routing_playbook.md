# AI Gateway Model Routing & Failover Playbook

## 1. Direct model routing (model in the path)

The client calls `POST /ai/v1/models/{model}:generateContent`.

```xml
<!-- PreFlow: model from path -->
<ExtractVariables continueOnError="true" enabled="true" name="EV-RequestDetails">
  <URIPath>
    <Pattern ignoreCase="true">/models/{model}:generateContent</Pattern>
  </URIPath>
  <VariablePrefix>flow</VariablePrefix>
</ExtractVariables>

<!-- TargetEndpoint PreFlow: build the Vertex URL -->
<AssignMessage continueOnError="false" enabled="true" name="AM-RouteGeminiTarget">
  <AssignVariable><Name>target.copy.pathsuffix</Name><Value>false</Value></AssignVariable>
  <AssignVariable>
    <Name>target.url</Name>
    <Template>https://aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/global/publishers/google/models/{flow.target_model}:generateContent</Template>
  </AssignVariable>
</AssignMessage>
```

- Set `target.url` in the **TargetEndpoint** flow. Set `target.copy.pathsuffix=false`, otherwise Apigee appends
  `/models/...` to your URL.
- Authenticate the target with `<Authentication><GoogleAccessToken><Scopes><Scope>https://www.googleapis.com/auth/cloud-platform</Scope>`
  and deploy with a service account that has `roles/aiplatform.user`.
- Use location `global` (the `aiplatform.googleapis.com` host) for the newest Gemini and Claude models.
  Regional hosts are `{region}-aiplatform.googleapis.com`.

## 2. Multi-provider: Claude on Vertex

```xml
<RouteRule name="claude-target">
  <Condition>flow.target_provider == "anthropic"</Condition>
  <TargetEndpoint>claude-vertex-target</TargetEndpoint>
</RouteRule>
<RouteRule name="gemini-target">            <!-- default: last, no condition -->
  <TargetEndpoint>gemini-vertex-target</TargetEndpoint>
</RouteRule>
```
- URL: `.../publishers/anthropic/models/{model}:rawPredict`. The body is the Anthropic Messages shape plus
  `"anthropic_version": "vertex-2023-10-16"`, with no `model` field.
- To keep **one client contract** (Gemini shape), convert the request (`contents` → `messages`) in a TargetEndpoint
  JavaScript. Convert the response back (`content[].text` → `candidates[].content.parts`,
  `usage.input_tokens/output_tokens` → `usageMetadata.*`) in the **target response** flow. Token quota, cost,
  Model Armor response screening and caching then work without provider-specific branches.
- Vertex reports Claude `modelVersion` as `claude-x-y-YYYYMMDD` (hyphen), while the request uses `@YYYYMMDD`. Never
  copy `modelVersion` into the variable that LLMTokenQuota keys on.

## 3. Auto routing (`/auto`) driven by API product attributes

```text
/auto request ─▶ (cache miss) LTQ enforce ─▶ KVM-GetRouterCredentials (private.*)
             ─▶ AM-PrepRouterRequest (new message "routerRequest", prompt via {escapeJSON(flow.userPrompt)})
             ─▶ SC-ModelRouter (ServiceCallout, timeout 2500 ms, continueOnError=true)
             ─▶ JS-AutoRouting: category → verifyapikey.VA-VerifyAPIKey.apiproduct.routing.model.<category>
                                fallback routing.model.general → flow.target_model / flow.target_provider
```

Product attributes (per persona):
```json
{"name":"routing.model.coding","value":"claude-opus-5-5"},
{"name":"routing.model.deep_reasoning","value":"gemini-3.1-pro-preview"},
{"name":"routing.model.simple","value":"gemini-3.5-flash-lite"},
{"name":"routing.model.general","value":"gemini-3.6-flash"}
```

The classifier can be any fast model or service, such as a small Gemini Flash-Lite call with a JSON
response schema, or a dedicated router API.

Rules:
- Put the routing chain in a **conditional flow** (`proxy.pathsuffix MatchesPath "/auto*"`), not in the PreFlow.
  Conditional flows run after the PreFlow, so on a semantic-cache hit the router is never called. Routing before
  the lookup costs a full classifier round trip and does not change the key, because the key is the prompt only.
- Only the **first** matching conditional flow runs. Order specific flows (for example a token-limit demo on one
  model) before `/auto`, and remember that `flow.model` still equals `auto` when flows are selected.
- Do not guard routing steps on `flow.cached`. It is optimistically "true" until the TargetEndpoint marks a miss,
  so the guard would skip routing and send an empty model to Vertex (404).
- Set `flow.quota_model=auto` before routing, so LLMTokenQuota resolves the `/auto` llmOperation even after
  `flow.model` is replaced with the routed model.
- Router failure: `continueOnError="true"` on the ServiceCallout, then fall back to `routing.model.general`.
- Expose the decision: `x-gateway-router-category`, `x-gateway-router-confidence`, `x-gateway-model`, `x-auto-routed`.

## 4. Failover

RouteRules choose a target **before** the call and cannot retry on failure. For resilience:
- **Same model, multiple regions:** a TargetServer per region in a `<LoadBalancer>` with `<MaxFailures>`,
  `<RetryEnabled>true</RetryEnabled>` and a HealthMonitor. Vertex `global` already load-balances regionally.
- **Fallback model on 429/5xx:** in the TargetEndpoint `<FaultRules>` (or a response-flow condition on
  `response.status.code = 429`), call the fallback with a `ServiceCallout` to the other model URL and replace the
  response. Record it in `x-gateway-fallback: true`.
- **Unsupported features:** refuse them explicitly. For example, if SSE is intentionally disabled (`--no-sse`), return
  HTTP `501 Not Implemented` for `:streamGenerateContent` instead of silently returning a non-streamed body.

## 5. Server-Sent Events (SSE) Streaming Across Gemini, Claude & OpenAI

### 5.1 Separate Buffered and Streaming `TargetEndpoint`s
Never mix SSE (`response.streaming.enabled=true`) and buffered calls in a single `TargetEndpoint` (doing so causes
empty `response.content` variables on buffered calls). Detect streaming in the request PreFlow (`:streamGenerateContent`,
`:streamRawPredict`, or JSON `body.stream === true` → `flow.is_streaming = "true"`) and dispatch with ordered `RouteRule`s:

```xml
<RouteRule name="claude-stream-target">
  <Condition>(flow.target_provider == "anthropic") and (flow.is_streaming == "true")</Condition>
  <TargetEndpoint>claude-vertex-stream-target</TargetEndpoint>
</RouteRule>
<RouteRule name="claude-target">
  <Condition>flow.target_provider == "anthropic"</Condition>
  <TargetEndpoint>claude-vertex-target</TargetEndpoint>
</RouteRule>
<RouteRule name="gemini-stream-target">
  <Condition>flow.is_streaming == "true"</Condition>
  <TargetEndpoint>gemini-vertex-stream-target</TargetEndpoint>
</RouteRule>
<RouteRule name="gemini-target">            <!-- default: last, no condition -->
  <TargetEndpoint>gemini-vertex-target</TargetEndpoint>
</RouteRule>
```

Extract `flow.model` for both buffered and streaming suffixes in `EV-RequestDetails`:
```xml
<ExtractVariables continueOnError="true" enabled="true" name="EV-RequestDetails">
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <URIPath>
    <Pattern ignoreCase="true">/models/{model}:streamGenerateContent</Pattern>
    <Pattern ignoreCase="true">/models/{model}:generateContent</Pattern>
    <Pattern ignoreCase="true">/models/{model}:streamRawPredict</Pattern>
    <Pattern ignoreCase="true">/models/{model}:rawPredict</Pattern>
  </URIPath>
  <VariablePrefix>flow</VariablePrefix>
</ExtractVariables>
```

### 5.2 Upstream Vertex AI Streaming URLs & Wire Formats
| Provider on Vertex AI | Upstream Streaming URL & Payload Requirement | SSE Event Delta & Usage Shape |
|---|---|---|
| **Gemini** (`publishers/google`) | `.../models/{flow.model}:streamGenerateContent?alt=sse` (**`?alt=sse` required**; without it Vertex returns chunked JSON `application/json` instead of `text/event-stream`) | `$.candidates[-1].content.parts[-1].text`; final chunk has `finishReason` + `$.usageMetadata.totalTokenCount` |
| **Anthropic Claude** (`publishers/anthropic`) | `.../models/{flow.model}:streamRawPredict` with `"anthropic_version": "vertex-2023-10-16"` **and `"stream": true`** in JSON body | `message_start` (`message.usage.input_tokens`), `content_block_delta` (`delta.text` / `delta.partial_json`), `message_delta` (`usage.output_tokens`) |
| **OpenAI-compatible** (`endpoints/openapi`) | `https://aiplatform.googleapis.com/v1beta1/projects/{PROJECT}/locations/global/endpoints/openapi/chat/completions` with `"model": "google/{model}"` and `"stream": true` (plus `"stream_options": {"include_usage": true}` for token quota) | `$.choices[-1].delta.content`; final chunk before `[DONE]` carries `$.usage.total_tokens` |

### 5.3 `<EventFlow content-type="text/event-stream">` Pattern
Place `<EventFlow>` on each streaming `TargetEndpoint` (when both `ProxyEndpoint` and `TargetEndpoint` define an
`EventFlow`, Apigee executes **only** the `TargetEndpoint`'s `EventFlow`):

```xml
<TargetEndpoint name="gemini-vertex-stream-target">
  <PreFlow name="PreFlow">
    <Request>
      <Step><Name>AM-RouteGeminiStreamTarget</Name></Step>
    </Request>
    <Response/>
  </PreFlow>
  <EventFlow name="EventFlow" content-type="text/event-stream">
    <Response>
      <!-- 1. Run JS on every chunk: resets flow.stream_done="false" on line 1, extracts chunk text + usage,
              and sets flow.stream_done="true" ONLY when candidates[0].finishReason is present -->
      <Step>
        <Name>JS-StreamMetrics</Name>
      </Step>
      <!-- 2. ContinuousScan Model Armor BEFORE LLMTokenQuota/DataCapture so downstream steps only see checked events -->
      <Step>
        <Name>SMR-SanitizeModelResponseSSE</Name>
        <Condition>response.event.current.data != null and response.event.current.data != "" and response.event.current.data != "[DONE]"</Condition>
      </Step>
      <!-- 3. Count total tokens ONCE on flow.stream_done="true" (never put escaped quotes \" inside JavaRegex) -->
      <Step>
        <Name>LTQ-TokenCountSSE</Name>
        <Condition>flow.stream_done = "true"</Condition>
      </Step>
      <!-- 4. Optional DataCapture once on flow.stream_done="true" (within the 4-policy EventFlow limit) -->
      <Step>
        <Name>DC-ModelAnalytics</Name>
        <Condition>flow.stream_done = "true"</Condition>
      </Step>
    </Response>
  </EventFlow>
  <HTTPTargetConnection>
    <Properties>
      <Property name="response.streaming.enabled">true</Property>
      <Property name="keepalive.timeout.millis">300000</Property>
      <Property name="io.timeout.millis">300000</Property>
    </Properties>
    <URL>https://aiplatform.googleapis.com</URL>
    <Authentication>
      <GoogleAccessToken>
        <Scopes><Scope>https://www.googleapis.com/auth/cloud-platform</Scope></Scopes>
      </GoogleAccessToken>
    </Authentication>
  </HTTPTargetConnection>
</TargetEndpoint>
```

> **Critical Apigee X `<EventFlow>` & Condition Gotchas**:
> 1. **Never use backslash-escaped quotes (`\"`) inside `<Condition>` `JavaRegex` literals** — Apigee X fails deployment with `expressions.parser.RegexRequired`. Parse JSON inside `JS-StreamMetrics` and check `flow.stream_done = "true"`.
> 2. **Always reset `context.setVariable("flow.stream_done", "false");` on line 1 of your `<EventFlow>` JS** — otherwise trailing SSE frames (`data: [DONE]` on OpenAI, `message_stop` on Claude) inherit `flow.stream_done = "true"` from the preceding usage frame and fire `LTQ-TokenCountSSE` and `DC-ModelAnalytics` **twice**.
> 3. **Intermediate Gemini SSE chunks can carry `candidatesTokenCount`** — only `candidates[0].finishReason` marks the single final Gemini chunk.

### 5.4 Multi-Provider One-Contract SSE (`FormatClaudeStream.js` & `/models/auto:streamGenerateContent`)
- `/models/auto:streamGenerateContent` runs the exact same fast classifier `ServiceCallout` (`AutoRoutingFlow` matches
  `^/models/auto:.*`) before the target is chosen, then streams from `claude-vertex-stream-target` or `gemini-vertex-stream-target`.
- On `claude-vertex-stream-target`, `JS-FormatClaudeStream` runs in `<EventFlow><Response>` (within the 4-policy limit:
  `JS-FormatClaudeStream` → `SMR-SanitizeModelResponseSSE` → `LTQ-TokenCountSSE`):
  - Rewrites `content_block_delta` (`text_delta`) into `data: {"candidates":[{"content":{"role":"model","parts":[{"text":"..."}]}}]}\n\n`
    (updating both `response.event.current.data` and `response.event.current.content`).
  - Accumulates `tool_use` + `input_json_delta` blocks and emits a Gemini `functionCall` chunk on `content_block_stop`.
  - Suppresses non-content Anthropic control events (`ping`, `message_start`, `message_stop`) by setting
    `response.event.current.content = ""` and `response.event.current.data = ""`.
  - Emits the final `finishReason` + `usageMetadata` chunk on `message_delta` (`input_tokens + output_tokens`), so
    `LTQ-TokenCountSSE` and `ML-CloudLogging` work identically across Gemini and Claude.
- Guard all buffered `PostFlow` payload policies (`EV-ModelResponse`, `LTQ-TokenCount`, `DC-ModelAnalytics`, `SCP-Semantic-Cache-Populate`,
  `SMR-SanitizeModelResponse`) and `SemanticCacheLookup` with `flow.is_streaming != "true"`.

See also: [ide_and_coding_agent_gateways.md](ide_and_coding_agent_gateways.md) for IDE & Copilot gateway routing (Antigravity `/v1internal`, Claude Code `/v1/messages` + `anthropic-beta` filtering, GitHub Copilot & Azure OpenAI `/v1/chat/completions` + `GET /v1/models`).
