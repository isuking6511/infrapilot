output "deploy_role_arn" {
  value       = aws_iam_role.github_deploy.arn
  description = "GitHub 저장소 변수 AWS_DEPLOY_ROLE_ARN 에 넣을 값"
}
