---
name: apigee-x-provisioning
description: >-
  Full-fledged Apigee X deployment with Terraform (paid orgs ONLY — PAYG or SUBSCRIPTION; Evaluation orgs are not
  supported). Always asks for deployment inputs first while defaulting to PAYG, INTERMEDIATE environment with
  Analytics enabled, PSC (Private Service Connect, no VPC peering), Google-managed encryption keys (GMEK),
  Google-managed SSL certificate, without Data Residency, and <envgroup>.<ip>.nip.io domain. Also supports VPC
  peering, CMEK, Data Residency, custom domains + Cloud DNS, multi-region HA, Regional External / Internal PSC
  routing, Cloud Armor WAF, Static Outbound NAT IPs, Southbound PSC Endpoint Attachments, Org Add-ons, and sample
  proxy verification.
---

# Apigee X Full-Fledged Provisioning (Terraform)

Provisions production-ready **paid** Apigee X organizations (`PAYG` or `SUBSCRIPTION`) across all paid options in Google's
[Apigee provisioning options](https://docs.cloud.google.com/apigee/docs/api-platform/get-started/provisioning-options)
using an offline-tested `hashicorp/google` Terraform module (`skills/apigee-x-provisioning/terraform/`) and stdlib CLI
wrapper (`skills/apigee-x-provisioning/scripts/provision.py`).

> [!IMPORTANT]
> 1. **Evaluation organizations are NOT supported** by this skill (`PAYG` and `SUBSCRIPTION` paid orgs only). If a user asks for a 60-day trial/eval org, inform them this skill only provisions paid Apigee X organizations.
> 2. **Always ask for inputs before running `plan` or `apply`**, presenting the recommended defaults clearly so the user can either confirm the defaults or customize any option.
> 3. **Always run `plan` (or `export`) before `apply`**, and never pass `--auto-approve` without explicit user consent.

After provisioning finishes, use [`apigee-proxy-builder`](../apigee-proxy-builder/SKILL.md), [`apigee-ai-gateway`](../apigee-ai-gateway/SKILL.md),
and [`apigee-mcp-gateway`](../apigee-mcp-gateway/SKILL.md) to build and deploy proxies. For deep architectural trade-offs, read
[`references/provisioning_architecture.md`](references/provisioning_architecture.md).

---

## Step 1: Ask for Deployment Inputs (Mandatory Defaults)

Before generating `terraform.tfvars` or running `provision.py`, ask the user to confirm or override the configuration below (only **Project ID** and **Region** have no default):

| # | Input | **Default (Used Unless Overridden)** | Supported Alternatives |
|---|---|---|---|
| 1 | **GCP Project ID & Region** | *(Required — ask user)* | Primary `region` (e.g. `us-central1`, `asia-south1`) + optional `additional_regions` for multi-region Active-Active HA |
| 2 | **Billing Model** | **`PAYG`** (Pay-As-You-Go) | `SUBSCRIPTION` *(Evaluation orgs are not supported)* |
| 3 | **Environments & Analytics** | **`dev`: `INTERMEDIATE` tier + Analytics enabled** (`dev-group`) | Custom env names (`prod`, `stage`, etc.), PAYG tiers (`BASE`, `INTERMEDIATE`, `COMPREHENSIVE`), `:noanalytics`, shared/custom envgroups |
| 4 | **Networking Topology** | **`PSC`** (Private Service Connect, `disable_vpc_peering = true`) | `VPC_PEERING` (Private Services Access `/22` + `/28` CIDR peering + optional Shared VPC / Peered DNS); existing `--network`/`--subnet` |
| 5 | **Encryption Keys** | **Google-Managed Encryption Keys (`GMEK`)** (`use_cmek = false`) | `CMEK` (`--cmek` / `use_cmek = true`: auto-creates Cloud KMS keys + IAM, or accepts existing KMS key IDs) |
| 6 | **Data Residency** | **Without Data Residency** (global control plane) | Enabled: `--control-plane-location` (e.g. `us`, `eu`) + `--consumer-data-region` |
| 7 | **Northbound Routing** | **`external`** (PSC NEG + Global External Managed HTTPS LB) | `external-regional` (Regional External HTTPS LB), `internal` (Internal PSC Endpoint in VPC), or `none` |
| 8 | **Domain / Hostname** | **`nip.io` wildcard** (`<envgroup>.<lb-ip-with-dashes>.nip.io`) | Custom domain(s) (`--hostname GROUP=api.example.com`) + optional `--dns-zone` to auto-create Cloud DNS `A` records |
| 9 | **TLS Certificate** | **Google-Managed SSL Certificate** (`cert_mode = "google_managed"`) | `existing` (`--ssl-certificate SELF_LINK`) or `self_managed` (`--ssl-cert-file` + `--ssl-key-file`) |
| 10 | **Optional Enterprise Add-ons** | *Disabled by default* | `--cloud-armor` (WAF/DDoS), `--http-redirect` (`:80`→`:443`), `--nat-ips` (static egress IPs), `--southbound-psc` (private backend attachments), `--addon api_security\|integration\|connectors\|monetization\|advanced_api_ops`, `--state-bucket` (GCS remote state) |

---

## Step 2: Prerequisites Checklist

1. **Billing-linked GCP project** with org policy `constraints/gcp.resourceLocations` allowing `global` (unless Data Residency is enabled).
2. **Tools & Auth**: Terraform `>= 1.5` and `gcloud auth application-default login` (or CI service account credentials).
3. **IAM Roles** for the operator:
   - `roles/serviceusage.serviceUsageAdmin` (enable APIs)
   - `roles/apigee.admin` (create org, instances, environments, envgroups, attachments)
   - `roles/compute.loadBalancerAdmin` and `roles/compute.networkAdmin` (PSC NEG, LB, VPC/peering)
   - *(Only if `use_cmek = true` or Data Residency)* `roles/cloudkms.admin` and KMS IAM admin
   - *(Only if `--dns-zone` is used)* `roles/dns.admin`

---

## Step 3: Run Provisioning (`wizard`, `plan`, `apply`, or `export`)

### A. Default Deployment (PAYG · INTERMEDIATE + Analytics · PSC · GMEK · Google Cert · No DR · nip.io)
```bash
SK=<path-to>/skills
P=$SK/apigee-x-provisioning/scripts/provision.py

# 1. Review the Terraform plan (~20 resources with GMEK + PSC defaults; no changes made)
python3 $P plan --project $PROJECT --region asia-south1

# 2. Apply (~25-40 min) and optionally deploy /httpbin sample proxy to verify
python3 $P apply --project $PROJECT --region asia-south1 --sample

# 3. Export environment variables for downstream proxy skills
source apigee-x-infra/apigee-x.env    # sets APIGEE_ORG, APIGEE_ENV, APIGEE_HOST
curl -i https://$APIGEE_HOST/httpbin/headers
```

### B. Interactive CLI Wizard (Prompts for All Options with Defaults)
```bash
python3 $P wizard
# Or combine flags with -i to prompt interactively:
python3 $P plan --project $PROJECT --region us-central1 -i
```

### C. Full-Fledged Production Examples
```bash
# Multi-region Active-Active HA + Custom Domain + Cloud DNS + Cloud Armor WAF + Static NAT IPs + Advanced API Security:
python3 $P plan \
  --project $PROJECT \
  --region us-central1 \
  --additional-region us-east1 \
  --env dev:INTERMEDIATE@nonprod-group \
  --env prod:COMPREHENSIVE@prod-group \
  --hostname nonprod-group=api-dev.example.com \
  --hostname prod-group=api.example.com \
  --dns-zone example-com-zone \
  --cloud-armor \
  --http-redirect \
  --nat-ips \
  --addon api_security \
  --southbound-psc orders-psc=projects/backend-proj/regions/us-central1/serviceAttachments/orders-sa \
  --state-bucket my-tf-state-bucket

# VPC Peering topology + CMEK + Private DNS Peering:
python3 $P plan \
  --project $PROJECT \
  --region europe-west1 \
  --network-mode VPC_PEERING \
  --cmek \
  --peered-dns corp-internal=corp.internal.

# Export a clean, standalone Terraform folder (terraform.tfvars + *.tf) to share or commit to GitOps:
python3 $P export --project $PROJECT --region asia-south1 --env prod:INTERMEDIATE@prod-group --workdir ./customer-tf
```

---

## What Gets Created (Terraform Module Layout)

| File | Responsibilities |
|---|---|
| `versions.tf` | Terraform `>= 1.5`, `google` & `google-beta` `>= 6.0`, automatic regional `apigee_custom_endpoint` for Data Residency |
| `variables.tf` | Validated inputs with defaults (`PAYG`, `INTERMEDIATE` + Analytics, `PSC`, `GMEK`, `google_managed` cert, no DR, `nip.io`; rejects `EVALUATION`) |
| `main.tf` | APIs, optional service identity & Cloud KMS CMEK keys (`prevent_destroy`), optional PSA VPC peering (`/22` per instance + `/28`), `google_apigee_organization`, primary + multi-region `google_apigee_instance`s, `google_apigee_environment`s, PAYG Analytics add-ons, envgroups & attachments |
| `routing.tf` | Auto-created `/28` PSC consumer subnet(s) when `network` is omitted, regional PSC NEGs, `external` Global HTTPS LB (Google-managed / self-managed / existing cert, optional HTTP→HTTPS redirect & Cloud Armor WAF), `external-regional` Regional HTTPS LB, `internal` PSC endpoint, optional Cloud DNS `A` records |
| `enterprise.tf` | Optional `google_apigee_addons_config`, per-instance `google_apigee_nat_address` static egress IPs, `google_apigee_endpoint_attachment` southbound PSC attachments, and `google_apigee_dns_zone` private DNS peering |
| `outputs.tf` | `org`, `billing_type`, `network_mode`, `encryption_mode`, `instance`, `instances`, `environments`, `ingress_ip`, `nat_ips`, `southbound_psc_endpoints`, `env_file`, `next_steps` |

---

## Day-2 Operations, Importing & Teardown

- **Adding/Updating Environments or Regions Later**: Re-run `provision.py plan`/`apply` with the complete `--env` and `--additional-region` lists (or edit `terraform.tfvars` directly). Adding `additional_regions` expands a single-region org to multi-region active-active without recreating the primary instance.
- **Importing an Existing Paid Org**: Add `import` blocks in `apigee-x-infra/imports.tf` (for `google_apigee_organization.org`, `google_apigee_instance.instance`, `google_apigee_environment.env["..."]`, `google_apigee_envgroup.group["..."]`) and match `network_mode` and `use_cmek` to the existing org before running `plan`.
- **Teardown Safety**: `provision.py destroy` intentionally refuses automatic execution because deleting an Apigee org deletes all proxies, products, developer apps, and keys (and CMEK keys carry `prevent_destroy = true`). Always tear down routing and environments deliberately via targeted `terraform destroy` only with explicit written confirmation.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Org creation fails on location constraint | Org policy `constraints/gcp.resourceLocations` must include `global` (unless Data Residency is enabled) |
| HTTPS handshake / `FAILED_NOT_VISIBLE` on cert | Google-managed SSL cert stays `PROVISIONING` until DNS points at `ingress_ip` and the `:443` forwarding rule is live (~10–60 min for validation) |
| `404` / `ApplicationNotFound` | Request `Host` header does not match any hostname on the attached `envgroup`, or no proxy is deployed to that environment |
| Instance creation times out | Runtime instance creation takes ~25–45 min (up to 60+ min); re-run `apply` and Terraform will resume |
| `Evaluation organizations are not supported` | Switch `billing_type` to `PAYG` (default) or `SUBSCRIPTION` |

## Offline Verification
`terraform -chdir=skills/apigee-x-provisioning/terraform test` runs 11 mocked-provider tests offline covering defaults (`PAYG`, `INTERMEDIATE` + Analytics, `PSC`, `GMEK`, Google-managed cert, no DR, `nip.io`), `SUBSCRIPTION`, `VPC_PEERING` + peered DNS, multi-region HA + Cloud Armor + Cloud DNS + NAT IPs + Southbound PSC + Add-ons, `external-regional` LB, `internal` PSC endpoint, Data Residency, optional CMEK, and input validation (including `EVALUATION` rejection).
