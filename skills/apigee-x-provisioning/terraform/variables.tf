# ================================================================ Core & Billing
variable "project_id" {
  description = "Google Cloud project ID. The Apigee organization gets the same name."
  type        = string
}

variable "region" {
  description = "Primary runtime location: Apigee instance, optional disk KMS key, and PSC NEG (a region such as us-central1, not a zone)."
  type        = string
  validation {
    condition     = !can(regex("-[a-z]$", var.region))
    error_message = "region must be a region (e.g. us-central1 or asia-south1), not a zone (us-central1-a)."
  }
}

variable "additional_regions" {
  description = <<-EOT
    Optional additional runtime regions for multi-region Active-Active HA (e.g. { "us-east1" = {} }).
    Creates an Apigee runtime instance, attaches environments, and wires a regional PSC NEG into the global external LB.
  EOT
  type = map(object({
    instance_name   = optional(string)
    ip_range        = optional(string)
    peering_address = optional(string)
    disk_kms_key_id = optional(string)
    subnetwork      = optional(string)
    psc_subnet_cidr = optional(string)
  }))
  default = {}
  validation {
    condition     = alltrue([for r in keys(var.additional_regions) : !can(regex("-[a-z]$", r))])
    error_message = "All keys in additional_regions must be regions (e.g. us-east1), not zones."
  }
}

variable "analytics_region" {
  description = "Region where Apigee analytics data is stored. Defaults to region. Ignored when data residency is enabled."
  type        = string
  default     = null
}

variable "billing_type" {
  description = "Paid Apigee billing model: PAYG (Pay-as-you-go, default) or SUBSCRIPTION. Evaluation orgs are not supported."
  type        = string
  default     = "PAYG"
  validation {
    condition     = contains(["PAYG", "SUBSCRIPTION"], var.billing_type)
    error_message = "billing_type must be PAYG or SUBSCRIPTION (Evaluation organizations are not supported)."
  }
}

variable "instance_name" {
  description = "Primary runtime instance name. Defaults to region."
  type        = string
  default     = null
}

variable "consumer_accept_list" {
  description = "Extra GCP project IDs/numbers allowed to connect to the instance PSC service attachment (project_id is always included)."
  type        = list(string)
  default     = []
}

variable "access_logging" {
  description = "Optional Envoy access logging configuration on Apigee runtime instance(s)."
  type = object({
    enabled = optional(bool, false)
    filter  = optional(string)
  })
  default = {}
}

# ================================================================ Environments & Analytics
variable "environments" {
  description = <<-EOT
    Environments to create. Defaults to one PAYG INTERMEDIATE environment "dev" with the Analytics add-on enabled,
    attached to envgroup "dev-group". type and analytics apply to PAYG orgs only.
  EOT
  type = map(object({
    type              = optional(string, "INTERMEDIATE")
    analytics         = optional(bool, true)
    envgroup          = optional(string)
    display_name      = optional(string)
    description       = optional(string)
    deployment_type   = optional(string)
    api_proxy_type    = optional(string)
    forward_proxy_uri = optional(string)
  }))
  default = {
    dev = {
      type      = "INTERMEDIATE"
      analytics = true
    }
  }
  validation {
    condition     = alltrue([for e in values(var.environments) : contains(["BASE", "INTERMEDIATE", "COMPREHENSIVE"], e.type)])
    error_message = "environment type must be BASE, INTERMEDIATE, or COMPREHENSIVE."
  }
}

variable "envgroup_hostnames" {
  description = <<-EOT
    Hostnames per environment group, e.g. { "dev-group" = ["api.dev.example.com"] }.
    Groups without an entry default to <group>.<lb-ip-with-dashes>.nip.io when routing is external/external-regional,
    or <group>.<project_id>.internal when routing is internal.
  EOT
  type        = map(list(string))
  default     = {}
}

# ================================================================ Networking Topology (PSC vs VPC Peering)
variable "network_mode" {
  description = "Networking topology: PSC (Private Service Connect without VPC peering, default) or VPC_PEERING (Service Networking PSA peering)."
  type        = string
  default     = "PSC"
  validation {
    condition     = contains(["PSC", "VPC_PEERING"], var.network_mode)
    error_message = "network_mode must be PSC or VPC_PEERING."
  }
}

