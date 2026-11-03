# Full-fledged paid Apigee X organization (PAYG or SUBSCRIPTION; Evaluation orgs not supported), covering:
#   https://docs.cloud.google.com/apigee/docs/api-platform/get-started/provisioning-options
#
# Defaults:
#   - Billing: PAYG with one INTERMEDIATE environment ("dev") and Analytics enabled
#   - Networking: PSC (Private Service Connect, disable_vpc_peering = true)
#   - Encryption: Google-managed encryption keys (GMEK, use_cmek = false)
#   - Data Residency: Disabled (global control plane)
#   - Northbound: Global External Managed HTTPS LB + Google-managed SSL cert + <envgroup>.<ip>.nip.io domain

locals {
  is_payg         = var.billing_type == "PAYG"
  is_psc          = var.network_mode == "PSC"
  is_peered       = var.network_mode == "VPC_PEERING"
  data_residency  = var.control_plane_location != null
  instance_name   = coalesce(var.instance_name, var.region)
  runtime_key_loc = coalesce(var.runtime_db_key_location, var.region)
  envgroups       = toset([for name, e in var.environments : coalesce(e.envgroup, "${name}-group")])
  env_group_of    = { for name, e in var.environments : name => coalesce(e.envgroup, "${name}-group") }
  analytics_envs  = { for name, e in var.environments : name => e if local.is_payg && e.analytics }

  # CMEK flags (auto-create vs bring-your-own KMS keys)
  use_cmek              = var.use_cmek
  create_runtime_db_key = local.use_cmek && var.runtime_db_kms_key_id == null
  create_primary_disk   = local.use_cmek && var.disk_kms_key_id == null
  additional_disk_keys  = { for r, cfg in var.additional_regions : r => cfg if local.use_cmek && cfg.disk_kms_key_id == null }
  cp_keys_needed        = local.data_residency && var.create_control_plane_keys
  create_cp_key         = local.cp_keys_needed && var.control_plane_kms_key_id == null
  create_consumer_key   = local.cp_keys_needed && var.consumer_data_kms_key_id == null
  needs_kms = (
    local.use_cmek || local.cp_keys_needed ||
    var.runtime_db_kms_key_id != null || var.disk_kms_key_id != null ||
    var.control_plane_kms_key_id != null || var.consumer_data_kms_key_id != null
  )

  # Effective KMS key IDs (null when GMEK)
  runtime_db_key_id    = local.use_cmek ? (var.runtime_db_kms_key_id != null ? var.runtime_db_kms_key_id : try(google_kms_crypto_key.runtime_db[0].id, null)) : null
  primary_disk_key_id  = local.use_cmek ? (var.disk_kms_key_id != null ? var.disk_kms_key_id : try(google_kms_crypto_key.disk[0].id, null)) : null
  control_plane_key_id = local.data_residency ? (var.control_plane_kms_key_id != null ? var.control_plane_kms_key_id : try(google_kms_crypto_key.control_plane[0].id, null)) : null
  consumer_data_key_id = local.data_residency ? (var.consumer_data_kms_key_id != null ? var.consumer_data_kms_key_id : try(google_kms_crypto_key.consumer_data[0].id, null)) : null

  # VPC Peering network & PSA ranges (only when network_mode = "VPC_PEERING")
  create_peered_vpc = local.is_peered && var.network == null
  peered_network    = local.is_peered ? (local.create_peered_vpc ? google_compute_network.apigee_vpc[0].id : var.network) : null
  peering_regions = merge(
    { (var.region) = var.peering_address },
    { for r, cfg in var.additional_regions : r => cfg.peering_address }
  )

  # Multi-region environment attachment cross-product
  additional_env_attachments = merge([
    for r, cfg in var.additional_regions : {
      for env_name, _ in var.environments : "${r}/${env_name}" => {
        region = r
        env    = env_name
      }
    }
  ]...)

  needs_dns_api = var.dns_managed_zone != null || length(var.peered_dns_zones) > 0
  required_apis = toset(compact([
    "apigee.googleapis.com",
    "apihub.googleapis.com",
    "compute.googleapis.com",
    local.needs_kms ? "cloudkms.googleapis.com" : "",
    local.is_peered ? "servicenetworking.googleapis.com" : "",
    local.needs_dns_api ? "dns.googleapis.com" : "",
  ]))
}

