variable "vpc_id" {
  type        = string
  description = "VPC ID"
}

variable "subnet_ids" {
  type        = list(string)
  description = "Lambda가 들어갈 서브넷 ID"
}

variable "ecr_image_uri" {
  type        = string
  description = "ECR 이미지 URI"
}

variable "db_host" {
  type        = string
  description = "RDS 엔드포인트"
}

variable "db_password" {
  type        = string
  sensitive   = true
  description = "DB 패스워드"
}
variable "lambda_sg_id" {
  type = string
}

variable "gemini_api_key" {
  type      = string
  sensitive = true
  description = "Google Gemini API 키"
}

variable "copilot_token" {
  type      = string
  sensitive = true
  default   = ""
  description = "GitHub Copilot OAuth 토큰 (선택)"
}