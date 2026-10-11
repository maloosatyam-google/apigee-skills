terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 6.0"
    }
  }
}

locals {
  # Data residency orgs are managed through the regional control-plane endpoint.
  apigee_endpoint = var.control_plane_location == null ? null : "https://${var.control_plane_location}-apigee.googleapis.com/v1/"
}

provider "google" {
  project                = var.project_id
  region                 = var.region
  apigee_custom_endpoint = local.apigee_endpoint
}

provider "google-beta" {
  project                = var.project_id
  region                 = var.region
  apigee_custom_endpoint = local.apigee_endpoint
}
