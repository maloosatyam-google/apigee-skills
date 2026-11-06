# WebSockets, Server-Sent Events, and Streaming

## WebSocket Proxy Support

### How WebSocket Works in Apigee X

Apigee X acts as a **pass-through proxy** for WebSocket traffic. It does NOT terminate or generate WebSocket connections. The backend MUST exist and MUST respond with HTTP 101 Switching Protocols.

Flow:

```
Client --> HTTP GET with "Upgrade: websocket" --> Apigee X (pass-through) --> Backend
Client <-- HTTP 101 Switching Protocols     <-- Apigee X              <-- Backend
Client <-> Full-duplex WebSocket frames     <-> Apigee X (transparent) <-> Backend
```

Apigee X is transparent to WebSocket frames after the initial handshake. It does not inspect, modify, or buffer individual frames.

### WebSocket Proxy Configuration

ProxyEndpoint:

```xml
<ProxyEndpoint name="default">
  <HTTPProxyConnection>
    <BasePath>/v1/ws</BasePath>
  </HTTPProxyConnection>
  <PreFlow name="PreFlow">
    <Request>
      <Step><Name>VAK-VerifyKey</Name></Step>
    </Request>
    <Response/>
  </PreFlow>
  <Flows/>
  <PostFlow name="PostFlow">
    <Request/>
    <Response/>
  </PostFlow>
  <RouteRule name="default">
    <TargetEndpoint>websocket-backend</TargetEndpoint>
  </RouteRule>
</ProxyEndpoint>
```

TargetEndpoint:

```xml
<TargetEndpoint name="websocket-backend">
  <HTTPTargetConnection>
    <URL>wss://ws-backend.example.com/socket</URL>
    <Properties>
      <Property name="keepalive.timeout.millis">3600000</Property>
      <Property name="io.timeout.millis">3600000</Property>
    </Properties>
  </HTTPTargetConnection>
</TargetEndpoint>
```

Key points:

- Use `wss://` for TLS-encrypted WebSocket (recommended) or `ws://` for unencrypted.
- `keepalive.timeout.millis`: default 60 seconds. MUST be increased for WebSocket. Set to 3600000 (1 hour) or higher.
- `io.timeout.millis`: increase to match expected connection duration.

### Critical Rule: Policies Execute ONLY Before the Handshake

**ALL policies execute ONLY during the initial HTTP upgrade request, BEFORE the 101 response.** Once the WebSocket connection is established, NO policies execute on WebSocket frames.

This means:

- Authentication (VerifyAPIKey, OAuthV2, VerifyJWT) runs once during handshake.
- Rate limiting (SpikeArrest, Quota) runs once during handshake.
- MessageLogging captures only the handshake, not frame traffic.
- No payload inspection or transformation of WebSocket frames is possible.
- No policies can be triggered by individual WebSocket messages.

### Policies That Work (During Handshake Only)

| Policy | Works? | Notes |
|---|---|---|
| VerifyAPIKey | Yes | Validate key in handshake request |
| OAuthV2 (VerifyAccessToken) | Yes | Validate token in handshake |
| VerifyJWT | Yes | Validate JWT in handshake |
| SpikeArrest / Quota | Yes | Counts the handshake as one request |
| AssignMessage | Yes | Modify handshake request headers |
| ExtractVariables | Yes | Extract from handshake headers/params (NOT body) |
| MessageLogging | Yes | Log handshake details |
| CORS | Yes | Set CORS headers on handshake response |

### Policies That Do NOT Work

| Policy | Why |
|---|---|
| Any response-modifying policy | Cannot modify 101 response or WebSocket frames |
| JSONThreatProtection | No payload to inspect after handshake |
| ServiceCallout | Not compatible with WebSocket protocol |
| JavaScript (on response) | No response body to process |
| ResponseCache | Cannot cache WebSocket connections |

### Timeout Gotchas

**Gotcha 1: 60-second idle timeout kills connections**

The default `keepalive.timeout.millis` is 60 seconds. If no data flows for 60 seconds, Apigee drops the connection. Fix:

