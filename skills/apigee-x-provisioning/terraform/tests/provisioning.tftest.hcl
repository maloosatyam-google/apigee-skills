# Offline tests: mocked providers, no Google Cloud credentials or resources needed.
#   terraform init -backend=false && terraform test

mock_provider "google" {
  override_during = plan
  mock_resource "google_compute_global_address" {
    defaults = { address = "34.1.2.3" }
  }
  mock_resource "google_compute_address" {
    defaults = { address = "10.10.0.5" }
  }
  mock_resource "google_apigee_instance" {
    defaults = { service_attachment = "projects/tp-abc/regions/us-central1/serviceAttachments/apigee-us-central1-x" }
  }
  mock_resource "google_apigee_nat_address" {
    defaults = { ip_address = "35.200.10.20", state = "ACTIVE" }
  }
  mock_resource "google_apigee_endpoint_attachment" {
    defaults = { host = "7.0.0.2", connection_state = "ACCEPTED" }
  }
}

mock_provider "google-beta" {
  override_during = plan
  mock_resource "google_project_service_identity" {
    defaults = { member = "serviceAccount:service-123@gcp-sa-apigee.iam.gserviceaccount.com" }
  }
}

variables {
  project_id = "my-proj"
  region     = "us-central1"
}

run "defaults_payg_psc_gmek_gcert_no_dr_nipio" {
  command = plan

  assert {
    condition     = google_apigee_organization.org.billing_type == "PAYG"
    error_message = "default billing must be PAYG"
  }
  assert {
    condition     = google_apigee_organization.org.disable_vpc_peering == true && google_apigee_organization.org.authorized_network == null && output.network_mode == "PSC"
    error_message = "default network_mode must be PSC (without VPC peering)"
  }
  assert {
    condition     = google_apigee_organization.org.runtime_database_encryption_key_name == null && google_apigee_instance.instance.disk_encryption_key_name == null && length(google_kms_crypto_key.runtime_db) == 0 && length(google_kms_crypto_key.disk) == 0 && output.encryption_mode == "GMEK"
    error_message = "Google-managed encryption keys (GMEK) must be used by default"
  }
  assert {
    condition     = output.data_residency_enabled == false && google_apigee_organization.org.analytics_region == "us-central1" && output.apigee_api_base == "https://apigee.googleapis.com"
    error_message = "Data residency must be disabled by default"
  }
  assert {
    condition     = google_apigee_environment.env["dev"].type == "INTERMEDIATE" && google_apigee_environment_addons_config.analytics["dev"].analytics_enabled == true
    error_message = "default environment must be INTERMEDIATE with Analytics enabled"
  }
  assert {
    condition     = google_apigee_envgroup.group["dev-group"].hostnames == tolist(["dev-group.34-1-2-3.nip.io"])
    error_message = "default domain must be <group>.<ip>.nip.io"
  }
  assert {
    condition     = length(google_compute_managed_ssl_certificate.apigee) == 1 && length(google_compute_ssl_certificate.self_managed) == 0
    error_message = "Google-managed SSL certificate must be used by default"
  }
  assert {
    condition     = length(google_compute_global_forwarding_rule.apigee) == 1 && google_compute_region_network_endpoint_group.psc[0].psc_target_service == "projects/tp-abc/regions/us-central1/serviceAttachments/apigee-us-central1-x"
    error_message = "PSC NEG + Global External HTTPS LB must be configured by default"
  }
}

run "subscription_has_no_env_type_or_addon" {
  command = plan
  variables {
    billing_type = "SUBSCRIPTION"
  }
  assert {
    condition     = output.environments["dev"].type == "SUBSCRIPTION" && output.billing_type == "SUBSCRIPTION"
    error_message = "subscription environments have no PAYG type"
  }
  assert {
    condition     = length(google_apigee_environment_addons_config.analytics) == 0
    error_message = "no PAYG analytics add-on for subscription orgs"
  }
}

run "vpc_peering_mode_with_peered_dns" {
  command = plan
  variables {
    network_mode = "VPC_PEERING"
    peered_dns_zones = {
      "corp-internal" = {
        domain = "corp.internal."
      }
    }
  }
  assert {
    condition     = google_apigee_organization.org.disable_vpc_peering == false && output.network_mode == "VPC_PEERING"
    error_message = "VPC_PEERING must set disable_vpc_peering = false"
  }
  assert {
    condition     = length(google_service_networking_connection.apigee_vpc_connection) == 1 && length(google_compute_global_address.peering_support) == 1 && length(google_compute_global_address.peering_runtime) == 1
    error_message = "VPC_PEERING must provision PSA connection with /22 and /28 ranges"
  }
  assert {
    condition     = length(google_apigee_dns_zone.peered) == 1
    error_message = "Private DNS peering zone must be created"
  }
}

