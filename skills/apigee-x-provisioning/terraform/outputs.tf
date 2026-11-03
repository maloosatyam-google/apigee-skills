output "org" {
  description = "Apigee organization name (= project_id)."
  value       = google_apigee_organization.org.name
}

output "billing_type" {
  description = "Apigee billing model (PAYG or SUBSCRIPTION)."
  value       = google_apigee_organization.org.billing_type
}

output "network_mode" {
  description = "Networking topology (PSC or VPC_PEERING)."
  value       = var.network_mode
}

output "encryption_mode" {
  description = "Encryption mode (GMEK or CMEK)."
  value       = var.use_cmek ? "CMEK" : "GMEK"
}

output "data_residency_enabled" {
  description = "Whether Apigee data residency is enabled."
  value       = local.data_residency
}

output "instance" {
  description = "Primary runtime instance name, internal host, and PSC service attachment."
  value = {
    name               = google_apigee_instance.instance.name
    region             = google_apigee_instance.instance.location
    host               = google_apigee_instance.instance.host
    service_attachment = google_apigee_instance.instance.service_attachment
  }
}

output "instances" {
  description = "Map of all runtime instances by region (primary + additional_regions)."
  value = merge(
    {
      (var.region) = {
        name               = google_apigee_instance.instance.name
        host               = google_apigee_instance.instance.host
        service_attachment = google_apigee_instance.instance.service_attachment
      }
    },
    {
      for r, inst in google_apigee_instance.additional : r => {
        name               = inst.name
        host               = inst.host
        service_attachment = inst.service_attachment
      }
    }
  )
}

output "environments" {
  description = "Environment -> type, analytics add-on, envgroup, and hostnames."
  value = {
    for name, e in var.environments : name => {
      type      = local.is_payg ? e.type : "SUBSCRIPTION"
      analytics = local.is_payg ? e.analytics : true
      envgroup  = local.env_group_of[name]
      hostnames = local.hostnames[local.env_group_of[name]]
    }
  }
}

output "ingress_ip" {
  description = "Northbound ingress IP: Global External LB IP (external), Regional External LB IP (external-regional), or Internal PSC IP (internal)."
  value       = local.lb_ip
}

output "nat_ips" {
  description = "Static outbound NAT IP addresses provisioned per runtime instance (when enable_nat_ips = true)."
  value = merge(
    { for k, n in google_apigee_nat_address.primary : k => n.ip_address },
    { for k, n in google_apigee_nat_address.additional : k => n.ip_address }
  )
}

output "southbound_psc_endpoints" {
  description = "Southbound PSC Endpoint Attachment target hosts (use directly in TargetEndpoint or TargetServer)."
  value = {
    for k, ep in google_apigee_endpoint_attachment.southbound : k => {
      host             = ep.host
      location         = ep.location
      connection_state = ep.connection_state
    }
  }
}

output "apigee_api_base" {
  description = "Management API base URL (regional when data residency is enabled)."
  value       = local.apigee_endpoint == null ? "https://apigee.googleapis.com" : trimsuffix(local.apigee_endpoint, "/v1/")
}

output "env_file" {
  description = "Shell exports for the other apigee-* skills (written to apigee-x.env by provision.py)."
  value = join("\n", compact([
    "export APIGEE_ORG=${google_apigee_organization.org.name}",
    "export APIGEE_ENV=${keys(var.environments)[0]}",
    "export APIGEE_HOST=${local.hostnames[local.env_group_of[keys(var.environments)[0]]][0]}",
    local.apigee_endpoint == null ? "" : "export APIGEE_API_BASE=${trimsuffix(local.apigee_endpoint, "/v1/")}",
  ]))
}

output "next_steps" {
  value = local.external ? join("\n", compact([
    local.effective_cert_mode == "google_managed" ? "Certificate status (ACTIVE before HTTPS works, can take up to ~60 min):\n  gcloud compute ssl-certificates describe ${var.lb_name_prefix}-cert --global --format='get(managed.status,managed.domainStatus)' --project=${var.project_id}" : "",
    length(var.envgroup_hostnames) > 0 && var.dns_managed_zone == null ? "Create DNS A records for ${join(", ", local.all_hostnames)} -> ${local.lb_ip}" : (length(var.envgroup_hostnames) > 0 ? "Cloud DNS A records created in zone ${var.dns_managed_zone} -> ${local.lb_ip}" : "nip.io hostnames resolve automatically to ${local.lb_ip}"),
    "Test after deploying a proxy:  curl -i https://${local.all_hostnames[0]}/httpbin/headers",
    ])) : local.external_regional ? join("\n", [
    length(var.envgroup_hostnames) > 0 && var.dns_managed_zone == null ? "Create DNS A records for ${join(", ", local.all_hostnames)} -> ${local.lb_ip}" : "nip.io / Cloud DNS resolves to ${local.lb_ip}",
    "Test after deploying a proxy:  curl -i https://${local.all_hostnames[0]}/httpbin/headers",
  ]) : local.internal ? "Map ${join(", ", local.all_hostnames)} -> ${local.lb_ip} in a private Cloud DNS zone, then call from inside the VPC." : "routing = none: configure northbound access targeting instance service_attachment."
}
