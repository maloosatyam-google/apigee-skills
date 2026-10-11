# MCP / Tools Gateway Policies (ParsePayload and friends)

How to put Apigee in front of **MCP servers** (Model Context Protocol, JSON-RPC 2.0 over
streamable HTTP) so that agents get per-tool authorization, per-tool quota, business-rule
limits and audit logging. The pattern comes from production MCP proxies.

## 1. ParsePayload (`PP-`)

```xml
<ParsePayload async="false" continueOnError="false" enabled="true" name="PP-MCP">
  <DisplayName>PP-MCP</DisplayName>
  <Source>request</Source>
  <PayloadType>JSON-RPC-2.0</PayloadType>
  <Protocol>MCP</Protocol>
</ParsePayload>
```

Variables it sets (prefix `parsepayload.{policyName}.`):

| Variable | Example |
|---|---|
| `operation` | `tools/call/get_weather`, `tools/list` |
| `json-rpc.request.method` | `tools/call`, `tools/list`, `initialize`, `ping`, `notifications/initialized` |
| `json-rpc.request.id` | `req_001` |
| `json-rpc.request.params.name` | `get_weather` |
| `json-rpc.request.params.arguments.{arg}` | `San Francisco, CA` |

`operation` is what **API product `payloadOperationGroup`** matches on. This means
VerifyAPIKey (and Quota with `UseQuotaConfigInAPIProduct`) authorizes **per tool**, not per path.
ParsePayload is an Extensible policy. Avoid combining it with request streaming.

## 2. API product: per-tool entitlement and quota

```json
{
  "name": "Banking Tools MCP - Support and Sales",
  "approvalType": "auto",
  "environments": ["prod"],
  "attributes": [{ "name": "access", "value": "private" }],
  "payloadOperationGroup": {
    "operationConfigs": [
      { "apiSource": "banking-mcp",
        "operations": [ { "operation": "tools/list" } ],
        "quota": { "limit": "30", "interval": "1", "timeUnit": "minute" } },
      { "apiSource": "banking-mcp",
        "operations": [ { "operation": "tools/call/searchCustomers" } ],
        "quota": { "limit": "20", "interval": "1", "timeUnit": "minute" } },
      { "apiSource": "banking-mcp",
        "operations": [ { "operation": "tools/call/reverseFee" } ],
        "quota": { "limit": "5", "interval": "1", "timeUnit": "minute" } }
    ]
  }
}
```

A tool that is **not listed** is rejected by VerifyAPIKey for keys on that product. Persona
products (support, operations, admin, insights) are therefore just different lists of tools.

```xml
<Quota continueOnError="false" enabled="true" name="Q-Limit">
  <UseQuotaConfigInAPIProduct stepName="VA-VerifyAPIKey">
    <DefaultConfig><Allow>10</Allow><Interval>1</Interval><TimeUnit>minute</TimeUnit></DefaultConfig>
  </UseQuotaConfigInAPIProduct>
  <Distributed>true</Distributed>
  <Synchronous>true</Synchronous>
</Quota>
```

## 3. Reference ProxyEndpoint

