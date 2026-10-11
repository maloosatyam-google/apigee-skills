# Securing Applications and APIs with Apigee (layered security architecture)

Source: [Best practices for securing applications and APIs using Apigee](https://docs.cloud.google.com/architecture/best-practices-securing-applications-and-apis-using-apigee)
(Google Cloud Architecture Center). This reference turns that guide into concrete decisions for API, AI Gateway
and MCP proxies built with these skills. Proxy-level design rules are in
[best_practices_checklist.md](best_practices_checklist.md); policy XML is in [policies_security.md](policies_security.md).

## 1. Security points by actor

| Actor | Controls (guide) | How to do it here |
|---|---|---|
| End users | OAuth 2.0, IP access control | `--auth oauth\|jwt`; IP/geo rules in **Cloud Armor** (section 4) |
| Applications | API keys, OAuth 2.0, TLS | `--auth apikey\|oauth`; API products; TLS on the load balancer |
| Developers & partners | SSO, RBAC | Integrated developer portal / API hub with SSO; product approval (`approvalType: manual` for partners) |
| APIs | OAuth 2.0, OpenID Connect, quotas, spike arrest, threat protection | `--auth jwt` (OIDC via JWKS), `--quota`, `--spike-arrest`, JSON/XML threat protection, `--oas` |
| API team | IAM RBAC, federated login, **data masking**, **audit logs** | Section 6 |
| Backend | Private networking, **mutual TLS**, IP access control | Sections 5 and 7 |

## 2. Policy selection (the guide's recommended default policies)

| Category | Policy | Use case | Where it appears in this repo |
|---|---|---|---|
| Traffic management | SpikeArrest | Rate-limit requests reaching the backend | all scaffolds (`SA-`) |
| Traffic management | Quota | Per-consumer call allowance | `--quota` (product-driven), MCP per tool, `LLMTokenQuota` for tokens |
| Traffic management | ResponseCache | Fewer backend calls | `--cache-ttl`; AI: semantic cache |
| Message-level | OASValidation | Validate requests/responses against OpenAPI 3 | `--oas` (body validation on) |
| Message-level | SOAPMessageValidation | XML schema / WSDL validation, well-formed JSON/XML | hand-add for SOAP APIs |
| Message-level | JSONThreatProtection | Limits on JSON structure | all REST scaffolds (`JTP-`) |
| Message-level | XMLThreatProtection | Malformed / abusive XML before parsing | `--xml-threat` (`XTP-`) |
| Message-level | RegularExpressionProtection | Reject content matching attack patterns | hand-add for specific params; prefer Cloud Armor WAF rules for generic OWASP coverage |
| Security | BasicAuthentication | Encode/decode basic credentials (mostly backend credential injection from KVM) | hand-add |
| Security | VerifyAPIKey | Only approved apps with a key on a matching API product | `--auth apikey`, AI and MCP scaffolds |
| Security | OAuthV2 | Generate/verify access tokens | `--auth oauth [--token-endpoint]` |
| Security | JWS / JWT | Generate, verify, decode JWT/JWS | `--auth jwt` (`JWT-VerifyToken`), AI: DecodeJWT for attribution |
| Security | HMAC | Message integrity / signed webhooks | hand-add (`HMAC-VerifySignature`) |
| Security | SAMLAssertion | Validate/generate signed SAML assertions | hand-add |
| Security | CORS | Browser clients | `--cors` |

AI and MCP traffic add **Model Armor** (`SanitizeUserPrompt` / `SanitizeModelResponse`), `PromptTokenLimit`, and
per-tool `payloadOperationGroup` entitlement on top of these.

## 3. API products as the access-control boundary
Bundle operations (proxy + resource path + HTTP methods + quota) into products per **client type**, not per API:

```json
{"name": "payments-mobile", "operationGroup": {"operationConfigs": [{"apiSource": "payments-v1",
  "operations": [{"resource": "/payments", "methods": ["POST"]}],
  "quota": {"limit": "100", "interval": "1", "timeUnit": "minute"}}]}}
{"name": "payments-callcenter", "operationGroup": {"operationConfigs": [{"apiSource": "payments-v1",
  "operations": [{"resource": "/payments/*", "methods": ["PUT", "DELETE"]}],
  "quota": {"limit": "1000", "interval": "1", "timeUnit": "minute"}}]}}
```
The mobile app's key can only `POST /payments`; the call-center app can reverse payments. VerifyAPIKey (or OAuthV2
VerifyAccessToken) enforces this with no proxy logic. The same pattern drives AI (`llmOperationGroup`, models) and MCP
(`payloadOperationGroup`, tools). Apply with `apigee_ctl.py product-apply`.

## 4. Perimeter: Cloud Armor, WAAP, reCAPTCHA, Cloud CDN (in front of Apigee)
Recommended request path, outermost first:

```text
Client ─▶ External Application Load Balancer (TLS)
          ├─ Cloud CDN (optional: cache hits never reach Apigee)
          ├─ Cloud Armor (WAF: OWASP Top 10 rules, DDoS, IP/geo allow/deny, rate-based ban)
          │    └─ reCAPTCHA (bot/risk scoring for web & mobile traffic)          ← WAAP = Armor + reCAPTCHA + Apigee
          ▼
        Apigee (keys/tokens, products, quotas, threat protection, Model Armor, routing) ─▶ private backends
```

- **IP and geo access control belong in Cloud Armor**; use the Apigee `AccessControl` policy only when Cloud Armor is not
  possible (for example, per-proxy allowlists on an internal env, or hybrid without a Google LB).
