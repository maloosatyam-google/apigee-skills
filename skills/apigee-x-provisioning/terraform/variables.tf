# ---------------------------------------------------------------- core
variable "project_id" {
  description = "Google Cloud project. The Apigee organization gets the same name."
  type        = string
}

variable "region" {
  description = "Runtime location: Apigee instance, disk key and PSC NEG (a region such as us-central1, not a zone)."
  type        = string
  validation {
    condition     = !can(regex("-[a-z]$", var.region))
    error_message = "region must be a region (us-west1), not a zone (us-west1-a)."
  }
}

variable "analytics_region" {
  description = "Where analytics data is stored. Defaults to region. Ignored for data residency orgs."
  type        = string
  default     = null
}

variable "billing_type" {
  description = "PAYG (Pay-as-you-go) or SUBSCRIPTION."
  type        = string
  default     = "PAYG"
  validation {
    condition     = contains(["PAYG", "SUBSCRIPTION"], var.billing_type)
    error_message = "billing_type must be PAYG or SUBSCRIPTION."
  }
}

variable "instance_name" {
  description = "Runtime instance name. Defaults to the region."
  type        = string
  default     = null
}

variable "consumer_accept_list" {
  description = "Extra projects allowed to connect to the instance service attachment (the org project is always included)."
  type        = list(string)
  default     = []
}

# ---------------------------------------------------------------- environments
variable "environments" {
  description = <<-EOT
    Environments to create. Defaults to one PAYG INTERMEDIATE environment "dev" with the Analytics add-on enabled,
    attached to envgroup "dev-group". type and analytics apply to PAYG orgs only.
  EOT
  type = map(object({
    type         = optional(string, "INTERMEDIATE")
    analytics    = optional(bool, true)
    envgroup     = optional(string)
    display_name = optional(string)
    description  = optional(string)
  }))
  default = {
    dev = {}
  }
  validation {
    condition     = alltrue([for e in values(var.environments) : contains(["BASE", "INTERMEDIATE", "COMPREHENSIVE"], e.type)])
    error_message = "environment type must be BASE, INTERMEDIATE or COMPREHENSIVE."
  }
}

variable "envgroup_hostnames" {
  description = <<-EOT
    Hostnames per environment group, e.g. { "dev-group" = ["api.dev.example.com"] }. Groups without an entry get
    <group>.<lb-ip-with-dashes>.nip.io when routing = "external" (handy for demos; use real DNS for production).
  EOT
  type        = map(list(string))
  default     = {}
}

# ---------------------------------------------------------------- encryption keys
variable "use_cmek" {
  description = "Create and use Customer-Managed Encryption Keys (Cloud KMS) instead of Google-managed encryption keys (GMEK)."
  type        = bool
  default     = false
}

variable "key_ring_prefix" {
  description = "Prefix for the Cloud KMS key rings created for Apigee (when use_cmek = true or data residency is enabled)."
  type        = string
  default     = "apigee"
}

variable "runtime_db_key_location" {
  description = "Location of the runtime database CMEK key (region or multi-region like us) when use_cmek = true. Defaults to region."
  type        = string
  default     = null
}

# ---------------------------------------------------------------- data residency
variable "control_plane_location" {
  description = "Data residency control plane location (e.g. us, eu). null = no data residency."
  type        = string
  default     = null
}

variable "consumer_data_region" {
  description = "Data residency consumer data region inside the control plane location (e.g. us-west1)."
  type        = string
  default     = null
}

variable "create_control_plane_keys" {
  description = "Create control plane / consumer data CMEK keys (required when control_plane_location is us or eu)."
  type        = bool
  default     = true
}

# ---------------------------------------------------------------- routing
variable "routing" {
  description = "Northbound access: external (PSC + global external HTTPS LB), internal (PSC endpoint) or none."
  type        = string
  default     = "external"
  validation {
    condition     = contains(["external", "internal", "none"], var.routing)
    error_message = "routing must be external, internal or none."
  }
}

variable "network" {
  description = "Existing VPC network for the PSC NEG (external) or PSC endpoint (internal). null = create a dedicated PSC consumer network."
  type        = string
  default     = null
}

variable "subnetwork" {
  description = "Existing subnetwork in region for the PSC NEG / endpoint. null = create a dedicated PSC consumer subnetwork."
  type        = string
  default     = null
}

variable "psc_subnet_cidr" {
  description = "CIDR range for the PSC consumer subnetwork created when network is null (PSC NEG only uses 1 IP from this subnet)."
  type        = string
  default     = "10.0.0.0/28"
}

variable "lb_name_prefix" {
  description = "Prefix for load balancer resource names."
  type        = string
  default     = "apigee"
}

variable "ssl_certificate" {
  description = "Existing global SSL certificate self link to use instead of a Google-managed certificate."
  type        = string
  default     = null
}
