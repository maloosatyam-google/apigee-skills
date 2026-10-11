#!/usr/bin/env python3
"""Scaffold an Apigee X MCP (Tools Gateway) proxy + per-tool API products (stdlib only).

  python3 scaffold_mcp_proxy.py --out ./orders-mcp --name orders-mcp --basepath /orders/mcp \
      --target https://orders-mcp-xyz.a.run.app/mcp \
      --tools searchOrders,getOrder,issueRefund \
      --persona "Support:searchOrders,getOrder,issueRefund" --persona "Insights:searchOrders" \
      --limit "issueRefund:amount:50:Support:REFUND_LIMIT:Refunds over $50 need supervisor approval."

--target         MCP server URL (streamable HTTP). Cloud Run gets GoogleIDToken auth automatically
                 (audience = scheme://host); pass --no-id-token for other backends.
--tools          every tool the server exposes (tools/list is always granted)
--persona        "<Persona>:<tool,tool>" -> API product "<Name> Tools MCP - <Persona>" (repeatable)
--limit          "<tool>:<numeric arg>:<max>:<Persona>:<CODE>:<message>" -> business-rule refusal returned
                 as an MCP tool error (403, result.isError=true) for keys on that persona (repeatable)
--quota N        per-operation requests/minute in the products (default 20; tools/list 30)
"""
import argparse
import json
import os
from urllib.parse import urlparse

H = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
M = 'parsepayload.PP-MCP.json-rpc.request.method'
TOOLS_ONLY = f'{M} = "tools/list" or {M} = "tools/call"'


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--out", required=True)
    a.add_argument("--name", required=True)
    a.add_argument("--basepath", required=True)
    a.add_argument("--target", required=True)
    a.add_argument("--tools", required=True)
    a.add_argument("--persona", action="append", default=[])
    a.add_argument("--limit", action="append", default=[])
    a.add_argument("--quota", type=int, default=20)
    a.add_argument("--no-id-token", action="store_true")
    o = a.parse_args()

    tools = [t.strip() for t in o.tools.split(",") if t.strip()]
    title = o.name.replace("-mcp", "").replace("-", " ").title()
    personas = {}
    for p in o.persona or [f"All:{o.tools}"]:
        pname, tl = p.split(":", 1)
        personas[pname] = [t.strip() for t in tl.split(",") if t.strip()]
    product_name = {p: f"{title} Tools MCP - {p}" for p in personas}

    P = {}
    P["CORS-Allow"] = """<CORS continueOnError="false" enabled="true" name="CORS-Allow">
  <AllowOrigins>{request.header.origin}</AllowOrigins>
  <AllowMethods>POST, GET, HEAD</AllowMethods>
  <AllowHeaders>*</AllowHeaders>
  <ExposeHeaders>*</ExposeHeaders>
  <MaxAge>3628800</MaxAge>
  <GeneratePreflightResponse>true</GeneratePreflightResponse>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</CORS>"""
    P["PP-MCP"] = """<ParsePayload async="false" continueOnError="false" enabled="true" name="PP-MCP">
  <Source>request</Source>
  <PayloadType>JSON-RPC-2.0</PayloadType>
  <Protocol>MCP</Protocol>
</ParsePayload>"""
    P["RF-MethodNotAllowed"] = """<RaiseFault async="false" continueOnError="false" enabled="true" name="RF-MethodNotAllowed">
  <FaultResponse>
    <Set>
      <StatusCode>400</StatusCode>
      <ReasonPhrase>Bad Request</ReasonPhrase>
      <Payload contentType="application/json">{"jsonrpc":"2.0","id":null,"error":{"code":-32601,"message":"Method not allowed by gateway"}}</Payload>
    </Set>
  </FaultResponse>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</RaiseFault>"""
    P["VA-VerifyAPIKey"] = """<VerifyAPIKey async="false" continueOnError="false" enabled="true" name="VA-VerifyAPIKey">
  <APIKey ref="request.header.x-apikey"/>
</VerifyAPIKey>"""
    P["Q-Limit"] = """<Quota continueOnError="false" enabled="true" name="Q-Limit">
  <UseQuotaConfigInAPIProduct stepName="VA-VerifyAPIKey">
    <DefaultConfig><Allow>10</Allow><Interval>1</Interval><TimeUnit>minute</TimeUnit></DefaultConfig>
  </UseQuotaConfigInAPIProduct>
  <Distributed>true</Distributed>
  <Synchronous>true</Synchronous>
</Quota>"""
    P["AM-RemoveAuthorization"] = """<AssignMessage async="false" continueOnError="false" enabled="true" name="AM-RemoveAuthorization">
  <Remove>
    <Headers>
      <Header name="Authorization"/>
      <Header name="X-Serverless-Authorization"/>
      <Header name="X-Goog-IAP-JWT-Assertion"/>
      <Header name="x-api-key"/>
      <Header name="x-apikey"/>
    </Headers>
  </Remove>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <AssignTo createNew="false" transport="http" type="request"/>
</AssignMessage>"""
    P["ML-CloudLogging"] = """<MessageLogging continueOnError="true" enabled="true" name="ML-CloudLogging">
  <CloudLogging>
    <LogName>projects/{organization.name}/logs/apigee</LogName>
    <Message contentType="application/json">{"apiProxyName":"{apiproxy.name}","operation":"{parsepayload.PP-MCP.operation}","developerApp":"{verifyapikey.VA-VerifyAPIKey.app.name}","apiProduct":"{verifyapikey.VA-VerifyAPIKey.apiproduct.name}","responseStatusCode":"{response.status.code}","faultName":"{fault.name}","trackingId":"{messageid}"}</Message>
    <ResourceType>api</ResourceType>
  </CloudLogging>
</MessageLogging>"""

    steps = [("CORS-Allow", None),
             ("PP-MCP", 'request.verb != "OPTIONS"'),
             ("RF-MethodNotAllowed", f'request.verb != "OPTIONS" and not ({M} = "tools/list" or {M} = "tools/call" '
                                     f'or {M} = "initialize" or {M} = "ping" or {M} StartsWith "notifications/")'),
             ("VA-VerifyAPIKey", TOOLS_ONLY),
             ("Q-Limit", TOOLS_ONLY)]

    limits = [l.split(":", 5) for l in o.limit]
    if limits:
        args = sorted({l[1] for l in limits})
        ev_vars = "\n".join(
            f'    <Variable name="{x}" type="double"><JSONPath>$.params.arguments.{x}</JSONPath></Variable>\n'
            f'    <Variable name="{x}Text"><JSONPath>$.params.arguments.{x}</JSONPath></Variable>' for x in args)
        P["EV-ToolCall"] = f"""<ExtractVariables async="false" continueOnError="false" enabled="true" name="EV-ToolCall">
  <Source clearPayload="false">request</Source>
  <VariablePrefix>mcp</VariablePrefix>
  <JSONPayload>
    <Variable name="tool"><JSONPath>$.params.name</JSONPath></Variable>
{ev_vars}
    <Variable name="rpcId"><JSONPath>$.id</JSONPath></Variable>
  </JSONPayload>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</ExtractVariables>"""
        P["EV-RpcIdNumber"] = """<ExtractVariables async="false" continueOnError="true" enabled="true" name="EV-RpcIdNumber">
  <!-- resolves only for numeric ids: lets the fault echo the id with its JSON type -->
  <Source clearPayload="false">request</Source>
  <VariablePrefix>mcpn</VariablePrefix>
  <JSONPayload><Variable name="id" type="long"><JSONPath>$.id</JSONPath></Variable></JSONPayload>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</ExtractVariables>"""
        steps += [("EV-ToolCall", f'{M} = "tools/call"'), ("EV-RpcIdNumber", f'{M} = "tools/call"')]
        numeric_id = '(mcpn.id != null and not (mcp.rpcId JavaRegex "^-?[0-9]+$"))'
        for i, (tool, arg, mx, persona, code, msg) in enumerate(limits, 1):
            pn = product_name.get(persona, persona)
            for kind, idjson, idcond in (("Num", "@mcp.rpcId#", numeric_id), ("Str", '"@mcp.rpcId#"', f"not {numeric_id}")):
                name = f"RF-Limit{i}{kind}Id"
                text = (f'{{\\"status\\": 403, \\"error\\": \\"{code}\\", \\"message\\": \\"{msg} Requested @mcp.{arg}Text#.\\", '
                        f'\\"limit\\": {mx}, \\"requested\\": @mcp.{arg}Text#, \\"enforcedBy\\": \\"Apigee\\"}}')
                P[name] = f"""<RaiseFault async="false" continueOnError="false" enabled="true" name="{name}">
  <!-- {code}: {esc(msg)} -->
  <FaultResponse>
    <Set>
      <Headers><Header name="x-gateway-limit">{code}</Header></Headers>
      <StatusCode>403</StatusCode>
      <ReasonPhrase>Forbidden</ReasonPhrase>
      <Payload contentType="application/json" variablePrefix="@" variableSuffix="#">{{"jsonrpc":"2.0","id":{idjson},"result":{{"isError":true,"content":[{{"type":"text","text":"{esc(text)}"}}]}}}}</Payload>
    </Set>
  </FaultResponse>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</RaiseFault>"""
                steps.append((name, f'{M} = "tools/call" and mcp.tool = "{tool}" and mcp.{arg} GreaterThan {mx} '
                                    f'and verifyapikey.VA-VerifyAPIKey.apiproduct.name = "{pn}" and {idcond}'))
    steps.append(("AM-RemoveAuthorization", TOOLS_ONLY))

    root = os.path.join(o.out, "apiproxy")
    for d in ("policies", "proxies", "targets"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    for n, x in P.items():
        open(os.path.join(root, "policies", n + ".xml"), "w").write(H + x + "\n")

    def st(n, c, ind="      "):
        s = f"{ind}<Step>\n{ind}  <Name>{n}</Name>\n"
        if c:
            s += f"{ind}  <Condition>{esc(c)}</Condition>\n"
        return s + f"{ind}</Step>\n"

    open(os.path.join(root, "proxies/default.xml"), "w").write(
        f"""{H}<ProxyEndpoint name="default">
  <HTTPProxyConnection>
    <BasePath>{o.basepath}</BasePath>
  </HTTPProxyConnection>
  <PreFlow name="PreFlow">
    <Request>
{''.join(st(n, c) for n, c in steps)}    </Request>
    <Response/>
  </PreFlow>
  <PostClientFlow>
    <Response>
{st("ML-CloudLogging", None)}    </Response>
  </PostClientFlow>
  <RouteRule name="default">
    <TargetEndpoint>default</TargetEndpoint>
  </RouteRule>
</ProxyEndpoint>
""")
    u = urlparse(o.target)
    auth = "" if o.no_id_token else f"""
    <Authentication>
      <GoogleIDToken>
        <Audience>{u.scheme}://{u.netloc}</Audience>
      </GoogleIDToken>
    </Authentication>"""
    open(os.path.join(root, "targets/default.xml"), "w").write(f"""{H}<TargetEndpoint name="default">
  <HTTPTargetConnection>
    <URL>{o.target}</URL>{auth}
  </HTTPTargetConnection>
</TargetEndpoint>
""")
    pol = "\n".join(f"    <Policy>{n}</Policy>" for n in sorted(P))
    open(os.path.join(root, f"{o.name}.xml"), "w").write(f"""{H}<APIProxy revision="1" name="{o.name}">
  <DisplayName>{o.name}</DisplayName>
  <Description>MCP Tools Gateway scaffolded by the apigee-mcp-gateway skill</Description>
  <BasePaths>{o.basepath}</BasePaths>
  <Policies>
{pol}
  </Policies>
  <ProxyEndpoints>
    <ProxyEndpoint>default</ProxyEndpoint>
  </ProxyEndpoints>
  <TargetEndpoints>
    <TargetEndpoint>default</TargetEndpoint>
  </TargetEndpoints>
</APIProxy>
""")
    os.makedirs(os.path.join(o.out, "products"), exist_ok=True)
    for persona, ptools in personas.items():
        unknown = set(ptools) - set(tools)
        if unknown:
            a.error(f"persona {persona} lists unknown tools {unknown}")
        ops = [{"apiSource": o.name, "operations": [{"operation": "tools/list"}],
                "quota": {"limit": "30", "interval": "1", "timeUnit": "minute"}}]
        ops += [{"apiSource": o.name, "operations": [{"operation": f"tools/call/{t}"}],
                 "quota": {"limit": str(o.quota), "interval": "1", "timeUnit": "minute"}} for t in ptools]
        prod = {"name": product_name[persona], "displayName": product_name[persona], "approvalType": "auto",
                "attributes": [{"name": "access", "value": "private"}, {"name": "persona", "value": persona}],
                "environments": ["<ENV>"], "payloadOperationGroup": {"operationConfigs": ops}}
        fn = product_name[persona].lower().replace(" - ", "_").replace(" ", "_") + ".json"
        open(os.path.join(o.out, "products", fn), "w").write(json.dumps(prod, indent=2) + "\n")
    print(f"Scaffolded {o.out}: {len(P)} policies, {len(personas)} product(s) in {o.out}/products/")


if __name__ == "__main__":
    main()
