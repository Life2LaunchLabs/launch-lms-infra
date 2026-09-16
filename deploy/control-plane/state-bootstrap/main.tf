terraform {
  required_version = ">= 1.10.0"

  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.100"
    }
  }
}

provider "digitalocean" {}

variable "bucket_name" {
  description = "Existing private SFO3 bucket used for locked OpenTofu state."
  type        = string
}

resource "digitalocean_spaces_bucket" "state" {
  name          = var.bucket_name
  region        = "sfo3"
  acl           = "private"
  force_destroy = false

  versioning {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }
}