# ---------------------------------------------------------------- Step 2: APIs
resource "google_project_service" "apis" {
  for_each           = local.required_apis
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# ---------------------------------------------------------------- Step 3: Apigee service identity (when CMEK or VPC Peering is used)
resource "google_project_service_identity" "apigee" {
  count    = (local.needs_kms || local.is_peered) ? 1 : 0
  provider = google-beta
  project  = var.project_id
  service  = google_project_service.apis["apigee.googleapis.com"].service
}

# ---------------------------------------------------------------- Step 4a: Optional VPC Peering (network_mode = "VPC_PEERING")
resource "google_compute_network" "apigee_vpc" {
  count                   = local.create_peered_vpc ? 1 : 0
  name                    = "${var.lb_name_prefix}-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_global_address" "peering_support" {
  count         = local.is_peered && !var.use_existing_psa_connection ? 1 : 0
  name          = "${var.lb_name_prefix}-support-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 28
  address       = var.peering_support_address
  network       = local.peered_network
  depends_on    = [google_project_service.apis]
}

resource "google_compute_global_address" "peering_runtime" {
  for_each      = local.is_peered && !var.use_existing_psa_connection ? local.peering_regions : {}
  name          = "${var.lb_name_prefix}-range-${each.key}"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 22
  address       = each.value
  network       = local.peered_network
  depends_on    = [google_project_service.apis]
}

resource "google_service_networking_connection" "apigee_vpc_connection" {
  count   = local.is_peered && !var.use_existing_psa_connection ? 1 : 0
  network = local.peered_network
  service = "servicenetworking.googleapis.com"
  reserved_peering_ranges = concat(
    [google_compute_global_address.peering_support[0].name],
    [for k in sort(keys(google_compute_global_address.peering_runtime)) : google_compute_global_address.peering_runtime[k].name]
  )
  depends_on = [google_project_service.apis]
}

# ---------------------------------------------------------------- Step 4b: Optional CMEK keys (use_cmek = true; default GMEK)
resource "google_kms_key_ring" "runtime_db" {
  count      = local.create_runtime_db_key ? 1 : 0
  name       = "${var.key_ring_prefix}-runtime-db"
  location   = local.runtime_key_loc
  depends_on = [google_project_service.apis]
}

resource "google_kms_crypto_key" "runtime_db" {
  count    = local.create_runtime_db_key ? 1 : 0
  name     = "runtime-db"
  key_ring = google_kms_key_ring.runtime_db[0].id
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_kms_crypto_key_iam_member" "runtime_db" {
  count         = local.use_cmek ? 1 : 0
  crypto_key_id = local.runtime_db_key_id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = google_project_service_identity.apigee[0].member
}

resource "google_kms_key_ring" "control_plane" {
  count      = local.create_cp_key ? 1 : 0
  name       = "${var.key_ring_prefix}-control-plane"
  location   = var.control_plane_location
  depends_on = [google_project_service.apis]
}

resource "google_kms_crypto_key" "control_plane" {
  count    = local.create_cp_key ? 1 : 0
  name     = "control-plane"
  key_ring = google_kms_key_ring.control_plane[0].id
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_kms_crypto_key_iam_member" "control_plane" {
  count         = local.cp_keys_needed || var.control_plane_kms_key_id != null ? 1 : 0
  crypto_key_id = local.control_plane_key_id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = google_project_service_identity.apigee[0].member
}

resource "google_kms_key_ring" "consumer_data" {
  count      = local.create_consumer_key ? 1 : 0
  name       = "${var.key_ring_prefix}-consumer-data"
  location   = var.consumer_data_region
  depends_on = [google_project_service.apis]
}

resource "google_kms_crypto_key" "consumer_data" {
  count    = local.create_consumer_key ? 1 : 0
  name     = "consumer-data"
  key_ring = google_kms_key_ring.consumer_data[0].id
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_kms_crypto_key_iam_member" "consumer_data" {
  count         = local.cp_keys_needed || var.consumer_data_kms_key_id != null ? 1 : 0
  crypto_key_id = local.consumer_data_key_id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = google_project_service_identity.apigee[0].member
}

# ---------------------------------------------------------------- Step 4c: Apigee Organization
resource "google_apigee_organization" "org" {
  project_id                           = var.project_id
  display_name                         = var.project_id
  description                          = "Apigee X ${var.billing_type} organization (${local.is_psc ? "PSC / no VPC peering" : "VPC peering"}), managed by Terraform"
  runtime_type                         = "CLOUD"
  billing_type                         = var.billing_type
  disable_vpc_peering                  = local.is_psc
  authorized_network                   = local.is_peered ? local.peered_network : null
  analytics_region                     = local.data_residency ? null : coalesce(var.analytics_region, var.region)
  runtime_database_encryption_key_name = local.runtime_db_key_id

  api_consumer_data_location            = local.data_residency ? var.consumer_data_region : null
  control_plane_encryption_key_name     = local.control_plane_key_id
  api_consumer_data_encryption_key_name = local.consumer_data_key_id

  lifecycle {
    precondition {
      condition     = !local.data_residency || var.consumer_data_region != null
      error_message = "consumer_data_region is required when control_plane_location (data residency) is set."
    }
  }

  depends_on = [
    google_project_service.apis,
    google_service_networking_connection.apigee_vpc_connection,
    google_kms_crypto_key_iam_member.runtime_db,
    google_kms_crypto_key_iam_member.control_plane,
    google_kms_crypto_key_iam_member.consumer_data,
  ]
}

# ---------------------------------------------------------------- Step 5: Runtime Instance(s) (primary + optional multi-region)
resource "google_kms_key_ring" "disk" {
  count      = local.create_primary_disk ? 1 : 0
  name       = "${var.key_ring_prefix}-disk-${var.region}"
  location   = var.region
  depends_on = [google_project_service.apis]
}

resource "google_kms_crypto_key" "disk" {
  count    = local.create_primary_disk ? 1 : 0
  name     = "disk"
  key_ring = google_kms_key_ring.disk[0].id
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_kms_crypto_key_iam_member" "disk" {
  count         = local.use_cmek ? 1 : 0
  crypto_key_id = local.primary_disk_key_id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = google_project_service_identity.apigee[0].member
}

resource "google_apigee_instance" "instance" {
  org_id                   = google_apigee_organization.org.id
  name                     = local.instance_name
  location                 = var.region
  ip_range                 = local.is_peered ? var.instance_ip_range : null
  disk_encryption_key_name = local.primary_disk_key_id
  consumer_accept_list     = distinct(concat([var.project_id], var.consumer_accept_list))

  dynamic "access_logging_config" {
    for_each = coalesce(var.access_logging.enabled, false) ? [1] : []
    content {
      enabled = true
      filter  = var.access_logging.filter
    }
  }

  timeouts {
    create = "90m"
  }
  depends_on = [google_kms_crypto_key_iam_member.disk]
}

resource "google_kms_key_ring" "disk_additional" {
  for_each   = local.additional_disk_keys
  name       = "${var.key_ring_prefix}-disk-${each.key}"
  location   = each.key
  depends_on = [google_project_service.apis]
}

resource "google_kms_crypto_key" "disk_additional" {
  for_each = local.additional_disk_keys
  name     = "disk"
  key_ring = google_kms_key_ring.disk_additional[each.key].id
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_kms_crypto_key_iam_member" "disk_additional" {
  for_each      = local.use_cmek ? var.additional_regions : {}
  crypto_key_id = coalesce(each.value.disk_kms_key_id, try(google_kms_crypto_key.disk_additional[each.key].id, null))
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = google_project_service_identity.apigee[0].member
}

resource "google_apigee_instance" "additional" {
  for_each                 = var.additional_regions
  org_id                   = google_apigee_organization.org.id
  name                     = coalesce(each.value.instance_name, each.key)
  location                 = each.key
  ip_range                 = local.is_peered ? each.value.ip_range : null
  disk_encryption_key_name = local.use_cmek ? coalesce(each.value.disk_kms_key_id, try(google_kms_crypto_key.disk_additional[each.key].id, null)) : null
  consumer_accept_list     = distinct(concat([var.project_id], var.consumer_accept_list))

  dynamic "access_logging_config" {
    for_each = coalesce(var.access_logging.enabled, false) ? [1] : []
    content {
      enabled = true
      filter  = var.access_logging.filter
    }
  }

  timeouts {
    create = "90m"
  }
  depends_on = [google_kms_crypto_key_iam_member.disk_additional]
}

# ---------------------------------------------------------------- Step 6: Environments, Analytics Add-on & Envgroups
resource "google_apigee_environment" "env" {
  for_each          = var.environments
  org_id            = google_apigee_organization.org.id
  name              = each.key
  display_name      = coalesce(each.value.display_name, each.key)
  description       = coalesce(each.value.description, local.is_payg ? "${each.value.type} environment" : "Environment")
  type              = local.is_payg ? each.value.type : null
  deployment_type   = each.value.deployment_type
  api_proxy_type    = each.value.api_proxy_type
  forward_proxy_uri = each.value.forward_proxy_uri
}

# PAYG Analytics add-on is enabled per environment (true by default).
resource "google_apigee_environment_addons_config" "analytics" {
  for_each          = local.analytics_envs
  env_id            = google_apigee_environment.env[each.key].id
  analytics_enabled = true
}

resource "google_apigee_instance_attachment" "env" {
  for_each    = var.environments
  instance_id = google_apigee_instance.instance.id
  environment = google_apigee_environment.env[each.key].name
}

resource "google_apigee_instance_attachment" "env_additional" {
  for_each    = local.additional_env_attachments
  instance_id = google_apigee_instance.additional[each.value.region].id
  environment = google_apigee_environment.env[each.value.env].name
}

resource "google_apigee_envgroup" "group" {
  for_each  = local.envgroups
  org_id    = google_apigee_organization.org.id
  name      = each.value
  hostnames = local.hostnames[each.value]
}

resource "google_apigee_envgroup_attachment" "env" {
  for_each    = local.env_group_of
  envgroup_id = google_apigee_envgroup.group[each.value].id
  environment = google_apigee_environment.env[each.key].name
}
