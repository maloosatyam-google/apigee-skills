---
name: apigee-mcp-gateway
description: >-
  Design, scaffold, update and operate Apigee X as an enterprise MCP / Tools Gateway for AI agents: MCP
  (Model Context Protocol) servers and the REST tool APIs behind them. Use for per-tool authorization with
  API products (payloadOperationGroup + ParsePayload), per-tool quota, persona-based tool sets, business-rule
  limits on tool arguments returned as MCP tool errors, keyless MCP handshake, JSON-RPC protocol hygiene,
  credential stripping, Cloud Run backends with Google ID tokens, Apigee API hub generated MCP servers, and
  tool-call audit logging. Standalone: includes a scaffold that generates the proxy and per-persona API products.
---

# Apigee MCP / Tools Gateway

Agents (ADK, Gemini CLI, Claude, any MCP client) should never hold backend credentials or reach tools
directly. Apigee sits in between and decides, **per tool call**, who may call what, how often, and
with which argument values. Shares the validator/deploy scripts and the full policy XML reference
([`policies_mcp_tools.md`](../apigee-proxy-builder/references/policies_mcp_tools.md)) with
[`apigee-proxy-builder`](../apigee-proxy-builder/SKILL.md).

```text
Agent ── MCP JSON-RPC (x-apikey) ──▶ Apigee /<domain>/mcp ──▶ MCP server (Cloud Run, GoogleIDToken)
                                         │                         └─▶ REST APIs (optionally via Apigee again)
  ParsePayload → method allowlist → VerifyAPIKey (per-tool product) → Quota (per tool) → arg limits → strip creds
```

## When to use
- "Govern my MCP server", "only Support agents may call issueRefund", "limit refunds over $50", "per-tool quota"
- Exposing REST APIs as agent tools (MCP over REST) with the same governance
- Debugging MCP clients that hang or reject gateway errors (id type, handshake, method allowlist)

## Step 0 - gather requirements
| Question | Drives |
|---|---|
| MCP server URL (streamable HTTP)? Cloud Run? | `--target` (GoogleIDToken auto, `--no-id-token` otherwise) |
| Full tool list (`tools/list` output)? | `--tools` |
| Which personas / agents get which tools? | `--persona "Name:tool,tool"` → one API product each |
| Argument-level business rules? | `--limit "tool:arg:max:Persona:CODE:message"` |
| Calls per minute per tool? | `--quota` |

## Quick start

```bash
SK=<path-to>/skills
python3 $SK/apigee-mcp-gateway/scripts/scaffold_mcp_proxy.py --out ./orders-mcp \
  --name orders-mcp --basepath /orders/mcp --target https://orders-mcp-xyz.a.run.app/mcp \
  --tools searchOrders,getOrder,issueRefund \
  --persona "Support:searchOrders,getOrder,issueRefund" --persona "Insights:searchOrders" \
  --limit 'issueRefund:amount:50:Support:REFUND_LIMIT:Refunds over $50 need supervisor approval.'
python3 $SK/apigee-proxy-builder/scripts/validate_bundle.py ./orders-mcp --strict
bash    $SK/apigee-proxy-builder/scripts/deploy_bundle.sh ./orders-mcp --org $ORG --env $ENV --sa $RUNTIME_SA
for f in ./orders-mcp/products/*.json; do
  python3 $SK/apigee-proxy-lifecycle/scripts/apigee_ctl.py product-apply "$f" --env $ENV
done
# then one developer app per persona (each app gets that persona's product)
```

Demo calls:
```bash
U=https://$HOST/orders/mcp; H=(-H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream')
curl -s $U "${H[@]}" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"demo","version":"1"}}}'   # keyless handshake OK
curl -s $U "${H[@]}" -H "x-apikey: $INSIGHTS_KEY" -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"issueRefund","arguments":{"orderId":"O-1","amount":10}}}'  # 401: tool not in product
curl -s $U "${H[@]}" -H "x-apikey: $SUPPORT_KEY"  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"issueRefund","arguments":{"orderId":"O-1","amount":80}}}'  # 403 isError REFUND_LIMIT
curl -s $U "${H[@]}" -d '{"jsonrpc":"2.0","id":4,"method":"resources/list"}'   # 400 -32601 method not allowed
```

Point an agent at it, for example in Gemini CLI `settings.json`:
`"mcpServers": {"orders": {"httpUrl": "https://HOST/orders/mcp", "headers": {"x-apikey": "$SUPPORT_KEY"}}}`.

