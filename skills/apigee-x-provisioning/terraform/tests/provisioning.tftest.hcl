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

run "defaults_payg_intermediate_with_analytics" {
  command = plan

  assert {
    condition     = google_apigee_organization.org.billing_type == "PAYG"
    error_message = "default billing must be PAYG"
  }
  assert {
    condition     = google_apigee_organization.org.disable_vpc_peering == true
    error_message = "org must be created without VPC peering"
  }
  assert {
    condition     = google_apigee_organization.org.runtime_database_encryption_key_name == null && google_apigee_instance.instance.disk_encryption_key_name == null && length(google_kms_crypto_key.runtime_db) == 0 && length(google_kms_crypto_key.disk) == 0
    error_message = "Google-managed encryption keys (GMEK) must be used by default"
  }
  assert {
    condition     = google_apigee_organization.org.analytics_region == "us-central1"
    error_message = "analytics region defaults to region"
  }
  assert {
    condition     = google_apigee_environment.env["dev"].type == "INTERMEDIATE"
    error_message = "default environment must be INTERMEDIATE"
  }
  assert {
    condition     = google_apigee_environment_addons_config.analytics["dev"].analytics_enabled == true
    error_message = "Analytics add-on must be enabled by default"
  }
  assert {
    condition     = google_apigee_envgroup.group["dev-group"].hostnames == tolist(["dev-group.34-1-2-3.nip.io"])
    error_message = "default hostname must be <group>.<ip>.nip.io"
  }
  assert {
    condition     = length(google_compute_global_forwarding_rule.apigee) == 1 && length(google_compute_forwarding_rule.psc) == 0
    error_message = "external routing by default"
  }
  assert {
    condition     = google_compute_region_network_endpoint_group.psc[0].psc_target_service == "projects/tp-abc/regions/us-central1/serviceAttachments/apigee-us-central1-x"
    error_message = "PSC NEG must target the instance service attachment"
  }
  assert {
    condition     = google_apigee_instance.instance.name == "us-central1"
    error_message = "instance name defaults to region"
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
    condition     = google_apigee_envgroup_attachment.env["test"].environment == "test"
    error_message = "every env attached to its group"
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
    condition     = google_apigee_organization.org.analytics_region == null && google_apigee_organization.org.api_consumer_data_location == "us-west1"
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

run "payg_asia_south1_prod_intermediate_analytics" {
  command = plan
  variables {
    project_id   = "my-asia-south1-project"
    region       = "asia-south1"
    billing_type = "PAYG"
    environments = {
      prod = {
        type      = "INTERMEDIATE"
        analytics = true
        envgroup  = "prod-group"
      }
    }
  }
  assert {
    condition     = google_apigee_organization.org.billing_type == "PAYG" && google_apigee_organization.org.analytics_region == "asia-south1" && google_apigee_organization.org.disable_vpc_peering == true
    error_message = "PAYG org in asia-south1 (PSC / no VPC peering)"
  }
  assert {
    condition     = google_apigee_organization.org.runtime_database_encryption_key_name == null && google_apigee_instance.instance.disk_encryption_key_name == null
    error_message = "Google-managed encryption keys used by default"
  }
  assert {
    condition     = google_apigee_environment.env["prod"].type == "INTERMEDIATE" && google_apigee_environment_addons_config.analytics["prod"].analytics_enabled == true
    error_message = "prod environment is INTERMEDIATE with Analytics enabled"
  }
  assert {
    condition     = google_apigee_envgroup.group["prod-group"].hostnames == tolist(["prod-group.34-1-2-3.nip.io"])
    error_message = "prod-group nip.io hostname configured"
  }
}

run "optional_cmek_enabled" {
  command = plan
  variables {
    use_cmek = true
  }
  assert {
    condition     = length(google_kms_crypto_key.runtime_db) == 1 && length(google_kms_crypto_key.disk) == 1
    error_message = "CMEK keys created when use_cmek = true"
  }
}