```xml
<Property name="keepalive.timeout.millis">3600000</Property>
```

AND implement application-level ping/pong frames as a safety net.

**Gotcha 2: Ingress gateway has a hard 300-second timeout**

The Apigee ingress load balancer enforces a 300-second (5-minute) timeout that is NOT configurable. If `api.timeout` is not set on your proxy, this ingress timeout applies. The Message Processor uses the LESSER of the ingress timeout and `api.timeout`.

Mitigation: Ensure your WebSocket clients send data or ping frames at least every 4 minutes.

**Gotcha 3: Token expiry during active connection**

OAuth/JWT tokens are validated only during the handshake. If a token expires during an active WebSocket connection:

- The connection continues to work (no re-validation occurs).
- If the connection drops and the client reconnects, the expired token is rejected.
- Token revocation has a ~180-second cache TTL. Revoked tokens may continue to work for up to 3 minutes.

### WebSocket Proxy Checklist

1. Backend MUST generate the HTTP 101 response (cannot use null route).
2. Set `keepalive.timeout.millis` to at least 3600000 (1 hour).
3. Set `io.timeout.millis` to match expected connection duration.
4. Place ALL authentication/validation in PreFlow (only chance to run policies).
5. Use `wss://` for production (TLS-encrypted WebSocket).
6. Implement application-level ping/pong for keepalive.
7. Use long-lived tokens for WebSocket auth (short-lived tokens cause reconnection churn).
8. Do NOT mix WebSocket and regular HTTP in the same TargetEndpoint.
9. Load balancing requires sticky sessions (connection pinned to one backend).

### Analytics and Debugging Limitations

- **Analytics**: Each WebSocket session counts as ONE API call. Individual frame traffic is NOT tracked.
- **Debug tool**: Shows only the HTTP handshake (101 response). Cannot inspect WebSocket frames.
- **Workaround**: Use backend-side logging for frame-level monitoring. Use MessageLogging in PreFlow to capture handshake metadata (client IP, auth token, timestamp).

## Server-Sent Events (SSE)

### How SSE Differs from WebSocket

| Aspect | WebSocket | SSE |
|---|---|---|
| Direction | Bidirectional | Server to Client only |
| Protocol | HTTP 101 upgrade | Standard HTTP 200 |
| Policy execution | Only before handshake | Possible via EventFlow |
| Frame inspection | Not possible | Limited via EventFlow |
| Max event size | N/A | 10 MB per event |
| Use case | Chat, bidirectional | Server push, LLM streaming |

### SSE Proxy & `<EventFlow>` Configuration

SSE uses standard HTTP 200 with continuous response streaming (`Content-Type: text/event-stream`). Always use a **dedicated `TargetEndpoint`** for SSE targets—do **not** mix SSE and non-SSE calls in the same `TargetEndpoint` (causes empty `response.content` on buffered requests).

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
      <Step>
        <Name>SMR-SanitizeModelResponseSSE</Name>
        <Condition>response.event.current.data != null and response.event.current.data != ""</Condition>
      </Step>
      <Step>
        <Name>LTQ-TokenCountSSE</Name>
        <Condition>response.event.current.data JavaRegex "(?s).*(\"finishReason\"|\"candidatesTokenCount\").*"</Condition>
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

### `<EventFlow>` Rules, Variables, and Policy Allowlist

1. **Attributes & Placement**:
   - `<EventFlow name="EventFlow" content-type="text/event-stream">` (`content-type` **must** be `text/event-stream`).
   - Can be placed inside `TargetEndpoint` or `ProxyEndpoint`, **but only one `EventFlow` executes**:
     - If multiple `<EventFlow>` stanzas exist in one endpoint, only the **last** one runs.
     - If both `ProxyEndpoint` and `TargetEndpoint` define `<EventFlow>`, **only the `TargetEndpoint`'s `EventFlow` executes** (`ProxyEndpoint`'s is ignored). Prefer placing `<EventFlow>` per streaming `TargetEndpoint` in multi-provider AI gateways.
