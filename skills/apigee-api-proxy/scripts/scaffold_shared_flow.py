#!/usr/bin/env python3
"""Scaffold a reusable Apigee X shared flow bundle (stdlib only).

  python3 scaffold_shared_flow.py --out ./sf-security --name sf-security \
      --features apikey,spikearrest,threat,strip-auth [--apikey-header x-apikey] [--rate 100ps]

  python3 scaffold_shared_flow.py --out ./sf-logging --name sf-logging --features cloud-logging

Features (applied in this order):
  apikey         VerifyAPIKey from --apikey-header (variables: verifyapikey.VA-VerifyAPIKey.*)
  jwt            VerifyJWT via --jwks-url / --issuer [--audience]
  spikearrest    SpikeArrest --rate, keyed per client when apikey/jwt is enabled
  threat         JSONThreatProtection for JSON bodies
  strip-auth     remove Authorization / API key headers before the target
  cors-headers   add permissive CORS response headers (use in a Post* flow hook)
  cloud-logging  MessageLogging to Cloud Logging (call from PostClientFlow or a PostProxyFlowHook;
                 deploy with a service account that has roles/logging.logWriter)

Consume it from a proxy with a FlowCallout (<SharedFlowBundle>NAME</SharedFlowBundle>), or attach it to an
environment flow hook: apigee_ctl.py flowhook-attach PreProxyFlowHook NAME --env ENV
"""
import argparse
import os
import sys

H = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
ORDER = ["apikey", "jwt", "spikearrest", "threat", "strip-auth", "cors-headers", "cloud-logging"]


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--out", required=True)
    a.add_argument("--name", required=True)
    a.add_argument("--features", required=True, help=",".join(ORDER))
    a.add_argument("--apikey-header", default="x-apikey")
    a.add_argument("--rate", default="100ps")
    a.add_argument("--jwks-url")
    a.add_argument("--issuer")
    a.add_argument("--audience")
    o = a.parse_args()
    feats = [f.strip() for f in o.features.split(",") if f.strip()]
    unknown = set(feats) - set(ORDER)
    if unknown:
        a.error(f"unknown features {unknown}; choose from {ORDER}")
    if "jwt" in feats and not (o.jwks_url and o.issuer):
        a.error("jwt needs --jwks-url and --issuer")

    P, steps = {}, []
    ident = None
    for f in sorted(feats, key=ORDER.index):
        if f == "apikey":
            P["VA-VerifyAPIKey"] = f"""<VerifyAPIKey continueOnError="false" enabled="true" name="VA-VerifyAPIKey">
  <APIKey ref="request.header.{o.apikey_header}"/>
</VerifyAPIKey>"""
            steps.append(("VA-VerifyAPIKey", 'request.verb != "OPTIONS"'))
            ident = "verifyapikey.VA-VerifyAPIKey.client_id"
        elif f == "jwt":
            aud = f"\n  <Audience>{o.audience}</Audience>" if o.audience else ""
            P["JWT-VerifyToken"] = f"""<VerifyJWT continueOnError="false" enabled="true" name="JWT-VerifyToken">
  <Algorithm>RS256</Algorithm>
  <Source>request.header.authorization</Source>
  <PublicKey>
    <JWKS uri="{o.jwks_url}"/>
  </PublicKey>
  <Issuer>{o.issuer}</Issuer>{aud}
  <TimeAllowance>60s</TimeAllowance>
</VerifyJWT>"""
            steps.append(("JWT-VerifyToken", 'request.verb != "OPTIONS"'))
            ident = ident or "jwt.JWT-VerifyToken.decoded.claim.sub"
        elif f == "spikearrest":
            idx = f'\n  <Identifier ref="{ident}"/>' if ident else ""
            P["SA-SpikeArrest"] = f"""<SpikeArrest continueOnError="false" enabled="true" name="SA-SpikeArrest">
  <Rate>{o.rate}</Rate>{idx}
  <UseEffectiveCount>true</UseEffectiveCount>
</SpikeArrest>"""
            steps.append(("SA-SpikeArrest", None))
        elif f == "threat":
            P["JTP-JSONThreatProtection"] = """<JSONThreatProtection continueOnError="false" enabled="true" name="JTP-JSONThreatProtection">
  <ArrayElementCount>500</ArrayElementCount>
  <ContainerDepth>20</ContainerDepth>
  <ObjectEntryCount>200</ObjectEntryCount>
  <ObjectEntryNameLength>100</ObjectEntryNameLength>
  <StringValueLength>100000</StringValueLength>
  <Source>request</Source>
</JSONThreatProtection>"""
            steps.append(("JTP-JSONThreatProtection", 'request.header.content-type StartsWith "application/json"'))
        elif f == "strip-auth":
            P["AM-RemoveCredentials"] = f"""<AssignMessage continueOnError="false" enabled="true" name="AM-RemoveCredentials">
  <Remove>
    <Headers>
      <Header name="{o.apikey_header}"/>
      <Header name="Authorization"/>
    </Headers>
  </Remove>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <AssignTo createNew="false" transport="http" type="request"/>
</AssignMessage>"""
            steps.append(("AM-RemoveCredentials", None))
        elif f == "cors-headers":
            P["AM-CORSHeaders"] = """<AssignMessage continueOnError="true" enabled="true" name="AM-CORSHeaders">
  <Set>
    <Headers>
      <Header name="Access-Control-Allow-Origin">{request.header.origin}</Header>
      <Header name="Access-Control-Expose-Headers">*</Header>
    </Headers>
  </Set>
  <IgnoreUnresolvedVariables>true</IgnoreUnresolvedVariables>
  <AssignTo createNew="false" transport="http" type="response"/>
</AssignMessage>"""
            steps.append(("AM-CORSHeaders", None))
        elif f == "cloud-logging":
            P["ML-CloudLogging"] = """<MessageLogging continueOnError="true" enabled="true" name="ML-CloudLogging">
  <CloudLogging>
    <LogName>projects/{organization.name}/logs/apigee-shared</LogName>
    <Message contentType="application/json">{"proxy":"{apiproxy.name}","revision":"{apiproxy.revision}","env":"{environment.name}","verb":"{request.verb}","path":"{proxy.pathsuffix}","app":"{developer.app.name}","status":"{response.status.code}","fault":"{fault.name}","messageId":"{messageid}"}</Message>
    <ResourceType>api</ResourceType>
  </CloudLogging>
</MessageLogging>"""
            steps.append(("ML-CloudLogging", None))

    root = os.path.join(o.out, "sharedflowbundle")
    if os.path.exists(root):
        sys.exit(f"{root} already exists")
    for d in ("policies", "sharedflows"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    for n, x in P.items():
        open(os.path.join(root, "policies", n + ".xml"), "w").write(H + x + "\n")
    body = ""
    for n, c in steps:
        body += f"  <Step>\n    <Name>{n}</Name>\n"
        if c:
            body += f"    <Condition>{c}</Condition>\n"
        body += "  </Step>\n"
    open(os.path.join(root, "sharedflows", "default.xml"), "w").write(f'{H}<SharedFlow name="default">\n{body}</SharedFlow>\n')
    pol = "\n".join(f"    <Policy>{n}</Policy>" for n in sorted(P))
    open(os.path.join(root, f"{o.name}.xml"), "w").write(f"""{H}<SharedFlowBundle revision="1" name="{o.name}">
  <DisplayName>{o.name}</DisplayName>
  <Description>Shared flow scaffolded by the apigee-api-proxy skill ({', '.join(feats)})</Description>
  <Policies>
{pol}
  </Policies>
  <Resources/>
  <SharedFlows>
    <SharedFlow>default</SharedFlow>
  </SharedFlows>
</SharedFlowBundle>
""")
    print(f"Scaffolded shared flow {o.out} ({', '.join(feats)})")
    print(f"Next: validate_bundle.py {o.out} -> deploy_bundle.sh {o.out} --org ORG --env ENV"
          + (" --sa RUNTIME_SA" if "cloud-logging" in feats else ""))


if __name__ == "__main__":
    main()
