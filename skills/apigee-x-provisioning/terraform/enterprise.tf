# Optional Enterprise Capabilities:
#   1. Organization Add-ons (Advanced API Security, API Ops, Integration, Connectors, Monetization)
#   2. Static Outbound Egress NAT IP Addresses (per runtime instance)
#   3. Southbound Private Service Connect (PSC) Endpoint Attachments (for private backend targets)
#   4. Private Cloud DNS Peering Zones (when network_mode = "VPC_PEERING")

locals {
  enable_addons = (
    coalesce(var.addons.api_security, false) ||
    coalesce(var.addons.advanced_api_ops, false) ||
    coalesce(var.addons.integration, false) ||
    coalesce(var.addons.connectors, false) ||
    coalesce(var.addons.monetization, false)
  )

  nat_count = var.enable_nat_ips ? max(1, var.nat_ips_per_instance) : 0
  primary_nat_keys = {
    for idx in range(local.nat_count) : "${local.instance_name}-nat-${idx + 1}" => idx
  }
  additional_nat_keys = merge([
    for r, cfg in var.additional_regions : {
      for idx in range(local.nat_count) : "${coalesce(cfg.instance_name, r)}-nat-${idx + 1}" => {
        region = r
        index  = idx
      }
    }
  ]...)
}

# ---------------------------------------------------------------- Organization Add-ons
resource "google_apigee_addons_config" "org" {
  count = local.enable_addons ? 1 : 0
  org   = google_apigee_organization.org.name

  addons_config {
    dynamic "api_security_config" {
      for_each = coalesce(var.addons.api_security, false) ? [1] : []
      content {
        enabled = true
      }
    }
    dynamic "advanced_api_ops_config" {
      for_each = coalesce(var.addons.advanced_api_ops, false) ? [1] : []
      content {
        enabled = true
      }
    }
    dynamic "integration_config" {
      for_each = coalesce(var.addons.integration, false) ? [1] : []
      content {
        enabled = true
      }
    }
    dynamic "connectors_platform_config" {
      for_each = coalesce(var.addons.connectors, false) ? [1] : []
      content {
        enabled = true
      }
    }
    dynamic "monetization_config" {
      for_each = coalesce(var.addons.monetization, false) ? [1] : []
      content {
        enabled = true
      }
    }
  }
}

# ---------------------------------------------------------------- Static Outbound NAT IPs
resource "google_apigee_nat_address" "primary" {
  for_each    = local.primary_nat_keys
  instance_id = google_apigee_instance.instance.id
  name        = each.key
  activate    = true
}

resource "google_apigee_nat_address" "additional" {
  for_each    = local.additional_nat_keys
  instance_id = google_apigee_instance.additional[each.value.region].id
  name        = each.key
  activate    = true
}

# ---------------------------------------------------------------- Southbound PSC Endpoint Attachments
resource "google_apigee_endpoint_attachment" "southbound" {
  for_each               = var.southbound_psc_endpoints
  org_id                 = google_apigee_organization.org.id
  endpoint_attachment_id = each.key
  location               = coalesce(each.value.location, var.region)
  service_attachment     = each.value.service_attachment
}

# ---------------------------------------------------------------- Private DNS Peering (VPC_PEERING mode)
resource "google_apigee_dns_zone" "peered" {
  for_each    = var.peered_dns_zones
  org_id      = google_apigee_organization.org.id
  dns_zone_id = each.key
  domain      = each.value.domain
  description = coalesce(each.value.description, "Private DNS peering for ${each.value.domain}")

  peering_config {
    target_project_id = coalesce(each.value.target_project_id, var.project_id)
    target_network_id = each.value.target_network_id != null ? each.value.target_network_id : (local.peered_network != null ? local.peered_network : "")
  }

  lifecycle {
    precondition {
      condition     = local.is_peered
      error_message = "peered_dns_zones requires network_mode = \"VPC_PEERING\"."
    }
  }
}
