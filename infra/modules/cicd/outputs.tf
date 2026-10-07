output "role_arn" {
  value       = aws_iam_role.github_actions.arn
  description = "GitHub Actions가 빌릴 역할 ARN"
}