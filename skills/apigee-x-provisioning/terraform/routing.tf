# Step 7: northbound routing through Private Service Connect (non-peered orgs).
#   external: PSC NEG -> global external managed HTTPS load balancer (Google-managed certificate)
#   internal: PSC endpoint (internal IP in your VPC); map the envgroup hostname to it in private DNS

locals {
  external           = var.routing == "external"
  internal           = var.routing == "internal"
  create_psc_network = (local.external || local.internal) && var.network == null
  psc_network        = local.create_psc_network ? google_compute_network.psc[0].id : var.network
  psc_subnetwork     = local.create_psc_network ? google_compute_subnetwork.psc[0].id : var.subnetwork
  lb_ip              = local.external ? google_compute_global_address.lb[0].address : (local.internal ? google_compute_address.psc[0].address : null)
  ip_dash            = local.lb_ip == null ? "" : replace(local.lb_ip, ".", "-")

  # Hostnames per envgroup: explicit, else <group>.<ip>.nip.io (external) or <group>.<project>.internal.
  hostnames = {
    for g in local.envgroups : g => lookup(var.envgroup_hostnames, g,
    local.external ? ["${g}.${local.ip_dash}.nip.io"] : ["${g}.${var.project_id}.internal"])
  }
  all_hostnames = distinct(flatten(values(local.hostnames)))
  psc_target    = google_apigee_instance.instance.service_attachment
}

# ---------------------------------------------------------------- PSC consumer network & subnet (when var.network is null)
# Apigee X is provisioned WITHOUT VPC peering (disable_vpc_peering = true).
# Compute Engine requires a consumer network + regional subnetwork to attach the
# Private Service Connect (PSC) NEG or internal PSC endpoint to Apigee's service_attachment.
resource "google_compute_network" "psc" {
  count                   = local.create_psc_network ? 1 : 0
  name                    = "${var.lb_name_prefix}-psc-network"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "psc" {
  count         = local.create_psc_network ? 1 : 0
  name          = "${var.lb_name_prefix}-psc-subnet"
  region        = var.region
  network       = google_compute_network.psc[0].id
  ip_cidr_range = var.psc_subnet_cidr
}

# ---------------------------------------------------------------- external
resource "google_compute_global_address" "lb" {
  count      = local.external ? 1 : 0
  name       = "${var.lb_name_prefix}-ip"
  ip_version = "IPV4"
  depends_on = [google_project_service.apis]
}

resource "google_compute_region_network_endpoint_group" "psc" {
  count                 = local.external ? 1 : 0
  name                  = "${var.lb_name_prefix}-neg"
  region                = var.region
  network_endpoint_type = "PRIVATE_SERVICE_CONNECT"
  psc_target_service    = local.psc_target
  network               = local.psc_network
  subnetwork            = local.psc_subnetwork
}

resource "google_compute_backend_service" "apigee" {
  count                 = local.external ? 1 : 0
  name                  = "${var.lb_name_prefix}-backend"
  protocol              = "HTTPS"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  backend {
    group = google_compute_region_network_endpoint_group.psc[0].id
  }
  # Attach a Cloud Armor policy here (security_policy = ...) - see security_architecture.md
}

resource "google_compute_url_map" "apigee" {
  count           = local.external ? 1 : 0
  name            = "${var.lb_name_prefix}-urlmap"
  default_service = google_compute_backend_service.apigee[0].id
}

resource "google_compute_managed_ssl_certificate" "apigee" {
  count = local.external && var.ssl_certificate == null ? 1 : 0
  name  = "${var.lb_name_prefix}-cert"
  managed {
    domains = local.all_hostnames
  }
  lifecycle {
    create_before_destroy = true
  }
}

resource "google_compute_target_https_proxy" "apigee" {
  count            = local.external ? 1 : 0
  name             = "${var.lb_name_prefix}-https-proxy"
  url_map          = google_compute_url_map.apigee[0].id
  ssl_certificates = [coalesce(var.ssl_certificate, try(google_compute_managed_ssl_certificate.apigee[0].id, null))]
}

resource "google_compute_global_forwarding_rule" "apigee" {
  count                 = local.external ? 1 : 0
  name                  = "${var.lb_name_prefix}-fwd-rule"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_protocol           = "TCP"
  port_range            = "443"
  ip_address            = google_compute_global_address.lb[0].id
  target                = google_compute_target_https_proxy.apigee[0].id
}

# ---------------------------------------------------------------- internal
resource "google_compute_address" "psc" {
  count        = local.internal ? 1 : 0
  name         = "${var.lb_name_prefix}-psc-ip"
  region       = var.region
  address_type = "INTERNAL"
  subnetwork   = local.psc_subnetwork
  depends_on   = [google_project_service.apis]
}

resource "google_compute_forwarding_rule" "psc" {
  count                 = local.internal ? 1 : 0
  name                  = "${var.lb_name_prefix}-psc-endpoint"
  region                = var.region
  network               = local.psc_network
  ip_address            = google_compute_address.psc[0].id
  target                = local.psc_target
  load_balancing_scheme = "" # required for PSC endpoints to published services
}
