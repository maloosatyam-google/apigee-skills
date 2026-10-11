#!/usr/bin/env python3
"""Scaffold a production-shaped Apigee X REST API proxy + API product (stdlib only; PyYAML optional for YAML specs).

  python3 scaffold_api_proxy.py --out ./orders-v1 --name orders-v1 --basepath /orders/v1 \
      --target https://orders-xyz.a.run.app --target-auth idtoken \
      --auth apikey --spike-arrest 50ps --cors "https://app.example.com" \
      --oas ./orders.yaml --cache-ttl 60 --health

Options
  --name / --basepath / --out          required
  --target URL                         backend URL (use https://mocktarget.apigee.net for a quick test)
  --target-server NAME[,NAME]          instead of --target: LoadBalancer over environment TargetServers
  --target-path /path                  path appended for --target-server (default /)
  --target-auth none|idtoken|accesstoken
                                       idtoken = GoogleIDToken (Cloud Run / IAP, audience = scheme://host)
                                       accesstoken = GoogleAccessToken (Google APIs, cloud-platform scope)
  --auth apikey|oauth|jwt|none         client auth (default apikey)
     --apikey-header x-apikey          header carrying the key (apikey)
     --token-endpoint                  (oauth) add POST /token client_credentials flow in the same proxy
     --jwks-url URL --issuer ISS --audience AUD   (jwt) RS256 verification via JWKS
  --spike-arrest RATE                  e.g. 30ps / 600pm (default 100ps; "off" to disable)
  --quota                              product-driven Quota (apikey/oauth), or --jwt-quota N per minute per sub (jwt)
  --cors ORIGINS                       comma list or "*"; adds CORS policy + preflight handling
  --oas SPEC.(json|yaml)               copy spec to resources/oas, add OASValidation (body validation on),
                                       one conditional flow per operation + 404 for unknown paths,
                                       and API product operations per path
  --cache-ttl SECONDS                  ResponseCache for GET (after auth; key = request.uri)
  --shared-flow NAME                   FlowCallout to an existing shared flow at the start of the PreFlow (repeatable)
  --health                             unauthenticated GET /ping answered by the proxy (no target)
  --cloud-logging                      MessageLogging to Cloud Logging in PostClientFlow (deploy with --sa)
  --pass-4xx                           (default on) backend 4xx are passed through; 5xx are sanitized
"""
import argparse
import json
import os
import re
import shutil
import sys
from urllib.parse import urlparse

H = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
METHODS = ("get", "put", "post", "delete", "patch", "head", "options")


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def load_spec(path):
    txt = open(path, encoding="utf-8").read()
    if path.endswith(".json"):
        return json.loads(txt)
    try:
        import yaml  # type: ignore
    except ImportError:
        sys.exit("YAML spec needs PyYAML (pip install pyyaml) - or pass a JSON spec")
    return yaml.safe_load(txt)


def path_to_pattern(p):
    return re.sub(r"\{[^}]+\}", "*", p)


