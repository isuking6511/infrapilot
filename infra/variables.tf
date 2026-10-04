variable "key_name" {
  type    = string
  default = "infrapilot"
}
# SSH(22)·K3s API(6443)를 열어줄 내 IP. 기본값을 두지 않는다 — 예전 기본값 0.0.0.0/0 때문에
# tfvars에 안 적으면 SSH가 전 세계에 열려 있었음. 배포는 SSM(인바운드 0)으로 하니 좁혀도 지장 없음.
variable "my_ip" {
  type        = string
  description = "My public IP for SSH/K3s access (x.x.x.x/32)"

  validation {
    condition     = can(cidrnetmask(var.my_ip)) && var.my_ip != "0.0.0.0/0"
    error_message = "my_ip는 x.x.x.x/32 형태의 CIDR이어야 하고 0.0.0.0/0은 허용하지 않습니다."
  }
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
