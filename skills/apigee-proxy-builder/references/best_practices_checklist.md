# Official Best Practices Checklist (Apigee X / hybrid)

Source: [Best practices for API proxy design and development with Apigee](https://docs.cloud.google.com/apigee/docs/api-platform/fundamentals/best-practices-api-proxy-design-and-development).
Use this list when reviewing any proxy (API, AI Gateway or MCP). The **Enforced by** column shows where the
repo already applies the rule; everything else is a review item. Also see
[anti_patterns_and_best_practices.md](anti_patterns_and_best_practices.md).

Legend: **V** = `validate_bundle.py` check · **S** = scaffolds generate it · **R** = review manually

## 1. Development standards
| Practice | Enforced by |
|---|---|
| Inline XML comments in ProxyEndpoint/TargetEndpoint where step names do not explain a flow; no obvious comments; consistent indentation | R (`bundle_edit.py` re-indents edited files) |
| DRY: reusable, single-purpose policies and scripts; reuse via source control and shared flows | R, S (`scaffold_shared_flow.py`) |
| Remove unused policies and resources (they slow import/deploy) | **V** (unattached policies, unreferenced resource files) |
| Policy `name` attribute == policy file name | **V** (error) |
| JavaScript/Python/ServiceCallout policy name should match the resource it uses (e.g. `JS-ExtractPrompt` ↔ `ExtractPrompt.js` is fine if your convention is consistent) | R |
| `DisplayName` describes the function to someone new to the proxy | R |
| Function-based prefixes (`AM-`, `EV-`, `VA-`, ...); consider [apigeelint](https://github.com/apigee/apigeelint) in CI | S, R ([rules](../../../rules/apigee_proxy_standards.md)) |
| Proper resource extensions (`.js`, `.py`, `.jar`) | R |
| Consistent variable naming style and purpose prefixes (`flow.*`, `private.*`, `consumer.*`) | R |

## 2. API proxy design
| Practice | Enforced by |
|---|---|
| **Never call the Apigee management API from inside a proxy** (stale caches, performance) | R |
| Prefer built-in policies over JavaScript/Java/Python | R (see section 6) |
| Several flows with one condition each, rather than many conditional steps in PreFlow/PostFlow | R. Exception: authentication and other must-run-for-everything policies belong in the PreFlow |
| A failsafe proxy on base path `/` so unknown paths get a useful response instead of `messaging.adaptors.http.flow.ApplicationNotFound` | R (recipe below) |
| ≤ 3,000 base paths per environment/environment group | R |
| TargetServers instead of hard-coded target URLs, for environment promotion | S (`--target-server`), R |
| With several RouteRules, exactly one default (no condition), defined **last** | **V** (error) |
| Bundle ≤ 15 MB | **V** (error) |
| Version APIs (`/v1`, `/v2`); see the *Web API Design* e-book | S (base path), R |

Failsafe `/` proxy:
```bash
python3 skills/apigee-api-proxy/scripts/scaffold_api_proxy.py --out ./default-catchall --name default-catchall \
  --basepath / --target https://developers.example.com --auth none --spike-arrest 10ps
# then replace the target with a RaiseFault 404 / redirect to the developer portal if preferred
```

## 3. CORS and payloads
| Practice | Enforced by |
|---|---|
| CORS policy in the ProxyEndpoint request PreFlow before publishing browser-facing APIs | S (`--cors`) |
| CORS (preflight) responses must **not** be cached | S (cache only on GET), **V** (warns if ResponseCache can populate non-GET responses) |
| Default payload limit 10 MB; raise up to 30 MB with `request.payload.parse.limit` / `response.payload.parse.limit` properties; exceeding it → `protocol.http.TooBigBody` | R |
| Isolate large-payload proxies in a dedicated environment; minimise policies that parse/copy big payloads; consider streaming (policies then cannot read the body) | R ([websockets_and_streaming.md](websockets_and_streaming.md)) |

## 4. Fault handling
| Practice | Enforced by |
|---|---|
| Handle errors in FaultRules; RaiseFault only to stop the flow and enter error processing | R |
| Build fault responses with **AssignMessage** inside FaultRules (conditioned on fault type), not with RaiseFault payloads | S (DefaultFaultRule uses `AM-FaultResponse`). Note: scaffolds still use self-contained RaiseFault payloads for deliberate refusals (404 catch-all, MCP JSON-RPC errors) where the exact body must be preserved |
| Always a catch-all (DefaultFaultRule) mapping system faults to your format | S, R |
| Match company-standard error formats; human-readable messages that suggest a fix | S (`{"error":{code,status,message,requestId}}`), R |

## 5. Persistence and caching
| Practice | Enforced by |
|---|---|
| KVMs only for limited, slowly changing data (config, secrets), not as a datastore; mind lookup cost (use `ExpiryTimeInSecs`) | R |
| Do not populate the response cache for non-2xx or non-GET: `<SkipCachePopulation>response.status.code != 200 or request.verb != "GET"</SkipCachePopulation>` | S, **V** (warning) |
| Cache one content type; convert after lookup (JSONToXML/XMLToJSON) | R |
| Cache key sufficient for the data (often `request.uri` / `request.querystring`); **do not include client_id/API key** unless responses differ per client | S (`request.uri`), R |
| Expiry short enough to avoid dirty reads | S (`--cache-ttl`), R |
| Lookup early (ProxyEndpoint request PreFlow, minimal logic before it); populate late (ProxyEndpoint response PostFlow, after mediation) | S |
| Multiple ResponseCache policies: mutually exclusive conditions and distinct `<CacheResource>` | **V** (warning) |

## 6. Policies vs custom code
| Practice | Enforced by |
|---|---|
| Built-in policies first (AssignMessage, ExtractVariables, ...) | R |
| JavaScript preferred over Python; Java when raw performance is the priority or logic cannot be done in JS | R |
| Python only if absolutely required (interpreted, slower) | **V** (warning when a PythonScript policy is present) |
| Scripts: global try/catch, throw/catch meaningful errors early, null/undefined checks for optional variables | R (scaffold JS follows this) |
| **No HTTP calls from scripts** (`httpClient`); use ServiceCallout | **V** (warning) |
| Java: do not rely on Apigee-bundled libraries; keep sources in version control; share JARs as org/env resources; no thread pools | R |
| If a callout routinely takes > 1 s, the logic belongs in a backend service, not the gateway | R |

Custom-logic decision guide:
| Situation | Use |
|---|---|
| Simple/trivial inline logic | JavaScript (preferred) or PythonScript |
| Inline logic that needs more performance | JavaCallout |
| Externalised logic that must read/modify the message context | ExternalCallout (gRPC; host on Cloud Run/Functions) |
| Existing REST/SOAP/GraphQL service, independent lifecycle | ServiceCallout |

## 7. ServiceCallouts and chaining
| Practice | Enforced by |
|---|---|
| Avoid recursive callouts into the same proxy; same org/env chaining uses `LocalTargetConnection` | R ([advanced_patterns.md](advanced_patterns.md)) |
| Build the callout request with AssignMessage into a message variable (payload, path, verb) | R (inline `<Request><Set>` is acceptable for small requests, as in the AI scaffold) |
| The callout `<URL>` protocol cannot be a variable; use separate variables for host and path | **V** (error when `<URL>` starts with `{`) |
| Store the callout response in its own message variable; keep the original message intact | S, R |

## 8. Entities, logging, monitoring, security
| Practice | Enforced by |
|---|---|
| AccessEntity: look up apps by UUID rather than name for performance | R |
| One common MessageLogging format across bundles (shared flow) | S (`scaffold_shared_flow.py --features cloud-logging`), R |
| Apigee Analytics for non-critical monitoring; Debug (trace) for runtime issues | `apigee_ctl.py debug` |
| AccessControl IP allowlist on test environments | R ([api_proxy_patterns.md](../../apigee-api-proxy/references/api_proxy_patterns.md)) |
| Content protection (JSON/XML threat protection) on every production proxy; also RegularExpressionProtection, SOAPMessageValidation where relevant | S (JSONThreatProtection), R |
