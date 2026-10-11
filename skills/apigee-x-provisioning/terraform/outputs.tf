output "org" {
  description = "Apigee organization name (= project id)."
  value       = google_apigee_organization.org.name
}

output "billing_type" {
  value = google_apigee_organization.org.billing_type
}

output "instance" {
  description = "Runtime instance name, internal host and PSC service attachment."
  value = {
    name               = google_apigee_instance.instance.name
    host               = google_apigee_instance.instance.host
    service_attachment = google_apigee_instance.instance.service_attachment
  }
}

output "environments" {
  description = "Environment -> type, analytics add-on, envgroup and hostnames."
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
  description = "External LB IP (routing=external) or PSC endpoint IP (routing=internal)."
  value       = local.lb_ip
}

output "apigee_api_base" {
  description = "Management API base URL (regional for data residency)."
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
  value = local.external ? join("\n", [
    "Certificate status (ACTIVE before HTTPS works, can take up to ~60 min):",
    "  gcloud compute ssl-certificates describe ${var.lb_name_prefix}-cert --global --format='get(managed.status,managed.domainStatus)' --project=${var.project_id}",
    length(var.envgroup_hostnames) > 0 ? "Create DNS A records for ${join(", ", local.all_hostnames)} -> ${local.lb_ip}" : "nip.io hostnames resolve automatically to ${local.lb_ip}",
    "Test after deploying a proxy:  curl -i https://${local.all_hostnames[0]}/httpbin/headers",
  ]) : local.internal ? "Map ${join(", ", local.all_hostnames)} -> ${local.lb_ip} in a private DNS zone, then call from inside the VPC." : "routing = none: configure northbound access yourself."
}
