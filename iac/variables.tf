variable "my_ip" {
  type        = string
  description = "My public IP for SSH/K3s access (x.x.x.x/32)"
}
variable "key_name" {
  type    = string
  default = "infrapilot"
}