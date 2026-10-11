# Update Playbook: change recipes, review checklist, CI/CD

## 1. Pre-change review checklist
- [ ] Exported the revision **deployed in the target env** (`apigee_ctl.py export NAME --env ENV`)
- [ ] Change is in the right layer (product / KVM / target server / shared flow / bundle)
- [ ] New policies registered in the manifest and attached exactly once in the intended flow/phase
- [ ] Security order intact: auth before anything that costs money or touches data
- [ ] Conditions parenthesized; new conditional flows ordered before broader ones
- [ ] `validate_bundle.py --strict` passes; `apigee_ctl.py diff` shows only the intended change
- [ ] Rollback revision noted (printed by `update`)

## 2. Change recipes

### API proxy
| Change | Steps |
|---|---|
| Add rate limiting to one operation | write `SA-Orders.xml` → `add-policy` → `attach SA-Orders --flow createOrder --phase Request --first` |
| Require a scope on writes (OAuth) | policy `OA-VerifyWriteScope` (`<Scope>write</Scope>`) → attach in the write flows after the PreFlow verification |
| New backend host per environment | create TargetServers (`targetserver-apply`), switch target to `<LoadBalancer><Server name=.../>` |
| Expose a new operation | update the OAS resource (`add-resource --type oas`, `--force` semantics: overwrite the file), `add-flow getInvoice --condition '(proxy.pathsuffix MatchesPath "/invoices/*") and (request.verb = "GET")' --before NotFound`, attach `OAS-ValidateRequest`, add the path to the product |
| Change the base path (v2) | prefer a **new proxy** (`orders-v2`) so v1 clients keep working; otherwise `set-basepath` |

### AI Gateway proxy
| Change | Steps |
|---|---|
| Allow a new model | product: add an `llmOperationGroup.operationConfigs[]` entry (`/models/<m>:*`, `model: <m>`, `llmTokenQuota`) → `product-apply`. Proxy change only for a new provider |
| Change auto-routing map | product attributes `routing.model.<category>` → `product-apply` (no redeploy) |
| Add Model Armor | create templates → add `SUP-UserPrompt` (PreFlow, after VerifyAPIKey) + `SMR-SanitizeModelResponse` (PostFlow) → redeploy with an SA that has `roles/modelarmor.user` |
| Add token quota | add BOTH `LTQ-TokenEnforce` (conditional flow(s), request) and `LTQ-TokenCount` (PostFlow response) with identical Identifier/LLMModelSource/SharedName; product `llmTokenQuota` |
| Update rate card | `kvm-put ai-gateway-config rate-card - --env ENV < rate_card.json` (no redeploy; mind the KVM cache expiry) |

### MCP proxy
| Change | Steps |
|---|---|
| Give a persona a new tool | product: add `tools/call/<tool>` operation (+quota) → `product-apply` |
| New argument limit | `EV-ToolCall` variable for the argument + two RaiseFaults (numeric/string id) → attach after `EV-RpcIdNumber` with tool + product-name condition |
| Allow `resources/*` | extend the `RF-MethodNotAllowed` condition and add matching product operations |

## 3. Debugging a failed change
1. `apigee_ctl.py debug NAME --env ENV --seconds 60` and reproduce. The summary lists executed steps per call;
   open the saved JSON for `fault.name`, variables and timing.
2. Common faults: `steps.oauth.v2.FailedToResolveAccessToken`, `oauth.v2.InvalidApiKeyForGivenResource`,
   `policies.ratelimit.QuotaViolation`, `steps.oasvalidation.Failed`, `messaging.adaptors.http.flow.GatewayTimeout`,
   `GoogleTokenGenerationFailure`. See [debugging_and_performance.md](../../apigee-proxy-builder/references/debugging_and_performance.md)
   and [fault_handling.md](../../apigee-proxy-builder/references/fault_handling.md).
3. If production is affected: `rollback` first, debug second.

## 4. Source control and CI/CD
Keep bundles in git as `proxies/<name>/apiproxy/...`, `sharedflows/<name>/sharedflowbundle/...`, products as JSON.

```yaml
# .github/workflows/apigee.yml (sketch)
on: { pull_request: {}, push: { branches: [main] } }
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: for d in proxies/* sharedflows/*; do python3 skills/apigee-proxy-builder/scripts/validate_bundle.py "$d" --strict || exit 1; done
  deploy-dev:
    if: github.ref == 'refs/heads/main'
    needs: validate
    runs-on: ubuntu-latest
    permissions: { id-token: write, contents: read }
    steps:
      - uses: actions/checkout@v4
      - uses: google-github-actions/auth@v2
        with: { workload_identity_provider: ${{ vars.WIF_PROVIDER }}, service_account: ${{ vars.DEPLOYER_SA }} }
      - uses: google-github-actions/setup-gcloud@v2
      - run: |
          export APIGEE_ORG=${{ vars.APIGEE_ORG }} APIGEE_ENV=dev APIGEE_DEPLOY_SA=${{ vars.RUNTIME_SA }}
          for d in sharedflows/*; do python3 skills/apigee-proxy-lifecycle/scripts/apigee_ctl.py update "$d"; done
          for d in proxies/*;     do python3 skills/apigee-proxy-lifecycle/scripts/apigee_ctl.py update "$d"; done
          for f in products/*.json; do python3 skills/apigee-proxy-lifecycle/scripts/apigee_ctl.py product-apply "$f" --env dev; done
```
Promote to prod by deploying the tested revision number (`apigee_ctl.py deploy NAME --rev N --env prod`) behind a manual approval.