variable "network" {
  description = "Existing VPC network name or self-link (used for PSC NEG / endpoint, and for PSA peering when network_mode = VPC_PEERING). null = create a dedicated network."
  type        = string
  default     = null
}

variable "subnetwork" {
  description = "Existing subnetwork in primary region for the PSC NEG / endpoint. Required when routing = internal; auto-created when null for external routing."
  type        = string
  default     = null
}

variable "psc_subnet_cidr" {
  description = "CIDR range for the primary-region PSC consumer subnetwork created when subnetwork is null (PSC NEG only uses 1 IP)."
  type        = string
  default     = "10.0.0.0/28"
}

variable "use_existing_psa_connection" {
  description = "When network_mode = VPC_PEERING, skip creating PSA peering ranges and google_service_networking_connection if already configured on Shared VPC."
  type        = bool
  default     = false
}

variable "peering_address" {
  description = "Optional starting IP address (e.g. 10.100.0.0) for the primary /22 VPC peering range when network_mode = VPC_PEERING. null = GCP auto-allocates."
  type        = string
  default     = null
}

variable "peering_support_address" {
  description = "Optional starting IP address (e.g. 10.100.4.0) for the /28 support VPC peering range when network_mode = VPC_PEERING. null = GCP auto-allocates."
  type        = string
  default     = null
}

variable "instance_ip_range" {
  description = "Optional explicit CIDR range(s) (e.g. '10.100.0.0/22' or '10.100.0.0/22,10.100.4.0/28') for the primary runtime instance when network_mode = VPC_PEERING."
  type        = string
  default     = null
}

# ================================================================ Encryption Keys (GMEK default, optional CMEK)
variable "use_cmek" {
  description = "Use Customer-Managed Encryption Keys (Cloud KMS) instead of Google-managed encryption keys (GMEK, default)."
  type        = bool
  default     = false
}

variable "key_ring_prefix" {
  description = "Prefix for Cloud KMS key rings created when use_cmek = true or data residency is enabled."
  type        = string
  default     = "apigee"
}

variable "runtime_db_key_location" {
  description = "Location of the runtime database CMEK key (region or multi-region like us/europe) when use_cmek = true. Defaults to region."
  type        = string
  default     = null
}

variable "runtime_db_kms_key_id" {
  description = "Optional existing Cloud KMS cryptoKey ID for runtime database encryption (used when use_cmek = true; if null, a new key is created)."
  type        = string
  default     = null
}

variable "disk_kms_key_id" {
  description = "Optional existing Cloud KMS cryptoKey ID for the primary runtime instance disk (used when use_cmek = true; if null, a new key is created)."
  type        = string
  default     = null
}

# ================================================================ Data Residency (Disabled by default)
variable "control_plane_location" {
  description = "Data residency control-plane location (e.g. us, eu, ch, au). null = without data residency (default)."
  type        = string
  default     = null
}

variable "consumer_data_region" {
  description = "Data residency consumer data region inside control_plane_location (e.g. us-west1). Required when control_plane_location is set."
  type        = string
  default     = null
}

variable "create_control_plane_keys" {
  description = "Create control-plane and consumer-data CMEK keys when control_plane_location is set."
  type        = bool
  default     = true
}

variable "control_plane_kms_key_id" {
  description = "Optional existing Cloud KMS cryptoKey ID for data-residency control plane encryption."
  type        = string
  default     = null
}

variable "consumer_data_kms_key_id" {
  description = "Optional existing Cloud KMS cryptoKey ID for data-residency consumer data encryption."
  type        = string
  default     = null
}

# ================================================================ Northbound Routing, TLS Certificate, DNS & WAF
variable "routing" {
  description = <<-EOT
    Northbound access mode:
      - "external"          (default): PSC NEG(s) + Global External Managed HTTPS Load Balancer
      - "external-regional"          : PSC NEG + Regional External Managed HTTPS Load Balancer (for strict regional residency)
      - "internal"                   : Internal PSC Endpoint IP inside your VPC
      - "none"                       : Skip northbound routing resources
  EOT
  type        = string
  default     = "external"
  validation {
    condition     = contains(["external", "external-regional", "internal", "none"], var.routing)
    error_message = "routing must be external, external-regional, internal, or none."
  }
}