run "multi_region_ha_armor_dns_addons_nat_southbound" {
  command = plan
  variables {
    additional_regions = {
      "us-east1" = {}
    }
    envgroup_hostnames   = { "dev-group" = ["api.example.com"] }
    dns_managed_zone     = "example-zone"
    enable_cloud_armor   = true
    enable_http_redirect = true
    enable_nat_ips       = true
    addons = {
      api_security = true
    }
    southbound_psc_endpoints = {
      "orders-backend" = {
        service_attachment = "projects/backend-p/regions/us-central1/serviceAttachments/orders-sa"
      }
    }
  }
  assert {
    condition     = length(google_apigee_instance.additional) == 1 && length(google_compute_region_network_endpoint_group.psc_additional) == 1 && length(google_apigee_instance_attachment.env_additional) == 1
    error_message = "additional_regions must create secondary instance, env attachment, and PSC NEG"
  }
  assert {
    condition     = length(google_compute_security_policy.apigee) == 1 && length(google_compute_global_forwarding_rule.http_redirect) == 1
    error_message = "Cloud Armor policy and HTTP->HTTPS redirect must be created"
  }
  assert {
    condition     = length(google_dns_record_set.apigee) == 1 && google_dns_record_set.apigee["api.example.com"].name == "api.example.com."
    error_message = "Cloud DNS A record must be created for custom hostname"
  }
  assert {
    condition     = length(google_apigee_nat_address.primary) == 1 && length(google_apigee_nat_address.additional) == 1 && length(google_apigee_endpoint_attachment.southbound) == 1 && length(google_apigee_addons_config.org) == 1
    error_message = "NAT IPs, southbound PSC endpoint attachment, and org add-ons must be created"
  }
}

run "external_regional_lb_with_self_managed_cert" {
  command = plan
  variables {
    routing             = "external-regional"
    cert_mode           = "self_managed"
    ssl_certificate_pem = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"
    ssl_private_key_pem = "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----"
  }
  assert {
    condition     = length(google_compute_forwarding_rule.regional_https) == 1 && length(google_compute_subnetwork.regional_proxy) == 1 && length(google_compute_global_forwarding_rule.apigee) == 0
    error_message = "external-regional must create regional HTTPS LB and proxy-only subnet"
  }
}

run "multiple_envs_custom_hosts_internal_routing" {
  command = plan
  variables {
    environments = {
      dev  = { type = "INTERMEDIATE", envgroup = "nonprod" }
      test = { type = "BASE", analytics = false, envgroup = "nonprod" }
      prod = { type = "COMPREHENSIVE" }
    }
    envgroup_hostnames = { nonprod = ["api-nonprod.example.com"], prod-group = ["api.example.com"] }
    routing            = "internal"
    network            = "projects/my-proj/global/networks/vpc"
    subnetwork         = "projects/my-proj/regions/us-central1/subnetworks/apps"
  }
  assert {
    condition     = google_apigee_environment.env["prod"].type == "COMPREHENSIVE" && google_apigee_environment.env["test"].type == "BASE"
    error_message = "per-environment types"
  }
  assert {
    condition     = toset(keys(google_apigee_environment_addons_config.analytics)) == toset(["dev", "prod"])
    error_message = "analytics only where requested"
  }
  assert {
    condition     = length(google_apigee_envgroup.group) == 2 && google_apigee_envgroup.group["nonprod"].hostnames == tolist(["api-nonprod.example.com"])
    error_message = "shared envgroup with explicit hostname"
  }
  assert {
    condition     = length(google_compute_forwarding_rule.psc) == 1 && length(google_compute_global_forwarding_rule.apigee) == 0
    error_message = "internal routing creates a PSC endpoint only"
  }
}

run "data_residency" {
  command = plan
  variables {
    control_plane_location = "us"
    consumer_data_region   = "us-west1"
    region                 = "us-west1"
  }
  assert {
    condition     = google_apigee_organization.org.analytics_region == null && google_apigee_organization.org.api_consumer_data_location == "us-west1" && output.data_residency_enabled == true
    error_message = "data residency uses consumer data location, not analytics region"
  }
  assert {
    condition     = length(google_kms_crypto_key.control_plane) == 1 && length(google_kms_crypto_key.consumer_data) == 1
    error_message = "control plane and consumer data keys created"
  }
  assert {
    condition     = output.apigee_api_base == "https://us-apigee.googleapis.com"
    error_message = "regional management endpoint"
  }
}

run "optional_cmek_enabled" {
  command = plan
  variables {
    use_cmek = true
  }
  assert {
    condition     = length(google_kms_crypto_key.runtime_db) == 1 && length(google_kms_crypto_key.disk) == 1 && output.encryption_mode == "CMEK"
    error_message = "CMEK keys created when use_cmek = true"
  }
}

run "rejects_evaluation_billing_type" {
  command = plan
  variables {
    billing_type = "EVALUATION"
  }
  expect_failures = [var.billing_type]
}

run "rejects_invalid_env_type" {
  command = plan
  variables {
    environments = { dev = { type = "LARGE" } }
  }
  expect_failures = [var.environments]
}

run "rejects_zone_as_region" {
  command = plan
  variables {
    region = "us-central1-a"
  }
  expect_failures = [var.region]
}
