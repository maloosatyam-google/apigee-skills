---
name: apigee-x-provisioning
description: >-
  Provision Apigee X itself with Terraform (paid org WITHOUT VPC peering): APIs, Apigee service identity, CMEK
  keys, organization, runtime instance, environments, environment groups, attachments and northbound routing
  (Private Service Connect + global external HTTPS load balancer, or internal PSC endpoint). Defaults to a
  Pay-as-you-go org with one INTERMEDIATE environment and the Analytics add-on enabled. Also adds environments
  later, supports Subscription billing, data residency, shared GCS state, importing an existing org, and
  deploying a sample proxy to verify the install. Use before the proxy skills when there is no Apigee org yet.
---

# Apigee X Provisioning (Terraform)

Turns Google's [command-line provisioning without VPC peering](https://docs.cloud.google.com/apigee/docs/api-platform/get-started/install-cli-non-peered)
into a tested Terraform root module, which is the IaC option on the
[provisioning options](https://docs.cloud.google.com/apigee/docs/api-platform/get-started/provisioning-options) page.
It uses only the official `hashicorp/google` and `google-beta` resources
([`google_apigee_organization`](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/apigee_organization) and friends).
After it finishes, use [`apigee-api-proxy`](../apigee-api-proxy/SKILL.md), [`apigee-ai-gateway`](../apigee-ai-gateway/SKILL.md)
and [`apigee-mcp-gateway`](../apigee-mcp-gateway/SKILL.md) to build proxies.

## Defaults

| Setting | Default | Change with |
|---|---|---|
| Billing | **PAYG** (Pay-as-you-go) | `--billing SUBSCRIPTION` |
| Environment | **`dev`**, type **INTERMEDIATE** | `--env NAME[:BASE\|INTERMEDIATE\|COMPREHENSIVE][:noanalytics][@GROUP]` (repeatable) |
| Analytics add-on | **enabled** on every PAYG environment | `:noanalytics` per env |
| Environment group | `<env>-group` | `@GROUP` in `--env` |
| Hostname | `<group>.<lb-ip-with-dashes>.nip.io` (resolves automatically, good for demos) | `--hostname GROUP=api.example.com` |
| Networking | No VPC peering; northbound through **PSC + global external managed HTTPS LB** with a Google-managed certificate | `--routing internal\|none`, `--network/--subnet`, `--ssl-certificate` |
| Encryption | CMEK keys (runtime DB, disk; plus control plane / consumer data with data residency), `prevent_destroy` | `key_ring_prefix`, `runtime_db_key_location` in tfvars |
| Instance | name = region | `--instance` |
| State | local in `./apigee-x-infra` | `--state-bucket BUCKET` (GCS backend) |

## Prerequisites (check before running)
1. A Google Cloud project **linked to a billing account**, with an org policy that allows location `global`
   (a narrower resource-location constraint makes organization creation fail).
2. Terraform ≥ 1.5 and credentials for it: `gcloud auth application-default login` (or a CI service account).
3. Permissions for the operator: Apigee Organization Admin (`roles/apigee.admin`), Cloud KMS Admin
   (`roles/cloudkms.admin`), Compute Load Balancer Admin + Network Admin, Service Usage Admin, and permission to
   set IAM on KMS keys. Grant least privilege for production; see Apigee "Provisioning permissions".
4. Agree the cost with the user: a PAYG org bills for the environment type, Analytics add-on and proxy traffic,
   and the load balancer bills separately. Point them to the Apigee pricing page; do not guess prices.

## Quick start

```bash
SK=<path-to>/skills
P=$SK/apigee-x-provisioning/scripts/provision.py
python3 $P plan  --project $PROJECT --region us-central1                 # review: ~25 resources, nothing changes
python3 $P apply --project $PROJECT --region us-central1 --sample        # confirm with "yes"; 30-90 min
source apigee-x-infra/apigee-x.env                                        # APIGEE_ORG, APIGEE_ENV, APIGEE_HOST
curl -i https://$APIGEE_HOST/httpbin/headers                               # after the certificate is ACTIVE
```

`provision.py` writes `terraform.tfvars.json` from the flags into the workdir, copies the module there, then runs
`terraform init`, `plan` and `apply`. After apply it writes `apigee-x.env` and can deploy an `/httpbin`
sample proxy (`--sample`, built with `scaffold_api_proxy.py` and deployed with `apigee_ctl.py update`). The same
module also works with plain Terraform: `cd skills/apigee-x-provisioning/terraform`, then write a
`terraform.tfvars` and run `terraform init && terraform apply`.

> [!IMPORTANT]
> Always run `plan` first and show it to the user. `apply` creates billable resources and the organization and
> CMEK keys cannot simply be recreated. Never pass `--auto-approve` for a user's project without explicit consent.

## What gets created (mapped to the guide's steps)

| Guide step | Terraform resources |
|---|---|
| 2 Enable APIs | `google_project_service` for apigee, apihub, compute, cloudkms |
| 3 Service identity | `google_project_service_identity` (google-beta) for apigee.googleapis.com |
| 4 Organization | KMS key ring + key `runtime-db` (+ `control-plane`, `consumer-data` for data residency), IAM for the Apigee service agent, `google_apigee_organization` (`disable_vpc_peering = true`, `billing_type`) |
| 5 Runtime instance | KMS key `disk` in the region, `google_apigee_instance` (consumer accept list, 90 min create timeout) |
| 6 Environments | `google_apigee_environment` (`type` for PAYG), `google_apigee_environment_addons_config` (`analytics_enabled`), `google_apigee_instance_attachment`, `google_apigee_envgroup`, `google_apigee_envgroup_attachment` |
| 7 Routing (external) | global address, PSC `google_compute_region_network_endpoint_group` → instance `service_attachment`, `EXTERNAL_MANAGED` backend service, URL map, managed certificate, target HTTPS proxy, forwarding rule :443 |
| 7 Routing (internal) | internal `google_compute_address` + PSC endpoint `google_compute_forwarding_rule` (`load_balancing_scheme = ""`) |
| 8 Sample proxy | `provision.py --sample` → `/httpbin` → httpbin.org |

## Environments (PAYG)
- **Types:** `BASE` has limited capacity and features and suits getting started. `INTERMEDIATE` adds API management features and more capacity (the default here). `COMPREHENSIVE` adds advanced capabilities and the highest capacity.
  An org can mix types, for example `--env dev --env test:BASE:noanalytics --env prod:COMPREHENSIVE`.
- **Analytics** is a PAYG add-on enabled per environment (`google_apigee_environment_addons_config`). Disable it
  per env with `:noanalytics`. Subscription orgs get analytics through the subscription, so no add-on resource is created.
- **Adding or changing environments later:** re-run `apply` with the complete `--env` list. The tfvars file is
  regenerated from the flags, so leaving an env out of the list plans its deletion. Always read the plan. You can
  also edit `apigee-x-infra/terraform.tfvars.json` and run `terraform -chdir=apigee-x-infra apply`.

## Hostnames, DNS and certificates
- External default: `<group>.<ip>.nip.io` needs no DNS and gets a Google-managed certificate automatically. Use it for demos and PoCs.
- Production: `--hostname prod-group=api.example.com`, then create an A record to `ingress_ip`. The managed
  certificate stays `PROVISIONING` until DNS resolves, which can take up to about 60 minutes. Check with the command in `next_steps`.
- Add Cloud Armor to the backend service (`security_policy`), as described in
  [security_architecture.md](../apigee-proxy-builder/references/security_architecture.md).
- Internal: map the hostname to the PSC endpoint IP in a private Cloud DNS zone, then call from inside the VPC.

## Data residency
`--control-plane-location us --consumer-data-region us-west1 --region us-west1`: the provider talks to
`https://us-apigee.googleapis.com/v1/` (`apigee_custom_endpoint`). The org uses `api_consumer_data_location`
instead of `analytics_region`, and control-plane and consumer-data CMEK keys are created. The runtime region must be
inside the control-plane location. Use a regional external LB with a self-managed certificate if traffic must stay
regional (the guide's recommendation); this module ships the global LB.

## Existing organizations
Adopt an org created in the console or with curl instead of creating a new one. Add `import` blocks to
`apigee-x-infra/imports.tf`, then run `plan` until it shows no destructive changes:

```hcl
import {
  to = google_apigee_organization.org
  id = "organizations/MY_PROJECT"
}
import {
  to = google_apigee_instance.instance
  id = "organizations/MY_PROJECT/instances/us-central1"
}
import {
  to = google_apigee_environment.env["dev"]
  id = "organizations/MY_PROJECT/environments/dev"
}
import {
  to = google_apigee_envgroup.group["dev-group"]
  id = "organizations/MY_PROJECT/envgroups/dev-group"
}
```
Keys, attachments and the add-on config import the same way (`{{env_id}}`, `{{instance_id}}/attachments/{{name}}`,
`{{envgroup_id}}/attachments/{{name}}`). If the existing org uses Google-managed encryption or different key names,
change `main.tf` to match before applying; the org's encryption keys cannot be updated in place.

## Teardown
`provision.py destroy` deliberately refuses. Deleting an Apigee org deletes every proxy, product, app and key.
CMEK keys carry `prevent_destroy` because destroying a key makes data unrecoverable. If the user explicitly asks:
remove routing and environments first (`terraform destroy -target=...`). Delete the org only after confirming the
retention behaviour for their billing type in the Apigee docs. Never schedule key destruction without written approval.

## Troubleshooting
| Symptom | Fix |
|---|---|
| Org create fails with a location constraint error | Org policy `gcp.resourceLocations` must allow `global` |
| `INVALID_ARGUMENT ... cryptoKeyEncrypterDecrypter` | Service identity missing; re-apply (the identity resource runs first) or check the project number |
| Instance create times out | It can take 60+ min; re-run `apply`, Terraform resumes |
| HTTPS handshake failure after apply | Certificate still `PROVISIONING`; wait, and check that DNS points at `ingress_ip` |
| 404 / `messaging.adaptors.http.flow.ApplicationNotFound` | Host header not in an envgroup, or no proxy deployed at that base path |
| PSC NEG creation fails | The project default network doesn't exist; pass `--network` and `--subnet` |
| `Error 409 ... already exists` | The resource exists outside Terraform; import it (see above) |

## Tests
`terraform -chdir=skills/apigee-x-provisioning/terraform test` runs offline with mocked providers. It checks the
defaults (PAYG, INTERMEDIATE, Analytics on, nip.io hostname, PSC target), Subscription, multiple environments with
internal routing, data residency, and input validation. `make test` also runs these when Terraform is installed.
