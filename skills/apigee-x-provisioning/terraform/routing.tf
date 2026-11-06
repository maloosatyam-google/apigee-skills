# Step 7: Northbound routing through Private Service Connect (works for both PSC and VPC_PEERING orgs).
#   - external (default): PSC NEG(s) -> Global External Managed HTTPS LB (Google-managed cert + nip.io default)
#   - external-regional : PSC NEG -> Regional External Managed HTTPS LB (keeps TLS termination in-region)
#   - internal          : Internal PSC Endpoint IP inside your VPC
#   - none              : Skip routing resources

locals {
  external          = var.routing == "external"
  external_regional = var.routing == "external-regional"
  internal          = var.routing == "internal"
  is_external       = local.external || local.external_regional

  # Reuse peered VPC when network_mode = VPC_PEERING; otherwise create a minimal PSC consumer VPC when var.network is null
  create_psc_network = local.is_psc && local.is_external && var.network == null
  psc_network = (
    var.network != null ? var.network :
    local.is_peered ? local.peered_network :
    local.create_psc_network ? google_compute_network.psc[0].id : null
  )
  create_psc_subnet = local.is_external && var.subnetwork == null
  psc_subnetwork    = var.subnetwork != null ? var.subnetwork : (local.create_psc_subnet ? google_compute_subnetwork.psc[0].id : null)

  additional_region_keys = sort(keys(var.additional_regions))
  additional_psc_subnets = {
    for r, cfg in var.additional_regions : r => cfg
    if local.external && cfg.subnetwork == null
  }

  lb_ip = (
    local.external ? google_compute_global_address.lb[0].address :
    local.external_regional ? google_compute_address.regional_lb[0].address :
    local.internal ? google_compute_address.psc[0].address : null
  )
  ip_dash = local.lb_ip == null ? "" : replace(local.lb_ip, ".", "-")

  # Hostnames per envgroup: explicit custom domains, else <group>.<ip>.nip.io (external) or <group>.<project>.internal
  hostnames = {
    for g in local.envgroups : g => lookup(
      var.envgroup_hostnames,
      g,
      local.is_external ? ["${g}.${local.ip_dash}.nip.io"] : ["${g}.${var.project_id}.internal"]
    )
  }
  all_hostnames = distinct(flatten(values(local.hostnames)))
  psc_target    = google_apigee_instance.instance.service_attachment

  # Effective TLS certificate mode (auto-detects existing / self_managed if provided)
  effective_cert_mode = (
    var.ssl_certificate != null ? "existing" :
    (var.ssl_certificate_pem != null || var.cert_mode == "self_managed") ? "self_managed" :
    var.cert_mode
  )

  security_policy = var.cloud_armor_policy != null ? var.cloud_armor_policy : try(google_compute_security_policy.apigee[0].id, null)
  dns_hostnames   = var.dns_managed_zone != null && local.lb_ip != null ? toset([for h in local.all_hostnames : h if !endswith(h, ".nip.io")]) : toset([])
}

