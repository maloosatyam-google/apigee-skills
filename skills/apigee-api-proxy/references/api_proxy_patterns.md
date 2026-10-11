# API Proxy Patterns, Checklist and Test Matrix

## 1. Design checklist (review every new or changed proxy against this)

**Security**
- [ ] Every non-public path is authenticated in the **PreFlow** (not only in some conditional flows)
- [ ] Credentials (`x-apikey`, `Authorization`) are removed before the target
- [ ] Backend auth uses `GoogleIDToken` / `GoogleAccessToken` or mTLS, never a static secret in XML
- [ ] Secrets live in an **encrypted** KVM (`apigee_ctl.py kvm-put MAP KEY -`), read into `private.*` variables
- [ ] `JSONThreatProtection` / `XMLThreatProtection` for bodies; `OASValidation` with `ValidateMessageBody=true`
- [ ] CORS lists explicit origins in production (`*` only for public, credential-less APIs)
- [ ] Errors never echo backend bodies, stack traces, hostnames or project ids (DefaultFaultRule)

**Traffic**
- [ ] SpikeArrest (burst smoothing, per client) **and** Quota (business allowance, from the API product)
- [ ] Quota after authentication, and keyed on the caller identity
- [ ] Target timeouts set (`connect.timeout.millis`, `io.timeout.millis`); LoadBalancer `MaxFailures` + HealthMonitor for multi-host

**Flows and routing**
- [ ] Only the first matching conditional flow runs: specific flows first, catch-all last
- [ ] Conditions parenthesized: `(a) and (b)`; `MatchesPath` with `*` (one segment) or `**` (many)
- [ ] `target.copy.pathsuffix=false` whenever `target.url` is set dynamically
- [ ] NoRoute RouteRules (no `<TargetEndpoint>`) for responses the proxy generates itself

**Operability**
- [ ] `x-request-id` (`{messageid}`) returned on success and error
- [ ] MessageLogging in **PostClientFlow** (runs after the response is sent, also after faults)
- [ ] API product JSON in source control next to the bundle; `<ENV>` placeholder replaced at apply time

## 2. Common additions (policy snippets)

**Config from KVM** (read once per request into a private variable):
```xml
<KeyValueMapOperations name="KVM-GetConfig" mapIdentifier="orders-config">
  <Scope>environment</Scope>
  <Get assignTo="private.backend.apikey"><Key><Parameter>backend-apikey</Parameter></Key></Get>
  <ExpiryTimeInSecs>300</ExpiryTimeInSecs>
</KeyValueMapOperations>
```

**Static backend API key injection** (after KVM-GetConfig, in the TargetEndpoint PreFlow):
```xml
<AssignMessage name="AM-SetBackendKey">
  <Set><Headers><Header name="x-backend-key">{private.backend.apikey}</Header></Headers></Set>
  <AssignTo createNew="false" transport="http" type="request"/>
</AssignMessage>
```

**Per-operation scope check (OAuth)**: in the operation's flow use a second
`<OAuthV2><Operation>VerifyAccessToken</Operation><Scope>write</Scope></OAuthV2>`.

**IP allowlist**:
```xml
<AccessControl name="AC-AllowCorpNat">
  <IPRules noRuleMatchAction="DENY">
    <MatchRule action="ALLOW"><SourceAddress mask="32">203.0.113.10</SourceAddress></MatchRule>
  </IPRules>
  <ValidateBasedOn>X_FORWARDED_FOR_FIRST_IP</ValidateBasedOn>
</AccessControl>
```

**Response transformation**: prefer `AssignMessage` / `ExtractVariables`; use `JavaScript` (ES5, Rhino) only for
real reshaping. See [javascript_development.md](../../apigee-proxy-builder/references/javascript_development.md).

## 3. API products, apps and keys

```json
{
  "name": "orders-v1-standard",
  "environments": ["<ENV>"],
  "approvalType": "auto",
  "operationGroup": {"operationConfigs": [{
    "apiSource": "orders-v1",
    "operations": [{"resource": "/orders", "methods": ["GET", "POST"]}, {"resource": "/orders/*", "methods": ["GET"]}],
    "quota": {"limit": "1000", "interval": "1", "timeUnit": "hour"}
  }]}
}
```
- Product = plan. Create `-standard`, `-premium` products with different quotas instead of branching in the proxy.
- `apigee_ctl.py product-apply FILE --env ENV` creates or updates (idempotent).
- Developer apps hold the key; a key can carry several products. Revoking a product from the app revokes access.

## 4. Test matrix (run after every deploy)

| Case | Expect |
|---|---|
| No credential | 401, JSON error with `requestId` |
| Valid credential, allowed path/verb | 2xx, `x-request-id` header |
| Valid credential, path outside the product | 401/403 |
| Burst above SpikeArrest | 429 `policies.ratelimit.SpikeArrestViolation` |
| Above product quota | 429 `policies.ratelimit.QuotaViolation` |
| Body violating the spec (missing required field) | 400 `steps.oasvalidation.Failed` |
| Unknown path (with OAS) | 404 JSON |
| Backend 4xx | passed through unchanged |
| Backend 5xx / timeout | 5xx generic JSON, no backend details |
| CORS preflight from an allowed origin | 200 with `Access-Control-Allow-*` |
| GET twice within cache TTL | second response faster; backend called once |

Use `apigee_ctl.py debug NAME --env ENV --seconds 60` while running the matrix to capture a trace and see
which policy faulted.