- Use **WAAP** (Cloud Armor + reCAPTCHA + Apigee) when APIs are called from websites and mobile apps; apps load the
  reCAPTCHA library and send the token with each request; Cloud Armor evaluates it before Apigee.
- **Cloud CDN** on the LB in front of Apigee offloads cacheable public GETs and absorbs seasonal spikes. Only cache
  responses that are identical for all callers; never cache authenticated, per-user responses at the edge.

Example Cloud Armor setup (run by the platform team; adjust names):
```bash
gcloud compute security-policies create apigee-waf --description="WAF in front of Apigee"
# OWASP preconfigured rules (start in preview, then enforce)
gcloud compute security-policies rules create 1000 --security-policy=apigee-waf \
  --expression="evaluatePreconfiguredWaf('sqli-v33-stable')" --action=deny-403 --preview
gcloud compute security-policies rules create 1001 --security-policy=apigee-waf \
  --expression="evaluatePreconfiguredWaf('xss-v33-stable')" --action=deny-403 --preview
# Per-IP rate-based ban (complements Apigee SpikeArrest/Quota, which work per app/key)
gcloud compute security-policies rules create 2000 --security-policy=apigee-waf --src-ip-ranges="*" \
  --action=rate-based-ban --rate-limit-threshold-count=600 --rate-limit-threshold-interval-sec=60 \
  --ban-duration-sec=300 --conform-action=allow --exceed-action=deny-429 --enforce-on-key=IP
# Attach to the backend service that fronts Apigee; optionally enable CDN on it
gcloud compute backend-services update APIGEE_BACKEND --security-policy=apigee-waf --global
gcloud compute backend-services update APIGEE_BACKEND --enable-cdn --global     # only if responses are cacheable
```
Division of labour: Cloud Armor stops volumetric, bot and generic injection traffic per **IP**; Apigee enforces
identity, entitlement and business quotas per **app/user/model/tool**.

## 5. Private backends
- Prefer internal addresses: Apigee reaches peered/PSC networks (GKE, internal load balancers) over RFC 1918
  addresses, and on-prem data centers over Cloud Interconnect / VPN, instead of the public internet.
- For third-party backends, provision **Apigee NAT IPs** and give them to the partner for allowlisting.
- Cloud Run / Google APIs: `GoogleIDToken` / `GoogleAccessToken` with a dedicated runtime service account
  (`--target-auth idtoken|accesstoken` + `--sa`), never static secrets.
- The validator accepts `http://` only for internal addresses (`10.x`, `172.16-31.x`, `192.168.x`, `*.internal`,
  `*.svc`); anything else must be HTTPS.

## 6. API team controls
| Control | How |
|---|---|
| IAM RBAC | Least privilege: developers `roles/apigee.apiAdminV2` in dev; deployment via a CI service account; prod deploy rights limited; `roles/apigee.readOnlyAdmin` for auditors |
| Federated login | Google Cloud identity federation / Workforce Identity for console and API access |
| **Data masking** | Mask secrets and PII in debug (trace) sessions per environment: `apigee_ctl.py debugmask-apply mask.json --env ENV`; store secrets only in `private.*` variables (never shown in trace) |
| **Audit logs** | Cloud Audit Logs record Apigee admin activity (deployments, product and KVM changes); route to a locked-down log bucket / SIEM |

Example `mask.json`:
```json
{"requestJSONPaths": ["$.password", "$.card.number"], "responseJSONPaths": ["$.ssn", "$.accessToken"],
 "faultJSONPaths": ["$.password"], "variables": ["request.header.authorization", "request.header.x-apikey"]}
```

## 7. Backend transport: TLS and mutual TLS
Create a keystore (client certificate + key) and a truststore (backend CA) in the environment, expose them through
**references** (so certificates rotate without redeploying), then:

```bash
scaffold_api_proxy.py ... --target https://payments.internal.example.com \
  --target-truststore ref://payments-truststore --target-mtls ref://apigee-client-keystore:apigee-client
```
generates:
```xml
<SSLInfo>
  <Enabled>true</Enabled>
  <ClientAuthEnabled>true</ClientAuthEnabled>
  <KeyStore>ref://apigee-client-keystore</KeyStore>
  <KeyAlias>apigee-client</KeyAlias>
  <TrustStore>ref://payments-truststore</TrustStore>
</SSLInfo>
```
With TargetServers, put the same settings in the TargetServer `sSLInfo`. The validator errors on mTLS without a
keystore/alias and warns on `IgnoreValidationErrors=true`.

## 8. Security review checklist
- [ ] LB + Cloud Armor (OWASP rules, DDoS, per-IP rate ban, IP/geo rules) in front of Apigee; reCAPTCHA for web/mobile (WAAP)
- [ ] Every proxy authenticates apps (key/token) and, where needed, users (OAuth/OIDC/JWT)
- [ ] Products scoped per client type with path + verb + quota; partner products require manual approval
- [ ] SpikeArrest + Quota on every proxy; LLM token limits and Model Armor on AI proxies; per-tool products on MCP proxies
- [ ] OAS validation and JSON/XML threat protection on all request bodies
- [ ] Backends private (peering/PSC/Interconnect) or HTTPS with certificate validation; mTLS for sensitive backends; NAT IPs allowlisted for third parties
- [ ] No secrets in bundles; encrypted KVMs; Google tokens via service accounts
- [ ] Debug masks configured in every environment; admin access least-privilege; audit logs retained
- [ ] Cloud CDN only for public, caller-independent responses