def flow_name(op, method, path):
    n = op.get("operationId") or f"{method}{re.sub(r'[^A-Za-z0-9]+', '_', path)}"
    return re.sub(r"[^A-Za-z0-9_\-]", "_", n)[:100]


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--out", required=True)
    a.add_argument("--name", required=True)
    a.add_argument("--basepath", required=True)
    g = a.add_mutually_exclusive_group(required=True)
    g.add_argument("--target")
    g.add_argument("--target-server")
    a.add_argument("--target-path", default="/")
    a.add_argument("--target-auth", choices=["none", "idtoken", "accesstoken"], default="none")
    a.add_argument("--auth", choices=["apikey", "oauth", "jwt", "none"], default="apikey")
    a.add_argument("--apikey-header", default="x-apikey")
    a.add_argument("--token-endpoint", action="store_true")
    a.add_argument("--jwks-url")
    a.add_argument("--issuer")
    a.add_argument("--audience")
    a.add_argument("--spike-arrest", default="100ps")
    a.add_argument("--quota", action="store_true")
    a.add_argument("--jwt-quota", type=int, default=0)
    a.add_argument("--cors")
    a.add_argument("--oas")
    a.add_argument("--cache-ttl", type=int, default=0)
    a.add_argument("--shared-flow", action="append", default=[])
    a.add_argument("--health", action="store_true")
    a.add_argument("--cloud-logging", action="store_true")
    a.add_argument("--no-pass-4xx", action="store_true")
    o = a.parse_args()
    if o.auth == "jwt" and not (o.jwks_url and o.issuer):
        a.error("--auth jwt needs --jwks-url and --issuer (and usually --audience)")
    if o.token_endpoint and o.auth != "oauth":
        a.error("--token-endpoint requires --auth oauth")
    if o.target_server and o.target_auth == "idtoken":
        a.error("--target-auth idtoken needs a fixed --target URL for the audience")
    if not o.basepath.startswith("/"):
        a.error("--basepath must start with /")

    P = {}          # policy name -> xml
    pre = []        # proxy PreFlow request steps (name, condition)
    post_resp = []  # proxy PostFlow response steps
    flows = []      # conditional flows: (name, condition, [req steps], [resp steps])
    route_rules = []

    # --- which requests are "protected"
    unprotected = ['request.verb = "OPTIONS"'] if o.cors else []
    if o.health:
        unprotected.append('(proxy.pathsuffix MatchesPath "/ping" and request.verb = "GET")')
    if o.token_endpoint:
        unprotected.append('(proxy.pathsuffix MatchesPath "/token" and request.verb = "POST")')
    PROT = f"not ({' or '.join(unprotected)})" if unprotected else None

    def cond(*parts):
        # Always parenthesize each clause: Apigee's not/and/or precedence is a classic source of bugs.
        parts = [p for p in parts if p]
        if len(parts) <= 1:
            return parts[0] if parts else None
        return " and ".join(f"({p})" for p in parts)

    # --- CORS
    if o.cors:
        origins = "{request.header.origin}" if o.cors.strip() == "*" else o.cors
        P["CORS-AllowOrigins"] = f"""<CORS continueOnError="false" enabled="true" name="CORS-AllowOrigins">
  <AllowOrigins>{esc(origins)}</AllowOrigins>
  <AllowMethods>GET, PUT, POST, DELETE, PATCH, OPTIONS</AllowMethods>
  <AllowHeaders>origin, x-requested-with, accept, content-type, authorization, {o.apikey_header}</AllowHeaders>
  <ExposeHeaders>*</ExposeHeaders>
  <MaxAge>3628800</MaxAge>
  <AllowCredentials>false</AllowCredentials>
  <GeneratePreflightResponse>true</GeneratePreflightResponse>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</CORS>"""
        pre.append(("CORS-AllowOrigins", None))
        route_rules.append(("preflight", 'request.verb = "OPTIONS"', None))

    # --- shared flows (e.g. org-wide security) run early
    for sf in o.shared_flow:
        n = f"FC-{sf}"
        P[n] = f"""<FlowCallout continueOnError="false" enabled="true" name="{n}">
  <SharedFlowBundle>{sf}</SharedFlowBundle>
</FlowCallout>"""
        pre.append((n, PROT))

    # --- authentication
    auth_step = None
    if o.auth == "apikey":
        auth_step = "VA-VerifyAPIKey"
        P[auth_step] = f"""<VerifyAPIKey continueOnError="false" enabled="true" name="{auth_step}">
  <APIKey ref="request.header.{o.apikey_header}"/>
</VerifyAPIKey>"""
    elif o.auth == "oauth":
        auth_step = "OA-VerifyAccessToken"
        P[auth_step] = f"""<OAuthV2 continueOnError="false" enabled="true" name="{auth_step}">
  <Operation>VerifyAccessToken</Operation>
</OAuthV2>"""
    elif o.auth == "jwt":
        auth_step = "JWT-VerifyToken"
        aud = f"\n  <Audience>{esc(o.audience)}</Audience>" if o.audience else ""
        P[auth_step] = f"""<VerifyJWT continueOnError="false" enabled="true" name="{auth_step}">
  <Algorithm>RS256</Algorithm>
  <Source>request.header.authorization</Source>
  <PublicKey>
    <JWKS uri="{esc(o.jwks_url)}"/>
  </PublicKey>
  <Issuer>{esc(o.issuer)}</Issuer>{aud}
  <TimeAllowance>60s</TimeAllowance>
</VerifyJWT>"""
    if auth_step:
        pre.append((auth_step, PROT))
    client_id = {"apikey": "verifyapikey.VA-VerifyAPIKey.client_id",
                 "oauth": "client_id",
                 "jwt": "jwt.JWT-VerifyToken.decoded.claim.sub"}.get(o.auth)

    # --- traffic management
    if o.spike_arrest.lower() != "off":
        ident = f'\n  <Identifier ref="{client_id}"/>' if client_id else ""
        P["SA-SpikeArrest"] = f"""<SpikeArrest continueOnError="false" enabled="true" name="SA-SpikeArrest">
  <Rate>{o.spike_arrest}</Rate>{ident}
  <UseEffectiveCount>true</UseEffectiveCount>
</SpikeArrest>"""
        pre.append(("SA-SpikeArrest", PROT))
    if o.quota and o.auth in ("apikey", "oauth"):
        P["Q-ProductQuota"] = f"""<Quota continueOnError="false" enabled="true" name="Q-ProductQuota" type="calendar">
  <!-- Limits come from the API product operationConfigs[].quota of the caller's key/token -->
  <UseQuotaConfigInAPIProduct stepName="{auth_step}">
    <DefaultConfig>
      <Allow>1000</Allow>
      <Interval>1</Interval>
      <TimeUnit>hour</TimeUnit>
    </DefaultConfig>
  </UseQuotaConfigInAPIProduct>
  <Distributed>true</Distributed>
  <Synchronous>true</Synchronous>
  <StartTime>2025-01-01 00:00:00</StartTime>
</Quota>"""
        pre.append(("Q-ProductQuota", PROT))
    elif o.auth == "jwt" and o.jwt_quota:
        P["Q-PerSubject"] = f"""<Quota continueOnError="false" enabled="true" name="Q-PerSubject" type="rollingwindow">
  <Identifier ref="{client_id}"/>
  <Allow count="{o.jwt_quota}"/>
  <Interval>1</Interval>
  <TimeUnit>minute</TimeUnit>
  <Distributed>true</Distributed>
  <Synchronous>true</Synchronous>
</Quota>"""
        pre.append(("Q-PerSubject", PROT))

    # --- payload protection
    P["JTP-JSONThreatProtection"] = """<JSONThreatProtection continueOnError="false" enabled="true" name="JTP-JSONThreatProtection">
  <ArrayElementCount>500</ArrayElementCount>
  <ContainerDepth>20</ContainerDepth>
  <ObjectEntryCount>200</ObjectEntryCount>
  <ObjectEntryNameLength>100</ObjectEntryNameLength>
  <StringValueLength>100000</StringValueLength>
  <Source>request</Source>
</JSONThreatProtection>"""
    pre.append(("JTP-JSONThreatProtection",
                cond(PROT, 'request.header.content-type StartsWith "application/json"')))

    # --- OpenAPI
    operations = []
    if o.oas:
        spec = load_spec(o.oas)
        spec_file = os.path.basename(o.oas)
        P["OAS-ValidateRequest"] = f"""<OASValidation continueOnError="false" enabled="true" name="OAS-ValidateRequest">
  <OASResource>oas://{spec_file}</OASResource>
  <Options>
    <ValidateMessageBody>true</ValidateMessageBody>
    <AllowUnspecifiedParameters>
      <Header>true</Header>
      <Query>true</Query>
      <Cookie>true</Cookie>
    </AllowUnspecifiedParameters>
  </Options>
  <Source>request</Source>
</OASValidation>"""
        for path, item in (spec.get("paths") or {}).items():
            for method, op in (item or {}).items():
                if method.lower() not in METHODS or not isinstance(op, dict):
                    continue
                pat = path_to_pattern(path)
                operations.append((path, method.upper()))
                flows.append((flow_name(op, method.lower(), path),
                              f'(proxy.pathsuffix MatchesPath "{pat}") and (request.verb = "{method.upper()}")',
                              [("OAS-ValidateRequest", None)], []))
        P["RF-NotFound"] = """<RaiseFault continueOnError="false" enabled="true" name="RF-NotFound">
  <FaultResponse>
    <Set>
      <StatusCode>404</StatusCode>
      <ReasonPhrase>Not Found</ReasonPhrase>
      <Payload contentType="application/json" variablePrefix="@" variableSuffix="#">{"error":{"code":"resource.not_found","status":404,"message":"No operation matches @request.verb# @proxy.pathsuffix#","requestId":"@messageid#"}}</Payload>
    </Set>
  </FaultResponse>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</RaiseFault>"""

    # --- health + token flows (placed first: only the first matching flow runs)
    special = []
    if o.health:
        P["AM-HealthResponse"] = """<AssignMessage continueOnError="false" enabled="true" name="AM-HealthResponse">
  <Set>
    <Payload contentType="application/json" variablePrefix="@" variableSuffix="#">{"status":"ok","proxy":"@apiproxy.name#","revision":"@apiproxy.revision#"}</Payload>
    <StatusCode>200</StatusCode>
  </Set>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <AssignTo createNew="false" transport="http" type="response"/>
</AssignMessage>"""
        special.append(("Health", '(proxy.pathsuffix MatchesPath "/ping") and (request.verb = "GET")', [],
                        [("AM-HealthResponse", None)]))
        route_rules.append(("health-noroute", '(proxy.pathsuffix MatchesPath "/ping") and (request.verb = "GET")', None))
    if o.token_endpoint:
        P["OA-GenerateAccessToken"] = """<OAuthV2 continueOnError="false" enabled="true" name="OA-GenerateAccessToken">
  <Operation>GenerateAccessToken</Operation>
  <ExpiresIn>1800000</ExpiresIn>
  <SupportedGrantTypes>
    <GrantType>client_credentials</GrantType>
  </SupportedGrantTypes>
  <GrantType>request.formparam.grant_type</GrantType>
  <GenerateResponse enabled="true"/>
</OAuthV2>"""
        special.append(("Token", '(proxy.pathsuffix MatchesPath "/token") and (request.verb = "POST")',
                        [("OA-GenerateAccessToken", None)], []))
        route_rules.append(("token-noroute", '(proxy.pathsuffix MatchesPath "/token") and (request.verb = "POST")', None))
    flows = special + flows
    if o.oas:
        flows.append(("NotFound", 'request.verb != "OPTIONS"' if o.cors else None, [("RF-NotFound", None)], []))

    # --- response cache (after auth so cached data is never served to anonymous callers)
    if o.cache_ttl:
        P["RC-ResponseCache"] = f"""<ResponseCache continueOnError="false" enabled="true" name="RC-ResponseCache">
  <CacheKey>
    <KeyFragment ref="request.uri" type="string"/>
  </CacheKey>
  <Scope>Exclusive</Scope>
  <ExpirySettings>
    <TimeoutInSec>{o.cache_ttl}</TimeoutInSec>
  </ExpirySettings>
  <SkipCacheLookup>request.header.cache-control = "no-cache"</SkipCacheLookup>
  <SkipCachePopulation>response.status.code != 200</SkipCachePopulation>
  <UseResponseCacheHeaders>false</UseResponseCacheHeaders>
</ResponseCache>"""
        pre.append(("RC-ResponseCache", cond(PROT, 'request.verb = "GET"')))
        post_resp.append(("RC-ResponseCache", 'request.verb = "GET"'))

    # --- strip client credentials before the backend
    P["AM-RemoveCredentials"] = f"""<AssignMessage continueOnError="false" enabled="true" name="AM-RemoveCredentials">
  <Remove>
    <Headers>
      <Header name="{o.apikey_header}"/>
      <Header name="Authorization"/>
    </Headers>
    <QueryParams>
      <QueryParam name="apikey"/>
    </QueryParams>
  </Remove>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <AssignTo createNew="false" transport="http" type="request"/>
</AssignMessage>"""
    pre.append(("AM-RemoveCredentials", PROT))

    # --- response headers + fault handling
    P["AM-SecurityHeaders"] = """<AssignMessage continueOnError="true" enabled="true" name="AM-SecurityHeaders">
  <Set>
    <Headers>
      <Header name="x-request-id">{messageid}</Header>
      <Header name="Strict-Transport-Security">max-age=31536000; includeSubDomains</Header>
      <Header name="X-Content-Type-Options">nosniff</Header>
    </Headers>
  </Set>
  <Remove>
    <Headers>
      <Header name="Server"/>
      <Header name="X-Powered-By"/>
    </Headers>
  </Remove>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <AssignTo createNew="false" transport="http" type="response"/>
</AssignMessage>"""
    post_resp.append(("AM-SecurityHeaders", None))
    P["AM-FaultResponse"] = """<AssignMessage continueOnError="false" enabled="true" name="AM-FaultResponse">
  <!-- One error shape for every policy / backend fault. Never leaks backend bodies or hostnames. -->
  <Set>
    <Payload contentType="application/json" variablePrefix="@" variableSuffix="#">{"error":{"code":"@fault.name#","status":@error.status.code#,"message":"@error.reason.phrase#","requestId":"@messageid#"}}</Payload>
    <Headers>
      <Header name="x-request-id">{messageid}</Header>
    </Headers>
  </Set>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
</AssignMessage>"""

    if o.cloud_logging:
        cid = f"{{{client_id}}}" if client_id else ""
        P["ML-CloudLogging"] = f"""<MessageLogging continueOnError="true" enabled="true" name="ML-CloudLogging">
  <CloudLogging>
    <LogName>projects/{{organization.name}}/logs/apigee-proxies</LogName>
    <Message contentType="application/json">{{"proxy":"{{apiproxy.name}}","revision":"{{apiproxy.revision}}","verb":"{{request.verb}}","path":"{{proxy.pathsuffix}}","client":"{cid}","status":"{{response.status.code}}","targetStatus":"{{target.received.status.code}}","fault":"{{fault.name}}","clientIp":"{{proxy.client.ip}}","messageId":"{{messageid}}"}}</Message>
    <ResourceType>api</ResourceType>
  </CloudLogging>
</MessageLogging>"""

    # ---------------------------------------------------------------- write files
    root = os.path.join(o.out, "apiproxy")
    if os.path.exists(root):
        sys.exit(f"{root} already exists - choose another --out or delete it")
    for d in ("policies", "proxies", "targets"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    for n, x in P.items():
        open(os.path.join(root, "policies", n + ".xml"), "w").write(H + x + "\n")
    resources = []
    if o.oas:
        os.makedirs(os.path.join(root, "resources", "oas"), exist_ok=True)
        shutil.copyfile(o.oas, os.path.join(root, "resources", "oas", os.path.basename(o.oas)))
        resources.append(f"oas://{os.path.basename(o.oas)}")

    def st(lst, ind="      "):
        out = ""
        for n, c in lst:
            out += f"{ind}<Step>\n{ind}  <Name>{n}</Name>\n"
            if c:
                out += f"{ind}  <Condition>{esc(c)}</Condition>\n"
            out += f"{ind}</Step>\n"
        return out

    flow_xml = ""
    for fname, fcond, req, resp in flows:
        c = f"\n      <Condition>{esc(fcond)}</Condition>" if fcond else ""
        flow_xml += f"""    <Flow name="{fname}">
      <Request>
{st(req, "        ")}      </Request>
      <Response>
{st(resp, "        ")}      </Response>{c}
    </Flow>
"""
    rr_xml = ""
    for rname, rcond, tgt in route_rules:
        rr_xml += f'  <RouteRule name="{rname}">\n    <Condition>{esc(rcond)}</Condition>\n  </RouteRule>\n'
    rr_xml += '  <RouteRule name="default">\n    <TargetEndpoint>default</TargetEndpoint>\n  </RouteRule>\n'
    pcf = f"""  <PostClientFlow>
    <Response>
{st([("ML-CloudLogging", None)])}    </Response>
  </PostClientFlow>
""" if o.cloud_logging else ""

    open(os.path.join(root, "proxies", "default.xml"), "w").write(f"""{H}<ProxyEndpoint name="default">
  <Description>{esc(o.name)} proxy endpoint</Description>
  <FaultRules/>
  <DefaultFaultRule name="default-fault">
    <AlwaysEnforce>true</AlwaysEnforce>
    <Step>
      <Name>AM-FaultResponse</Name>
      <!-- RaiseFault policies already build their own response -->
      <Condition>fault.name != "RaiseFault"</Condition>
    </Step>
  </DefaultFaultRule>
  <PreFlow name="PreFlow">
    <Request>
{st(pre)}    </Request>
    <Response/>
  </PreFlow>
  <Flows>
{flow_xml}  </Flows>
  <PostFlow name="PostFlow">
    <Request/>
    <Response>
{st(post_resp)}    </Response>
  </PostFlow>
{pcf}  <HTTPProxyConnection>
    <BasePath>{o.basepath}</BasePath>
  </HTTPProxyConnection>
{rr_xml}</ProxyEndpoint>
""")

    # target
    success = "1xx,2xx,3xx" if o.no_pass_4xx else "1xx,2xx,3xx,4xx"
    auth = ""
    if o.target_auth == "idtoken":
        u = urlparse(o.target)
        auth = f"""
    <Authentication>
      <GoogleIDToken>
        <Audience>{u.scheme}://{u.netloc}</Audience>
      </GoogleIDToken>
    </Authentication>"""
    elif o.target_auth == "accesstoken":
        auth = """
    <Authentication>
      <GoogleAccessToken>
        <Scopes>
          <Scope>https://www.googleapis.com/auth/cloud-platform</Scope>
        </Scopes>
      </GoogleAccessToken>
    </Authentication>"""
    if o.target:
        conn = f"<URL>{esc(o.target)}</URL>"
    else:
        servers = "\n".join(f'        <Server name="{s.strip()}"/>' for s in o.target_server.split(","))
        conn = f"""<LoadBalancer>
      <Algorithm>RoundRobin</Algorithm>
{servers}
      <MaxFailures>3</MaxFailures>
      <ServerUnhealthyResponse>
        <ResponseCode>502</ResponseCode>
        <ResponseCode>503</ResponseCode>
      </ServerUnhealthyResponse>
    </LoadBalancer>
    <Path>{esc(o.target_path)}</Path>"""
    open(os.path.join(root, "targets", "default.xml"), "w").write(f"""{H}<TargetEndpoint name="default">
  <Description>{esc(o.name)} backend</Description>
  <FaultRules/>
  <PreFlow name="PreFlow">
    <Request/>
    <Response/>
  </PreFlow>
  <PostFlow name="PostFlow">
    <Request/>
    <Response/>
  </PostFlow>
  <HTTPTargetConnection>
    <Properties>
      <Property name="success.codes">{success}</Property>
      <Property name="connect.timeout.millis">5000</Property>
      <Property name="io.timeout.millis">30000</Property>
    </Properties>
    {conn}{auth}
  </HTTPTargetConnection>
</TargetEndpoint>
""")

    pol = "\n".join(f"    <Policy>{n}</Policy>" for n in sorted(P))
    res = "\n".join(f"    <Resource>{r}</Resource>" for r in resources)
    open(os.path.join(root, f"{o.name}.xml"), "w").write(f"""{H}<APIProxy revision="1" name="{o.name}">
  <DisplayName>{o.name}</DisplayName>
  <Description>REST API proxy scaffolded by the apigee-api-proxy skill</Description>
  <BasePaths>{o.basepath}</BasePaths>
  <Policies>
{pol}
  </Policies>
  <Resources>
{res}
  </Resources>
  <ProxyEndpoints>
    <ProxyEndpoint>default</ProxyEndpoint>
  </ProxyEndpoints>
  <TargetEndpoints>
    <TargetEndpoint>default</TargetEndpoint>
  </TargetEndpoints>
</APIProxy>
""")

    # ---------------------------------------------------------------- API product + target server stub
    if o.auth in ("apikey", "oauth"):
        if operations:
            by_path = {}
            for p, m in operations:
                by_path.setdefault(path_to_pattern(p), set()).add(m)
            ops = [{"resource": p, "methods": sorted(ms)} for p, ms in sorted(by_path.items())]
        else:
            ops = [{"resource": "/**", "methods": ["GET", "POST", "PUT", "PATCH", "DELETE"]}]
        if o.health:
            ops.append({"resource": "/ping", "methods": ["GET"]})
        product = {
            "name": f"{o.name}-standard",
            "displayName": f"{o.name} - Standard",
            "description": f"Standard access to {o.name}",
            "approvalType": "auto",
            "attributes": [{"name": "access", "value": "public"}],
            "environments": ["<ENV>"],
            "operationGroup": {"operationConfigs": [{
                "apiSource": o.name,
                "operations": ops,
                "quota": {"limit": "1000", "interval": "1", "timeUnit": "hour"},
            }]},
        }
        if o.auth == "oauth":
            product["scopes"] = ["read", "write"]
        open(os.path.join(o.out, "api_product.json"), "w").write(json.dumps(product, indent=2) + "\n")
    if o.target_server:
        os.makedirs(os.path.join(o.out, "targetservers"), exist_ok=True)
        for s in o.target_server.split(","):
            ts = {"name": s.strip(), "host": "backend.example.com", "port": 443, "isEnabled": True,
                  "protocol": "HTTP", "sSLInfo": {"enabled": True}}
            open(os.path.join(o.out, "targetservers", f"{s.strip()}.json"), "w").write(json.dumps(ts, indent=2) + "\n")

    print(f"Scaffolded {o.out}: {len(P)} policies, {len(flows)} conditional flow(s), auth={o.auth}")
    nxt = ["validate_bundle.py " + o.out]
    if o.target_server:
        nxt.append(f"edit + apply {o.out}/targetservers/*.json (apigee_ctl.py targetserver-apply)")
    nxt.append("deploy_bundle.sh " + o.out + " --org ORG --env ENV"
               + (" --sa RUNTIME_SA" if o.target_auth != "none" or o.cloud_logging else ""))
    if o.auth in ("apikey", "oauth"):
        nxt.append(f"apigee_ctl.py product-apply {o.out}/api_product.json --env ENV, then create a developer app")
    print("Next: " + " -> ".join(nxt))


if __name__ == "__main__":
    main()