```xml
<ProxyEndpoint name="default">
  <HTTPProxyConnection><BasePath>/banking/mcp</BasePath></HTTPProxyConnection>
  <PreFlow name="PreFlow">
    <Request>
      <Step><Name>CORS-Allow</Name></Step>
      <Step><Name>PP-MCP</Name><Condition>request.verb != "OPTIONS"</Condition></Step>
      <!-- 1. Method allowlist: anything else never reaches the backend -->
      <Step>
        <Name>RF-MethodNotAllowed</Name>
        <Condition>request.verb != "OPTIONS" and not (parsepayload.PP-MCP.json-rpc.request.method = "tools/list"
          or parsepayload.PP-MCP.json-rpc.request.method = "tools/call"
          or parsepayload.PP-MCP.json-rpc.request.method = "initialize"
          or parsepayload.PP-MCP.json-rpc.request.method = "ping"
          or parsepayload.PP-MCP.json-rpc.request.method StartsWith "notifications/")</Condition>
      </Step>
      <!-- 2. Key + quota only on tools/*: the MCP handshake stays keyless, otherwise clients break -->
      <Step><Name>VA-VerifyAPIKey</Name>
        <Condition>parsepayload.PP-MCP.json-rpc.request.method = "tools/list" or parsepayload.PP-MCP.json-rpc.request.method = "tools/call"</Condition></Step>
      <Step><Name>Q-Limit</Name>
        <Condition>parsepayload.PP-MCP.json-rpc.request.method = "tools/list" or parsepayload.PP-MCP.json-rpc.request.method = "tools/call"</Condition></Step>
      <!-- 3. Business-rule limit on tool arguments -->
      <Step><Name>EV-ToolCall</Name><Condition>parsepayload.PP-MCP.json-rpc.request.method = "tools/call"</Condition></Step>
      <Step><Name>EV-RpcIdNumber</Name><Condition>parsepayload.PP-MCP.json-rpc.request.method = "tools/call"</Condition></Step>
      <Step>
        <Name>RF-LimitNumericId</Name>
        <Condition>parsepayload.PP-MCP.json-rpc.request.method = "tools/call" and mcp.tool = "reverseFee"
          and mcp.amount GreaterThan 50
          and verifyapikey.VA-VerifyAPIKey.apiproduct.name = "Banking Tools MCP - Support and Sales"
          and (mcpn.id != null and not (mcp.rpcId JavaRegex "^-?[0-9]+$"))</Condition>
      </Step>
      <Step>
        <Name>RF-LimitStringId</Name>
        <Condition>parsepayload.PP-MCP.json-rpc.request.method = "tools/call" and mcp.tool = "reverseFee"
          and mcp.amount GreaterThan 50
          and verifyapikey.VA-VerifyAPIKey.apiproduct.name = "Banking Tools MCP - Support and Sales"
          and not (mcpn.id != null and not (mcp.rpcId JavaRegex "^-?[0-9]+$"))</Condition>
      </Step>
      <!-- 4. Never forward client credentials -->
      <Step><Name>AM-RemoveAuthorization</Name>
        <Condition>parsepayload.PP-MCP.json-rpc.request.method = "tools/list" or parsepayload.PP-MCP.json-rpc.request.method = "tools/call"</Condition></Step>
    </Request>
    <Response/>
  </PreFlow>
  <PostClientFlow><Response><Step><Name>ML-CloudLogging</Name></Step></Response></PostClientFlow>
  <RouteRule name="default"><TargetEndpoint>default</TargetEndpoint></RouteRule>
</ProxyEndpoint>
```

Target (Cloud Run MCP server, authenticated with the Apigee service account):
```xml
<TargetEndpoint name="default">
  <HTTPTargetConnection>
    <URL>https://{SERVICE}-{HASH}.{REGION}.run.app/banking/mcp</URL>
    <Authentication><GoogleIDToken><Audience>https://{SERVICE}-{HASH}.{REGION}.run.app</Audience></GoogleIDToken></Authentication>
  </HTTPTargetConnection>
</TargetEndpoint>
```

## 4. Supporting policies

```xml
<!-- Read tool name, typed argument and the JSON-RPC id -->
<ExtractVariables continueOnError="false" enabled="true" name="EV-ToolCall">
  <Source clearPayload="false">request</Source>
  <VariablePrefix>mcp</VariablePrefix>
  <JSONPayload>
    <Variable name="tool"><JSONPath>$.params.name</JSONPath></Variable>
    <Variable name="amount" type="double"><JSONPath>$.params.arguments.amount</JSONPath></Variable>
    <Variable name="amountText"><JSONPath>$.params.arguments.amount</JSONPath></Variable>
    <Variable name="rpcId"><JSONPath>$.id</JSONPath></Variable>
  </JSONPayload>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</ExtractVariables>

<!-- Resolves only when id is numeric: lets the fault echo the id with its original JSON type -->
<ExtractVariables continueOnError="true" enabled="true" name="EV-RpcIdNumber">
  <Source clearPayload="false">request</Source>
  <VariablePrefix>mcpn</VariablePrefix>
  <JSONPayload><Variable name="id" type="long"><JSONPath>$.id</JSONPath></Variable></JSONPayload>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</ExtractVariables>
```