variable "lb_name_prefix" {
  description = "Prefix for load balancer, PSC NEG, and auto-created network resource names."
  type        = string
  default     = "apigee"
}

variable "cert_mode" {
  description = "TLS certificate mode for external routing: google_managed (default), existing (uses ssl_certificate), or self_managed (uses ssl_certificate_pem + ssl_private_key_pem)."
  type        = string
  default     = "google_managed"
  validation {
    condition     = contains(["google_managed", "existing", "self_managed"], var.cert_mode)
    error_message = "cert_mode must be google_managed, existing, or self_managed."
  }
}

variable "ssl_certificate" {
  description = "Existing SSL certificate self-link (global cert for routing=external, regional cert for routing=external-regional)."
  type        = string
  default     = null
}

variable "ssl_certificate_pem" {
  description = "PEM-encoded TLS certificate content when cert_mode = self_managed."
  type        = string
  default     = null
}

variable "ssl_private_key_pem" {
  description = "PEM-encoded TLS private key content when cert_mode = self_managed."
  type        = string
  default     = null
  sensitive   = true
}

variable "enable_http_redirect" {
  description = "Create an HTTP (:80) -> HTTPS (:443) redirect forwarding rule when routing = external."
  type        = bool
  default     = false
}

variable "regional_proxy_subnetwork" {
  description = "Existing REGIONAL_MANAGED_PROXY subnetwork in region when routing = external-regional (if null, one is created)."
  type        = string
  default     = null
}

variable "regional_proxy_subnet_cidr" {
  description = "CIDR range for the auto-created proxy-only subnetwork when routing = external-regional."
  type        = string
  default     = "10.129.0.0/23"
}

variable "enable_cloud_armor" {
  description = "Create and attach a baseline Google Cloud Armor security policy (Adaptive Protection + OWASP SQLi/XSS rules) to the external global LB backend."
  type        = bool
  default     = false
}

variable "cloud_armor_policy" {
  description = "Existing Cloud Armor security policy self-link/name to attach to the external LB backend service."
  type        = string
  default     = null
}

variable "dns_managed_zone" {
  description = "Optional Cloud DNS managed zone name. When set alongside custom envgroup_hostnames, creates DNS A records pointing to ingress_ip."
  type        = string
  default     = null
}

variable "dns_project_id" {
  description = "GCP project hosting dns_managed_zone (defaults to project_id)."
  type        = string
  default     = null
}

# ================================================================ Enterprise Add-ons, Southbound PSC & Egress NAT
variable "addons" {
  description = "Optional Apigee organization-level add-ons (Advanced API Security, API Ops, Integration, Connectors, Monetization)."
  type = object({
    api_security     = optional(bool, false)
    advanced_api_ops = optional(bool, false)
    integration      = optional(bool, false)
    connectors       = optional(bool, false)
    monetization     = optional(bool, false)
  })
  default = {}
}

variable "enable_nat_ips" {
  description = "Reserve and activate dedicated static outbound NAT IP address(es) per runtime instance for backend IP allowlisting."
  type        = bool
  default     = false
}

variable "nat_ips_per_instance" {
  description = "Number of static outbound NAT IPs to provision per runtime instance when enable_nat_ips = true."
  type        = number
  default     = 1
}

variable "southbound_psc_endpoints" {
  description = "Southbound Apigee PSC Endpoint Attachments (map of attachment_id => { service_attachment, location }) to reach private backend services without VPC peering."
  type = map(object({
    service_attachment = string
    location           = optional(string)
  }))
  default = {}
}

variable "peered_dns_zones" {
  description = "Private Cloud DNS peering zones for Apigee southbound resolution (requires network_mode = VPC_PEERING)."
  type = map(object({
    domain            = string
    description       = optional(string)
    target_project_id = optional(string)
    target_network_id = optional(string)
  }))
  default = {}
}
