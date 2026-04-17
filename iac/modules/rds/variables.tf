variable "private_subnet_ids" {
  type        = list(string)
  description = "Private subnet ID"
}

variable "db_username" {
  type        = string
  description = "DB 마스터 사용자 이름"
}

variable "db_password" {
  type        = string
  sensitive   = true
  description = "DB 마스터 패스워드"
}

variable "rds_sg_id" {
  type        = string
  description = "RDS 보안그룹 ID"
}