**Business-limit refusal as an MCP tool error.** The agent sees the reason and can escalate,
for example by opening an approval ticket. Use `variablePrefix="@"`/`variableSuffix="#"` so JSON
braces do not need escaping. Use two variants: one echoes a **numeric** id unquoted, the other
echoes a **string** id quoted. MCP clients match responses by id **including its type**.
```xml
<RaiseFault continueOnError="false" enabled="true" name="RF-LimitNumericId">
  <FaultResponse>
    <Set>
      <Headers><Header name="x-gateway-limit">FEE_REVERSAL_LIMIT</Header></Headers>
      <StatusCode>403</StatusCode>
      <ReasonPhrase>Forbidden</ReasonPhrase>
      <Payload contentType="application/json" variablePrefix="@" variableSuffix="#">{"jsonrpc":"2.0","id":@mcp.rpcId#,"result":{"isError":true,"content":[{"type":"text","text":"{\"status\": 403, \"error\": \"FEE_REVERSAL_LIMIT\", \"message\": \"Fee reversals over $50 need supervisor approval. Requested $@mcp.amountText#.\", \"limit\": 50, \"requested\": @mcp.amountText#, \"enforcedBy\": \"Apigee\"}"}]}}</Payload>
    </Set>
  </FaultResponse>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</RaiseFault>
<!-- RF-LimitStringId: identical except  "id":"@mcp.rpcId#"  -->

<RaiseFault continueOnError="false" enabled="true" name="RF-MethodNotAllowed">
  <FaultResponse><Set>
    <StatusCode>400</StatusCode><ReasonPhrase>Bad Request</ReasonPhrase>
    <Payload contentType="application/json">{"jsonrpc":"2.0","id":null,"error":{"code":-32601,"message":"Method not allowed by gateway"}}</Payload>
  </Set></FaultResponse>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</RaiseFault>
```

> [!NOTE]
> Apigee strips `x-apigee-*` response headers. Use your own prefix (for example `x-gateway-limit`) for
> markers that tests and traces depend on.

**Limit that depends on the caller's products (REST backend behind an MCP server).** When an
Apigee-hosted MCP server forwards the caller's `x-apikey` to a REST proxy, `AccessEntity` can
look up which products that key holds. A KVM-configured rule then applies only to governed
products, and fails closed if the key cannot be resolved:
```xml
<AccessEntity continueOnError="true" enabled="true" name="AE-CallerKey">
  <EntityType value="app"/>
  <EntityIdentifier ref="request.header.x-apikey" type="consumerkey"/>
</AccessEntity>
```
The app XML is available in `AccessEntity.AE-CallerKey`. Inspect only the `<Credential>` block whose
`<ConsumerKey>` matches the presented key, so that other keys on the same app do not count.

## 5. Checklist

- [ ] `ParsePayload` before anything that reads `parsepayload.*`
- [ ] Method allowlist (`tools/list`, `tools/call`, `initialize`, `ping`, `notifications/*`)
- [ ] VerifyAPIKey + Quota on `tools/*` only (handshake keyless)
- [ ] Product `payloadOperationGroup` lists `tools/list` + each `tools/call/{name}`
- [ ] Business limits return JSON-RPC `result.isError=true` with the id echoed in its original type
- [ ] Strip `Authorization`, `x-apikey`, `X-Serverless-Authorization`, IAP headers before the target
- [ ] `GoogleIDToken` auth to Cloud Run. MessageLogging in PostClientFlow

See also: [tools-gateway-manager skill](../../tools-gateway-manager/SKILL.md) | [policies_ai_gateway.md](policies_ai_gateway.md)
