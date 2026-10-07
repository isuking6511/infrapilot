variable "github_repo" {
  type        = string
  description = "OIDC로 역할을 빌릴 수 있는 GitHub 레포"
}

variable "ecr_repository_arn" {
  type        = string
  description = "push를 허용할 ECR 레포 ARN"
}