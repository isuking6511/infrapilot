variable "key_name" {
  type    = string
  default = "infrapilot"
}
variable "my_ip" {
  type        = string
  description = "My public IP for SSH/K3s access (x.x.x.x/32)"
  default     = "0.0.0.0/0"
}
variable "vpc_cidr" {
  type        = string
  description = "CIDR block for the VPC"
  default     = "10.0.0.0/16"
}
variable "subnet1_cidr" {
  type        = string
  description = "CIDR block for the first subnet"
  default     = "10.0.1.0/24"
}
variable "subnet2_cidr" {
  type        = string
  description = "CIDR block for the second subnet"
  default     = "10.0.2.0/24"
}
variable "subnet3_cidr" {
  type        = string
  description = "CIDR block for the third subnet"
  default     = "10.0.3.0/24"
}
variable "subnet4_cidr" {
  type        = string
  description = "CIDR block for the fourth subnet"
  default     = "10.0.4.0/24"
}
variable "az_a" {
  type        = string
  description = "Availability Zone A"
  default     = "ap-northeast-2a"
}
variable "az_b" {
  type        = string
  description = "Availability Zone B"
  default     = "ap-northeast-2b"
}

variable "db_username" {
  type        = string
  description = "RDS master username"
}

variable "db_password" {
  type        = string
  sensitive   = true
  description = "RDS master password"
}
