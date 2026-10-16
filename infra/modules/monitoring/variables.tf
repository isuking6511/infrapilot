variable "vpc_id" {
  type = string
}

variable "k3s_sg_id" {
  type = string
}

variable "subnet_id" {
  type = string
}

variable "key_name" {
  type        = string
  description = "EC2 key pair name for SSH access"
}