2. **Policy Limits & Strict Allowlist**:
   - Policies execute **only in `<Response>`** of `<EventFlow>` (up to **4 policies maximum**).
   - **Only 7 policy types are allowed in `<EventFlow>`** (enforced by `validate_bundle.py`):
     `DataCapture`, `Javascript`, `LLMTokenQuota`, `MessageLogging`, `PublishMessage`, `RaiseFault`, `SanitizeModelResponse`.
   - Place `SanitizeModelResponse` (`<ContinuousScan>true</ContinuousScan>`) **first** before `LLMTokenQuota` and `DataCapture` so downstream policies only process checked events.
   - If `DataCapture` runs on multiple events, only the **latest** value captured before stream close is recorded in Analytics.
3. **`<EventFlow>` Flow Variables** (scoped strictly to the current event inside `<EventFlow>`):
   - `response.event.current.content`: Full raw SSE event string (including `event:` / `data:` fields). Setting `response.event.current.content` to `""` **suppresses** sending that event to the client.
   - `response.event.current.data`: The `data:` payload string of the current event. Modify it in `JavaScript` to transform the payload sent to the client, and read it in `SanitizeModelResponse` (`jsonPath(..., response.event.current.data, true)`) and `LLMTokenQuota` (`jsonPath(..., response.event.current.data, false)` — `want-array` must be `false`).
   - `response.event.current.count`: 1-based counter of events received in the stream (`1` on first event).

**Billing note:** Proxies deployed with an `EventFlow` configuration are billed as **Extensible** proxies.

### SSE Limitations & Load Balancer Timeout Gotcha

- Each response event is limited to **10 MB**.
- Do **NOT** mix SSE and non-SSE in the same `TargetEndpoint` (causes empty `response.content`).
- `response.content` is **NOT** populated during streaming (it is empty). Guard buffered `PostFlow` payload policies (`ExtractVariables`, `JavaScript`, `SemanticCachePopulate`, buffered `LLMTokenQuota` / `SanitizeModelResponse`) with `flow.is_streaming != "true"` so they do not fail or trigger silent full-response buffering.
- **Downstream Cloud Load Balancer 30-second default timeout:** Google Cloud HTTPS Load Balancers default `backend_service.timeout_sec` to **30 seconds**, which cuts off long LLM SSE streams mid-generation. Set `timeout_sec >= 300` on the LB backend service (`lb_backend_timeout_sec = 300` is the default in `apigee-x-provisioning`).
- **HTTP status vs mid-stream faults:** HTTP `200 OK` headers are flushed when the stream starts. If a fault occurs inside `<EventFlow>` (e.g., `RaiseFault` or `SanitizeModelResponse` `FilterMatched`), Apigee emits the fault as an SSE `data: {"fault":...}` event and terminates the stream immediately.

### SSE Common Use Case: LLM Streaming Responses

LLM APIs stream token deltas via SSE (`curl -N`):
- **Vertex AI Gemini**: `:streamGenerateContent?alt=sse` (`?alt=sse` is mandatory for `text/event-stream` framing).
- **Vertex AI Anthropic Claude**: `:streamRawPredict` with `"stream": true` in the JSON request body.
- **Vertex AI OpenAI Chat Completions**: `/v1beta1/projects/{p}/locations/global/endpoints/openapi/chat/completions` with `"stream": true`.

## HTTP Streaming (Non-WebSocket, Non-SSE)

### When to Enable Streaming

Enable streaming for proxies that handle large payloads without inspecting them.

ProxyEndpoint:

```xml
<ProxyEndpoint name="default">
  <HTTPProxyConnection>
    <BasePath>/v1/files</BasePath>
    <Properties>
      <Property name="request.streaming.enabled">true</Property>
      <Property name="response.streaming.enabled">true</Property>
    </Properties>
  </HTTPProxyConnection>
  ...
</ProxyEndpoint>
```

TargetEndpoint:

