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
- Unsupported features: refuse them explicitly. For example, if SSE is not implemented, return 501 for `:streamGenerateContent`
  instead of silently returning a non-streamed body. For SSE, set `response.streaming.enabled=true` on the target and
  count tokens in an `<EventFlow content-type="text/event-stream">`.
