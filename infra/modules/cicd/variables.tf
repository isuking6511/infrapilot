variable "github_repo" {
  type        = string
  description = "OIDC로 Role을 쓸 수 있는 GitHub 저장소 (owner/repo)"
  default     = "isuking6511/infrapilot"
}

variable "instance_name_tag" {
  type        = string
  description = "배포 대상 EC2의 Name 태그 (SSM SendCommand 허용 범위)"
  default     = "infrapilot"
}

variable "ec2_role_name" {
  type        = string
  description = "K3s EC2 인스턴스 프로파일의 IAM Role 이름 (SSM 권한 부여 대상)"
}

variable "ecr_repository_arn" {
  type        = string
  description = "배포 Role이 push할 수 있는 ECR 레포 ARN (이 레포 하나로 권한 제한)"
}