```xml
<TargetEndpoint name="default">
  <HTTPTargetConnection>
    <URL>https://storage.example.com</URL>
    <Properties>
      <Property name="request.streaming.enabled">true</Property>
      <Property name="response.streaming.enabled">true</Property>
    </Properties>
  </HTTPTargetConnection>
</TargetEndpoint>
```

### Streaming vs Buffering

| Mode | Behavior | Use when |
|---|---|---|
| Buffered (default) | Full payload loaded into memory | Need to inspect/transform payload |
| Streaming | Payload passed through without buffering | Large files, passthrough, no inspection needed |

### Policies Incompatible with Streaming

These policies require the full payload in memory and will trigger silent buffering if used with streaming enabled:

- JSONtoXML / XMLtoJSON
- JSONThreatProtection / XMLThreatProtection
- ExtractVariables (payload extraction; header/queryparam extraction still works)
- OASValidation (body validation)
- JavaScript (when accessing `request.content` or `response.content`)
- XSLTransform

**Gotcha**: If these policies execute while streaming is enabled, Apigee silently buffers the entire payload. For large payloads this can cause OutOfMemory errors. Either disable streaming OR remove incompatible policies.

### Streaming Use Cases

- File upload/download proxies (images, PDFs, binaries).
- Video/media streaming.
- Large dataset export (CSV, JSON arrays).
- Passthrough proxies where Apigee only handles auth/routing (not transformation).

## Complete WebSocket Proxy Example

This example demonstrates a production-ready WebSocket proxy with PreFlow authentication via VerifyAPIKey, connection quota enforcement, and PostClientFlow logging for guaranteed handshake capture.

ProxyEndpoint:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<ProxyEndpoint name="default">
  <HTTPProxyConnection>
    <BasePath>/v1/realtime</BasePath>
  </HTTPProxyConnection>
  <PreFlow name="PreFlow">
    <Request>
      <Step><Name>VAK-VerifyKey</Name></Step>
      <Step><Name>Q-ConnectionQuota</Name></Step>
    </Request>
    <Response/>
  </PreFlow>
  <Flows/>
  <PostFlow name="PostFlow">
    <Request/>
    <Response/>
  </PostFlow>
  <PostClientFlow name="PostClientFlow">
    <Request/>
    <Response>
      <Step><Name>ML-LogConnection</Name></Step>
    </Response>
  </PostClientFlow>
  <RouteRule name="default">
    <TargetEndpoint>ws-backend</TargetEndpoint>
  </RouteRule>
</ProxyEndpoint>
```

TargetEndpoint:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<TargetEndpoint name="ws-backend">
  <HTTPTargetConnection>
    <URL>wss://realtime-api.example.com/ws</URL>
    <Properties>
      <Property name="keepalive.timeout.millis">3600000</Property>
      <Property name="io.timeout.millis">3600000</Property>
      <Property name="connect.timeout.millis">10000</Property>
    </Properties>
  </HTTPTargetConnection>
</TargetEndpoint>
```

MessageLogging policy (ML-LogConnection):

```xml
<MessageLogging name="ML-LogConnection">
  <CloudLogging>
    <LogName>projects/{organization.name}/logs/websocket-connections</LogName>
    <Message contentType="application/json">
      {
        "event": "ws_connect",
        "clientIp": "{client.ip}",
        "apiKey": "{verifyapikey.VAK-VerifyKey.client_id}",
        "path": "{request.path}",
        "timestamp": "{system.timestamp}",
        "requestId": "{system.uuid}"
      }
    </Message>
    <Labels>
      <Label>
        <Key>type</Key>
        <Value>websocket</Value>
      </Label>
      <Label>
        <Key>proxy</Key>
        <Value>{apiproxy.name}</Value>
      </Label>
    </Labels>
    <ResourceType>api</ResourceType>
  </CloudLogging>
</MessageLogging>
```

---

See also: [endpoints_and_routing.md](endpoints_and_routing.md) | [debugging_and_performance.md](debugging_and_performance.md) | [policies_security.md](policies_security.md) | [load_balancing_and_routing.md](load_balancing_and_routing.md)
