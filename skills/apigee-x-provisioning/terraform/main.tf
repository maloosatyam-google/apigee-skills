# Apigee X paid organization WITHOUT VPC peering (Subscription or Pay-as-you-go), following
# https://docs.cloud.google.com/apigee/docs/api-platform/get-started/install-cli-non-peered
# Steps 2-6: APIs, service identity, CMEK keys, organization, instance, environments, envgroups, attachments.

locals {
  is_payg         = var.billing_type == "PAYG"
  data_residency  = var.control_plane_location != null
  instance_name   = coalesce(var.instance_name, var.region)
  runtime_key_loc = coalesce(var.runtime_db_key_location, var.region)
  envgroups       = toset([for name, e in var.environments : coalesce(e.envgroup, "${name}-group")])
  env_group_of    = { for name, e in var.environments : name => coalesce(e.envgroup, "${name}-group") }
  analytics_envs  = { for name, e in var.environments : name => e if local.is_payg && e.analytics }
  cp_keys         = local.data_residency && var.create_control_plane_keys
}

# ---------------------------------------------------------------- Step 2: APIs
resource "google_project_service" "apis" {
  for_each           = toset(["apigee.googleapis.com", "apihub.googleapis.com", "compute.googleapis.com", "cloudkms.googleapis.com"])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# ---------------------------------------------------------------- Step 3: Apigee service identity
resource "google_project_service_identity" "apigee" {
  provider = google-beta
  project  = var.project_id
  service  = google_project_service.apis["apigee.googleapis.com"].service
}

# ---------------------------------------------------------------- Step 4a: CMEK keys
# Keys are protected from destroy: deleting them makes the org/instance data unreadable.
resource "google_kms_key_ring" "runtime_db" {
  name       = "${var.key_ring_prefix}-runtime-db"
  location   = local.runtime_key_loc
  depends_on = [google_project_service.apis]
}

resource "google_kms_crypto_key" "runtime_db" {
  name     = "runtime-db"
  key_ring = google_kms_key_ring.runtime_db.id
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_kms_crypto_key_iam_member" "runtime_db" {
  crypto_key_id = google_kms_crypto_key.runtime_db.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = google_project_service_identity.apigee.member
}

resource "google_kms_key_ring" "control_plane" {
  count      = local.cp_keys ? 1 : 0
  name       = "${var.key_ring_prefix}-control-plane"
  location   = var.control_plane_location
  depends_on = [google_project_service.apis]
}

resource "google_kms_crypto_key" "control_plane" {
  count    = local.cp_keys ? 1 : 0
  name     = "control-plane"
  key_ring = google_kms_key_ring.control_plane[0].id
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_kms_crypto_key_iam_member" "control_plane" {
  count         = local.cp_keys ? 1 : 0
  crypto_key_id = google_kms_crypto_key.control_plane[0].id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = google_project_service_identity.apigee.member
}

resource "google_kms_key_ring" "consumer_data" {
  count      = local.cp_keys ? 1 : 0
  name       = "${var.key_ring_prefix}-consumer-data"
  location   = var.consumer_data_region
  depends_on = [google_project_service.apis]
}

resource "google_kms_crypto_key" "consumer_data" {
  count    = local.cp_keys ? 1 : 0
  name     = "consumer-data"
  key_ring = google_kms_key_ring.consumer_data[0].id
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_kms_crypto_key_iam_member" "consumer_data" {
  count         = local.cp_keys ? 1 : 0
  crypto_key_id = google_kms_crypto_key.consumer_data[0].id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = google_project_service_identity.apigee.member
}

# ---------------------------------------------------------------- Step 4b: organization
resource "google_apigee_organization" "org" {
  project_id                           = var.project_id
  display_name                         = var.project_id
  description                          = "Apigee X ${var.billing_type} organization (no VPC peering), managed by Terraform"
  runtime_type                         = "CLOUD"
  billing_type                         = var.billing_type
  disable_vpc_peering                  = true
  analytics_region                     = local.data_residency ? null : coalesce(var.analytics_region, var.region)
  runtime_database_encryption_key_name = google_kms_crypto_key.runtime_db.id

  api_consumer_data_location            = local.data_residency ? var.consumer_data_region : null
  control_plane_encryption_key_name     = local.cp_keys ? google_kms_crypto_key.control_plane[0].id : null
  api_consumer_data_encryption_key_name = local.cp_keys ? google_kms_crypto_key.consumer_data[0].id : null

  depends_on = [
    google_kms_crypto_key_iam_member.runtime_db,
    google_kms_crypto_key_iam_member.control_plane,
    google_kms_crypto_key_iam_member.consumer_data,
  ]
}

# ---------------------------------------------------------------- Step 5: runtime instance
resource "google_kms_key_ring" "disk" {
  name       = "${var.key_ring_prefix}-disk-${var.region}"
  location   = var.region
  depends_on = [google_project_service.apis]
}

resource "google_kms_crypto_key" "disk" {
  name     = "disk"
  key_ring = google_kms_key_ring.disk.id
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_kms_crypto_key_iam_member" "disk" {
  crypto_key_id = google_kms_crypto_key.disk.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = google_project_service_identity.apigee.member
}

resource "google_apigee_instance" "instance" {
  org_id                   = google_apigee_organization.org.id
  name                     = local.instance_name
  location                 = var.region
  disk_encryption_key_name = google_kms_crypto_key.disk.id
  consumer_accept_list     = distinct(concat([var.project_id], var.consumer_accept_list))

  timeouts {
    create = "90m"
  }
  depends_on = [google_kms_crypto_key_iam_member.disk]
}

# ---------------------------------------------------------------- Step 6: environments + add-ons
resource "google_apigee_environment" "env" {
  for_each     = var.environments
  org_id       = google_apigee_organization.org.id
  name         = each.key
  display_name = coalesce(each.value.display_name, each.key)
  description  = coalesce(each.value.description, local.is_payg ? "${each.value.type} environment" : "Environment")
  # Environment types exist only for Pay-as-you-go orgs.
  type = local.is_payg ? each.value.type : null
}

# PAYG Analytics add-on is enabled per environment.
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
