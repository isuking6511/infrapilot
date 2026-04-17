output "repository_url" {
  value       = aws_ecr_repository.lambda.repository_url
  description = "ECR 리포지토리 URL (이미지 푸시할 때 사용)"
}