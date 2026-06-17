output "repository_url" {
  value       = aws_ecr_repository.lambda.repository_url
  description = "ECR 리포지토리 URL (이미지 푸시할 때 사용)"
}

output "dashboard_repository_url" {
  value       = aws_ecr_repository.dashboard.repository_url
  description = "Dashboard ECR 리포지토리 URL"
}