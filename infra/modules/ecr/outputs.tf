output "repository_url" {
  value       = aws_ecr_repository.web.repository_url
  description = "웹/스캐너 이미지 ECR URL"
}

output "repository_arn" {
  value       = aws_ecr_repository.web.arn
  description = "웹/스캐너 이미지 ECR ARN (CI/CD 배포 Role 권한 범위)"
}