# ---------------------------------------------------------------- PSC Consumer Network & Subnets
resource "google_compute_network" "psc" {
  count                   = local.create_psc_network ? 1 : 0
  name                    = "${var.lb_name_prefix}-psc-network"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "psc" {
  count         = local.create_psc_subnet ? 1 : 0
  name          = "${var.lb_name_prefix}-psc-subnet"
  region        = var.region
  network       = local.psc_network
  ip_cidr_range = var.psc_subnet_cidr
}

resource "google_compute_subnetwork" "psc_additional" {
  for_each      = local.additional_psc_subnets
  name          = "${var.lb_name_prefix}-psc-subnet-${each.key}"
  region        = each.key
  network       = local.psc_network
  ip_cidr_range = coalesce(each.value.psc_subnet_cidr, cidrsubnet("10.0.16.0/20", 8, index(local.additional_region_keys, each.key)))
}

# ---------------------------------------------------------------- PSC NEGs (Primary + Multi-Region)
resource "google_compute_region_network_endpoint_group" "psc" {
  count                 = local.is_external ? 1 : 0
  name                  = "${var.lb_name_prefix}-neg"
  region                = var.region
  network_endpoint_type = "PRIVATE_SERVICE_CONNECT"
  psc_target_service    = local.psc_target
  network               = local.psc_network
  subnetwork            = local.psc_subnetwork
}

resource "google_compute_region_network_endpoint_group" "psc_additional" {
  for_each              = local.external ? var.additional_regions : {}
  name                  = "${var.lb_name_prefix}-neg-${each.key}"
  region                = each.key
  network_endpoint_type = "PRIVATE_SERVICE_CONNECT"
  psc_target_service    = google_apigee_instance.additional[each.key].service_attachment
  network               = local.psc_network
  subnetwork            = each.value.subnetwork != null ? each.value.subnetwork : try(google_compute_subnetwork.psc_additional[each.key].id, null)
}

# ---------------------------------------------------------------- Optional Cloud Armor Security Policy
resource "google_compute_security_policy" "apigee" {
  count       = local.external && var.enable_cloud_armor && var.cloud_armor_policy == null ? 1 : 0
  name        = "${var.lb_name_prefix}-armor-policy"
  description = "Baseline Cloud Armor WAF and Adaptive Protection policy for Apigee X"

  adaptive_protection_config {
    layer_7_ddos_defense_config {
      enable = true
    }
  }

  rule {
    action      = "deny(403)"
    priority    = 1000
    description = "Block OWASP CRS SQL injection attacks"
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('sqli-v33-stable')"
      }
    }
  }

  rule {
    action      = "deny(403)"
    priority    = 1001
    description = "Block OWASP CRS cross-site scripting (XSS) attacks"
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('xss-v33-stable')"
      }
    }
  }

  rule {
    action      = "allow"
    priority    = 2147483647
    description = "Default allow rule"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# ---------------------------------------------------------------- Global External Managed HTTPS LB (routing = "external")
resource "google_compute_global_address" "lb" {
  count      = local.external ? 1 : 0
  name       = "${var.lb_name_prefix}-ip"
  ip_version = "IPV4"
  depends_on = [google_project_service.apis]
}

resource "google_compute_backend_service" "apigee" {
  count                 = local.external ? 1 : 0
  name                  = "${var.lb_name_prefix}-backend"
  protocol              = "HTTPS"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  timeout_sec           = var.lb_backend_timeout_sec
  security_policy       = local.security_policy

  backend {
    group = google_compute_region_network_endpoint_group.psc[0].id
  }

  dynamic "backend" {
    for_each = google_compute_region_network_endpoint_group.psc_additional
    content {
      group = backend.value.id
    }
  }
}

resource "google_compute_url_map" "apigee" {
  count           = local.external ? 1 : 0
  name            = "${var.lb_name_prefix}-urlmap"
  default_service = google_compute_backend_service.apigee[0].id
}

resource "google_compute_managed_ssl_certificate" "apigee" {
  count = local.external && local.effective_cert_mode == "google_managed" ? 1 : 0
  name  = "${var.lb_name_prefix}-cert"
  managed {
    domains = local.all_hostnames
  }
  lifecycle {
    create_before_destroy = true
  }
}

resource "google_compute_ssl_certificate" "self_managed" {
  count       = local.external && local.effective_cert_mode == "self_managed" ? 1 : 0
  name_prefix = "${var.lb_name_prefix}-self-cert-"
  certificate = var.ssl_certificate_pem
  private_key = var.ssl_private_key_pem
  lifecycle {
    create_before_destroy = true
    precondition {
      condition     = var.ssl_certificate_pem != null && var.ssl_private_key_pem != null
      error_message = "cert_mode = \"self_managed\" requires ssl_certificate_pem and ssl_private_key_pem."
    }
  }
}

resource "google_compute_target_https_proxy" "apigee" {
  count   = local.external ? 1 : 0
  name    = "${var.lb_name_prefix}-https-proxy"
  url_map = google_compute_url_map.apigee[0].id
  ssl_certificates = compact([
    var.ssl_certificate != null ? var.ssl_certificate : (
      local.effective_cert_mode == "self_managed" ? try(google_compute_ssl_certificate.self_managed[0].id, "") : try(google_compute_managed_ssl_certificate.apigee[0].id, "")
    )
  ])
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

# Optional HTTP (:80) -> HTTPS (:443) redirect on Global External LB
resource "google_compute_url_map" "http_redirect" {
  count = local.external && var.enable_http_redirect ? 1 : 0
  name  = "${var.lb_name_prefix}-http-redirect"
  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }
}

resource "google_compute_target_http_proxy" "http_redirect" {
  count   = local.external && var.enable_http_redirect ? 1 : 0
  name    = "${var.lb_name_prefix}-http-proxy"
  url_map = google_compute_url_map.http_redirect[0].id
}

resource "google_compute_global_forwarding_rule" "http_redirect" {
  count                 = local.external && var.enable_http_redirect ? 1 : 0
  name                  = "${var.lb_name_prefix}-http-fwd-rule"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_protocol           = "TCP"
  port_range            = "80"
  ip_address            = google_compute_global_address.lb[0].id
  target                = google_compute_target_http_proxy.http_redirect[0].id
}

# ---------------------------------------------------------------- Regional External Managed HTTPS LB (routing = "external-regional")
resource "google_compute_address" "regional_lb" {
  count        = local.external_regional ? 1 : 0
  name         = "${var.lb_name_prefix}-reg-ip"
  region       = var.region
  address_type = "EXTERNAL"
  depends_on   = [google_project_service.apis]
}

resource "google_compute_subnetwork" "regional_proxy" {
  count         = local.external_regional && var.regional_proxy_subnetwork == null ? 1 : 0
  name          = "${var.lb_name_prefix}-proxy-only"
  region        = var.region
  network       = local.psc_network
  ip_cidr_range = var.regional_proxy_subnet_cidr
  purpose       = "REGIONAL_MANAGED_PROXY"
  role          = "ACTIVE"
}

resource "google_compute_region_ssl_certificate" "self_managed" {
  count       = local.external_regional && local.effective_cert_mode == "self_managed" ? 1 : 0
  name_prefix = "${var.lb_name_prefix}-reg-cert-"
  region      = var.region
  certificate = var.ssl_certificate_pem
  private_key = var.ssl_private_key_pem
  lifecycle {
    create_before_destroy = true
    precondition {
      condition     = var.ssl_certificate_pem != null && var.ssl_private_key_pem != null
      error_message = "routing = \"external-regional\" with cert_mode = \"self_managed\" requires ssl_certificate_pem and ssl_private_key_pem."
    }
  }
}

resource "google_compute_region_backend_service" "apigee" {
  count                 = local.external_regional ? 1 : 0
  name                  = "${var.lb_name_prefix}-reg-backend"
  region                = var.region
  protocol              = "HTTPS"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  timeout_sec           = var.lb_backend_timeout_sec
  security_policy       = var.cloud_armor_policy

  backend {
    group           = google_compute_region_network_endpoint_group.psc[0].id
    capacity_scaler = 1.0
  }
}

resource "google_compute_region_url_map" "apigee" {
  count           = local.external_regional ? 1 : 0
  name            = "${var.lb_name_prefix}-reg-urlmap"
  region          = var.region
  default_service = google_compute_region_backend_service.apigee[0].id
}

resource "google_compute_region_target_https_proxy" "apigee" {
  count   = local.external_regional ? 1 : 0
  name    = "${var.lb_name_prefix}-reg-https-proxy"
  region  = var.region
  url_map = google_compute_region_url_map.apigee[0].id
  ssl_certificates = compact([
    var.ssl_certificate != null ? var.ssl_certificate : try(google_compute_region_ssl_certificate.self_managed[0].id, "")
  ])

  lifecycle {
    precondition {
      condition     = var.ssl_certificate != null || (var.ssl_certificate_pem != null && var.ssl_private_key_pem != null)
      error_message = "routing = \"external-regional\" requires a regional SSL certificate (ssl_certificate or ssl_certificate_pem + ssl_private_key_pem)."
    }
  }
  depends_on = [google_compute_subnetwork.regional_proxy]
}

resource "google_compute_forwarding_rule" "regional_https" {
  count                 = local.external_regional ? 1 : 0
  name                  = "${var.lb_name_prefix}-reg-fwd-rule"
  region                = var.region
  ip_protocol           = "TCP"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  port_range            = "443"
  ip_address            = google_compute_address.regional_lb[0].id
  network               = local.psc_network
  target                = google_compute_region_target_https_proxy.apigee[0].id
  depends_on            = [google_compute_subnetwork.regional_proxy]
}

# ---------------------------------------------------------------- Internal PSC Endpoint (routing = "internal")
resource "google_compute_address" "psc" {
  count        = local.internal ? 1 : 0
  name         = "${var.lb_name_prefix}-psc-ip"
  region       = var.region
  address_type = "INTERNAL"
  subnetwork   = var.subnetwork
  depends_on   = [google_project_service.apis]
  lifecycle {
    precondition {
      condition     = var.network != null && var.subnetwork != null
      error_message = "routing = \"internal\" requires network and subnetwork."
    }
  }
}

resource "google_compute_forwarding_rule" "psc" {
  count                 = local.internal ? 1 : 0
  name                  = "${var.lb_name_prefix}-psc-endpoint"
  region                = var.region
  network               = var.network
  ip_address            = google_compute_address.psc[0].id
  target                = local.psc_target
  load_balancing_scheme = "" # required for PSC endpoints to published services
}

# ---------------------------------------------------------------- Optional Cloud DNS A Records (custom domains)
resource "google_dns_record_set" "apigee" {
  for_each     = local.dns_hostnames
  project      = coalesce(var.dns_project_id, var.project_id)
  managed_zone = var.dns_managed_zone
  name         = "${trimsuffix(each.value, ".")}."
  type         = "A"
  ttl          = 300
  rrdatas      = [local.lb_ip]
  depends_on   = [google_project_service.apis]
}
