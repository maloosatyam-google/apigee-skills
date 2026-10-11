---
name: apigee-proxy-lifecycle
description: >-
  Safely update EXISTING Apigee X API proxies and shared flows (API, AI Gateway and MCP proxies alike) and
  manage their runtime configuration. Use for "change/fix/add a policy to the deployed proxy", exporting the
  revision that is actually deployed, structural edits that keep the manifest consistent, diffing local vs
  deployed, importing a new revision, deploying with a service account, waiting for READY, rolling back,
  undeploying, environment promotion, API products, encrypted KVM entries, target servers, flow hooks and
  debug/trace sessions. Standalone: stdlib-only apigee_ctl.py (Apigee management API) and bundle_edit.py.
---

# Apigee Proxy Lifecycle (update existing proxies)

Changing a live proxy is where most incidents happen: editing a stale local copy, forgetting to register a
policy, deploying without the service account, or having no rollback path. This skill makes every change
**export → edit → validate → diff → update → verify → (rollback)**.

Scripts (Python 3.9+, no dependencies; auth via `$APIGEE_TOKEN` or `gcloud auth print-access-token`):
- `scripts/apigee_ctl.py`: Apigee management API (proxies, shared flows, deployments, products, KVMs, target servers, flow hooks, debug)
- `scripts/bundle_edit.py`: manifest-safe structural edits of a local bundle
- validation comes from [`apigee-proxy-builder`](../apigee-proxy-builder/SKILL.md) (`validate_bundle.py`), run automatically by `import`/`update`

```bash
SK=<path-to>/skills; C="python3 $SK/apigee-proxy-lifecycle/scripts/apigee_ctl.py"; E="python3 $SK/apigee-proxy-lifecycle/scripts/bundle_edit.py"
export APIGEE_ORG=my-org APIGEE_ENV=dev APIGEE_DEPLOY_SA=apigee-runtime@my-proj.iam.gserviceaccount.com
```

## The update workflow (follow in order)

```bash
# 1. Inspect: what exists and what is deployed where
$C info orders-v1
# 2. Export the revision deployed in the target env (NOT the latest revision, NOT a local copy)
$C export orders-v1 --env dev --out ./work/orders-v1
$E outline ./work/orders-v1                               # flows, steps, conditions, route rules
# 3. Edit: hand-edit XML, or structural edits that keep the manifest in sync
$E add-policy ./work/orders-v1 ./SA-Burst.xml
$E attach     ./work/orders-v1 SA-Burst --flow PreFlow --phase Request --after VA-VerifyAPIKey
# 4. Validate + review the exact change
python3 $SK/apigee-proxy-builder/scripts/validate_bundle.py ./work/orders-v1 --strict
$C diff ./work/orders-v1 --env dev
# 5. Import a new revision, deploy (override = zero-downtime swap), wait for READY
$C update ./work/orders-v1 --env dev                      # prints the rollback command
# 6. Verify (curl matrix / trace), and if anything is wrong:
$C rollback orders-v1 --env dev                           # previous revision, or --to-rev N
```

Use `--sharedflow` before the command for shared flows (`$C --sharedflow export sf-security --env dev --out ./sf`);
`import`/`update` detect the bundle type automatically.

> [!IMPORTANT]
> Show the user the `diff` and get confirmation before `update` in shared or production environments.
> Never `undeploy` or delete in production without explicit approval.

## bundle_edit.py commands
| Command | Effect |
|---|---|
| `outline DIR` | Print endpoints, flows, steps, conditions, route rules |
| `add-policy DIR FILE.xml [--force]` | Copy into `policies/` and register in the manifest |
| `add-resource DIR FILE --type jsc\|py\|xsl\|oas\|properties\|java` | Copy into `resources/<type>/` and register |
| `attach DIR POLICY --flow PreFlow\|PostFlow\|PostClientFlow\|<Flow>\|DefaultFaultRule --phase Request\|Response [--condition C] [--before S\|--after S\|--first] [--endpoint targets/x.xml]` | Insert a Step at an exact position |
| `detach DIR POLICY [--endpoint] [--flow] [--phase]` | Remove Steps |
| `remove-policy DIR POLICY` | Detach everywhere, delete, unregister |
| `add-flow DIR NAME --condition C [--first\|--before F]` | New conditional flow (first match wins: order matters) |
| `set-basepath DIR /path` / `set-target-url DIR URL` | Common one-line changes |
| `sync-manifest DIR` | Rebuild manifest lists from the files on disk |

Edited files are re-indented (comments preserved), so the first diff after a structural edit may show whitespace changes.

