output "droplet_id" {
  value = digitalocean_droplet.operations.id
}

output "public_ipv4" {
  value = digitalocean_droplet.operations.ipv4_address
}

output "private_ipv4" {
  value = digitalocean_droplet.operations.ipv4_address_private
}

output "firewall_id" {
  value = digitalocean_firewall.operations.id
}
