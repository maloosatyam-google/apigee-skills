# MCP (Model Context Protocol) and REST Bridging Guide

## 1. Concept
MCP exposes tools over JSON-RPC 2.0 (streamable HTTP: `POST` with
`Accept: application/json, text/event-stream`). Apigee governs the MCP traffic and, where needed, the REST APIs
behind the MCP server. There are two common topologies:

| Topology | Where tools live | Apigee role |
|---|---|---|
| **A. Govern an MCP server** | MCP server (for example Cloud Run) implements tools | MCP proxy: ParsePayload, per-tool products, limits |
| **B. MCP over REST** | REST APIs. The MCP server (custom, or Apigee API hub generated) maps tools to REST calls | MCP proxy in front, plus REST proxies behind (private, reachable only from the MCP layer) |

## 2. Protocol mapping (topology B)

1. **Agent tool call**
   ```json
   {"jsonrpc":"2.0","id":7,"method":"tools/call",
    "params":{"name":"getCustomer","arguments":{"customerId":"CUST-1001"}}}
   ```
2. **Apigee MCP proxy (inbound)**: `ParsePayload` → `operation = tools/call/getCustomer` → VerifyAPIKey against the
   product's `payloadOperationGroup` → per-tool Quota → argument limits → strip client credentials.
3. **MCP server → REST**: `GET /customer-service/v1/customers/CUST-1001`, forwarding the caller's `x-apikey`
   if the REST proxy needs to know which products the caller holds (AccessEntity).
4. **REST proxy (private)**: allow only internal callers (for example via trusted `X-Forwarded-For` hops or the NAT
   IP allowlist kept in a KVM), `OASValidation` with `ValidateMessageBody=true`, business limits
   (KVM threshold + AccessEntity product check, fail closed), `GoogleIDToken` to Cloud Run.
5. **Response back to MCP** (the server wraps REST JSON):
   ```json
   {"jsonrpc":"2.0","id":7,"result":{"content":[{"type":"text","text":"{\"name\":\"Alice Johnson\",\"tier\":\"gold\"}"}]}}
   ```
   Errors that the agent should reason about go in `result.isError=true` (tool error). Protocol errors use
   `error.code` (for example `-32601` method not found, `-32602` invalid params).

## 3. Test matrix (run after every deploy)

| Case | Expect |
|---|---|
| `initialize` / `ping` / `notifications/initialized` without a key | 200 (handshake is keyless) |
| `tools/list` without a key | 401 |
| `tools/list` with a persona key | 200, only the tools in that product |
| `tools/call` for a tool outside the product | 401 / 403 from VerifyAPIKey |
| `tools/call` beyond the per-tool quota | 429 |
| Limited argument above the threshold (numeric id **and** string id) | 403, `result.isError=true`, id type preserved, `x-gateway-limit` set |
| Same call with an unrestricted persona | 200 |
| `resources/list`, `prompts/get` | 400, `-32601` |
| Direct call to the private REST proxy from the internet | 403 |
