# Apigee X Provisioning Architecture & Decision Guide

Reference companion for [`SKILL.md`](../SKILL.md), aligned with Google Cloud's official
[Apigee provisioning options](https://docs.cloud.google.com/apigee/docs/api-platform/get-started/provisioning-options)
for **paid organizations only** (`PAYG` and `SUBSCRIPTION`).

> [!IMPORTANT]
> **Evaluation (60-day trial) organizations are intentionally not supported** by this skill or Terraform module. All deployments target production-capable paid organizations (`PAYG` or `SUBSCRIPTION`).

---

## 1. Billing Model & Environment Tiers (`PAYG` vs `SUBSCRIPTION`)

| Aspect | `PAYG` (Default) | `SUBSCRIPTION` |
|---|---|---|
| **Billing** | Billed hourly per environment tier + add-ons + proxy call volume | Contract-based entitlement (Standard, Enterprise, Enterprise Plus) |
| **Environment Tiers** | Per-environment `type`:<br>• `BASE` — entry-level gateway capabilities<br>• **`INTERMEDIATE` (Default)** — full API management, quota, caching, security<br>• `COMPREHENSIVE` — highest throughput & enterprise capabilities | Not set (`type = null`); governed by contract tier |
| **Analytics** | Per-environment add-on (`google_apigee_environment_addons_config`, **`analytics = true` by default**; disable with `:noanalytics`) | Included automatically in subscription |
| **Heterogeneous Envs** | Supported in one org (e.g. `dev:BASE:noanalytics`, `stage:INTERMEDIATE`, `prod:COMPREHENSIVE`) | All environments share subscription capabilities |

---

## 2. Networking Topology (`PSC` vs `VPC_PEERING`)

| Capability | `PSC` — Without VPC Peering (**Default**) | `VPC_PEERING` — Private Services Access |
|---|---|---|
| **Terraform setting** | `network_mode = "PSC"` (`disable_vpc_peering = true`) | `network_mode = "VPC_PEERING"` (`disable_vpc_peering = false`) |
| **IP space required** | Only a `/28` consumer subnet per region for the PSC NEG (1 IP used; zero RFC 1918 exhaustion) | `/22` per Apigee runtime instance + `/28` support range reserved for `servicenetworking.googleapis.com` |
| **Transitive peering limits** | Avoids VPC peering transitive routing limits; connect from any authorized consumer project (`consumer_accept_list`) | Subject to GCP VPC peering limits; requires Shared VPC or hub-and-spoke planning |
| **Northbound (Client → Apigee)** | PSC NEG → Global/Regional External HTTPS LB, or Internal PSC Endpoint | PSC NEG → Global/Regional External HTTPS LB, or Internal PSC Endpoint |
| **Southbound (Apigee → Private Backend)** | **Southbound PSC Endpoint Attachments** (`southbound_psc_endpoints` / `google_apigee_endpoint_attachment`) targeting producer `serviceAttachments` | Direct private RFC 1918 routing over VPC peering + optional **Private DNS Peering** (`peered_dns_zones` / `google_apigee_dns_zone`) |
| **VPC Service Controls (VPC-SC)** | Check current GCP VPC-SC support matrix before choosing `PSC` for perimeter-enforced projects | Full VPC Service Controls perimeter support |

**When to choose `VPC_PEERING` over the `PSC` default:**
1. You require **VPC Service Controls (VPC-SC)** around Apigee.
2. You have dozens of legacy internal backends on RFC 1918 IPs where publishing Internal LB Service Attachments (Southbound PSC) is impractical, or you need **Private Cloud DNS Peering** (`google_apigee_dns_zone`).

---

## 3. Encryption Keys (`GMEK` vs `CMEK`)

| Option | Terraform / CLI | When to Use |
|---|---|---|
| **Google-Managed Keys (`GMEK`, Default)** | `use_cmek = false` (default) | Zero KMS management overhead; Google manages and rotates runtime DB and disk encryption keys automatically. |
| **Auto-Created Cloud KMS (`CMEK`)** | `use_cmek = true` / `--cmek` | Compliance requires customer-controlled key rotation/revocation in Cloud KMS. Creates runtime DB key + per-region disk key(s) with `prevent_destroy = true` and grants `roles/cloudkms.cryptoKeyEncrypterDecrypter` to the Apigee Service Agent. |
| **Bring-Your-Own KMS Keys (`CMEK`)** | `use_cmek = true` + `runtime_db_kms_key_id` / `disk_kms_key_id` | Central security team pre-provisions Cloud KMS keys (including Cloud HSM or Cloud EKM). |

> [!WARNING]
> Encryption mode (`GMEK` vs `CMEK`) and the runtime database key **cannot be changed in-place** after the Apigee organization or instance is created. Confirm this requirement upfront.

---

## 4. Data Residency

- **Without Data Residency (Default)**: `control_plane_location = null`. Uses the global management endpoint (`https://apigee.googleapis.com`), stores analytics in `analytics_region` (defaults to `region`), and requires org policy `constraints/gcp.resourceLocations` (if enforced) to allow `global`.
- **With Data Residency**: Set `control_plane_location` (e.g. `us`, `eu`, `ch`, `au`) and `consumer_data_region` (a region inside `control_plane_location`, e.g. `us-west1`).
  - Uses regional management API `https://<control_plane_location>-apigee.googleapis.com/v1/`.
  - Automatically provisions control-plane and consumer-data Cloud KMS keys (`create_control_plane_keys = true`).
  - All runtime regions (`region` and `additional_regions`) must reside inside `control_plane_location`.
  - If TLS termination must also stay strictly regional (avoiding global Anycast LB), pair with `routing = "external-regional"` and a regional SSL certificate (`cert_mode = "self_managed"` or `existing`).

---

## 5. Northbound Routing, Domain & TLS Certificates

| `routing` Mode | Architecture | Domain & Certificate Behaviour |
|---|---|---|
| **`external` (Default)** | Global External Managed Application Load Balancer (`EXTERNAL_MANAGED`) → Regional PSC NEG(s) across primary + `additional_regions` | • **Default domain**: `<envgroup>.<lb-ip-with-dashes>.nip.io` (zero DNS setup)<br>• **Custom domain**: `envgroup_hostnames` + optional `dns_managed_zone` (auto-creates Cloud DNS `A` records)<br>• **Default cert**: `cert_mode = "google_managed"` (`google_compute_managed_ssl_certificate`), or `existing` / `self_managed`<br>• **Optional**: `enable_cloud_armor = true` (Adaptive Protection + OWASP SQLi/XSS WAF) and `enable_http_redirect = true` (`:80` → `:443`) |
| **`external-regional`** | Regional External Managed Application Load Balancer + `REGIONAL_MANAGED_PROXY` subnet → Regional PSC NEG | Keeps load balancing & TLS termination strictly inside `region` (recommended for strict Data Residency). Requires a regional certificate (`cert_mode = "self_managed"` PEM or `existing` regional cert self-link). |
| **`internal`** | Internal PSC Endpoint (`google_compute_forwarding_rule` with internal IP in `network`/`subnetwork`) | Private VPC-only access (RFC 1918 IP). Map `<envgroup>` hostnames to `ingress_ip` in a private Cloud DNS zone. |
| **`none`** | No LB resources created | Outputs `instance.service_attachment` so a central networking team can wire an existing shared Load Balancer. |

---

## 6. Multi-Region Active-Active HA & Enterprise Egress / Add-ons

- **Multi-Region Expansion (`additional_regions` / `--additional-region`)**:
  Adding regions (e.g., primary `us-central1` + `us-east1`, or `asia-south1` + `asia-south2`) provisions an independent Apigee runtime instance in each region, attaches your environments to all instances, creates a regional PSC NEG per region, and registers all PSC NEGs behind the Global External Managed HTTPS Load Balancer for automatic closest-region routing and zero-downtime regional failover.
- **Static Outbound NAT IPs (`enable_nat_ips = true` / `--nat-ips`)**:
  Reserves and activates dedicated egress IPv4 addresses (`google_apigee_nat_address`) on every runtime instance when downstream partners or firewalls require static IP allowlisting.
- **Southbound PSC Endpoint Attachments (`southbound_psc_endpoints` / `--southbound-psc`)**:
  Creates `google_apigee_endpoint_attachment` resources pointing to producer `serviceAttachments`, outputting a private `host` IP you can reference directly in Apigee `TargetServer` or `HTTPTargetConnection`.
- **Organization Add-ons (`addons` / `--addon`)**:
  Enables org-wide paid add-ons via `google_apigee_addons_config`: `api_security` (Advanced API Security), `advanced_api_ops`, `integration`, `connectors`, and `monetization`.