## apigee_ctl.py commands
| Area | Commands |
|---|---|
| Read | `list`, `info NAME`, `deployments [--env]`, `export NAME --out DIR [--rev N\|--env E] [--force]`, `diff DIR [NAME] [--rev\|--env]` |
| Write | `import DIR`, `deploy NAME --rev N --env E [--sa]`, `update DIR --env E [--sa]`, `rollback NAME --env E [--to-rev N]`, `undeploy NAME --env E [--rev N]` |
| API products | `product-apply FILE.json [--env E]` (create or update, replaces `<ENV>`), `product-get NAME`, `product-list`, `product-delete NAME --yes` |
| Developers | `developer-create EMAIL [--first --last --username --attr k=v]` (idempotent), `developer-get`, `developer-list`, `developer-delete EMAIL --yes` |
| Apps & keys | `app-create DEV_EMAIL APP --product P [--product P2] [--create-developer] [--callback URL] [--key-expires-in-ms MS] [--credentials-out FILE] [--show-secret]` (idempotent: re-running adds missing products to the existing key), `app-get`, `app-list DEV_EMAIL`, `app-delete ... --yes`, `key-approve` / `key-revoke DEV_EMAIL APP KEY [--product P]` |
| KVMs | scope: `--env E` (default `$APIGEE_ENV`), `--org-scope` or `--proxy NAME`. `kvm-put MAP KEY VALUE\|-` (creates an **encrypted** map; `-` reads stdin), `kvm-list`, `kvm-entries MAP`, `kvm-get MAP KEY` (values masked unless `--show`), `kvm-delete-entry MAP KEY --yes`, `kvm-delete MAP --yes` |
| Target servers | `targetserver-apply FILE.json --env E`, `targetserver-list`, `targetserver-get NAME`, `targetserver-delete NAME --yes` |
| Flow hooks | `flowhook-attach HOOK SHAREDFLOW --env E` |
| Debug | `debug NAME --env E [--seconds 60] [--out DIR]`: starts a trace session, you send traffic, it saves every transaction and prints the executed policies per call; `debugmask-get --env E`, `debugmask-apply mask.json --env E` mask secrets/PII in traces (set this before tracing production) |

## Onboarding a consumer (product → developer → app → key)
```bash
$C product-apply ./orders-v1/api_product.json --env dev                     # plan = product (paths, verbs, quota)
$C app-create jane@partner.com partner-portal --product orders-v1-standard \
   --create-developer --credentials-out ./partner-portal.creds.json          # prints the API key; secret only in the 0600 file
$C key-revoke jane@partner.com partner-portal KEY --product orders-v1-standard   # cut off one product
```
Secrets (consumer secrets, KVM values) are masked in output unless `--show-secret` / `--show`; never paste them into chat
or commit them. Every `*-delete` requires `--yes` and must be confirmed with the user first.

## Choosing what to change
| Requirement | Change the... | Not the... |
|---|---|---|
| New quota / plan / allowed paths / tools / models | **API product** (`product-apply`) | proxy |
| Per-environment backend host | **TargetServer** (`targetserver-apply`) | target URL in XML |
| Secret or tunable value (rate card, thresholds, keys) | **encrypted KVM** (`kvm-put`) | policy XML |
| Policy for every proxy in an env | **shared flow + flow hook** | each proxy |
| Request/response behaviour | proxy bundle (this workflow) | - |

## Promotion across environments
Promote the **same revision** you tested: `$C deploy orders-v1 --rev 7 --env prod --sa $PROD_SA`. Create the
prod-side products, KVM entries and target servers first (`<ENV>` placeholders keep product JSON reusable).
In CI, run `validate_bundle.py --strict` on every PR and `apigee_ctl.py update` on merge
(see [references/update_playbook.md](references/update_playbook.md)).

## Troubleshooting
| Symptom | Fix |
|---|---|
| `HTTP 403 ... permission` | Caller needs `roles/apigee.apiAdminV2` (proxies) / `roles/apigee.environmentAdmin` (deploy, KVM); for `--sa` also `iam.serviceAccounts.actAs` on that SA |
| Deployment `ERROR`: basepath conflict | Another proxy in the env uses the same base path (`deployments --env`) |
| Deployment `ERROR`: shared flow not found | Deploy the shared flow to the env first |
| `GoogleTokenGenerationFailure` at runtime | Redeploy with `--sa` (the SA is bound per deployment) |
| `export` says not deployed | Pass `--rev N` (see `info NAME`) |
| Change "didn't take effect" | You deployed to another env, or a cached KVM value (`ExpiryTimeInSecs`) is still live |

More: [references/update_playbook.md](references/update_playbook.md) (change recipes for API, AI and MCP proxies, CI/CD, review checklist).
