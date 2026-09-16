variable "name" {
  description = "Stable operations host name."
  type        = string
  default     = "launch-operations-1"
}

variable "region" {
  description = "DigitalOcean region slug."
  type        = string
  default     = "sfo3"
}

variable "size" {
  description = "DigitalOcean size slug; eight GiB is the accepted minimum."
  type        = string
  default     = "s-4vcpu-8gb"
}

variable "image" {
  description = "Pinned base image family."
  type        = string
  default     = "ubuntu-24-04-x64"
}

variable "ssh_key_names" {
  description = "DigitalOcean account SSH-key names installed only when creating a replacement host."
  type        = list(string)
  default     = ["henry-l2l-desktop-wsl"]

  validation {
    condition     = length(var.ssh_key_names) > 0
    error_message = "At least one administrative SSH key is required."
  }
}

variable "ssh_source_cidrs" {
  description = "CIDRs permitted to reach SSH. Keep GitHub-hosted deployment access in mind when narrowing."
  type        = list(string)
  default     = ["0.0.0.0/0", "::/0"]
}
