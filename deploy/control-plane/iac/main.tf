locals {
  tags = ["launch-operations", "control-plane"]
}

data "digitalocean_ssh_key" "operations" {
  for_each = toset(var.ssh_key_names)
  name     = each.value
}

resource "digitalocean_droplet" "operations" {
  name       = var.name
  image      = var.image
  region     = var.region
  size       = var.size
  ssh_keys   = [for key in data.digitalocean_ssh_key.operations : key.id]
  backups    = true
  monitoring = true
  ipv6       = true
  tags       = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "digitalocean_firewall" "operations" {
  name        = "launch-operations"
  droplet_ids = [digitalocean_droplet.operations.id]

  inbound_rule {
    protocol         = "tcp"
    port_range       = "22"
    source_addresses = var.ssh_source_cidrs
  }

  inbound_rule {
    protocol         = "tcp"
    port_range       = "80"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  inbound_rule {
    protocol         = "tcp"
    port_range       = "443"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  inbound_rule {
    protocol         = "udp"
    port_range       = "443"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  inbound_rule {
    protocol         = "icmp"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "tcp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "udp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "icmp"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
}

resource "digitalocean_project" "operations" {
  name        = "Launch Operations"
  description = "Reusable application control plane and isolated autonomous workers."
  purpose     = "Operational / Developer tooling"
  environment = "Production"
  resources   = [digitalocean_droplet.operations.urn]
}