## 1. Per-tool authorization: ParsePayload + API products
- `ParsePayload` (`<PayloadType>JSON-RPC-2.0</PayloadType><Protocol>MCP</Protocol>`) sets
  `parsepayload.PP-MCP.operation` = `tools/call/<tool>` or `tools/list`, plus `json-rpc.request.method/id/params.*`.
- API products use **`payloadOperationGroup`** with operations `tools/list` and `tools/call/<tool>`, each with its own quota.
  VerifyAPIKey then rejects any tool that is not in the caller's product. **Persona = product = tool set**.
- `Quota` with `<UseQuotaConfigInAPIProduct stepName="VA-VerifyAPIKey">` applies the per-tool quota from the product.
- Adding a tool to a persona is a **product change**, not a proxy change: `product-get` → edit → `product-apply`.

## 2. Protocol hygiene
- **Allowlist methods** (`tools/list`, `tools/call`, `initialize`, `ping`, `notifications/*`). Reject everything else
  with JSON-RPC `-32601` before the backend. Add `resources/*` / `prompts/*` only if the server implements them and
  products cover them.
- **Keep the handshake keyless**: run VerifyAPIKey and Quota only on `tools/*`. Products define only tool operations,
  so verifying `initialize` would break every MCP client.
- Strip `Authorization`, `x-apikey`, `X-Serverless-Authorization` and `X-Goog-IAP-JWT-Assertion` before the target.
  The backend trusts Apigee's **GoogleIDToken** (Cloud Run audience = service URL; grant the runtime SA `roles/run.invoker`).
- MCP clients match responses by `id` **including its JSON type**. Any gateway-generated response must echo
  a numeric id as a number and a string id as a string (two RaiseFault variants, see the scaffold).
- Streamable HTTP may answer with `text/event-stream`; do not add response-body policies that parse JSON on the MCP
  proxy unless you enable streaming handling ([websockets_and_streaming.md](../apigee-proxy-builder/references/websockets_and_streaming.md)).

## 3. Business-rule limits on tool arguments
Extract the arguments with ExtractVariables (`type="double"`), then use a RaiseFault conditioned on
tool + argument + **product name**. Return HTTP 403 with
`{"jsonrpc":"2.0","id":…,"result":{"isError":true,"content":[{"type":"text","text":"{…REFUND_LIMIT…}"}]}}`.
The agent reads the reason and can take the approved path (for example create an approval ticket). It should not
retry or claim success. Mark the refusal with a custom header (`x-gateway-limit`), because Apigee strips `x-apigee-*`.

For limits in a **REST** proxy behind an MCP server that forwards the caller's key, use `AccessEntity`
(app by `consumerkey`) to read the key's products, and keep thresholds in a KVM. Fail closed if the key is unknown.

## 4. Generated MCP servers (Apigee API hub)
Apigee can expose any OpenAPI-described proxy as an MCP server (tools generated from operations). Apply the same
governance: register the spec in API hub, enable MCP, and protect it with API products listing the generated
`tools/call/<operationId>` operations. Hand-built proxies (above) give full control over argument limits.
Build the REST proxy itself with [`apigee-api-proxy`](../apigee-api-proxy/SKILL.md).

## 5. Observability
`MessageLogging` in PostClientFlow with `parsepayload.PP-MCP.operation`, app, product, status and fault. Optional
`DataCapture` with `dc_tool_name` for per-tool analytics.

## 6. Updating a deployed MCP proxy
New limit or method: `apigee_ctl.py export orders-mcp --env $ENV --out ./m` → add the policy with
`bundle_edit.py add-policy` / `attach --after VA-VerifyAPIKey` → `validate_bundle.py` → `apigee_ctl.py diff` → `update`.
Re-run the test matrix in [references/mcp_rest_bridge.md](references/mcp_rest_bridge.md) after every deploy.

## Troubleshooting
| Symptom | Likely cause |
|---|---|
| Client hangs after a gateway error | Error `id` type differs from the request (string vs number) |
| Every client fails on connect | VerifyAPIKey runs on `initialize` (must be `tools/*` only) |
| `tools/list` shows tools the persona cannot call | Expected: the MCP server lists all tools; calls are enforced. Filter the list in the server if needed |
| 401 for an allowed tool | Product operation name differs from `tools/call/<exact tool name>` (case-sensitive), or product not in this env |
| 401/403 from Cloud Run | Missing `--sa`, SA lacks `run.invoker`, or audience is not `scheme://host` |
| Limit never triggers | Argument not numeric in the request (`type="double"` extraction fails) or product name in the condition differs |
