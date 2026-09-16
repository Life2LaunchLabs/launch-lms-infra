locals {
  topology = yamldecode(file("${path.module}/../../environments/launch-lms.yaml"))
}

resource "digitalocean_record" "unstable_apex" {
  domain = local.topology.dns.app_zone
  type   = "A"
  name   = local.topology.dns.records.unstable_apex
  value  = local.topology.dns.unstable_ipv4
  ttl    = 300

  lifecycle {
    prevent_destroy = true
  }
}

resource "digitalocean_record" "unstable_wildcard" {
  domain = local.topology.dns.app_zone
  type   = "A"
  name   = local.topology.dns.records.unstable_wildcard
  value  = local.topology.dns.unstable_ipv4
  ttl    = 300

  lifecycle {
    prevent_destroy = true
  }
}

# The .dev apex remains on the old unstable host until the repository topology
# explicitly records acceptance of the nested unstable deployment.
resource "digitalocean_record" "operations_apex" {
  count  = local.topology.dns.operations_apex_cutover ? 1 : 0
  domain = local.topology.dns.operations_zone
  type   = "A"
  name   = local.topology.dns.records.operations
  value  = digitalocean_droplet.operations.ipv4_address
  ttl    = 300

  lifecycle {
    prevent_destroy = true
  }
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

  lifecycle {
    prevent_destroy = true

    # DigitalOcean accepts SSH keys only at creation and does not return the
    # original key IDs when an existing Droplet is imported. Keep ssh_keys in
    # the creation contract while preventing an unknowable imported value from
    # forcing replacement of the adopted host.
    ignore_changes = [ssh_keys]
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
